"""Async HTTP client for the VoucherVault token API (extapi overlay).

VoucherVault upstream has no write REST API; the deployment adds the `extapi`
overlay which exposes a token-authenticated JSON API. This client speaks
exactly that API — plain HTTP with a Bearer token, no session, no CSRF, no
local Django user:

  Base:  {VOUCHERVAULT_URL}         e.g. http://10.0.0.194:8000 (internal container URL)
  Auth:  Authorization: Bearer {VOUCHERVAULT_API_TOKEN}   (same token as the read stats API)

Endpoints (all JSON; errors: 400 {"errors": {...}}, 401/403, 404):

  GET    /api/v1/items?search=&type=&include_used=&include_expired=&username=
  POST   /api/v1/items/                     -> 201 + serialized item
  GET    /api/v1/items/{id}                 -> serialized item
  PATCH  /api/v1/items/{id}                 -> serialized item (partial update)
  POST   /api/v1/items/{id}/toggle-status/  -> serialized item (used <-> available)
  DELETE /api/v1/items/{id}                 -> 204

Serialized item fields: id, type, name, redeem_code, code_type, issuer,
value (string), value_type, currency, issue_date, expiry_date, description,
is_used, days_left. Ordered by expiry_date. `days_left` is computed
server-side; the client does not recompute it.

The client tolerates both trailing-slash variants of every route (retries
the alternate form once on a 404).

Environment variables:
  VOUCHERVAULT_URL        - base URL of the VoucherVault container
  VOUCHERVAULT_API_TOKEN  - Bearer token (generated in the VoucherVault Django admin)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

_TIMEOUT = 30.0

# Item.ITEM_TYPES from upstream myapp/models.py
ITEM_TYPES: tuple[str, ...] = ("voucher", "giftcard", "coupon", "loyaltycard")
# Item.VALUE_TYPES from upstream myapp/models.py
VALUE_TYPES: tuple[str, ...] = ("money", "percentage", "multiplier")

# Fields the extapi write API accepts (create + partial update).
# `item_type` is accepted as an alias and sent as `type` (the server-side
# field name); `issue_date`/`code_type` are optional extras the overlay
# also understands.
WRITE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "issuer",
        "redeem_code",
        "expiry_date",
        "value",
        "value_type",
        "type",
        "item_type",
        "currency",
        "description",
        "issue_date",
        "code_type",
    }
)

_FIELD_ALIASES: dict[str, str] = {"item_type": "type"}


def _canonical_fields(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - WRITE_FIELDS
    if unknown:
        raise VoucherVaultError(
            f"unknown fields for the VoucherVault write API: {sorted(unknown)}"
        )
    return {_FIELD_ALIASES.get(key, key): val for key, val in fields.items()}


class VoucherVaultError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        errors: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.errors = errors
        super().__init__(message)


def _scrub(text: str, *secrets: str) -> str:
    """Remove credentials/tokens from error text before it is surfaced."""
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    out = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1***", out)
    out = re.sub(r"(?i)(cookie\s*:\s*)\S+", r"\1***", out)
    return out


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


class VoucherVaultClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        base_url = os.environ.get("VOUCHERVAULT_URL", "").rstrip("/")
        if not base_url:
            raise ValueError("VOUCHERVAULT_URL environment variable is required")
        token = os.environ.get("VOUCHERVAULT_API_TOKEN", "")
        if not token:
            raise ValueError(
                "VOUCHERVAULT_API_TOKEN environment variable is required "
                "(Bearer token for the token API — same token as the read "
                "stats endpoint, generated in the VoucherVault Django admin)"
            )
        self._base = base_url
        self._token = token
        self._transport = transport

    # ------------------------------------------------------------------ #
    # low-level helpers
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base, timeout=_TIMEOUT, transport=self._transport
        )

    def _scrub_response(self, text: str) -> str:
        return _scrub(text, self._token)

    @staticmethod
    def _alt_path(path: str) -> str:
        """The same route with the opposite trailing-slash convention."""
        if path.endswith("/"):
            trimmed = path.rstrip("/")
            return trimmed if trimmed else path
        return f"{path}/"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> httpx.Response:
        async with self._client() as c:
            r = await c.request(
                method, path, params=params, json=json_body, headers=self._headers()
            )
        if r.status_code == 404:
            # trailing-slash tolerance: retry the alternate form once
            alt = self._alt_path(path)
            if alt != path:
                async with self._client() as c:
                    alt_r = await c.request(
                        method,
                        alt,
                        params=params,
                        json=json_body,
                        headers=self._headers(),
                    )
                if alt_r.status_code != 404:
                    return alt_r
        return r

    def _error(self, r: httpx.Response, action: str) -> VoucherVaultError:
        status = r.status_code
        if status in (401, 403):
            return VoucherVaultError(
                f"VoucherVault API rejected the token (HTTP {status}) — check "
                "VOUCHERVAULT_API_TOKEN against the token configured in the "
                "VoucherVault Django admin",
                status_code=status,
            )
        if status == 404:
            return VoucherVaultError(
                f"VoucherVault API returned 404 for {action} — item not found "
                "(or not owned by this token's user)",
                status_code=404,
            )
        if status == 400:
            payload = _safe_json(r)
            if isinstance(payload, dict) and isinstance(payload.get("errors"), dict):
                errors = payload["errors"]
                rendered = self._scrub_response(json.dumps(errors, ensure_ascii=False))
                return VoucherVaultError(
                    f"VoucherVault rejected the request (HTTP 400): {rendered}",
                    status_code=400,
                    errors=errors,
                )
        body = self._scrub_response(r.text[:500])
        return VoucherVaultError(
            f"VoucherVault API error on {action} (HTTP {status}): {body}",
            status_code=status,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        action: str = "request",
    ) -> Any:
        r = await self._request(method, path, params=params, json_body=json_body)
        if r.status_code >= 400:
            raise self._error(r, action)
        try:
            return r.json()
        except ValueError as exc:
            raise VoucherVaultError(
                f"VoucherVault API returned invalid JSON for {action}: {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # API calls
    # ------------------------------------------------------------------ #

    async def list_items(
        self,
        search: str | None = None,
        item_type: str | None = None,
        include_used: bool = False,
        include_expired: bool = False,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /api/v1/items — server-side filtering, ordered by expiry_date."""
        params: dict[str, str] = {
            "include_used": "true" if include_used else "false",
            "include_expired": "true" if include_expired else "false",
        }
        if search:
            params["search"] = search
        if item_type:
            params["type"] = item_type
        if username:
            params["username"] = username
        data = await self._request_json(
            "GET", "/api/v1/items", params=params, action="list items"
        )
        if not isinstance(data, list):
            raise VoucherVaultError(
                "VoucherVault API returned an unexpected payload for the item list"
            )
        return [dict(item) for item in data if isinstance(item, dict)]

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        """GET /api/v1/items/{id} — serialized item, or None when missing."""
        try:
            data = await self._request_json(
                "GET", f"/api/v1/items/{item_id}", action=f"get item {item_id}"
            )
        except VoucherVaultError as exc:
            if exc.status_code == 404:
                return None
            raise
        return dict(data) if isinstance(data, dict) else None

    async def create_item(self, fields: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/items/ — returns the serialized created item (201)."""
        body = _canonical_fields(fields)
        data = await self._request_json(
            "POST", "/api/v1/items/", json_body=body, action="create item"
        )
        return dict(data) if isinstance(data, dict) else {}

    async def update_item(self, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """PATCH /api/v1/items/{id} — partial update, returns the updated item."""
        body = _canonical_fields(fields)
        data = await self._request_json(
            "PATCH",
            f"/api/v1/items/{item_id}",
            json_body=body,
            action=f"update item {item_id}",
        )
        return dict(data) if isinstance(data, dict) else {}

    async def toggle_status(self, item_id: str) -> dict[str, Any]:
        """POST /api/v1/items/{id}/toggle-status/ — used <-> available."""
        data = await self._request_json(
            "POST",
            f"/api/v1/items/{item_id}/toggle-status/",
            action=f"toggle item {item_id}",
        )
        return dict(data) if isinstance(data, dict) else {}

    async def delete_item(self, item_id: str) -> None:
        """DELETE /api/v1/items/{id} — 204 on success."""
        r = await self._request("DELETE", f"/api/v1/items/{item_id}")
        if r.status_code >= 400:
            raise self._error(r, f"delete item {item_id}")
