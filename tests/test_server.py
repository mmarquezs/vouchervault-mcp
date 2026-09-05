"""Tests for the FastMCP tool layer (server.py)."""

from __future__ import annotations

import pytest

from tests.conftest import ITEM_COUPON, ITEM_USED, REAL_ITEM_FORM_FIELDS
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


async def test_all_tools_registered():
    tools = await server.mcp.list_tools()
    assert EXPECTED_TOOLS <= {t.name for t in tools}


async def test_require_env_missing_url(monkeypatch, capsys):
    for var in (
        "VOUCHERVAULT_URL",
        "VOUCHERVAULT_USERNAME",
        "VOUCHERVAULT_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        server._require_env()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "VOUCHERVAULT_URL" in err
    assert "VOUCHERVAULT_USERNAME" in err
    assert "VOUCHERVAULT_PASSWORD" in err
    assert "OIDC" in err  # local-account hint


async def test_require_env_ok(monkeypatch):
    monkeypatch.setenv("VOUCHERVAULT_URL", "https://x.example.com")
    monkeypatch.setenv("VOUCHERVAULT_USERNAME", "u")
    monkeypatch.setenv("VOUCHERVAULT_PASSWORD", "p")
    server._require_env()  # must not raise


async def test_coupons_list_compact_projection(client_factory):
    coupons_list = await get_tool("coupons_list")
    items = await coupons_list(include_used=True, include_expired=True)
    assert len(items) == 4
    for item in items:
        assert set(item) == COMPACT_KEYS


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
    missing = await coupon_get("00000000-0000-0000-0000-000000000000")
    assert "error" in missing


async def test_coupon_create_posts_expected_defaults(client_factory, fake_vault):
    coupon_create = await get_tool("coupon_create")
    result = await coupon_create(
        name="Fresh Deal",
        issuer="amazon.es",
        redeem_code="FRESH-1",
        expiry_date="2027-01-01",
    )
    posts = fake_vault.calls("POST", "/en/items/create/")
    assert len(posts) == 1
    sent = set(posts[0]["data"]) - {"csrfmiddlewaretoken"}
    assert sent <= set(REAL_ITEM_FORM_FIELDS)
    # tool-injected defaults matching the web UI
    assert posts[0]["data"]["code_type"] == "qrcode"
    assert posts[0]["data"]["value_type"] == "money"
    assert posts[0]["data"]["type"] == "coupon"
    assert posts[0]["data"]["currency"] == "EUR"
    assert "issue_date" in sent
    # best-effort return of the created item from the stats API
    assert result["status"] == "created" or result.get("name") == "Fresh Deal"


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


async def test_coupon_update_only_changed_fields(client_factory, fake_vault):
    coupon_update = await get_tool("coupon_update")
    result = await coupon_update(ITEM_COUPON, value=12.5, issuer="amazon.com")
    posts = fake_vault.calls("POST", f"/en/items/edit/{ITEM_COUPON}")
    assert len(posts) == 1
    assert posts[0]["data"]["value"] == "12.5"
    assert posts[0]["data"]["issuer"] == "amazon.com"
    assert posts[0]["data"]["name"] == "Amazon 10EUR"  # preserved
    assert result.get("id") == ITEM_COUPON or result.get("status") == "updated"


async def test_coupon_update_no_fields_raises(client_factory):
    coupon_update = await get_tool("coupon_update")
    with pytest.raises(VoucherVaultError):
        await coupon_update(ITEM_COUPON)


async def test_coupon_mark_used_toggles(client_factory, fake_vault):
    coupon_mark_used = await get_tool("coupon_mark_used")
    await coupon_mark_used(ITEM_COUPON)
    assert "[used]" in fake_vault.items[ITEM_COUPON]["name"]
    await coupon_mark_used(ITEM_COUPON)
    assert "[used]" not in fake_vault.items[ITEM_COUPON]["name"]


async def test_coupon_delete(client_factory, fake_vault):
    coupon_delete = await get_tool("coupon_delete")
    result = await coupon_delete(ITEM_USED)
    assert result == {"status": "deleted", "item_id": ITEM_USED}
    assert ITEM_USED in fake_vault.deleted
