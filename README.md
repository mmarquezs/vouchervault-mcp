# VoucherVault MCP Server

MCP server for [VoucherVault](https://github.com/l4rm4nd/VoucherVault) voucher/coupon management. Enables AI agents to list, search, create, update, mark used, and delete vouchers, coupons, gift cards, and loyalty cards programmatically.

Built with [FastMCP](https://github.com/jlowin/fastmcp), runs as a stdio subprocess — no HTTP server, no Docker.

## Installation

```bash
pip install vouchervault-mcp
```

Or install directly from GitHub (pin to a tag or commit):

```bash
pip install vouchervault-mcp@git+https://github.com/mmarquezs/vouchervault-mcp@<ref>
```

## Configuration

Set these environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `VOUCHERVAULT_URL` | Base URL of the VoucherVault container (the **internal** URL) | `http://vouchervault.internal:8000` |
| `VOUCHERVAULT_API_TOKEN` | Bearer token for the token API (same token as the read stats endpoint; generate in the VoucherVault **Django admin**) | `abc123...` |

That's all — no username/password, no session, no CSRF.

## MCP Client Configuration

Add to your MCP client config (e.g. Claude Desktop, opencode, Cursor):

```json
{
  "mcpServers": {
    "vouchervault": {
      "command": "vouchervault-mcp",
      "env": {
        "VOUCHERVAULT_URL": "http://vouchervault.internal:8000",
        "VOUCHERVAULT_API_TOKEN": "your-api-token"
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
        "VOUCHERVAULT_URL": "http://vouchervault.internal:8000",
        "VOUCHERVAULT_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

## How it works — token API (extapi overlay)

VoucherVault upstream exposes **no write REST API**. This deployment adds the
`extapi` overlay patch (maintained in the ansible repo and applied on top of
the pinned upstream image), which provides a token-authenticated JSON API.
The server talks only to that API — the base URL is the **internal container
URL** (e.g. `http://vouchervault.internal:8000`), and every call carries
`Authorization: Bearer ${VOUCHERVAULT_API_TOKEN}` (the same token the legacy
read-stats endpoint uses).

- No session login, no CSRF tokens, no local Django user needed.
- Reads and writes all go through `/api/v1/*`; responses are JSON and errors
  carry the server's payload (`400 {"errors": {...}}`, `401`/`403` for a bad
  or missing token, `404` for unknown items). The client tolerates both
  trailing-slash variants of every route.
- `days_left` is computed server-side; the client never re-derives it.

Endpoints used:

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/api/v1/items?search=&type=&include_used=&include_expired=&username=` | list (ordered by `expiry_date`) |
| `POST` | `/api/v1/items/` | create |
| `GET` | `/api/v1/items/{id}` | detail |
| `PATCH` | `/api/v1/items/{id}` | partial update |
| `POST` | `/api/v1/items/{id}/toggle-status/` | toggle used/available |
| `DELETE` | `/api/v1/items/{id}` | delete |

### Pinning + overlay drift

The VoucherVault image is **pinned to the tested 1.30.x series**, and the
`extapi` overlay patch is rebuilt on top of it. The overlay build **fails
loudly** if the upstream files it touches drift from what the patch expects —
so a major upstream refactor cannot silently break this integration; it
breaks the image build instead and gets dealt with before deploy.

## Tools

| Tool | Description |
|------|-------------|
| `coupons_list` | List/search items with substring search, type filter, `include_used`/`include_expired` flags and server-side `days_left` annotation |
| `coupon_get` | Get full details of a single item by id (UUID) |
| `coupon_create` | Create a coupon/voucher/gift card/loyalty card (returns the created item) |
| `coupon_update` | Update item fields by id (only provided fields change; returns the updated item) |
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
ruff check vouchervault_mcp tests
```

Run the server:

```bash
VOUCHERVAULT_URL=http://vouchervault.internal:8000 \
VOUCHERVAULT_API_TOKEN=token \
vouchervault-mcp
```

## License

MIT
