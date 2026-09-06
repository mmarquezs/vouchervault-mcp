# extapi - token-authenticated item API overlay for VoucherVault.
# Derivative of VoucherVault (https://github.com/l4rm4nd/VoucherVault), licensed GPL-3.0.
# Portions Copyright l4rm4nd / VoucherVault contributors.
# Modifications Copyright (c) 2026 mmarquezs.
"""Token-authenticated JSON item API for VoucherVault (overlay package).

This package is NOT a Django app and must NOT be added to INSTALLED_APPS:
it provides views + urls only, wired into upstream ``myproject/urls.py`` by
``patches/0001-extapi-wire-urls.patch`` (kept as a tiny 2-line diff on
purpose so upstream drift breaks the image build loudly instead of silently
misbehaving).

Auth reuses upstream's ``myapp.decorators.require_authorization_header_with_api_token``
(the same AppSettings API token that protects the stats endpoint). The token
grants full item CRUD across ALL items (no per-user scoping) — treat it as
admin-equivalent.

Owner for API-created items: ``EXTAPI_ITEM_OWNER`` env var (a username) when
set, otherwise the first ACTIVE superuser, resolved at request time.

SECURITY: never log redeem codes, API tokens or Authorization headers here.
"""

import base64
import io
import json
import logging
import os

import qrcode
import treepoem

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone, translation
from django.utils.translation import gettext
from django.views.decorators.csrf import csrf_exempt

