from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from stores.models import (
    DebitNoteCandidate,
    GRN,
    GRNLine,
    SiteItemSetting,
    StockBalance,
    StockIssue,
    StockIssueLine,
    StockLedger,
    StockTransfer,
    StockTransferLine,
)


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


@admin.register(StockLedger)
class StockLedgerAdmin(admin.ModelAdmin):
    list_display = ["site", "item", "txn_date", "txn_type", "qty", "ref_doc_type", "ref_doc_id"]
    list_filter = ["txn_type", "site"]
    search_fields = ["item__name", "item__code"]


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ["site", "item", "quantity"]
    list_filter = ["site"]
    search_fields = ["item__name", "item__code"]


@admin.register(SiteItemSetting)
class SiteItemSettingAdmin(admin.ModelAdmin):
    list_display = ["site", "item", "min_stock_qty"]
    list_filter = ["site"]


class StockIssueLineInline(admin.TabularInline):
    model = StockIssueLine
    extra = 0


@admin.register(StockIssue)
class StockIssueAdmin(SimpleHistoryAdmin):
    list_display = ["issue_number", "site", "purpose", "status", "issue_date"]
    list_filter = ["status", "purpose", "site"]
    readonly_fields = ["issue_number"]
    inlines = [StockIssueLineInline]


class StockTransferLineInline(admin.TabularInline):
    model = StockTransferLine
    extra = 0


@admin.register(StockTransfer)
class StockTransferAdmin(SimpleHistoryAdmin):
    list_display = ["transfer_number", "from_site", "to_site", "status"]
    list_filter = ["status", "from_site", "to_site"]
    readonly_fields = ["transfer_number"]
    inlines = [StockTransferLineInline]
