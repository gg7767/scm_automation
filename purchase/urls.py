from django.urls import path

from purchase import views

app_name = "purchase"

urlpatterns = [
    path("", views.PurchaseOrderListView.as_view(), name="po_list"),
    path("new/", views.PurchaseOrderCreateView.as_view(), name="po_create"),
    path("items/search/", views.ItemSearchView.as_view(), name="item_search"),

    path("<int:pk>/", views.PurchaseOrderDetailView.as_view(), name="po_detail"),
    path("<int:pk>/edit/", views.PurchaseOrderUpdateView.as_view(), name="po_update"),
    path("<int:pk>/pdf/", views.POPdfView.as_view(), name="po_pdf"),

    path("<int:pk>/lines/add/", views.POLineCreateView.as_view(), name="po_line_create"),
    path("<int:pk>/lines/<int:line_pk>/edit/", views.POLineUpdateView.as_view(), name="po_line_update"),
    path("<int:pk>/lines/<int:line_pk>/delete/", views.POLineDeleteView.as_view(), name="po_line_delete"),

    path("<int:pk>/submit/", views.POSubmitView.as_view(), name="po_submit"),
    path("<int:pk>/approve/", views.POApproveView.as_view(), name="po_approve"),
    path("<int:pk>/reject/", views.PORejectView.as_view(), name="po_reject"),
    path("<int:pk>/send/", views.POMarkSentView.as_view(), name="po_send"),
    path("<int:pk>/cancel/", views.POCancelView.as_view(), name="po_cancel"),
    path("<int:pk>/close/", views.POCloseView.as_view(), name="po_close"),
    path("<int:pk>/amend/", views.POAmendView.as_view(), name="po_amend"),

    path("<int:pk>/attachments/add/", views.POAttachmentCreateView.as_view(), name="po_attachment_create"),
    path("<int:pk>/attachments/<int:att_pk>/delete/", views.POAttachmentDeleteView.as_view(), name="po_attachment_delete"),

    path("vendors/<int:pk>/ledger/", views.VendorLedgerView.as_view(), name="vendor_ledger"),
]
