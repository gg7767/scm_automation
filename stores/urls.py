from django.urls import path

from stores import views

app_name = "stores"

urlpatterns = [
    path("", views.GRNListView.as_view(), name="grn_list"),
    path("pick-po/", views.OpenPOPickerView.as_view(), name="po_picker"),
    path("new/", views.GRNCreateView.as_view(), name="grn_create"),
    path("new/reversal/", views.GRNReversalCreateView.as_view(), name="grn_reversal_create"),
    path("<int:pk>/", views.GRNDetailView.as_view(), name="grn_detail"),
    path("<int:pk>/lines/", views.GRNLinesUpdateView.as_view(), name="grn_lines_update"),
    path("<int:pk>/submit/", views.GRNSubmitView.as_view(), name="grn_submit"),

    path("stock/balances/", views.StockBalanceListView.as_view(), name="stock_balance_list"),
    path("stock/issues/", views.StockIssueListView.as_view(), name="stock_issue_list"),
    path("stock/issues/new/", views.StockIssueCreateView.as_view(), name="stock_issue_create"),
    path("stock/issues/<int:pk>/", views.StockIssueDetailView.as_view(), name="stock_issue_detail"),
    path("stock/issues/<int:pk>/submit/", views.StockIssueSubmitView.as_view(), name="stock_issue_submit"),
    path("reports/slow-moving/", views.SlowMovingStockView.as_view(), name="slow_moving_stock"),
]
