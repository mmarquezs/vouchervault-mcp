"""Shared fixtures: a fake VoucherVault extapi (token API) behind httpx.MockTransport.

Simulates the extapi overlay surface applied by the ansible deployment on top
of upstream 1.30.x:
  - Bearer-token auth on every /api/v1/* route (401 when missing/wrong)
  - GET    /api/v1/items  (search/type/include_used/include_expired/username)
  - POST   /api/v1/items  (create; 400 {"errors": {...}} on validation failure)
  - GET/PATCH/DELETE /api/v1/items/{id}
  - POST   /api/v1/items/{id}/toggle-status/
  - both trailing-slash variants exist; per-test slash_mode forces one
    variant to 404 so client retry behavior can be exercised
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

BASE = "http://10.0.0.194:8000"
API_TOKEN = "dummy-api-token-for-tests"

ITEM_COUPON = "11111111-1111-1111-1111-111111111111"
ITEM_USED = "22222222-2222-2222-2222-222222222222"
ITEM_EXPIRED = "33333333-3333-3333-3333-333333333333"
ITEM_LOYALTY = "44444444-4444-4444-4444-444444444444"

ITEM_TYPES = ("voucher", "giftcard", "coupon", "loyaltycard")
VALUE_TYPES = ("money", "percentage", "multiplier")


def default_items() -> dict[str, dict[str, Any]]:
    """Existing items in serialized form (days_left added at response time)."""
    return {
        ITEM_COUPON: {
            "id": ITEM_COUPON,
            "type": "coupon",
            "name": "Amazon 10EUR",
            "redeem_code": "AMZN-2026-XYZ",
            "code_type": "qrcode",
            "issuer": "amazon.es",
            "value": "25.00",
            "value_type": "money",
            "currency": "EUR",
            "issue_date": "2026-01-01",
            "expiry_date": (date.today() + timedelta(days=30)).isoformat(),
            "description": "birthday coupon",
            "is_used": False,
        },
        ITEM_USED: {
            "id": ITEM_USED,
            "type": "giftcard",
            "name": "El Corte Ingles Gift",
            "redeem_code": "ECI-GIFT-77",
            "code_type": "qrcode",
            "issuer": "elcorteingles.es",
            "value": "50.00",
            "value_type": "money",
            "currency": "EUR",
            "issue_date": "2026-01-15",
            "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            "description": "",
            "is_used": True,
        },
        ITEM_EXPIRED: {
            "id": ITEM_EXPIRED,
            "type": "voucher",
            "name": "Old Voucher",
            "redeem_code": "OLD-VOUCHER-1",
            "code_type": "qrcode",
            "issuer": "zalando.es",
            "value": "5.00",
            "value_type": "money",
            "currency": "EUR",
            "issue_date": "2019-01-01",
            "expiry_date": "2020-01-01",
            "description": "",
            "is_used": False,
        },
        ITEM_LOYALTY: {
            "id": ITEM_LOYALTY,
            "type": "loyaltycard",
            "name": "Loyalty Card",
            "redeem_code": "9990123456789",
            "code_type": "qrcode",
            "issuer": "mercadona.es",
            "value": "0.00",
            "value_type": "money",
            "currency": "EUR",
            "issue_date": "2026-02-02",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "description": "",
            "is_used": False,
        },
    }


def serialize(raw: dict[str, Any]) -> dict[str, Any]:
    """days_left is computed server-side (extapi overlay behavior)."""
    item = dict(raw)
    expiry = item.get("expiry_date")
    item["days_left"] = None
    if expiry:
        try:
            item["days_left"] = (
                date.fromisoformat(str(expiry)[:10]) - date.today()
            ).days
        except ValueError:
            item["days_left"] = None
    return item


class FakeVault:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.items = default_items()
        self.created_ids: list[str] = []
        self.deleted_ids: list[str] = []
        self.api_token = API_TOKEN
        # "both"  -> slashed and non-slashed routes both exist
        # "noslash" -> only non-slashed routes exist (slashed ones 404)
        # "slash" -> only slashed routes exist (non-slashed ones 404)
        self.slash_mode = "both"
        # next /api/v1 request gets a 500 whose body echoes the Authorization
        # header (used to assert token scrubbing in surfaced errors)
        self.leak_auth_on_next = False

    # ------------------------- helpers ------------------------- #

    def calls(self, method: str, path_suffix: str) -> list[dict]:
        return [
            r
            for r in self.requests
            if r["method"] == method and r["path"].endswith(path_suffix)
        ]

    def _path_allowed(self, path: str) -> bool:
        slashed = path.endswith("/")
        if self.slash_mode == "noslash":
            return not slashed
        if self.slash_mode == "slash":
            return slashed
        return True

    # ------------------------- handler ------------------------- #

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except ValueError:
                body = None
        self.requests.append(
            {
                "method": method,
                "path": path,
                "params": dict(request.url.params),
                "headers": dict(request.headers),
                "json": body,
            }
        )

        if self.leak_auth_on_next:
            self.leak_auth_on_next = False
            auth = request.headers.get("authorization", "")
            return httpx.Response(
                500,
                text=f"internal error handling {method} {path} with {auth}",
            )

        if not path.startswith("/api/v1/"):
            return httpx.Response(404, json={"detail": "Not found."})

        # ---------- token auth gate (every /api/v1 route) ---------- #
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.api_token}":
            return httpx.Response(
                401,
                json={"detail": "Invalid or missing authentication token."},
            )

        if not self._path_allowed(path):
            return httpx.Response(404, json={"detail": "Not found."})

        rest = path.removeprefix("/api/v1/items").strip("/")

        if rest == "":
            if method == "GET":
                return self._list(request)
            if method == "POST":
                return self._create(body)
            return httpx.Response(405, json={"detail": "Method not allowed."})

        parts = rest.split("/")
        if len(parts) == 1:
            item_id = parts[0]
            if method == "GET":
                return self._detail(item_id)
            if method == "PATCH":
                return self._patch(item_id, body)
            if method == "DELETE":
                return self._delete(item_id)
            return httpx.Response(405, json={"detail": "Method not allowed."})

        if len(parts) == 2 and parts[1] == "toggle-status":
            if method == "POST":
                return self._toggle(parts[0])
            return httpx.Response(405, json={"detail": "Method not allowed."})

        return httpx.Response(404, json={"detail": "Not found."})

    # ------------------------- endpoint impls ------------------------- #

    def _list(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        search = (p.get("search") or "").lower()
        item_type = p.get("type") or ""
        include_used = (p.get("include_used") or "false").lower() == "true"
        include_expired = (p.get("include_expired") or "false").lower() == "true"
        out: list[dict[str, Any]] = []
        for raw in self.items.values():
            item = serialize(raw)
            if search:
                haystack = " ".join(
                    str(item.get(field) or "")
                    for field in ("name", "issuer", "redeem_code")
                ).lower()
                if search not in haystack:
                    continue
            if item_type and item.get("type") != item_type:
                continue
            if not include_used and item.get("is_used"):
                continue
            days = item.get("days_left")
            if not include_expired and days is not None and days < 0:
                continue
            out.append(item)
        # ordered by expiry_date (items without expiry last)
        out.sort(key=lambda i: i.get("expiry_date") or "9999-12-31")
        return httpx.Response(200, json=out)

    def _create(self, body: Any) -> httpx.Response:
        if not isinstance(body, dict):
            return httpx.Response(
                400, json={"errors": {"non_field": ["invalid JSON body."]}}
            )
        errors: dict[str, list[str]] = {}
        for field in ("name", "issuer", "redeem_code", "type"):
            if not str(body.get(field) or "").strip():
                errors[field] = ["This field is required."]
        if body.get("value_type") and body["value_type"] not in VALUE_TYPES:
            errors["value_type"] = [
                f"'{body['value_type']}' is not a valid choice."
            ]
        if body.get("type") and body["type"] not in ITEM_TYPES:
            errors["type"] = [f"'{body['type']}' is not a valid choice."]
        if errors:
            return httpx.Response(400, json={"errors": errors})

        new_id = f"aaaaaaaa-{len(self.created_ids) + 1:04d}-1111-1111-111111111111"
        item = {
            "id": new_id,
            "type": body["type"],
            "name": body["name"],
            "redeem_code": body["redeem_code"],
            "code_type": body.get("code_type") or "qrcode",
            "issuer": body.get("issuer") or "",
            "value": str(body.get("value", "0")),
            "value_type": body.get("value_type") or "money",
            "currency": body.get("currency") or "EUR",
            "issue_date": body.get("issue_date") or date.today().isoformat(),
            "expiry_date": body.get("expiry_date") or "",
            "description": body.get("description") or "",
            "is_used": False,
        }
        self.items[new_id] = item
        self.created_ids.append(new_id)
        return httpx.Response(201, json=serialize(item))

    def _detail(self, item_id: str) -> httpx.Response:
        if item_id not in self.items:
            return httpx.Response(404, json={"detail": "Not found."})
        return httpx.Response(200, json=serialize(self.items[item_id]))

    def _patch(self, item_id: str, body: Any) -> httpx.Response:
        if item_id not in self.items:
            return httpx.Response(404, json={"detail": "Not found."})
        if not isinstance(body, dict) or not body:
            return httpx.Response(
                400, json={"errors": {"non_field": ["no fields to update."]}}
            )
        errors: dict[str, list[str]] = {}
        if "value_type" in body and body["value_type"] not in VALUE_TYPES:
            errors["value_type"] = [
                f"'{body['value_type']}' is not a valid choice."
            ]
        if "type" in body and body["type"] not in ITEM_TYPES:
            errors["type"] = [f"'{body['type']}' is not a valid choice."]
        if errors:
            return httpx.Response(400, json={"errors": errors})
        item = self.items[item_id]
        for key, value in body.items():
            if key == "value":
                item[key] = str(value)
            else:
                item[key] = value
        return httpx.Response(200, json=serialize(item))

    def _toggle(self, item_id: str) -> httpx.Response:
        if item_id not in self.items:
            return httpx.Response(404, json={"detail": "Not found."})
        item = self.items[item_id]
        item["is_used"] = not item["is_used"]
        return httpx.Response(200, json=serialize(item))

    def _delete(self, item_id: str) -> httpx.Response:
        if item_id not in self.items:
            return httpx.Response(404, json={"detail": "Not found."})
        del self.items[item_id]
        self.deleted_ids.append(item_id)
        return httpx.Response(204)


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def transport(fake_vault: FakeVault) -> httpx.MockTransport:
    return httpx.MockTransport(fake_vault.handler)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOUCHERVAULT_URL", BASE)
    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", API_TOKEN)
    # the legacy auth env vars must be irrelevant now
    monkeypatch.delenv("VOUCHERVAULT_USERNAME", raising=False)
    monkeypatch.delenv("VOUCHERVAULT_PASSWORD", raising=False)
    monkeypatch.delenv("VOUCHERVAULT_LANG_PREFIX", raising=False)


@pytest.fixture
def client(env, transport: httpx.MockTransport):
    from vouchervault_mcp.client import VoucherVaultClient

    return VoucherVaultClient(transport=transport)
