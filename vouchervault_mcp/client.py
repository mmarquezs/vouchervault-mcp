"""Async HTTP client for VoucherVault (Django).

VoucherVault exposes NO write REST API. Only one API endpoint exists:

  GET /api/get/stats[?user=<name>]   (Authorization: Bearer <api token>)

Everything else (create/edit/delete/toggle) is done through the regular web
UI forms, which means session cookie + Django CSRF handling:

  1. GET the form page, parse the `csrfmiddlewaretoken` hidden input.
  2. POST the form fields back to the same URL with cookies, an
     `X-CSRFToken` header and `Origin`/`Referer` headers set to the base URL
     (Django requires those over https).

Login is the standard Django login form at `{lang_prefix}/accounts/login/`
(username + password + csrfmiddlewaretoken). NOTE: only LOCAL Django
accounts can log in this way — OIDC users cannot password-login.

Routes are served under an i18n prefix (default `/en`); if that 404s the
client automatically falls back to no prefix.

Session expiry is handled by re-logging-in once and retrying a write call
that got redirected to the login page.

Environment variables:
  VOUCHERVAULT_URL          - base URL, e.g. https://vouchervault.example.com
  VOUCHERVAULT_USERNAME     - LOCAL Django username (not OIDC)
  VOUCHERVAULT_PASSWORD     - local account password
  VOUCHERVAULT_API_TOKEN    - Bearer token for the stats read API
                              (generated in the VoucherVault Django admin)
  VOUCHERVAULT_LANG_PREFIX  - i18n URL prefix, default `/en`
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from http.cookiejar import CookieJar
from typing import Any

import httpx
from bs4 import BeautifulSoup

_TIMEOUT = 30.0

# ItemForm.Meta.fields from upstream myapp/forms.py (VoucherVault main branch).
# These are the EXACT form field names the web UI posts; write payloads must
# stay within this set (plus `csrfmiddlewaretoken`).
ITEM_FORM_FIELDS: tuple[str, ...] = (
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
)

# Item.ITEM_TYPES from upstream myapp/models.py
ITEM_TYPES: tuple[str, ...] = ("voucher", "giftcard", "coupon", "loyaltycard")
# Item.VALUE_TYPES from upstream myapp/models.py
VALUE_TYPES: tuple[str, ...] = ("money", "percentage", "multiplier")

logger = logging.getLogger("vouchervault_mcp")


class VoucherVaultError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        self.status_code = status_code
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


class VoucherVaultClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        base_url = os.environ.get("VOUCHERVAULT_URL", "").rstrip("/")
        if not base_url:
            raise ValueError("VOUCHERVAULT_URL environment variable is required")

        username = os.environ.get("VOUCHERVAULT_USERNAME", "")
        password = os.environ.get("VOUCHERVAULT_PASSWORD", "")
        if not username or not password:
            raise ValueError(
                "VOUCHERVAULT_USERNAME and VOUCHERVAULT_PASSWORD environment "
                "variables are required (a LOCAL Django account — OIDC users "
                "cannot password-login)"
            )

        self._base = base_url
        self._username = username
        self._password = password
        self._api_token = os.environ.get("VOUCHERVAULT_API_TOKEN", "")
        prefix = os.environ.get("VOUCHERVAULT_LANG_PREFIX", "/en")
        self._prefix = prefix if prefix else ""
        self._logged_in = False
        self._transport = transport
        # persistent cookie jar shared by every request client, so the
        # Django session/csrftoken cookies survive across calls
        self._cookie_jar = CookieJar()

    # ------------------------------------------------------------------ #
    # low-level helpers
    # ------------------------------------------------------------------ #

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            transport=self._transport,
            cookies=self._cookie_jar,
        )

    def _form_headers(self, token: str | None = None) -> dict[str, str]:
        """Headers every browser form POST would send (Django CSRF over https)."""
        headers = {
            "Origin": self._base,
            "Referer": f"{self._base}/",
        }
        if token:
            headers["X-CSRFToken"] = token
        return headers

    def _secrets(self) -> tuple[str, ...]:
        return (self._password, self._api_token)

    def _scrub_response(self, text: str) -> str:
        return _scrub(text, *self._secrets())

    @staticmethod
    def _redirected_to_login(response: httpx.Response) -> bool:
        if "accounts/login" in str(response.url):
            return True
        if response.status_code in (301, 302, 303, 307, 308):
            return "accounts/login" in response.headers.get("location", "")
        return False

    @staticmethod
    def _parse_form(html: str, hint_field: str | None = None) -> tuple[str, dict[str, str]]:
        """Extract csrfmiddlewaretoken and current values from a form.

        `hint_field` selects the form that contains that input name, so the
        right form is picked on pages with several forms.
        """
        soup = BeautifulSoup(html, "html.parser")
        for form in soup.find_all("form"):
            token_input = form.find("input", {"name": "csrfmiddlewaretoken"})
            if token_input is None:
                continue
            if hint_field is not None and form.find("input", {"name": hint_field}) is None:
                continue
            token = token_input.get("value", "")
            values: dict[str, str] = {}
            for node in form.find_all(["input", "textarea", "select"]):
                name = node.get("name")
                if not name or name == "csrfmiddlewaretoken":
                    continue
                if node.name == "input":
                    itype = (node.get("type") or "text").lower()
                    if itype in ("checkbox", "radio"):
                        if node.has_attr("checked"):
                            values[name] = node.get("value", "on")
                    elif itype in ("submit", "button", "image", "file"):
                        continue
                    else:
                        values[name] = node.get("value", "")
                elif node.name == "textarea":
                    values[name] = node.get_text()
                elif node.name == "select":
                    option = node.find("option", selected=True) or node.find("option")
                    if option is not None:
                        values[name] = option.get("value", option.get_text(strip=True))
            return token, values
        raise VoucherVaultError(
            "could not find a form with a csrfmiddlewaretoken on the page"
        )

    @staticmethod
    def _extract_form_errors(html: str) -> list[str]:
        """Best-effort extraction of Django errorlist entries from a re-rendered form."""
        soup = BeautifulSoup(html, "html.parser")
        errors: list[str] = []
        for ul in soup.find_all("ul", class_="errorlist"):
            for li in ul.find_all("li"):
                text = li.get_text(" ", strip=True)
                if text:
                    errors.append(text)
        return errors

    @staticmethod
    def _form_str(value: Any) -> str:
        return str(value)

    # ------------------------------------------------------------------ #
    # login / session
    # ------------------------------------------------------------------ #

    async def login(self) -> None:
        """Authenticate with the Django login form and verify the session."""
        async with self._client() as c:
            page = await c.get(
                self._url(f"{self._prefix}/accounts/login/"),
                headers=self._form_headers(),
            )
            if page.status_code == 404 and self._prefix:
                # i18n prefix not enabled on this instance -> fall back to ""
                self._prefix = ""
                page = await c.get(
                    self._url(f"{self._prefix}/accounts/login/"),
                    headers=self._form_headers(),
                )
            if page.status_code >= 400:
                raise VoucherVaultError(
                    f"login page not reachable (HTTP {page.status_code}) at "
                    f"{self._prefix}/accounts/login/"
                )

            token, _ = self._parse_form(page.text, hint_field="username")

            response = await c.post(
                self._url(f"{self._prefix}/accounts/login/"),
                data={
                    "username": self._username,
                    "password": self._password,
                    "csrfmiddlewaretoken": token,
                    "next": "/",
                },
                headers=self._form_headers(token),
            )
            if self._redirected_to_login(response):
                raise VoucherVaultError(
                    "VoucherVault login failed — check VOUCHERVAULT_USERNAME/"
                    "VOUCHERVAULT_PASSWORD. NOTE: only LOCAL Django accounts "
                    "can password-login; OIDC users cannot."
                )

            verified = await c.get(
                self._url(f"{self._prefix}/"), headers=self._form_headers()
            )
            if verified.status_code >= 400 or self._redirected_to_login(verified):
                raise VoucherVaultError(
                    "VoucherVault login could not be verified (session cookie "
                    "missing or dashboard redirected to login)"
                )

        self._logged_in = True
        logger.info("logged in to VoucherVault")

    async def _ensure_session(self) -> None:
        if not self._logged_in:
            await self.login()

    async def _csrf_token(self, c: httpx.AsyncClient) -> str:
        """CSRF token for plain POST endpoints: prefer the csrftoken cookie
        (the AJAX mechanism Django supports), else parse it from a page."""
        token = c.cookies.get("csrftoken")
        if token:
            return token
        page = await c.get(self._url(f"{self._prefix}/"), headers=self._form_headers())
        token = c.cookies.get("csrftoken")
        if token:
            return token
        _, values = self._parse_form(page.text)
        if values.get("csrfmiddlewaretoken"):
            return values["csrfmiddlewaretoken"]
        raise VoucherVaultError("could not obtain a CSRF token for the session")

    # ------------------------------------------------------------------ #
    # read API (Bearer token)
    # ------------------------------------------------------------------ #

    async def get_stats(self, user: str | None = None) -> dict[str, Any]:
        """GET /api/get/stats with the Bearer API token; returns parsed JSON."""
        if not self._api_token:
            raise VoucherVaultError(
                "VOUCHERVAULT_API_TOKEN is not set — generate an API token in "
                "the VoucherVault Django admin (it authorizes the stats API)"
            )
        params = {"user": user} if user else None
        auth_headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Accept": "application/json",
        }
        async with self._client() as c:
            r = await c.get(
                self._url(f"{self._prefix}/api/get/stats"),
                params=params,
                headers=auth_headers,
            )
            if r.status_code == 404 and self._prefix:
                r = await c.get(
                    self._url("/api/get/stats"), params=params, headers=auth_headers
                )
            if r.status_code in (401, 403):
                raise VoucherVaultError(
                    "VoucherVault stats API rejected the token "
                    f"(HTTP {r.status_code}) — check VOUCHERVAULT_API_TOKEN "
                    "against the token configured in the VoucherVault Django admin",
                    status_code=r.status_code,
                )
            if r.status_code >= 400:
                raise VoucherVaultError(
                    f"VoucherVault stats API error (HTTP {r.status_code}): "
                    f"{self._scrub_response(r.text[:500])}",
                    status_code=r.status_code,
                )
            try:
                return r.json()
            except ValueError as exc:
                raise VoucherVaultError(
                    f"VoucherVault stats API returned invalid JSON: {exc}"
                ) from exc

    # ------------------------------------------------------------------ #
    # client-side filtering over item_details
    # ------------------------------------------------------------------ #

    async def list_items(
        self,
        search: str | None = None,
        item_type: str | None = None,
        include_used: bool = False,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        stats = await self.get_stats()
        items = stats.get("item_details", [])
        today = date.today()
        result: list[dict[str, Any]] = []
        for raw in items:
            item = dict(raw)
            query = (search or "").lower()
            if query:
                haystack = " ".join(
                    str(item.get(field) or "")
                    for field in ("name", "issuer", "redeem_code")
                ).lower()
                if query not in haystack:
                    continue
            if item_type and item.get("type") != item_type:
                continue
            if not include_used and item.get("is_used"):
                continue
            days_left: int | None = None
            expiry = item.get("expiry_date")
            if expiry:
                try:
                    days_left = (date.fromisoformat(str(expiry)[:10]) - today).days
                except ValueError:
                    days_left = None
            if not include_expired and days_left is not None and days_left < 0:
                continue
            try:
                item["value"] = float(item["value"])
            except (KeyError, TypeError, ValueError):
                pass
            item["days_left"] = days_left
            result.append(item)
        return result

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        """Fetch a single item (with days_left) from the stats API by id."""
        for item in await self.list_items(include_used=True, include_expired=True):
            if str(item.get("id")) == str(item_id):
                return item
        return None

    # ------------------------------------------------------------------ #
    # writes (session + CSRF form posts)
    # ------------------------------------------------------------------ #

    async def create_item(self, fields: dict[str, Any]) -> None:
        """POST the create form. `fields` keys must be ITEM_FORM_FIELDS."""
        unknown = set(fields) - set(ITEM_FORM_FIELDS)
        if unknown:
            raise VoucherVaultError(
                f"unknown form fields for VoucherVault ItemForm: {sorted(unknown)}"
            )
        await self._ensure_session()
        async with self._client() as c:
            path = f"{self._prefix}/items/create/"
            for attempt in (1, 2):
                page = await c.get(self._url(path), headers=self._form_headers())
                if self._redirected_to_login(page):
                    self._logged_in = False
                    if attempt == 1:
                        await self.login()
                        continue
                    raise VoucherVaultError("session expired and re-login failed")
                token, _ = self._parse_form(page.text, hint_field="redeem_code")
                data = {k: self._form_str(v) for k, v in fields.items()}
                data["csrfmiddlewaretoken"] = token
                r = await c.post(
                    self._url(path), data=data, headers=self._form_headers(token)
                )
                if self._redirected_to_login(r):
                    self._logged_in = False
                    if attempt == 1:
                        await self.login()
                        continue
                    raise VoucherVaultError("session expired and re-login failed")
                if "items/create" in str(r.url.path):
                    errors = self._extract_form_errors(r.text)
                    detail = "; ".join(errors[:5]) if errors else "form was rejected"
                    raise VoucherVaultError(
                        f"VoucherVault rejected the new item: {detail}"
                    )
                return  # success: redirected to the items list

    async def edit_item(self, item_uuid: str, changed_fields: dict[str, Any]) -> None:
        """GET the edit form, merge `changed_fields` into the current values,
        and POST ALL form fields back (Django validates required ones)."""
        unknown = set(changed_fields) - set(ITEM_FORM_FIELDS)
        if unknown:
            raise VoucherVaultError(
                f"unknown form fields for VoucherVault ItemForm: {sorted(unknown)}"
            )
        await self._ensure_session()
        async with self._client() as c:
            path = f"{self._prefix}/items/edit/{item_uuid}"
            for attempt in (1, 2):
                page = await c.get(self._url(path), headers=self._form_headers())
                if page.status_code == 404:
                    raise VoucherVaultError(
                        f"item {item_uuid} not found (or not owned by this user)",
                        status_code=404,
                    )
                if self._redirected_to_login(page):
                    self._logged_in = False
                    if attempt == 1:
                        await self.login()
                        continue
                    raise VoucherVaultError("session expired and re-login failed")
                token, current = self._parse_form(page.text, hint_field="redeem_code")
                current.pop("csrfmiddlewaretoken", None)
                merged = {**current, **changed_fields}
                data = {k: self._form_str(v) for k, v in merged.items() if v is not None}
                data["csrfmiddlewaretoken"] = token
                r = await c.post(
                    self._url(path), data=data, headers=self._form_headers(token)
                )
                if self._redirected_to_login(r):
                    self._logged_in = False
                    if attempt == 1:
                        await self.login()
                        continue
                    raise VoucherVaultError("session expired and re-login failed")
                if "items/edit" in str(r.url.path):
                    errors = self._extract_form_errors(r.text)
                    detail = "; ".join(errors[:5]) if errors else "form was rejected"
                    raise VoucherVaultError(
                        f"VoucherVault rejected the item update: {detail}"
                    )
                return  # success: redirected to the item view page

    async def toggle_status(self, item_uuid: str) -> None:
        """POST items/toggle_status/<uuid> (marks used <-> available)."""
        await self._ensure_session()
        async with self._client() as c:
            path = f"{self._prefix}/items/toggle_status/{item_uuid}"
            for attempt in (1, 2):
                token = await self._csrf_token(c)
                r = await c.post(
                    self._url(path), data={}, headers=self._form_headers(token)
                )
                if self._redirected_to_login(r):
                    self._logged_in = False
                    if attempt == 1:
                        await self.login()
                        continue
                    raise VoucherVaultError("session expired and re-login failed")
                # judge by the original POST response: redirects to the item
                # view page are success; a bare 404/4xx means item missing
                original = r.history[0] if r.history else r
                if original.status_code == 404:
                    raise VoucherVaultError(
                        f"item {item_uuid} not found (or not owned by this user)",
                        status_code=404,
                    )
                if original.status_code >= 400:
                    raise VoucherVaultError(
                        f"VoucherVault toggle failed (HTTP {original.status_code}): "
                        f"{self._scrub_response(r.text[:500])}",
                        status_code=original.status_code,
                    )
                return

    async def delete_item(self, item_uuid: str) -> None:
        """POST items/delete/<uuid> (upstream requires POST)."""
        await self._ensure_session()
        async with self._client() as c:
            path = f"{self._prefix}/items/delete/{item_uuid}"
            for attempt in (1, 2):
                token = await self._csrf_token(c)
                r = await c.post(
                    self._url(path), data={}, headers=self._form_headers(token)
                )
                if self._redirected_to_login(r):
                    self._logged_in = False
                    if attempt == 1:
                        await self.login()
                        continue
                    raise VoucherVaultError("session expired and re-login failed")
                # judge by the original POST response: a redirect to the items
                # list is success; a bare 404/4xx means item missing
                original = r.history[0] if r.history else r
                if original.status_code == 404:
                    raise VoucherVaultError(
                        f"item {item_uuid} not found (or not owned by this user)",
                        status_code=404,
                    )
                if original.status_code >= 400:
                    raise VoucherVaultError(
                        f"VoucherVault delete failed (HTTP {original.status_code}): "
                        f"{self._scrub_response(r.text[:500])}",
                        status_code=original.status_code,
                    )
                return
