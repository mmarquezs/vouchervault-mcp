# extapi - token-authenticated item API overlay for VoucherVault.
# Derivative of VoucherVault (https://github.com/l4rm4nd/VoucherVault), licensed GPL-3.0.
# Portions Copyright l4rm4nd / VoucherVault contributors.
# Modifications Copyright (c) 2026 mmarquezs.
"""URLconf for the public /tools/ surface (setup page + userscript).

Wired into upstream ``myproject/urls.py`` by
``patches/0002-extapi-tools-urls.patch``. These routes are intentionally
public (no auth): they carry no secrets — the userscript prompts the user
for their VoucherVault URL / username / API token at first run.
"""

from django.urls import path

from . import tools_views

urlpatterns = [
    path("vouchervault-checkout.user.js", tools_views.userscript, name="userscript"),
    path("vouchervault-checkout.user.js/", tools_views.userscript),
    path("setup", tools_views.setup_page, name="setup-page"),
    path("setup/", tools_views.setup_page),
]
