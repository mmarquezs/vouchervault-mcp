# extapi overlay for VoucherVault

A token-authenticated item API added on top of [VoucherVault](https://github.com/l4rm4nd/VoucherVault). It authenticates with the same AppSettings API token that protects the upstream `/api/get/stats` endpoint and is consumed by the [vouchervault-mcp](https://github.com/mmarquezs/vouchervault-mcp) client.

Upstream VoucherVault exposes no write REST API — this overlay adds one without forking the app: a small `extapi` package (views + urls only) plus a two-line patch that wires it into the project URLconf.

## Tools (public, no auth)

The overlay also serves the [checkout userscript](userscript/vouchervault-checkout.user.js) and a one-time setup page straight from the VoucherVault instance:

| Route | Purpose |
|-------|---------|
| `/tools/setup` | step-by-step setup instructions (install Tampermonkey → install the script → where to get the API token) |
| `/tools/vouchervault-checkout.user.js` | the userscript itself (`application/javascript`, inline disposition) |

Both routes are public by design — the script contains no secrets; it prompts the user for their VoucherVault URL / username / API token on first run (token from VoucherVault admin → API Settings). The userscript is original MIT-licensed code and lives in `userscript/`; the deployment copies it next to the `extapi` package so `tools_views.py` can serve it via `Path(__file__).parent`.

## Install (manual)

From this repository, copy the package next to the VoucherVault app root (so that `extapi/` sits alongside `myapp/` and `myproject/`), then apply the patches from the app root:

```bash
cp -r overlay/extapi /opt/app/extapi
cp overlay/userscript/vouchervault-checkout.user.js /opt/app/extapi/
cd /opt/app
patch -p1 < /path/to/overlay/patches/0001-extapi-wire-urls.patch
patch -p1 < /path/to/overlay/patches/0002-extapi-tools-urls.patch
patch -p1 < /path/to/overlay/patches/0003-nav-tools-link.patch   # optional sidebar link
```

(or bake the equivalent into a Docker image build — your choice). The patches are tiny end-of-file appends to `myproject/urls.py` (0001: mounts `api/v1/` to `extapi.urls`; 0002: mounts `tools/` to `extapi.tools_urls`) plus an optional one-block sidebar entry (0003).

## Endpoints

All routes require `Authorization: Bearer <AppSettings API token>`.

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/api/v1/items/` | list items (filters: `search`, `type`, `username`, `include_used`, `include_expired`) |
| `POST` | `/api/v1/items/` | create item |
| `GET` | `/api/v1/items/{id}/` | item detail |
| `PATCH` | `/api/v1/items/{id}/` | partial update |
| `DELETE` | `/api/v1/items/{id}/` | delete item |
| `POST` | `/api/v1/items/{id}/toggle-status/` | toggle used/available |

Items created via the API are owned by the `EXTAPI_ITEM_OWNER` env var (a username) when set, otherwise the first active superuser.

## Version note

The patches are generated against VoucherVault **v1.30.x** and applied in filename order (0001 → 0002 → 0003) — 0002 and 0003 assume 0001 is already applied. If any patch fails to apply, upstream changed — regenerate the affected patch against the new release; do not force it.

Drift risk per patch:

- `0001-extapi-wire-urls.patch` / `0002-extapi-tools-urls.patch` — one-line appends to `myproject/urls.py` (low risk).
- `0003-nav-tools-link.patch` — touches `myapp/templates/base.html` (a **template**, the highest drift risk of the three; regenerate it first when a release changes base.html). It only adds a sidebar link to `/tools/setup`; skipping it is fine, the setup page stays reachable at its URL.
