from decimal import Decimal


def vendor_performance_score(vendor):
    """Simple weighted score (0-100) from on-time delivery %, short/damage
    %, rate-contract adherence %, and bill mismatch rate — each None if
    there's not enough history to compute it, and excluded from the
    weighted average in that case."""
    from billing.models import VendorBill
    from purchase.models import PurchaseOrder, PurchaseOrderLine
    from stores.models import GRN, GRNLine

    pos = PurchaseOrder.objects.filter(vendor=vendor, expected_delivery_date__isnull=False).exclude(
        status=PurchaseOrder.Status.CANCELLED
    )
    total_with_grn, on_time = 0, 0
    for po in pos:
        first_grn_date = (
            po.grns.filter(status=GRN.Status.SUBMITTED).order_by("received_date")
            .values_list("received_date", flat=True).first()
        )
        if first_grn_date:
            total_with_grn += 1
            if first_grn_date <= po.expected_delivery_date:
                on_time += 1
    on_time_pct = (on_time / total_with_grn * 100) if total_with_grn else None

    grn_lines = GRNLine.objects.filter(grn__po__vendor=vendor, grn__status=GRN.Status.SUBMITTED)
    total_received = sum((l.qty_received for l in grn_lines), Decimal("0"))
    total_rejected = sum((l.qty_rejected for l in grn_lines), Decimal("0"))
    short_damage_pct = float(total_rejected / total_received * 100) if total_received else None

    po_lines = PurchaseOrderLine.objects.filter(po__vendor=vendor)
    total_lines = po_lines.count()
    deviating = po_lines.filter(deviates_from_contract=True).count()
    adherence_pct = ((total_lines - deviating) / total_lines * 100) if total_lines else None

    bills = VendorBill.objects.filter(vendor=vendor).exclude(status=VendorBill.Status.DRAFT)
    total_bills = bills.count()
    mismatched = bills.filter(status=VendorBill.Status.MISMATCH).count()
    mismatch_rate_pct = (mismatched / total_bills * 100) if total_bills else None

    weighted = []
    if on_time_pct is not None:
        weighted.append((on_time_pct, 0.35))
    if short_damage_pct is not None:
        weighted.append((100 - short_damage_pct, 0.25))
    if adherence_pct is not None:
        weighted.append((adherence_pct, 0.25))
    if mismatch_rate_pct is not None:
        weighted.append((100 - mismatch_rate_pct, 0.15))

    score = None
    if weighted:
        total_weight = sum(w for _, w in weighted)
        score = sum(v * w for v, w in weighted) / total_weight

    return {
        "on_time_delivery_pct": on_time_pct,
        "short_damage_pct": short_damage_pct,
        "rate_contract_adherence_pct": adherence_pct,
        "bill_mismatch_rate_pct": mismatch_rate_pct,
        "score": score,
    }
