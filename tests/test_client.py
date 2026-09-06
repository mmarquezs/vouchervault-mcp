"""Unit tests for VoucherVaultClient (mocked token API via httpx.MockTransport)."""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import (
    API_TOKEN,
    BASE,
    ITEM_COUPON,
    ITEM_EXPIRED,
    ITEM_LOYALTY,
    ITEM_USED,
)
from vouchervault_mcp.client import VoucherVaultClient, VoucherVaultError


def _base_create_fields() -> dict:
    return {
        "name": "New Coupon",
        "issuer": "amazon.es",
        "redeem_code": "NEW-CODE-1",
        "expiry_date": "2027-01-01",
        "value": 10.0,
        "value_type": "money",
        "type": "coupon",
        "currency": "EUR",
        "description": "",
    }


async def test_bearer_token_sent_on_every_call(client: VoucherVaultClient, fake_vault):
    await client.list_items()
    await client.get_item(ITEM_COUPON)
    await client.create_item(_base_create_fields())
    await client.update_item(ITEM_COUPON, {"description": "x"})
    await client.toggle_status(ITEM_COUPON)
    await client.delete_item(ITEM_COUPON)

    api_calls = [r for r in fake_vault.requests if r["path"].startswith("/api/v1")]
    assert len(api_calls) == 6
    for r in api_calls:
        assert r["headers"]["authorization"] == f"Bearer {API_TOKEN}"


async def test_list_items_returns_server_payload(client: VoucherVaultClient):
    items = await client.list_items(include_used=True, include_expired=True)
    assert len(items) == 4
    # server-provided fields come through untouched (value is a string,
    # days_left computed server-side — no local recomputation)
    coupon = next(i for i in items if i["id"] == ITEM_COUPON)
    assert coupon["value"] == "25.00"
    assert coupon["days_left"] == 30
    assert coupon["is_used"] is False


async def test_list_items_ordered_by_expiry_date(client: VoucherVaultClient):
    items = await client.list_items(include_used=True, include_expired=True)
    dates = [i["expiry_date"] for i in items]
    assert dates == sorted(dates)


async def test_list_items_filter_params_passthrough(client: VoucherVaultClient, fake_vault):
    await client.list_items(
        search="amazon",
        item_type="coupon",
        include_used=True,
        include_expired=True,
        username="admin",
    )
    calls = [
        r for r in fake_vault.requests if r["method"] == "GET" and r["path"] == "/api/v1/items"
    ]
    assert len(calls) == 1
    params = calls[0]["params"]
    assert params["search"] == "amazon"
    assert params["type"] == "coupon"
    assert params["include_used"] == "true"
    assert params["include_expired"] == "true"
    assert params["username"] == "admin"


async def test_list_items_default_flags_sent_false(client: VoucherVaultClient, fake_vault):
    await client.list_items()
    call = fake_vault.calls("GET", "/api/v1/items")[0]
    assert call["params"]["include_used"] == "false"
    assert call["params"]["include_expired"] == "false"
    assert "search" not in call["params"]
    assert "type" not in call["params"]


async def test_list_items_server_side_filtering_honored(client: VoucherVaultClient):
    names = [i["name"] for i in await client.list_items()]
    assert "El Corte Ingles Gift" not in names  # used
    assert "Old Voucher" not in names  # expired
    assert "Amazon 10EUR" in names

    types = {i["type"] for i in await client.list_items(item_type="coupon")}
    assert types == {"coupon"}

    expired = await client.list_items(include_expired=True)
    old = next(i for i in expired if i["id"] == ITEM_EXPIRED)
    assert old["days_left"] == (date.fromisoformat("2020-01-01") - date.today()).days

    everything = await client.list_items(include_used=True, include_expired=True)
    assert {i["id"] for i in everything} == {
        ITEM_COUPON,
        ITEM_USED,
        ITEM_EXPIRED,
        ITEM_LOYALTY,
    }


async def test_get_item_found_and_missing(client: VoucherVaultClient):
    item = await client.get_item(ITEM_COUPON)
    assert item is not None
    assert item["issuer"] == "amazon.es"
    assert await client.get_item("99999999-9999-9999-9999-999999999999") is None


async def test_create_payload_and_returned_item(client: VoucherVaultClient, fake_vault):
    fields = _base_create_fields()
    created = await client.create_item(fields)

    posts = fake_vault.calls("POST", "/api/v1/items/")
    assert len(posts) == 1
    post = posts[0]
    # JSON body with exactly the documented keys; `type` is the server-side name
    assert set(post["json"]) == set(fields)
    assert post["json"]["type"] == "coupon"
    assert post["json"]["value"] == 10.0
    # 201 + serialized item echoed back
    assert created["id"].startswith("aaaaaaaa-")
    assert created["value"] == "10.0"
    assert created["is_used"] is False
    assert created["id"] in fake_vault.created_ids


async def test_create_item_type_alias_mapped_to_type(client: VoucherVaultClient, fake_vault):
    fields = _base_create_fields()
    fields.pop("type")
    fields["item_type"] = "voucher"
    created = await client.create_item(fields)
    post = fake_vault.calls("POST", "/api/v1/items/")[0]
    assert post["json"]["type"] == "voucher"
    assert "item_type" not in post["json"]
    assert created["type"] == "voucher"


async def test_create_unknown_field_rejected(client: VoucherVaultClient):
    fields = _base_create_fields()
    fields["not_a_real_field"] = "y"
    with pytest.raises(VoucherVaultError):
        await client.create_item(fields)


