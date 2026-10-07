from django.urls import path

from indents import views

app_name = "indents"

urlpatterns = [
    path("", views.IndentListView.as_view(), name="indent_list"),
    path("new/", views.IndentCreateView.as_view(), name="indent_create"),
    path("items/search/", views.ItemSearchView.as_view(), name="item_search"),
    path("vendor-rates/", views.VendorRatesView.as_view(), name="vendor_rates"),

    path("<int:pk>/", views.IndentDetailView.as_view(), name="indent_detail"),
    path("<int:pk>/lines/add/", views.IndentLineCreateView.as_view(), name="indent_line_create"),
    path("<int:pk>/lines/<int:line_pk>/delete/", views.IndentLineDeleteView.as_view(), name="indent_line_delete"),
    path("<int:pk>/lines/<int:line_pk>/reject/", views.IndentLineRejectView.as_view(), name="indent_line_reject"),

    path("<int:pk>/submit/", views.IndentSubmitView.as_view(), name="indent_submit"),
    path("<int:pk>/approve/", views.IndentApproveView.as_view(), name="indent_approve"),
    path("<int:pk>/reject/", views.IndentRejectView.as_view(), name="indent_reject"),
    path("<int:pk>/cancel/", views.IndentCancelView.as_view(), name="indent_cancel"),
    path("<int:pk>/convert/", views.IndentConvertView.as_view(), name="indent_convert"),
]
