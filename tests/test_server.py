"""Tests for the FastMCP tool layer (server.py)."""

from __future__ import annotations

import pytest

from tests.conftest import ITEM_COUPON, ITEM_LOYALTY, ITEM_USED
from vouchervault_mcp import server
from vouchervault_mcp.client import VoucherVaultClient, VoucherVaultError

COMPACT_KEYS = {
    "id",
    "type",
    "name",
    "issuer",
    "redeem_code",
    "value",
    "value_type",
    "currency",
    "expiry_date",
    "is_used",
    "days_left",
}

EXPECTED_TOOLS = {
    "coupons_list",
    "coupon_get",
    "coupon_create",
    "coupon_update",
    "coupon_mark_used",
    "coupon_delete",
}


async def get_tool(name: str):
    """Resolve a registered FastMCP tool to its underlying function."""
    tool = await server.mcp._get_tool(name)
    assert tool is not None, f"tool {name} not registered"
    return getattr(tool, "fn", tool)


@pytest.fixture
def client_factory(monkeypatch, env, transport):
    """Make server._client() return a client wired to the fake transport."""
    def _factory() -> VoucherVaultClient:
        return VoucherVaultClient(transport=transport)

    monkeypatch.setattr(server, "_client", _factory)
    return _factory


async def test_all_six_tools_registered():
    tools = await server.mcp.list_tools()
    assert EXPECTED_TOOLS == {t.name for t in tools}


