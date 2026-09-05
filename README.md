# VoucherVault MCP Server

MCP server for [VoucherVault](https://github.com/l4rm4nd/VoucherVault) voucher/coupon management. Enables AI agents to list, search, create, update, mark used, and delete vouchers, coupons, gift cards, and loyalty cards programmatically.

Built with [FastMCP](https://github.com/jlowin/fastmcp), runs as a stdio subprocess — no HTTP server, no Docker.

## Installation

```bash
pip install vouchervault-mcp
```

Or install directly from GitHub (pin to a tag):

```bash
pip install vouchervault-mcp@git+https://github.com/mmarquezs/vouchervault-mcp@v0.1.0
```

## Configuration

Set these environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `VOUCHERVAULT_URL` | Base URL of your VoucherVault instance | `https://vouchervault.example.com` |
| `VOUCHERVAULT_USERNAME` | **LOCAL** Django account username | `admin` |
| `VOUCHERVAULT_PASSWORD` | Local account password | `your-password` |
| `VOUCHERVAULT_API_TOKEN` | Bearer token for the read API (generate in the VoucherVault **Django admin**) | `abc123...` |
| `VOUCHERVAULT_LANG_PREFIX` | i18n URL prefix (default `/en`; auto-falls back to none if `/en/` 404s) | `/en` |

> **Important:** only **LOCAL Django accounts** can password-login. If your
> instance authenticates via OIDC, create a dedicated local superuser for this
> server (OIDC users cannot log in through the Django login form).

## MCP Client Configuration

Add to your MCP client config (e.g. Claude Desktop, opencode, Cursor):

```json
{
  "mcpServers": {
    "vouchervault": {
      "command": "vouchervault-mcp",
      "env": {
        "VOUCHERVAULT_URL": "https://vouchervault.example.com",
        "VOUCHERVAULT_USERNAME": "admin",
        "VOUCHERVAULT_PASSWORD": "your-password",
        "VOUCHERVAULT_API_TOKEN": "your-api-token",
        "VOUCHERVAULT_LANG_PREFIX": "/en"
      }
    }
  }
}
```

Or with `uvx`:

```json
{
  "mcpServers": {
    "vouchervault": {
      "command": "uvx",
      "args": ["vouchervault-mcp"],
      "env": {
        "VOUCHERVAULT_URL": "https://vouchervault.example.com",
        "VOUCHERVAULT_USERNAME": "admin",
        "VOUCHERVAULT_PASSWORD": "your-password",
        "VOUCHERVAULT_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

## How writes work

VoucherVault upstream exposes **no write REST API**. The only API endpoint is
`GET /api/get/stats[?user=<name>]` (Bearer token) — which this server uses for
all reads.

Everything else is performed exactly like the web UI does it:

1. Session login via the Django login form at `{lang_prefix}/accounts/login/`
   (CSRF token parsed from the form, session cookie kept, `Origin`/`Referer`
   headers set to the base URL as Django requires over https).
2. Each write (`items/create/`, `items/edit/<uuid>`, `items/delete/<uuid>`,
   `items/toggle_status/<uuid>`) is a form POST with the CSRF token
   (`csrfmiddlewaretoken` field + `X-CSRFToken` header) and the full set of
   `ItemForm` fields (`name`, `issuer`, `redeem_code`, `pin`, `issue_date`,
   `expiry_date`, `description`, `logo_slug`, `type`, `value`, `value_type`,
   `currency`, `code_type`, `tile_color`) — mirrors of upstream
   `myapp/forms.py`. Edits first GET the form and merge changed fields into
   the current values so required fields are never lost.
3. If a write gets bounced to the login page (session expiry), the client
   re-logs-in once and retries automatically.

Field names and routes were verified against VoucherVault
[`myapp/forms.py`](https://github.com/l4rm4nd/VoucherVault/blob/main/myapp/forms.py)
and `myapp/urls.py`. Because the integration drives HTML forms, **pin your
VoucherVault image to the tested 1.30.x series** — a major UI/form refactor
upstream can break writes.

## Tools

| Tool | Description |
|------|-------------|
| `coupons_list` | List/search items with substring search, type filter, `include_used`/`include_expired` flags and `days_left` annotation |
| `coupon_get` | Get full details of a single item by id (UUID) |
| `coupon_create` | Create a coupon/voucher/gift card/loyalty card |
| `coupon_update` | Update item fields by id (only provided fields change) |
| `coupon_mark_used` | Toggle used status — calling again marks the item available |
| `coupon_delete` | Permanently delete an item |

### Field notes

- `issuer` should be the merchant **DOMAIN** like `amazon.es` — the checkout
  userscript matches on it.
- `item_type`: `voucher` | `giftcard` | `coupon` | `loyaltycard`
  (loyalty cards require `value` 0).
- `value_type`: `money` | `percentage` (0–100) | `multiplier` (≥ 1).
- Dates use `YYYY-MM-DD`. Pass `expiry_date=""` to let upstream set it 50
  years out.

## Development

```bash
git clone https://github.com/mmarquezs/vouchervault-mcp
cd vouchervault-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
bandit -r vouchervault_mcp -ll -ii
```

Run the server:

```bash
VOUCHERVAULT_URL=https://vouchervault.example.com \
VOUCHERVAULT_USERNAME=admin \
VOUCHERVAULT_PASSWORD=pass \
VOUCHERVAULT_API_TOKEN=token \
vouchervault-mcp
```

## License

MIT