from myapp.decorators import require_authorization_header_with_api_token
from myapp.forms import ItemForm
from myapp.models import Item, Transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tiny helpers copied from upstream myapp.views (calculate_ean13_check_digit /
# is_valid_ean13). Copied instead of imported so this overlay does not depend
# on the upstream views module (heavy import chain, more drift surface); the
# logic is deliberately identical.
# ---------------------------------------------------------------------------

def _calculate_ean13_check_digit(code):
    sum_odd = sum(int(code[i]) for i in range(0, 12, 2))
    sum_even = sum(int(code[i]) for i in range(1, 12, 2))
    checksum = (sum_odd + 3 * sum_even) % 10
    return (10 - checksum) % 10


def _is_valid_ean13(code):
    if len(code) != 13 or not code.isdigit():
        return False
    return int(code[-1]) == _calculate_ean13_check_digit(code)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _BodyError(Exception):
    """Raised when the request body cannot be parsed into a dict."""


def _parse_body(request):
    """Return a plain dict from a JSON or form-encoded request body."""
    if not request.body:
        return {}
    content_type = (request.content_type or "").lower()
    if "application/x-www-form-urlencoded" in content_type:
        return request.POST.dict()
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _BodyError("Request body must be valid JSON or form-encoded data.")
    if not isinstance(payload, dict):
        raise _BodyError("JSON body must be an object.")
    return payload


def _form_errors(form):
    # structured, JSON-safe version of form.errors (no secrets in there)
    return json.loads(form.errors.as_json())


def _resolve_owner():
    """Owner for API-created items.

    EXTAPI_ITEM_OWNER (username) when set, otherwise the first ACTIVE
    superuser by id. Returns None when the configured user does not exist
    or no active superuser exists — callers respond with an error then.
    """
    username = os.environ.get("EXTAPI_ITEM_OWNER", "").strip()
    if username:
        return User.objects.filter(username=username, is_active=True).first()
    return (
        User.objects.filter(is_active=True, is_superuser=True).order_by("id").first()
    )


def _generate_qr_base64(item):
    """Generate qr_code_base64 exactly like upstream create_item/edit_item.

    Mirrors upstream: if code_type is not qrcode but the redeem code is a
    valid EAN-13, force ean13; render qrcode via the qrcode package, anything
    else via treepoem (scale=2); store base64 PNG. May mutate item.code_type.
    """
    buffer = io.BytesIO()
    if item.code_type != "qrcode" and _is_valid_ean13(item.redeem_code):
        code_type = "ean13"
        item.code_type = "ean13"
    else:
        code_type = item.code_type

    if code_type == "qrcode":
        qr = qrcode.make(item.redeem_code)
        qr.save(buffer)
    else:
        barcode = treepoem.generate_barcode(
            barcode_type=code_type,
            data=item.redeem_code,
            scale=2,
        )
        barcode.save(buffer, "PNG")

    return base64.b64encode(buffer.getvalue()).decode()


def _serialize_item(item):
    return {
        "id": str(item.id),
        "type": item.type,
        "name": item.name,
        "redeem_code": item.redeem_code,
        "code_type": item.code_type,
        "issuer": item.issuer,
        "value": str(item.value),
        "value_type": item.value_type,
        "currency": item.currency,
        "issue_date": item.issue_date.isoformat() if item.issue_date else None,
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "description": item.description,
        "is_used": item.is_used,
        "days_left": (item.expiry_date - timezone.localdate()).days,
    }


def _mark_used_description():
    """The localized "marked as used" transaction description, deterministically.

    Upstream evaluates this string lazily in the request's active language.
    Pinning the default language here keeps API toggle-on/toggle-off
    consistent and matches transactions created by the web UI under the
    default language (the toggle-back path must find and delete them).
    """
    translation.activate(settings.LANGUAGE_CODE)
    return gettext("Marked as used, removing remaining value")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@csrf_exempt
@require_authorization_header_with_api_token
def items_collection(request):
    """GET /api/v1/items — filtered list. POST /api/v1/items — create."""
    if request.method == "GET":
        return _list_items(request)
    if request.method == "POST":
        return _create_item(request)
    return JsonResponse({"error": "Method not allowed"}, status=405)


def _list_items(request):
    items = Item.objects.all()

    search = (request.GET.get("search") or "").strip()
    if search:
        items = items.filter(
            Q(name__icontains=search)
            | Q(issuer__icontains=search)
            | Q(redeem_code__icontains=search)
        )

    item_type = (request.GET.get("type") or "").strip()
    if item_type:
        items = items.filter(type=item_type)

    username = (request.GET.get("username") or "").strip()
    if username:
        items = items.filter(user__username=username)

    include_used = (request.GET.get("include_used") or "false").lower() in ("1", "true", "yes")
    if not include_used:
        items = items.filter(is_used=False)

    include_expired = (request.GET.get("include_expired") or "false").lower() in ("1", "true", "yes")
    if not include_expired:
        items = items.filter(expiry_date__gte=timezone.localdate())

    items = items.order_by("expiry_date", "id")
    return JsonResponse([_serialize_item(i) for i in items], safe=False)


def _create_item(request):
    try:
        data = _parse_body(request)
    except _BodyError as exc:
        return JsonResponse({"errors": {"__all__": [{"message": str(exc), "code": "invalid"}]}}, status=400)

    # Match what the upstream web UI posts: value_type has a form initial of
    # "money", currency defaults to the model/UI default, issue_date is
    # prefilled with today. Absent keys fall back to those.
    data.setdefault("value_type", "money")
    data.setdefault("currency", "EUR")
    data.setdefault("issue_date", timezone.localdate().isoformat())
    # Upstream 1.30.x made code_type a required form field (no model-level
    # enforcement); the model default is qrcode, so fall back to that.
    data.setdefault("code_type", "qrcode")

    form = ItemForm(data=data)
    if not form.is_valid():
        return JsonResponse({"errors": _form_errors(form)}, status=400)

    owner = _resolve_owner()
    if owner is None:
        logger.error("extapi: no item owner available (EXTAPI_ITEM_OWNER unset or unknown, no active superuser)")
        return JsonResponse(
            {"errors": {"__all__": [{"message": "No item owner available. Set EXTAPI_ITEM_OWNER or create an active superuser.", "code": "invalid"}]}},
            status=500,
        )

    item = form.save(commit=False)
    item.user = owner
    try:
        item.qr_code_base64 = _generate_qr_base64(item)
    except Exception as exc:
        # Same behaviour as upstream create_item: surface a form error,
        # never a half-saved item. Message contains no redeem code.
        form.add_error(None, "Failed to generate barcode. Error: {}".format(exc))
        return JsonResponse({"errors": _form_errors(form)}, status=400)
    item.file = None  # no file upload via the JSON API
    item.save()
    logger.info("extapi: created item %s", item.id)
    return JsonResponse(_serialize_item(item), status=201)


@csrf_exempt
@require_authorization_header_with_api_token
def item_detail(request, item_id):
    """GET/PATCH/DELETE /api/v1/items/<uuid>/."""
    item = Item.objects.filter(id=item_id).first()
    if item is None:
        return JsonResponse({"error": "Item not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize_item(item))

    if request.method == "PATCH":
        return _patch_item(request, item)

    if request.method == "DELETE":
        # Replicate upstream delete_item: remove the attached file from disk
        if item.file:
            if os.path.isfile(item.file.path):
                os.remove(item.file.path)
        item.delete()
        logger.info("extapi: deleted item %s", item_id)
        return HttpResponse(status=204)

    return JsonResponse({"error": "Method not allowed"}, status=405)


def _patch_item(request, item):
    original_redeem_code = item.redeem_code
    original_code_type = item.code_type

    try:
        provided = _parse_body(request)
    except _BodyError as exc:
        return JsonResponse({"errors": {"__all__": [{"message": str(exc), "code": "invalid"}]}}, status=400)

    # Partial update: merge provided keys over the item's current values.
    # Dates are stringified (form expects YYYY-MM-DD); no 'file' key — the
    # JSON API never touches the attached file.
    current = {
        "name": item.name,
        "issuer": item.issuer,
        "redeem_code": item.redeem_code,
        "pin": item.pin or "",
        "issue_date": item.issue_date.isoformat() if item.issue_date else "",
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else "",
        "description": item.description or "",
        "logo_slug": item.logo_slug or "",
        "type": item.type,
        "value": str(item.value),
        "value_type": item.value_type,
        "currency": item.currency,
        "code_type": item.code_type,
        "tile_color": item.tile_color or "",
    }
    current.update(provided)

    form = ItemForm(instance=item, data=current)
    if not form.is_valid():
        return JsonResponse({"errors": _form_errors(form)}, status=400)

    item = form.save(commit=False)

    # Replicate upstream edit_item: regenerate the barcode when the code or
    # its type changed.
    if original_code_type != item.code_type or original_redeem_code != item.redeem_code:
        try:
            item.qr_code_base64 = _generate_qr_base64(item)
        except Exception as exc:
            form.add_error(None, "Failed to generate barcode. Error: {}".format(exc))
            return JsonResponse({"errors": _form_errors(form)}, status=400)

    item.save()
    logger.info("extapi: updated item %s", item.id)
    return JsonResponse(_serialize_item(item))


@csrf_exempt
@require_authorization_header_with_api_token
def item_toggle(request, item_id):
    """POST /api/v1/items/<uuid>/toggle-status — replicate upstream toggle_item_status."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    item = Item.objects.filter(id=item_id).first()
    if item is None:
        return JsonResponse({"error": "Item not found"}, status=404)

    desc_txt = _mark_used_description()

    if item.is_used:
        # Re-toggle to available: remove the previously created transaction
        item.is_used = False
        transactions = Transaction.objects.filter(item=item, description=desc_txt).all()
        if transactions:
            transactions.delete()
    else:
        # Mark as used and create a transaction removing the remaining value
        item.is_used = True
        transactions = item.transactions.all()
        value_to_remove = item.value + sum(t.value for t in transactions)
        transaction = Transaction(
            item=item,
            description=desc_txt,
            value=-value_to_remove,
        )
        transaction.save()

    item.save()
    logger.info("extapi: toggled item %s (is_used=%s)", item.id, item.is_used)
    return JsonResponse(_serialize_item(item))
