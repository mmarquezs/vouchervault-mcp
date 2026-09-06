# extapi overlay for VoucherVault

A token-authenticated item API added on top of [VoucherVault](https://github.com/l4rm4nd/VoucherVault). It authenticates with the same AppSettings API token that protects the upstream `/api/get/stats` endpoint and is consumed by the [vouchervault-mcp](https://github.com/mmarquezs/vouchervault-mcp) client.

Upstream VoucherVault exposes no write REST API — this overlay adds one without forking the app: a small `extapi` package (views + urls only) plus a two-line patch that wires it into the project URLconf.

## Install (manual)

From this repository, copy the package next to the VoucherVault app root (so that `extapi/` sits alongside `myapp/` and `myproject/`), then apply the patch from the app root:

```bash
cp -r overlay/extapi /opt/app/extapi
cd /opt/app
patch -p1 < /path/to/overlay/patches/0001-extapi-wire-urls.patch
```

(or bake the equivalent into a Docker image build — your choice). The patch is a single 2-line append to `myproject/urls.py` mounting `api/v1/` to `extapi.urls`.

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

The patch was generated against VoucherVault **v1.30.x**. If it fails to apply, upstream changed — regenerate it against the new release; do not force it.
