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
    pos = vendor.purchase_orders.select_related("site").prefetch_related("lines").order_by("-created_at")
    open_pos = [po for po in pos if po.status in OPEN_STATUSES]
    open_value = sum((po.grand_total for po in open_pos), start=0)
    received_value = sum((po.received_value for po in open_pos), start=0)
    return {"purchase_orders": pos, "open_value": open_value, "received_value": received_value}


def procurement_lead_time():
    """Indent approved -> PO sent -> first GRN -> delivery complete, in
    days, with medians by vendor and by vendor's (first) category.
    Delivery-complete date is approximated by the latest submitted GRN's
    received_date for that PO (there's no separate completed-at timestamp
    on PurchaseOrder — delivery_complete is a plain boolean)."""
    import statistics
    from collections import defaultdict

    rows = []
    qs = PurchaseOrder.objects.filter(
        source_indent__isnull=False, source_indent__approved_at__isnull=False, sent_at__isnull=False,
    ).select_related("vendor", "source_indent")

    for po in qs:
        grn_dates = list(po.grns.filter(status="submitted").values_list("received_date", flat=True))
        if not grn_dates:
            continue
        first_grn = min(grn_dates)
        last_grn = max(grn_dates) if po.delivery_complete else None

        indent_to_sent = (po.sent_at.date() - po.source_indent.approved_at.date()).days
        sent_to_first_grn = (first_grn - po.sent_at.date()).days
        sent_to_complete = (last_grn - po.sent_at.date()).days if last_grn else None

        category = po.vendor.categories.first()
        rows.append({
            "po": po, "vendor": po.vendor.name, "category": category.name if category else "Uncategorized",
            "indent_to_sent_days": indent_to_sent, "sent_to_first_grn_days": sent_to_first_grn,
            "sent_to_complete_days": sent_to_complete,
        })

    def medians_by(key):
        buckets = defaultdict(list)
        for row in rows:
            buckets[row[key]].append(row["sent_to_first_grn_days"])
        return {k: statistics.median(v) for k, v in buckets.items()}

    return {
        "rows": rows,
        "median_by_vendor": medians_by("vendor"),
        "median_by_category": medians_by("category"),
        "overall_median_sent_to_first_grn": statistics.median([r["sent_to_first_grn_days"] for r in rows]) if rows else None,
    }
