from django.urls import path

from assets import views

app_name = "assets"

urlpatterns = [
    path("", views.MachineListView.as_view(), name="machine_list"),
    path("new/", views.MachineCreateView.as_view(), name="machine_create"),
    path("<int:pk>/", views.MachineDetailView.as_view(), name="machine_detail"),
    path("<int:pk>/deploy/", views.MachineDeployView.as_view(), name="machine_deploy"),
    path("<int:pk>/logs/add/", views.MachineLogCreateView.as_view(), name="machine_log_create"),
    path("reports/maintenance-due/", views.MaintenanceDueReportView.as_view(), name="maintenance_due"),
]
