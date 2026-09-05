"""VoucherVault MCP server — exposes voucher/coupon management to AI agents.

Tools:
  - coupons_list     — list/search vouchers, coupons, gift cards, loyalty cards
  - coupon_get       — get single item details by id
  - coupon_create    — create a new item (web form post)
  - coupon_update    — update item fields by id (web form post)
  - coupon_mark_used — toggle used/available status
  - coupon_delete    — delete an item

Reads use the Bearer-token stats API; writes are session+CSRF form posts
(see client.py — upstream has no write REST API).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import Any

from fastmcp import FastMCP

from vouchervault_mcp.client import (
    ITEM_TYPES,
    VALUE_TYPES,
    VoucherVaultClient,
    VoucherVaultError,
)

mcp = FastMCP("VoucherVault")


def _client() -> VoucherVaultClient:
    return VoucherVaultClient()


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    """Compact projection for list output."""
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "name": item.get("name"),
        "issuer": item.get("issuer"),
        "redeem_code": item.get("redeem_code"),
        "value": item.get("value"),
        "value_type": item.get("value_type"),
        "currency": item.get("currency"),
        "expiry_date": item.get("expiry_date"),
        "is_used": item.get("is_used"),
        "days_left": item.get("days_left"),
    }


@mcp.tool
async def coupons_list(
    search: str | None = None,
    item_type: str | None = None,
    include_used: bool = False,
    include_expired: bool = False,
) -> list[dict]:
    """List vouchers, coupons, gift cards and loyalty cards.

    Use `search` for substring matching over name, issuer and redeem_code.
    Use `item_type` to filter: voucher | giftcard | coupon | loyaltycard.
    By default used and expired items are hidden; enable them with
    `include_used` / `include_expired`. Each item includes `days_left`
    (negative means expired).
    """
    if item_type is not None and item_type not in ITEM_TYPES:
        raise VoucherVaultError(
            f"invalid item_type '{item_type}' — expected one of {list(ITEM_TYPES)}"
        )
    items = await _client().list_items(
        search=search,
        item_type=item_type,
        include_used=include_used,
        include_expired=include_expired,
    )
    return [_compact(item) for item in items]


@mcp.tool
async def coupon_get(item_id: str) -> dict:
    """Get full details of a single item by its id (UUID string)."""
    item = await _client().get_item(item_id)
    if item is None:
        return {"error": f"Item {item_id} not found"}
    return item


@mcp.tool
async def coupon_create(
    name: str,
    issuer: str,
    redeem_code: str,
    expiry_date: str,
    value: float = 0.0,
    value_type: str = "money",
    item_type: str = "coupon",
    currency: str = "EUR",
    description: str = "",
) -> dict:
    """Create a new item (coupon, voucher, gift card or loyalty card).

    `issuer` should be the merchant DOMAIN like "amazon.es" — the checkout
    userscript matches on it. `redeem_code` is the coupon/voucher code.
    Dates use YYYY-MM-DD format; pass expiry_date as "" for no real expiry
    (upstream then sets it 50 years out). `value_type` is one of
    money | percentage | multiplier (percentage 0-100, multiplier >= 1).
    `item_type` is one of voucher | giftcard | coupon | loyaltycard
    (loyalty cards require value 0).
    """
    if item_type not in ITEM_TYPES:
        raise VoucherVaultError(
            f"invalid item_type '{item_type}' — expected one of {list(ITEM_TYPES)}"
        )
    if value_type not in VALUE_TYPES:
        raise VoucherVaultError(
            f"invalid value_type '{value_type}' — expected one of {list(VALUE_TYPES)}"
        )
    fields: dict[str, Any] = {
        "name": name,
        "issuer": issuer,
        "redeem_code": redeem_code,
        "type": item_type,
        "value": value,
        "value_type": value_type,
        "currency": currency,
        "expiry_date": expiry_date,
        "description": description,
        # defaults matching the web UI for required fields the tool
        # signature does not expose
        "issue_date": date.today().isoformat(),
        "code_type": "qrcode",
    }
    if item_type == "loyaltycard":
        fields["value"] = 0

    client = _client()
    await client.create_item(fields)
    # best effort: return the created item from the stats API
    try:
        items = await client.list_items(search=name, include_used=True)
        for item in items:
            if item.get("name") == name and item.get("redeem_code") == redeem_code:
                return item
    except VoucherVaultError:
        pass
    return {"status": "created", "name": name, "issuer": issuer}


@mcp.tool
async def coupon_update(
    item_id: str,
    name: str | None = None,
    issuer: str | None = None,
    redeem_code: str | None = None,
    expiry_date: str | None = None,
    description: str | None = None,
    currency: str | None = None,
    value: float | None = None,
    value_type: str | None = None,
) -> dict:
    """Update fields of an existing item by id. Only provided fields change.

    Editable: name, issuer, redeem_code, expiry_date (YYYY-MM-DD),
    description, currency, value, value_type (money | percentage | multiplier).
    """
    changed: dict[str, Any] = {}
    if name is not None:
        changed["name"] = name
    if issuer is not None:
        changed["issuer"] = issuer
    if redeem_code is not None:
        changed["redeem_code"] = redeem_code
    if expiry_date is not None:
        changed["expiry_date"] = expiry_date
    if description is not None:
        changed["description"] = description
    if currency is not None:
        changed["currency"] = currency
    if value is not None:
        changed["value"] = value
    if value_type is not None:
        if value_type not in VALUE_TYPES:
            raise VoucherVaultError(
                f"invalid value_type '{value_type}' — expected one of {list(VALUE_TYPES)}"
            )
        changed["value_type"] = value_type
    if not changed:
        raise VoucherVaultError("no fields provided to update")

    client = _client()
    await client.edit_item(item_id, changed)
    item = await client.get_item(item_id)
    if item is None:
        return {"status": "updated", "item_id": item_id}
    return item


@mcp.tool
async def coupon_mark_used(item_id: str) -> dict:
    """Toggle the used status of an item.

    Calling this once marks the item as USED; calling it again marks it
    available again. The returned item reflects the new state.
    """
    client = _client()
    await client.toggle_status(item_id)
    item = await client.get_item(item_id)
    if item is None:
        return {"status": "toggled", "item_id": item_id}
    return item


@mcp.tool
async def coupon_delete(item_id: str) -> dict:
    """Permanently delete an item by its id. This cannot be undone."""
    await _client().delete_item(item_id)
    return {"status": "deleted", "item_id": item_id}


def _require_env() -> None:
    missing = [
        var
        for var in ("VOUCHERVAULT_URL", "VOUCHERVAULT_USERNAME", "VOUCHERVAULT_PASSWORD")
        if not os.environ.get(var)
    ]
    if missing:
        print(
            "vouchervault-mcp: missing required environment variables: "
            + ", ".join(missing)
            + "\n"
            "Set VOUCHERVAULT_URL (e.g. https://vouchervault.example.com), "
            "VOUCHERVAULT_USERNAME and VOUCHERVAULT_PASSWORD (a LOCAL Django "
            "account — OIDC users cannot password-login). Also set "
            "VOUCHERVAULT_API_TOKEN (Bearer token for the read API, generated "
            "in the VoucherVault Django admin). Optionally set "
            "VOUCHERVAULT_LANG_PREFIX (default /en).",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> None:
    _require_env()
    mcp.run()


if __name__ == "__main__":
    main()
