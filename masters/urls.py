from django.urls import path

from masters import views

app_name = "masters"

urlpatterns = [
    path("sites/", views.SiteListView.as_view(), name="site_list"),
    path("sites/new/", views.SiteCreateView.as_view(), name="site_create"),
    path("sites/<int:pk>/edit/", views.SiteUpdateView.as_view(), name="site_update"),

    path("categories/", views.ItemCategoryListView.as_view(), name="category_list"),
    path("categories/new/", views.ItemCategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/edit/", views.ItemCategoryUpdateView.as_view(), name="category_update"),

    path("items/", views.ItemListView.as_view(), name="item_list"),
    path("items/new/", views.ItemCreateView.as_view(), name="item_create"),
    path("items/<int:pk>/", views.ItemDetailView.as_view(), name="item_detail"),
    path("items/<int:pk>/edit/", views.ItemUpdateView.as_view(), name="item_update"),
    path("items/<int:pk>/aliases/add/", views.ItemAliasCreateView.as_view(), name="item_alias_create"),
    path("items/<int:pk>/aliases/<int:alias_pk>/delete/", views.ItemAliasDeleteView.as_view(), name="item_alias_delete"),

    path("vendors/", views.VendorListView.as_view(), name="vendor_list"),
    path("vendors/new/", views.VendorCreateView.as_view(), name="vendor_create"),
    path("vendors/<int:pk>/", views.VendorDetailView.as_view(), name="vendor_detail"),
    path("vendors/<int:pk>/edit/", views.VendorUpdateView.as_view(), name="vendor_update"),
    path("vendors/<int:pk>/documents/add/", views.VendorDocumentCreateView.as_view(), name="vendor_document_create"),
    path("vendors/<int:pk>/documents/<int:doc_pk>/delete/", views.VendorDocumentDeleteView.as_view(), name="vendor_document_delete"),
]
