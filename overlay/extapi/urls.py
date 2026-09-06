# extapi - token-authenticated item API overlay for VoucherVault.
# Derivative of VoucherVault (https://github.com/l4rm4nd/VoucherVault), licensed GPL-3.0.
# Portions Copyright l4rm4nd / VoucherVault contributors.
# Modifications Copyright (c) 2026 mmarquezs.
from django.urls import path
from . import views

app_name = "extapi"

urlpatterns = [
    path("items", views.items_collection, name="items-collection"),
    path("items/", views.items_collection),
    path("items/<uuid:item_id>", views.item_detail, name="item-detail"),
    path("items/<uuid:item_id>/", views.item_detail),
    path("items/<uuid:item_id>/toggle-status", views.item_toggle, name="item-toggle"),
    path("items/<uuid:item_id>/toggle-status/", views.item_toggle),
]
