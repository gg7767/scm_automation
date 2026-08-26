from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("", views.VendorBillListView.as_view(), name="bill_list"),
    path("pick-po/", views.BillablePOPickerView.as_view(), name="po_picker"),
    path("new/", views.VendorBillCreateView.as_view(), name="bill_create"),
    path("<int:pk>/", views.VendorBillDetailView.as_view(), name="bill_detail"),
    path("<int:pk>/lines/", views.BillLinesUpdateView.as_view(), name="bill_lines_update"),
    path("<int:pk>/match/", views.BillSubmitMatchView.as_view(), name="bill_match"),
    path("<int:pk>/override/", views.BillOverrideView.as_view(), name="bill_override"),
    path("<int:pk>/cancel/", views.BillCancelView.as_view(), name="bill_cancel"),
    path("<int:pk>/remarks/add/", views.BillRemarkCreateView.as_view(), name="bill_remark_create"),

    path("debit-note-candidates/<int:candidate_pk>/create/", views.DebitNoteFromCandidateView.as_view(), name="debit_note_from_candidate"),
    path("debit-notes/<int:pk>/adjust/", views.DebitNoteAdjustView.as_view(), name="debit_note_adjust"),

    path("payments/", views.PaymentListView.as_view(), name="payment_list"),
    path("payments/new/", views.PaymentCreateView.as_view(), name="payment_create"),
    path("payments/<int:pk>/", views.PaymentDetailView.as_view(), name="payment_detail"),
    path("payments/<int:pk>/allocate/", views.PaymentAllocationCreateView.as_view(), name="payment_allocate"),
    path("payments/<int:pk>/allocations/<int:allocation_pk>/delete/", views.PaymentAllocationDeleteView.as_view(), name="payment_allocation_delete"),

    path("vendors/<int:pk>/ledger/", views.VendorFullLedgerView.as_view(), name="vendor_ledger"),
    path("vendors/<int:pk>/ledger/export/", views.VendorLedgerExcelView.as_view(), name="vendor_ledger_export"),
    path("payables-aging/", views.PayablesAgingView.as_view(), name="payables_aging"),
    path("payables-aging/export/", views.PayablesAgingExcelView.as_view(), name="payables_aging_export"),
]
