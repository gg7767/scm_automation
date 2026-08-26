from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from logistics.models import TransportTrip


@admin.register(TransportTrip)
class TransportTripAdmin(SimpleHistoryAdmin):
    list_display = ["trip_number", "trip_date", "transporter", "to_site", "freight_amount", "billable_to"]
    list_filter = ["billable_to", "to_site", "transporter"]
    search_fields = ["trip_number", "vehicle_number", "driver_name"]
    readonly_fields = ["trip_number"]
