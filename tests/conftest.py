"""Shared fixtures: a fake VoucherVault behind httpx.MockTransport.

Simulates the relevant upstream surface (VoucherVault main branch):
  - i18n-prefixed routes ({prefix}/...) incl. 404 fallback when no prefix
  - Django login form at {prefix}/accounts/login/ (csrfmiddlewaretoken)
  - session cookie gating on all pages
  - Bearer-token stats API at {prefix}/api/get/stats
  - create/edit forms (csrf + all ItemForm fields) with errorlist on failure
  - @require_POST delete + toggle endpoints
"""

from __future__ import annotations

import urllib.parse
from datetime import date, timedelta

import httpx
import pytest

BASE = "https://vouchervault.test"
CSRF = "test-csrf-token-value"
SESSION_ID = "test-session-id"

ITEM_COUPON = "11111111-1111-1111-1111-111111111111"
ITEM_USED = "22222222-2222-2222-2222-222222222222"
ITEM_EXPIRED = "33333333-3333-3333-3333-333333333333"
ITEM_LOYALTY = "44444444-4444-4444-4444-444444444444"

USERNAME = "admin"
PASSWORD = "dummy-password-for-tests"
API_TOKEN = "dummy-api-token-for-tests"

# Exact ItemForm.Meta.fields from upstream myapp/forms.py
# (https://github.com/l4rm4nd/VoucherVault, fetched 2026-09):
#   fields = ['name', 'issuer', 'redeem_code', 'pin', 'issue_date',
#             'expiry_date', 'description', 'logo_slug', 'type', 'value',
#             'value_type', 'currency', 'file', 'code_type', 'tile_color']
REAL_ITEM_FORM_FIELDS = [
    "name",
    "issuer",
    "redeem_code",
    "pin",
    "issue_date",
    "expiry_date",
    "description",
    "logo_slug",
    "type",
    "value",
    "value_type",
    "currency",
    "file",
    "code_type",
    "tile_color",
]

SELECT_FIELDS = {
    "type": ["voucher", "giftcard", "coupon", "loyaltycard"],
    "code_type": ["qrcode", "code39", "ean13"],
    "value_type": ["money", "percentage", "multiplier"],
    "currency": ["EUR", "USD"],
}


def default_items() -> dict[str, dict[str, str]]:
    """Current form-field values for existing items (mirrors stats_payload)."""
    return {
        ITEM_COUPON: {
            "name": "Amazon 10EUR",
            "issuer": "amazon.es",
            "redeem_code": "AMZN-2026-XYZ",
            "pin": "",
            "issue_date": "2026-01-01",
            "expiry_date": (date.today() + timedelta(days=30)).isoformat(),
            "description": "birthday coupon",
            "logo_slug": "",
            "type": "coupon",
            "value": "25.00",
            "value_type": "money",
            "currency": "EUR",
            "code_type": "qrcode",
            "tile_color": "",
        },
        ITEM_USED: {
            "name": "El Corte Ingles Gift",
            "issuer": "elcorteingles.es",
            "redeem_code": "ECI-GIFT-77",
            "pin": "1234",
            "issue_date": "2026-01-15",
            "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            "description": "",
            "logo_slug": "",
            "type": "giftcard",
            "value": "50.00",
            "value_type": "money",
            "currency": "EUR",
            "code_type": "qrcode",
            "tile_color": "",
        },
        ITEM_EXPIRED: {
            "name": "Old Voucher",
            "issuer": "zalando.es",
            "redeem_code": "OLD-VOUCHER-1",
            "pin": "",
            "issue_date": "2019-01-01",
            "expiry_date": "2020-01-01",
            "description": "",
            "logo_slug": "",
            "type": "voucher",
            "value": "5.00",
            "value_type": "money",
            "currency": "EUR",
            "code_type": "qrcode",
            "tile_color": "",
        },
        ITEM_LOYALTY: {
            "name": "Loyalty Card",
            "issuer": "mercadona.es",
            "redeem_code": "9990123456789",
            "pin": "",
            "issue_date": "2026-02-02",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "description": "",
            "logo_slug": "",
            "type": "loyaltycard",
            "value": "0.00",
            "value_type": "money",
            "currency": "EUR",
            "code_type": "qrcode",
            "tile_color": "",
        },
    }


