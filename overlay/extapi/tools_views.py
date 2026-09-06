# extapi - token-authenticated item API overlay for VoucherVault.
# Derivative of VoucherVault (https://github.com/l4rm4nd/VoucherVault), licensed GPL-3.0.
# Portions Copyright l4rm4nd / VoucherVault contributors.
# Modifications Copyright (c) 2026 mmarquezs.
"""Public tooling views for VoucherVault (overlay package).

Serve the checkout userscript and a one-time setup page under ``/tools/``.
Both views deliberately require NO authentication and carry no secrets:
the userscript prompts the user for their VoucherVault URL, username and
API token on first run (the token comes from VoucherVault admin > API
Settings).

VoucherVault's CSP allows inline styles but no inline JS — the setup page
is plain server-rendered HTML with a single ``<style>`` block and zero
client-side scripting.
"""

import html
import pathlib

from django.http import HttpResponse

_USERSCRIPT_NAME = "vouchervault-checkout.user.js"
# The Dockerfile copies the userscript next to this package:
# /opt/app/extapi/vouchervault-checkout.user.js
_USERSCRIPT_PATH = pathlib.Path(__file__).parent / _USERSCRIPT_NAME


def userscript(request):
    """GET /tools/vouchervault-checkout.user.js — download the userscript.

    No auth (the file contains no secrets) and only ``Cache-Control:
    no-cache`` so updates propagate without user action.
    """
    return HttpResponse(
        _USERSCRIPT_PATH.read_bytes(),
        content_type="application/javascript",
        headers={
            "Content-Disposition": f'inline; filename="{_USERSCRIPT_NAME}"',
            "Cache-Control": "no-cache",
        },
    )


_SETUP_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Checkout reminder — one-time setup</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2.5rem 1rem;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    line-height: 1.55;
    background: #f6f7f9; color: #1c1e21;
  }
  main { max-width: 40rem; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin: 0 0 1.25rem; }
  ol { padding-left: 1.25rem; margin: 0 0 1.5rem; }
  li { margin: 0 0 1rem; }
  a { color: #1a73e8; }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.9em;
    background: rgba(127, 127, 127, 0.14);
    border-radius: 4px; padding: 0.1em 0.35em;
    word-break: break-all;
  }
  .btn {
    display: inline-block;
    background: #1a73e8; color: #ffffff; text-decoration: none;
    font-weight: 600; font-size: 1rem;
    border-radius: 8px; padding: 0.65rem 1.25rem; margin: 0.15rem 0 0.25rem;
  }
  .btn:hover { background: #1558b0; }
  @media (prefers-color-scheme: dark) {
    body { background: #17181c; color: #e8e8ea; }
    a { color: #7aa2f7; }
    .btn { background: #7aa2f7; color: #10131a; }
    .btn:hover { background: #93b4f9; }
  }
</style>
</head>
<body>
<main>
<h1>Checkout reminder — one-time setup</h1>
<ol>
  <li>
    Install the <a href="https://tampermonkey.net/">Tampermonkey</a>
    browser extension (Chrome, Edge, Firefox, Safari — any userscript
    manager works).
  </li>
  <li>
    <a class="btn" href="/tools/vouchervault-checkout.user.js">Install userscript</a><br>
    Tampermonkey opens and offers to install the script — confirm it.
  </li>
  <li>
    On your first visit to any shop, the script asks for your VoucherVault
    URL, username and API token. You can get the token from VoucherVault
    under <strong>Admin → API Settings</strong>.
  </li>
</ol>
<p>
  Your VoucherVault base URL: <code>{base_url}</code><br>
  Paste exactly this into the first prompt of the script.
</p>
</main>
</body>
</html>
"""


def setup_page(request):
    """GET /tools/setup — self-contained setup instructions (no inline JS)."""
    base_url = request.scheme + "://" + request.get_host()
    page = _SETUP_PAGE.replace("{base_url}", html.escape(base_url, quote=True))
    return HttpResponse(
        page,
        content_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )
