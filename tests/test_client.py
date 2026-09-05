"""Unit tests for VoucherVaultClient (mocked HTTP via httpx.MockTransport)."""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import (
    API_TOKEN,
    BASE,
    CSRF,
    ITEM_COUPON,
    ITEM_EXPIRED,
    ITEM_USED,
    PASSWORD,
    REAL_ITEM_FORM_FIELDS,
    USERNAME,
)
from vouchervault_mcp.client import (
    ITEM_FORM_FIELDS,
    VoucherVaultClient,
    VoucherVaultError,
)


async def test_form_fields_match_upstream_forms_py():
    """The client's known form fields must be EXACTLY the ItemForm.Meta.fields
    list fetched from upstream myapp/forms.py (see conftest for the source)."""
    assert list(ITEM_FORM_FIELDS) == REAL_ITEM_FORM_FIELDS


async def test_login_csrf_flow(client: VoucherVaultClient, fake_vault):
    await client.login()

    login_gets = fake_vault.calls("GET", "/en/accounts/login/")
    assert len(login_gets) == 1

    posts = fake_vault.calls("POST", "/en/accounts/login/")
    assert len(posts) == 1
    post = posts[0]
    # CSRF token parsed from the login page form and sent back
    assert post["data"]["csrfmiddlewaretoken"] == CSRF
    assert post["data"]["username"] == USERNAME
    assert post["data"]["password"] == PASSWORD
    # Django CSRF over https needs Origin/Referer
    assert post["headers"]["origin"] == BASE
    assert post["headers"]["referer"] == f"{BASE}/"

    # authenticated dashboard fetch verifies the session
    assert any(r["path"] == "/en/" and r["method"] == "GET" for r in fake_vault.requests)


async def test_login_bad_credentials_raises_with_oidc_hint(fake_vault, transport, env, monkeypatch):
    monkeypatch.setenv("VOUCHERVAULT_PASSWORD", "definitely-wrong")
    client = VoucherVaultClient(transport=transport)
    with pytest.raises(VoucherVaultError) as exc:
        await client.login()
    assert "OIDC" in exc.value.message
    # the wrong password value must never appear in error text
    assert "definitely-wrong" not in exc.value.message


async def test_get_stats_success(client: VoucherVaultClient, fake_vault):
    stats = await client.get_stats()
    calls = fake_vault.calls("GET", "/en/api/get/stats")
    assert len(calls) == 1
    assert calls[0]["headers"]["authorization"] == f"Bearer {API_TOKEN}"
    assert len(stats["item_details"]) == 4


async def test_get_stats_bad_token_clear_error(monkeypatch, transport, env):
    monkeypatch.setenv("VOUCHERVAULT_API_TOKEN", "bad-token-value")
    client = VoucherVaultClient(transport=transport)
    with pytest.raises(VoucherVaultError) as exc:
        await client.get_stats()
    assert exc.value.status_code == 403
    assert "VOUCHERVAULT_API_TOKEN" in exc.value.message
    # token must be scrubbed from any surfaced error text
    assert "bad-token-value" not in exc.value.message


async def test_get_stats_missing_token(monkeypatch, transport):
    monkeypatch.setenv("VOUCHERVAULT_URL", BASE)
    monkeypatch.setenv("VOUCHERVAULT_USERNAME", USERNAME)
    monkeypatch.setenv("VOUCHERVAULT_PASSWORD", PASSWORD)
    monkeypatch.delenv("VOUCHERVAULT_API_TOKEN", raising=False)
    client = VoucherVaultClient(transport=transport)
    with pytest.raises(VoucherVaultError) as exc:
        await client.get_stats()
    assert "VOUCHERVAULT_API_TOKEN" in exc.value.message


async def test_list_items_default_hides_used_and_expired(client: VoucherVaultClient):
    items = await client.list_items()
    names = [item["name"] for item in items]
    assert "El Corte Ingles Gift" not in names  # used
    assert "Old Voucher" not in names  # expired
    assert "Amazon 10EUR" in names
    assert "Loyalty Card" in names
    # annotation + coercion
    coupon = next(i for i in items if i["name"] == "Amazon 10EUR")
    assert coupon["days_left"] == 30
    assert isinstance(coupon["value"], float)