async def test_create_400_error_payload_propagated(client: VoucherVaultClient, fake_vault):
    # omit required fields -> fake returns 400 {"errors": {...}}
    fields = _base_create_fields()
    fields.pop("name")
    fields.pop("type")
    with pytest.raises(VoucherVaultError) as exc:
        await client.create_item(fields)
    assert exc.value.status_code == 400
    assert exc.value.errors is not None
    assert "name" in exc.value.errors
    assert "type" in exc.value.errors
    # the rendered message carries the server's error detail
    assert "required" in exc.value.message.lower()


async def test_update_partial_payload_only_changed_fields(client: VoucherVaultClient, fake_vault):
    updated = await client.update_item(ITEM_COUPON, {"value": 30, "description": "upd"})

    patches = fake_vault.calls("PATCH", f"/api/v1/items/{ITEM_COUPON}")
    assert len(patches) == 1
    # partial update: ONLY the provided fields are in the body
    assert set(patches[0]["json"]) == {"value", "description"}
    assert patches[0]["json"]["value"] == 30
    assert updated["value"] == "30"
    assert updated["description"] == "upd"
    # untouched fields preserved server-side
    assert updated["name"] == "Amazon 10EUR"


async def test_update_missing_item_404(client: VoucherVaultClient):
    with pytest.raises(VoucherVaultError) as exc:
        await client.update_item("88888888-8888-8888-8888-888888888888", {"name": "x"})
    assert exc.value.status_code == 404


async def test_toggle_status_flips_and_returns_item(client: VoucherVaultClient, fake_vault):
    first = await client.toggle_status(ITEM_COUPON)
    assert first["is_used"] is True
    second = await client.toggle_status(ITEM_COUPON)
    assert second["is_used"] is False
    toggles = fake_vault.calls("POST", f"/api/v1/items/{ITEM_COUPON}/toggle-status/")
    assert len(toggles) == 2


async def test_toggle_missing_item_404(client: VoucherVaultClient):
    with pytest.raises(VoucherVaultError) as exc:
        await client.toggle_status("77777777-7777-7777-7777-777777777777")
    assert exc.value.status_code == 404


async def test_delete_success_and_missing_404(client: VoucherVaultClient, fake_vault):
    await client.delete_item(ITEM_COUPON)
    deletes = fake_vault.calls("DELETE", f"/api/v1/items/{ITEM_COUPON}")
    assert len(deletes) == 1
    assert ITEM_COUPON in fake_vault.deleted_ids

    with pytest.raises(VoucherVaultError) as exc:
        await client.delete_item(ITEM_COUPON)  # now gone
    assert exc.value.status_code == 404


async def test_401_wrong_token_clear_error(monkeypatch, transport, env):
    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", "bad-token-value")
    client = VoucherVaultClient(transport=transport)
    with pytest.raises(VoucherVaultError) as exc:
        await client.list_items()
    assert exc.value.status_code == 401
    assert "VOUCHERVAULT_API_TOKEN" in exc.value.message
    # the wrong token value must never appear in the surfaced error text
    assert "bad-token-value" not in exc.value.message


async def test_missing_env_vars_fail_fast(transport, monkeypatch):
    monkeypatch.setenv("VOUCHERVAULT_URL", BASE)
    monkeypatch.delenv("VOUCHERVAULT_API_TOKEN", raising=False)
    with pytest.raises(ValueError) as exc:
        VoucherVaultClient(transport=transport)
    assert "VOUCHERVAULT_API_TOKEN" in str(exc.value)

    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", API_TOKEN)
    monkeypatch.delenv("VOUCHERVAULT_URL", raising=False)
    with pytest.raises(ValueError) as exc:
        VoucherVaultClient(transport=transport)
    assert "VOUCHERVAULT_URL" in str(exc.value)


async def test_token_scrubbed_from_exception_text(client: VoucherVaultClient, fake_vault):
    # server error body echoes the Authorization header — must be scrubbed
    fake_vault.leak_auth_on_next = True
    with pytest.raises(VoucherVaultError) as exc:
        await client.get_item(ITEM_COUPON)
    assert exc.value.status_code == 500
    assert API_TOKEN not in exc.value.message
    assert "Bearer ***" in exc.value.message


async def test_trailing_slash_retry_when_only_noslash_exists(fake_vault, transport, env):
    # server serves only /api/v1/items (no trailing slash)
    fake_vault.slash_mode = "noslash"
    c = VoucherVaultClient(transport=transport)
    created = await c.create_item(_base_create_fields())
    assert created["id"] in fake_vault.created_ids
    assert fake_vault.calls("POST", "/api/v1/items")

    # non-404-on-both: after both variants 404 the client raises a 404 error
    with pytest.raises(VoucherVaultError) as exc:
        await c.delete_item("99999999-9999-9999-9999-999999999999")
    assert exc.value.status_code == 404


async def test_trailing_slash_retry_when_only_slash_exists(fake_vault, transport, env):
    # server serves only the slashed variant -> canonical no-slash GET list
    # must be retried with the slash and succeed
    fake_vault.slash_mode = "slash"
    c = VoucherVaultClient(transport=transport)
    items = await c.list_items(include_used=True, include_expired=True)
    assert len(items) == 4
    assert fake_vault.calls("GET", "/api/v1/items/")


async def test_legacy_session_env_vars_are_ignored(fake_vault, transport, monkeypatch):
    # old deployment env must neither be required nor break anything
    monkeypatch.setenv("VOUCHERVAULT_URL", BASE)
    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("VOUCHERVAULT_USERNAME", "leftover-user")
    monkeypatch.setenv("VOUCHERVAULT_PASSWORD", "leftover-pass")
    monkeypatch.setenv("VOUCHERVAULT_LANG_PREFIX", "/en")
    c = VoucherVaultClient(transport=transport)
    items = await c.list_items(include_used=True)
    assert isinstance(items, list)
    assert all("/api/v1" in r["path"] for r in fake_vault.requests)
