from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from billing.models import (
    BillRemark,
    DebitNote,
    Payment,
    PaymentAllocation,
    VendorBill,
    VendorBillLine,
    VendorOpeningBalance,
)


class VendorBillLineInline(admin.TabularInline):
    model = VendorBillLine
    extra = 0
    readonly_fields = ["line_total"]


class BillRemarkInline(admin.TabularInline):
    model = BillRemark
    extra = 0


@admin.register(VendorBill)
class VendorBillAdmin(SimpleHistoryAdmin):
    list_display = ["bill_number", "vendor", "vendor_invoice_number", "status", "grand_total", "due_date"]
    list_filter = ["status", "site"]
    search_fields = ["bill_number", "vendor_invoice_number", "vendor__name"]
    readonly_fields = ["bill_number", "financial_year", "match_result"]
    inlines = [VendorBillLineInline, BillRemarkInline]


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0


@admin.register(Payment)
class PaymentAdmin(SimpleHistoryAdmin):
    list_display = ["payment_number", "vendor", "amount", "payment_type", "mode", "payment_date"]
    list_filter = ["payment_type", "mode"]
    search_fields = ["payment_number", "vendor__name", "reference_number"]
    readonly_fields = ["payment_number"]
    inlines = [PaymentAllocationInline]


@admin.register(DebitNote)
class DebitNoteAdmin(SimpleHistoryAdmin):
    list_display = ["vendor", "amount", "status", "reason"]
    list_filter = ["status"]


@admin.register(VendorOpeningBalance)
class VendorOpeningBalanceAdmin(admin.ModelAdmin):
    list_display = ["vendor", "amount", "as_of_date"]