async def test_list_items_search_over_name_issuer_redeem_code(client: VoucherVaultClient):
    assert [i["id"] for i in await client.list_items(search="amazon")] == [ITEM_COUPON]
    assert [i["id"] for i in await client.list_items(search="zalando", include_expired=True)] == [ITEM_EXPIRED]
    assert [i["id"] for i in await client.list_items(search="eci-gift", include_used=True)] == [ITEM_USED]
    assert len(await client.list_items(search="nomatch-xyz", include_used=True, include_expired=True)) == 0


async def test_list_items_type_and_flags(client: VoucherVaultClient):
    types = {i["type"] for i in await client.list_items(item_type="coupon")}
    assert types == {"coupon"}

    used = await client.list_items(include_used=True)
    assert any(i["id"] == ITEM_USED for i in used)

    expired = await client.list_items(include_expired=True)
    old = next(i for i in expired if i["id"] == ITEM_EXPIRED)
    assert old["days_left"] < 0
    assert old["days_left"] == (date.fromisoformat("2020-01-01") - date.today()).days

    everything = await client.list_items(include_used=True, include_expired=True)
    assert len(everything) == 4


async def test_get_item_found_and_missing(client: VoucherVaultClient):
    item = await client.get_item(ITEM_COUPON)
    assert item is not None
    assert item["issuer"] == "amazon.es"
    assert await client.get_item("99999999-9999-9999-9999-999999999999") is None


async def test_create_post_payload_field_names(client: VoucherVaultClient, fake_vault):
    fields = {
        "name": "New Coupon",
        "issuer": "amazon.es",
        "redeem_code": "NEW-CODE-1",
        "type": "coupon",
        "value": 10.0,
        "value_type": "money",
        "currency": "EUR",
        "expiry_date": "",
        "description": "",
        "issue_date": date.today().isoformat(),
        "code_type": "qrcode",
    }
    await client.create_item(fields)

    posts = fake_vault.calls("POST", "/en/items/create/")
    assert len(posts) == 1
    post = posts[0]
    sent = set(post["data"]) - {"csrfmiddlewaretoken"}
    # every posted field must be a real ItemForm field from upstream forms.py
    assert sent <= set(REAL_ITEM_FORM_FIELDS)
    # required ItemForm fields are all present
    assert {
        "name",
        "issuer",
        "redeem_code",
        "type",
        "value",
        "value_type",
        "currency",
        "issue_date",
        "code_type",
    } <= sent
    assert post["data"]["csrfmiddlewaretoken"] == CSRF
    assert post["headers"]["x-csrftoken"] == CSRF
    assert post["headers"]["origin"] == BASE
    assert post["data"]["value"] == "10.0"


async def test_create_unknown_field_rejected(client: VoucherVaultClient):
    with pytest.raises(VoucherVaultError):
        await client.create_item({"name": "x", "not_a_form_field": "y"})


async def test_create_validation_failure_surfaces_errors(client: VoucherVaultClient):
    with pytest.raises(VoucherVaultError) as exc:
        await client.create_item(
            {
                "issuer": "amazon.es",
                "redeem_code": "X",
                "type": "coupon",
                "value": 10.0,
                "value_type": "money",
                "currency": "EUR",
                "expiry_date": "",
                "description": "",
                "issue_date": "2026-09-05",
                "code_type": "qrcode",
            }
        )
    assert "required" in exc.value.message.lower()


async def test_edit_merges_current_values_and_posts_all_fields(client: VoucherVaultClient, fake_vault):
    await client.edit_item(ITEM_COUPON, {"value": 30, "description": "updated"})

    posts = fake_vault.calls("POST", f"/en/items/edit/{ITEM_COUPON}")
    assert len(posts) == 1
    post = posts[0]
    sent = set(post["data"]) - {"csrfmiddlewaretoken"}
    # ALL form fields (except the file upload) must be sent back
    expected = set(REAL_ITEM_FORM_FIELDS) - {"file"}
    assert expected <= sent
    # changed fields applied
    assert post["data"]["value"] == "30"
    assert post["data"]["description"] == "updated"
    # unchanged fields preserved from the GET form
    assert post["data"]["name"] == "Amazon 10EUR"
    assert post["data"]["issuer"] == "amazon.es"
    assert post["data"]["issue_date"] == "2026-01-01"
    assert post["data"]["csrfmiddlewaretoken"] == CSRF