async def test_require_env_missing_url_and_token(monkeypatch, capsys):
    monkeypatch.delenv("VOUCHERVAULT_URL", raising=False)
    monkeypatch.delenv("VOUCHERVAULT_API_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        server._require_env()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "VOUCHERVAULT_URL" in err
    assert "VOUCHERVAULT_API_TOKEN" in err
    # the legacy session vars are gone from requirements and messaging
    assert "VOUCHERVAULT_USERNAME" not in err
    assert "VOUCHERVAULT_PASSWORD" not in err
    assert "vouchervault.internal" in err  # points at the internal container URL


async def test_require_env_ok(monkeypatch):
    monkeypatch.setenv("VOUCHERVAULT_URL", "http://vouchervault.internal:8000")
    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", "token")
    monkeypatch.delenv("VOUCHERVAULT_USERNAME", raising=False)
    monkeypatch.delenv("VOUCHERVAULT_PASSWORD", raising=False)
    server._require_env()  # must not raise


async def test_coupons_list_compact_projection(client_factory):
    coupons_list = await get_tool("coupons_list")
    items = await coupons_list(include_used=True, include_expired=True)
    assert len(items) == 4
    for item in items:
        assert set(item) == COMPACT_KEYS
        assert item["days_left"] is not None  # computed server-side


async def test_coupons_list_filters(client_factory):
    coupons_list = await get_tool("coupons_list")
    assert len(await coupons_list()) == 2  # used + expired hidden by default
    assert len(await coupons_list(item_type="coupon")) == 1
    with pytest.raises(VoucherVaultError):
        await coupons_list(item_type="bogus")


async def test_coupon_get(client_factory):
    coupon_get = await get_tool("coupon_get")
    item = await coupon_get(ITEM_COUPON)
    assert item["issuer"] == "amazon.es"
    assert "days_left" in item
    missing = await coupon_get("00000000-0000-0000-0000-000000000000")
    assert "error" in missing


async def test_coupon_create_sends_token_api_payload(client_factory, fake_vault):
    coupon_create = await get_tool("coupon_create")
    result = await coupon_create(
        name="Fresh Deal",
        issuer="amazon.es",
        redeem_code="FRESH-1",
        expiry_date="2027-01-01",
    )
    posts = fake_vault.calls("POST", "/api/v1/items/")
    assert len(posts) == 1
    body = posts[0]["json"]
    # documented create keys with tool defaults applied
    assert set(body) == {
        "name",
        "issuer",
        "redeem_code",
        "expiry_date",
        "value",
        "value_type",
        "type",
        "currency",
        "description",
    }
    assert body["type"] == "coupon"  # server-side field name, not item_type
    assert body["value_type"] == "money"
    assert body["currency"] == "EUR"
    assert body["value"] == 0.0
    assert body["expiry_date"] == "2027-01-01"
    # the create response (serialized item) is returned directly — no extra read
    assert result["id"].startswith("aaaaaaaa-")
    assert result["name"] == "Fresh Deal"
    assert result["is_used"] is False
    assert len(fake_vault.calls("GET", "/api/v1/items")) == 0


async def test_coupon_create_loyaltycard_forces_zero_value(client_factory, fake_vault):
    coupon_create = await get_tool("coupon_create")
    result = await coupon_create(
        name="Card",
        issuer="mercadona.es",
        redeem_code="LOYAL-1",
        expiry_date="",
        value=5,
        item_type="loyaltycard",
    )
    body = fake_vault.calls("POST", "/api/v1/items/")[0]["json"]
    assert body["type"] == "loyaltycard"
    assert body["value"] == 0
    assert result["value"] == "0"


async def test_coupon_create_validation(client_factory):
    coupon_create = await get_tool("coupon_create")
    with pytest.raises(VoucherVaultError):
        await coupon_create(
            name="x",
            issuer="y",
            redeem_code="z",
            expiry_date="",
            item_type="bogus",
        )
    with pytest.raises(VoucherVaultError):
        await coupon_create(
            name="x",
            issuer="y",
            redeem_code="z",
            expiry_date="",
            value_type="bogus",
        )


async def test_coupon_create_server_400_propagates(client_factory):
    coupon_create = await get_tool("coupon_create")
    # empty name passes tool validation but the fake server rejects it with
    # 400 {"errors": {...}} — must surface as VoucherVaultError
    with pytest.raises(VoucherVaultError) as exc:
        await coupon_create(
            name="",
            issuer="y",
            redeem_code="z",
            expiry_date="",
        )
    assert exc.value.status_code == 400
    assert "name" in exc.value.errors


async def test_coupon_update_patches_only_changed_fields(client_factory, fake_vault):
    coupon_update = await get_tool("coupon_update")
    result = await coupon_update(ITEM_COUPON, value=12.5, issuer="amazon.com")
    patches = fake_vault.calls("PATCH", f"/api/v1/items/{ITEM_COUPON}")
    assert len(patches) == 1
    assert set(patches[0]["json"]) == {"value", "issuer"}
    assert patches[0]["json"]["value"] == 12.5
    assert patches[0]["json"]["issuer"] == "amazon.com"
    # returns the serialized updated item
    assert result["value"] == "12.5"
    assert result["name"] == "Amazon 10EUR"  # preserved server-side


async def test_coupon_update_no_fields_raises(client_factory):
    coupon_update = await get_tool("coupon_update")
    with pytest.raises(VoucherVaultError):
        await coupon_update(ITEM_COUPON)


async def test_coupon_update_invalid_value_type_raises(client_factory):
    coupon_update = await get_tool("coupon_update")
    with pytest.raises(VoucherVaultError):
        await coupon_update(ITEM_COUPON, value_type="bogus")


async def test_coupon_mark_used_toggles(client_factory):
    coupon_mark_used = await get_tool("coupon_mark_used")
    first = await coupon_mark_used(ITEM_COUPON)
    assert first["is_used"] is True
    second = await coupon_mark_used(ITEM_COUPON)
    assert second["is_used"] is False


async def test_coupon_delete(client_factory, fake_vault):
    coupon_delete = await get_tool("coupon_delete")
    result = await coupon_delete(ITEM_USED)
    assert result == {"status": "deleted", "item_id": ITEM_USED}
    assert ITEM_USED in fake_vault.deleted_ids


async def test_coupon_delete_missing_propagates_404(client_factory):
    coupon_delete = await get_tool("coupon_delete")
    with pytest.raises(VoucherVaultError) as exc:
        await coupon_delete(ITEM_LOYALTY.replace("4", "5"))
    assert exc.value.status_code == 404