def stats_payload() -> dict:
    return {
        "item_stats": {"total_items": 4},
        "issuer_stats": [],
        "user_stats": {"total_users": 1},
        "item_details": [
            {
                "id": ITEM_COUPON,
                "type": "coupon",
                "name": "Amazon 10EUR",
                "redeem_code": "AMZN-2026-XYZ",
                "code_type": "qrcode",
                "pin": None,
                "issuer": "amazon.es",
                "value": "25.00",
                "value_type": "money",
                "currency": "EUR",
                "issue_date": "2026-01-01",
                "expiry_date": (date.today() + timedelta(days=30)).isoformat(),
                "description": "birthday coupon",
                "is_used": False,
                "is_pinned": False,
                "user__username": USERNAME,
            },
            {
                "id": ITEM_USED,
                "type": "giftcard",
                "name": "El Corte Ingles Gift",
                "redeem_code": "ECI-GIFT-77",
                "code_type": "qrcode",
                "pin": "1234",
                "issuer": "elcorteingles.es",
                "value": "50.00",
                "value_type": "money",
                "currency": "EUR",
                "issue_date": "2026-01-15",
                "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
                "description": None,
                "is_used": True,
                "is_pinned": False,
                "user__username": USERNAME,
            },
            {
                "id": ITEM_EXPIRED,
                "type": "voucher",
                "name": "Old Voucher",
                "redeem_code": "OLD-VOUCHER-1",
                "code_type": "qrcode",
                "pin": None,
                "issuer": "zalando.es",
                "value": "5.00",
                "value_type": "money",
                "currency": "EUR",
                "issue_date": "2019-01-01",
                "expiry_date": "2020-01-01",
                "description": None,
                "is_used": False,
                "is_pinned": False,
                "user__username": USERNAME,
            },
            {
                "id": ITEM_LOYALTY,
                "type": "loyaltycard",
                "name": "Loyalty Card",
                "redeem_code": "9990123456789",
                "code_type": "qrcode",
                "pin": None,
                "issuer": "mercadona.es",
                "value": "0.00",
                "value_type": "money",
                "currency": "EUR",
                "issue_date": "2026-02-02",
                "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
                "description": None,
                "is_used": False,
                "is_pinned": False,
                "user__username": USERNAME,
            },
        ],
    }


def render_login_page(token: str, with_error: bool = False) -> str:
    error = '<ul class="errorlist"><li>Wrong username or password.</li></ul>' if with_error else ""
    return f"""<html><body>{error}
<form method="post" action="/en/accounts/login/">
  <input type="hidden" name="csrfmiddlewaretoken" value="{token}" />
  <input type="hidden" name="next" value="/" />
  <input type="text" name="username" />
  <input type="password" name="password" />
  <button type="submit">Login</button>
</form>
</body></html>"""


def render_item_form(path: str, token: str, values: dict[str, str], errors: list[str] | None = None) -> str:
    parts = [
        (
            '<html><body><nav><form method="post" action="/en/logout/">'
            f'<input type="hidden" name="csrfmiddlewaretoken" value="{token}" />'
            "</form></nav>"
        )
    ]
    if errors:
        parts.append('<ul class="errorlist">')
        for err in errors:
            parts.append(f"<li>{err}</li>")
        parts.append("</ul>")
    parts.append(f'<form method="post" action="{path}" enctype="multipart/form-data">')
    parts.append(f'<input type="hidden" name="csrfmiddlewaretoken" value="{token}" />')
    for name in REAL_ITEM_FORM_FIELDS:
        if name == "file":
            parts.append(f'<input type="file" name="{name}" />')
            continue
        value = values.get(name, "")
        if name in SELECT_FIELDS:
            options = []
            for opt in SELECT_FIELDS[name]:
                selected = " selected" if opt == value else ""
                options.append(f'<option value="{opt}"{selected}>{opt}</option>')
            parts.append(f'<select name="{name}">{"".join(options)}</select>')
        elif name == "description":
            parts.append(f'<textarea name="{name}">{value}</textarea>')
        elif name == "value_type":
            # upstream renders it as a hidden input
            parts.append(f'<input type="hidden" name="{name}" value="{value}" />')
        else:
            input_type = "date" if name in ("issue_date", "expiry_date") else "text"
            parts.append(f'<input type="{input_type}" name="{name}" value="{value}" />')
    parts.append('<button type="submit">Save</button>')
    parts.append("</form></body></html>")
    return "".join(parts)
