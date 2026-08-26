from django.urls import path

from logistics import views

app_name = "logistics"

urlpatterns = [
    path("", views.TripListView.as_view(), name="trip_list"),
    path("new/", views.TripCreateView.as_view(), name="trip_create"),
    path("<int:pk>/", views.TripDetailView.as_view(), name="trip_detail"),
    path("reports/freight/", views.FreightReportView.as_view(), name="freight_report"),
]
