from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from stores.models import DebitNoteCandidate, GRN, GRNLine


class GRNLineInline(admin.TabularInline):
    model = GRNLine
    extra = 0


@admin.register(GRN)
class GRNAdmin(SimpleHistoryAdmin):
    list_display = ["grn_number", "po", "site", "status", "received_date", "is_reversal"]
    list_filter = ["status", "site", "is_reversal"]
    search_fields = ["grn_number", "po__po_number", "challan_number"]
    readonly_fields = ["grn_number"]
    inlines = [GRNLineInline]


@admin.register(DebitNoteCandidate)
class DebitNoteCandidateAdmin(admin.ModelAdmin):
    list_display = ["grn_line", "qty", "reason", "resolved"]
    list_filter = ["resolved"]
