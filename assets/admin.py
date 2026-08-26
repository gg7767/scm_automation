from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from assets.models import MachineDeployment, MachineLog, MaintenanceSchedule, Machine


class MachineDeploymentInline(admin.TabularInline):
    model = MachineDeployment
    extra = 0


class MaintenanceScheduleInline(admin.TabularInline):
    model = MaintenanceSchedule
    extra = 0


@admin.register(Machine)
class MachineAdmin(SimpleHistoryAdmin):
    list_display = ["code", "name", "category", "ownership", "status"]
    list_filter = ["category", "ownership", "status"]
    search_fields = ["code", "name"]
    readonly_fields = ["code"]
    inlines = [MachineDeploymentInline, MaintenanceScheduleInline]


@admin.register(MachineLog)
class MachineLogAdmin(admin.ModelAdmin):
    list_display = ["machine", "site", "log_date", "hours_run", "km", "fuel_litres"]
    list_filter = ["site"]
    search_fields = ["machine__code", "machine__name"]