async def test_edit_missing_item_404(client: VoucherVaultClient):
    with pytest.raises(VoucherVaultError) as exc:
        await client.edit_item("88888888-8888-8888-8888-888888888888", {"name": "x"})
    assert exc.value.status_code == 404


async def test_toggle_status_call_shape(client: VoucherVaultClient, fake_vault):
    await client.toggle_status(ITEM_COUPON)
    posts = fake_vault.calls("POST", f"/en/items/toggle_status/{ITEM_COUPON}")
    assert len(posts) == 1
    assert posts[0]["method"] == "POST"
    assert posts[0]["headers"]["x-csrftoken"] == CSRF
    assert posts[0]["headers"]["origin"] == BASE
    # name marker flipped by the fake proves the toggle reached the backend
    assert "[used]" in fake_vault.items[ITEM_COUPON]["name"]


async def test_delete_call_shape(client: VoucherVaultClient, fake_vault):
    await client.delete_item(ITEM_COUPON)
    posts = fake_vault.calls("POST", f"/en/items/delete/{ITEM_COUPON}")
    assert len(posts) == 1
    assert posts[0]["method"] == "POST"
    assert posts[0]["headers"]["x-csrftoken"] == CSRF
    assert ITEM_COUPON in fake_vault.deleted


async def test_delete_missing_item_404(client: VoucherVaultClient):
    with pytest.raises(VoucherVaultError) as exc:
        await client.delete_item("77777777-7777-7777-7777-777777777777")
    assert exc.value.status_code == 404


async def test_session_expiry_on_write_triggers_single_relogin(client: VoucherVaultClient, fake_vault):
    await client.login()
    logins_before = len(fake_vault.calls("POST", "/en/accounts/login/"))
    # next write GET is bounced to the login page -> client re-logins and retries
    fake_vault.force_login_redirect_get = 1
    await client.edit_item(ITEM_COUPON, {"description": "after relogin"})
    logins_after = len(fake_vault.calls("POST", "/en/accounts/login/"))
    assert logins_after == logins_before + 1
    posts = fake_vault.calls("POST", f"/en/items/edit/{ITEM_COUPON}")
    assert len(posts) == 1
    assert posts[0]["data"]["description"] == "after relogin"


async def test_session_expiry_on_post_triggers_single_relogin(client: VoucherVaultClient, fake_vault):
    await client.login()
    logins_before = len(fake_vault.calls("POST", "/en/accounts/login/"))
    fake_vault.force_login_redirect_post = 1
    await client.delete_item(ITEM_COUPON)
    logins_after = len(fake_vault.calls("POST", "/en/accounts/login/"))
    assert logins_after == logins_before + 1
    assert ITEM_COUPON in fake_vault.deleted


async def test_lang_prefix_fallback_when_en_404s(fake_vault, transport, env):
    fake_vault.prefix_mode = "none"
    client = VoucherVaultClient(transport=transport)
    await client.login()
    assert client._prefix == ""
    assert fake_vault.calls("GET", "/accounts/login/")
    # stats API is also served without prefix now
    await client.get_stats()
    assert fake_vault.calls("GET", "/api/get/stats")


async def test_stats_user_param_passed(client: VoucherVaultClient, fake_vault):
    await client.get_stats(user=USERNAME)
    call = fake_vault.calls("GET", "/en/api/get/stats")[0]
    assert call["params"] == {"user": USERNAME}


async def test_expired_filter_uses_today(client: VoucherVaultClient):
    """days_left is annotated relative to today (fake coupon expires today+30)."""
    items = await client.list_items()
    coupon = next(i for i in items if i["id"] == ITEM_COUPON)
    assert coupon["days_left"] == (
        date.fromisoformat(str(coupon["expiry_date"])[:10]) - date.today()
    ).days
    assert coupon["days_left"] == 30
