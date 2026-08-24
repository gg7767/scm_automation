from django.utils import timezone

from purchase.models import ApprovalRule, PurchaseOrder
from purchase.permissions import visible_po_queryset

OPEN_STATUSES = [
    PurchaseOrder.Status.DRAFT,
    PurchaseOrder.Status.PENDING_APPROVAL,
    PurchaseOrder.Status.APPROVED,
    PurchaseOrder.Status.SENT,
    PurchaseOrder.Status.PARTIALLY_DELIVERED,
]


def pending_approvals_for_user(user):
    candidates = visible_po_queryset(user, PurchaseOrder.objects.filter(
        status=PurchaseOrder.Status.PENDING_APPROVAL
    )).select_related("vendor", "site")
    return [po for po in candidates if ApprovalRule.can_user_approve(user, po.grand_total)]


def recent_purchase_orders(user, limit=5):
    return visible_po_queryset(user, PurchaseOrder.objects.select_related("vendor", "site")).order_by(
        "-created_at"
    )[:limit]


def this_month_stats(user):
    today = timezone.localdate()
    qs = visible_po_queryset(user, PurchaseOrder.objects.filter(
        created_at__year=today.year, created_at__month=today.month,
    ))
    count = qs.count()
    total_value = sum((po.grand_total for po in qs), start=0)
    return {"count": count, "total_value": total_value}


def overdue_purchase_orders(user):
    today = timezone.localdate()
    qs = visible_po_queryset(user, PurchaseOrder.objects.filter(
        expected_delivery_date__lt=today,
    ).exclude(status__in=[PurchaseOrder.Status.CLOSED, PurchaseOrder.Status.CANCELLED]))
    return qs.select_related("vendor", "site")


def vendor_ledger(vendor):
    pos = vendor.purchase_orders.select_related("site").order_by("-created_at")
    open_value = sum((po.grand_total for po in pos if po.status in OPEN_STATUSES), start=0)
    return {"purchase_orders": pos, "open_value": open_value}