class FakeVault:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.items = default_items()
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.login_failures = 0
        self.force_login_redirect_get = 0
        self.force_login_redirect_post = 0
        self.prefix_mode = "en"  # "en" or "none" (no i18n prefix)
        self.api_token = API_TOKEN

    # ------------------------- helpers ------------------------- #

    def calls(self, method: str, path_suffix: str) -> list[dict]:
        return [
            r
            for r in self.requests
            if r["method"] == method and r["path"].endswith(path_suffix)
        ]

    @staticmethod
    def _has_session(request: httpx.Request) -> bool:
        cookie = request.headers.get("cookie", "")
        return f"sessionid={SESSION_ID}" in cookie

    @staticmethod
    def _csrf_ok(data: dict, request: httpx.Request) -> bool:
        token = data.get("csrfmiddlewaretoken") or request.headers.get("x-csrftoken", "")
        return token == CSRF

    def _prefix(self, request: httpx.Request) -> str:
        return "/en" if request.url.path.startswith("/en") else ""

    # ------------------------- handler ------------------------- #

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        data: dict[str, str] = {}
        if method == "POST":
            data = dict(
                urllib.parse.parse_qsl(request.content.decode("utf-8"), keep_blank_values=True)
            )
        self.requests.append(
            {
                "method": method,
                "path": path,
                "params": dict(request.url.params),
                "headers": dict(request.headers),
                "data": data,
            }
        )

        # prefix disabled on this instance -> /en/* 404s
        if self.prefix_mode == "none" and path.startswith("/en"):
            return httpx.Response(404, text="not found")

        # simulate server-side session invalidation (redirect to login)
        if method == "GET" and self.force_login_redirect_get > 0 and path != f"{self._prefix(request)}/accounts/login/":
            self.force_login_redirect_get -= 1
            return httpx.Response(302, headers={"location": f"{self._prefix(request)}/accounts/login/?next={path}"})
        if method == "POST" and self.force_login_redirect_post > 0:
            self.force_login_redirect_post -= 1
            return httpx.Response(302, headers={"location": f"{self._prefix(request)}/accounts/login/?next={path}"})

        # ---------- login ---------- #
        if path.endswith("/accounts/login/") and method == "GET":
            return httpx.Response(
                200,
                html=render_login_page(CSRF),
                headers=[("set-cookie", f"csrftoken={CSRF}; Path=/")],
            )
        if path.endswith("/accounts/login/") and method == "POST":
            if not self._csrf_ok(data, request) or request.headers.get("origin") != BASE:
                return httpx.Response(403, text="CSRF verification failed")
            if data.get("username") != USERNAME or data.get("password") != PASSWORD:
                self.login_failures += 1
                return httpx.Response(200, html=render_login_page(CSRF, with_error=True))
            return httpx.Response(
                303,
                headers=[
                    ("location", data.get("next") or "/en/"),
                    ("set-cookie", f"sessionid={SESSION_ID}; Path=/"),
                ],
            )

        # ---------- read API (no session required upstream — token only) ---------- #
        if path.endswith("/api/get/stats") and method == "GET":
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self.api_token}":
                return httpx.Response(403, text="Unauthorized. Invalid or missing authorization token.")
            return httpx.Response(200, json=stats_payload())

        # ---------- auth gate for everything below ---------- #
        if not self._has_session(request):
            return httpx.Response(302, headers={"location": f"{self._prefix(request)}/accounts/login/?next={path}"})

        prefix = self._prefix(request)

        # ---------- dashboard (auth verification target) ---------- #
        if path == f"{prefix}/" and method == "GET":
            return httpx.Response(200, html=f"<html><body>items list {CSRF}</body></html>")

        # ---------- create ---------- #
        if path == f"{prefix}/items/create/" and method == "GET":
            return httpx.Response(
                200, html=render_item_form(path, CSRF, {"type": "", "value_type": "money", "currency": "EUR", "code_type": ""})
            )
        if path == f"{prefix}/items/create/" and method == "POST":
            if not self._csrf_ok(data, request):
                return httpx.Response(403, text="CSRF verification failed")
            errors = []
            for required in ("name", "issuer", "redeem_code", "issue_date", "code_type"):
                if not data.get(required):
                    errors.append(f"This field is required: {required}.")
            if errors:
                return httpx.Response(200, html=render_item_form(path, CSRF, dict(data), errors))
            new_uuid = "aaaaaaaa-1111-1111-1111-111111111111"
            self.created.append(dict(data))
            self.items[new_uuid] = {k: v for k, v in data.items() if k != "csrfmiddlewaretoken"}
            return httpx.Response(303, headers={"location": f"{prefix}/"})

        # ---------- edit ---------- #
        if "/items/edit/" in path:
            item_uuid = path.rsplit("/items/edit/", 1)[1]
            if item_uuid not in self.items:
                return httpx.Response(404, text="not found")
            if method == "GET":
                return httpx.Response(200, html=render_item_form(path, CSRF, self.items[item_uuid]))
            if method == "POST":
                if not self._csrf_ok(data, request):
                    return httpx.Response(403, text="CSRF verification failed")
                errors = []
                for required in ("name", "issuer", "redeem_code", "issue_date", "code_type"):
                    if not data.get(required):
                        errors.append(f"This field is required: {required}.")
                if errors:
                    return httpx.Response(200, html=render_item_form(path, CSRF, dict(data), errors))
                self.items[item_uuid] = {k: v for k, v in data.items() if k != "csrfmiddlewaretoken"}
                return httpx.Response(303, headers={"location": f"{prefix}/items/view/{item_uuid}"})

        # ---------- item view page (toggle/edit success redirect target) ---------- #
        if "/items/view/" in path and method == "GET":
            return httpx.Response(200, html="<html><body>item view</body></html>")

        # ---------- toggle / delete (@require_POST upstream) ---------- #
        if "/items/toggle_status/" in path and method == "POST":
            item_uuid = path.rsplit("/items/toggle_status/", 1)[1]
            if item_uuid not in self.items:
                return httpx.Response(404, text="not found")
            if not self._csrf_ok(data, request):
                return httpx.Response(403, text="CSRF verification failed")
            current = self.items[item_uuid]
            # simulate the toggle by flipping a marker in the stored item name
            current["name"] = (
                current["name"].removesuffix(" [used]") + " [used]"
                if not current["name"].endswith(" [used]")
                else current["name"].removesuffix(" [used]")
            )
            return httpx.Response(303, headers={"location": f"{prefix}/items/view/{item_uuid}"})

        if "/items/delete/" in path and method == "POST":
            item_uuid = path.rsplit("/items/delete/", 1)[1]
            if item_uuid not in self.items:
                return httpx.Response(404, text="not found")
            if not self._csrf_ok(data, request):
                return httpx.Response(403, text="CSRF verification failed")
            del self.items[item_uuid]
            self.deleted.append(item_uuid)
            return httpx.Response(303, headers={"location": f"{prefix}/"})

        return httpx.Response(404, text="not found")


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def transport(fake_vault: FakeVault) -> httpx.MockTransport:
    return httpx.MockTransport(fake_vault.handler)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOUCHERVAULT_URL", BASE)
    monkeypatch.setenv("VOUCHERVAULT_USERNAME", USERNAME)
    monkeypatch.setenv("VOUCHERVAULT_PASSWORD", PASSWORD)
    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", API_TOKEN)
    monkeypatch.delenv("VOUCHERVAULT_LANG_PREFIX", raising=False)


@pytest.fixture
def client(env, transport: httpx.MockTransport):
    from vouchervault_mcp.client import VoucherVaultClient

    return VoucherVaultClient(transport=transport)
