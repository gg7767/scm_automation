from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from purchase.models import ApprovalRule, POApprovalAction, POAttachment, PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0
    readonly_fields = ["line_total", "deviates_from_contract"]


class POAttachmentInline(admin.TabularInline):
    model = POAttachment
    extra = 0


class POApprovalActionInline(admin.TabularInline):
    model = POApprovalAction
    extra = 0
    readonly_fields = ["action", "actor", "comment", "created_at"]
    can_delete = False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(SimpleHistoryAdmin):
    list_display = ["po_number", "vendor", "site", "status", "grand_total", "expected_delivery_date"]
    list_filter = ["status", "site", "sent_via"]
    search_fields = ["po_number", "vendor__name", "vendor__code"]
    readonly_fields = ["po_number", "subtotal", "gst_amount", "grand_total", "revision_number"]
    inlines = [PurchaseOrderLineInline, POAttachmentInline, POApprovalActionInline]


@admin.register(ApprovalRule)
class ApprovalRuleAdmin(SimpleHistoryAdmin):
    list_display = ["min_amount", "max_amount", "approver_role"]
    list_filter = ["approver_role"]
