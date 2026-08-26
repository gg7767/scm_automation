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
]
