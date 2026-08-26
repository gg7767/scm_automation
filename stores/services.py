from decimal import Decimal


def populate_lines_from_po(grn):
    """Pre-fill GRN lines from the PO's pending (not-yet-received)
    quantities so the site user only has to adjust, not type from scratch.
    Reversal GRNs get every line at zero — HO enters the correction."""
    from stores.models import GRNLine

    for po_line in grn.po.lines.all():
        if grn.is_reversal:
            GRNLine.objects.create(
                grn=grn, po_line=po_line,
                qty_received=Decimal("0"), qty_accepted=Decimal("0"), qty_rejected=Decimal("0"),
            )
        else:
            pending = po_line.pending_quantity
            if pending > 0:
                GRNLine.objects.create(
                    grn=grn, po_line=po_line,
                    qty_received=pending, qty_accepted=pending, qty_rejected=Decimal("0"),
                )


def record_receipt(grn):
    """Writes StockLedger `grn_receipt` entries (accepted qty only) for
    every line of `grn`. This was a no-op stub through Phase 2/3 —
    filling it in here is the only inventory-related change GRN
    submission code ever needed."""
    from stores.models import StockLedger, write_stock_ledger_entry

    for line in grn.lines.select_related("po_line__item"):
        if line.qty_accepted > 0:
            write_stock_ledger_entry(
                site=grn.site, item=line.po_line.item, txn_date=grn.received_date,
                txn_type=StockLedger.TxnType.GRN_RECEIPT, qty=line.qty_accepted,
                rate=line.po_line.rate, ref_doc_type="GRN", ref_doc_id=grn.pk,
            )


def dispatch_transfer(from_site, to_site, vehicle_number, user, item_qtys, remarks=""):
    """item_qtys: {Item: qty_dispatched_decimal}. Validates available stock
    at from_site (unless ALLOW_NEGATIVE_STOCK) and writes transfer_out
    entries immediately — a StockTransfer starts life already dispatched."""
    from django.conf import settings as django_settings
    from django.db import transaction
    from django.utils import timezone

    from stores.models import StockBalance, StockLedger, StockTransfer, StockTransferLine, write_stock_ledger_entry

    if not django_settings.ALLOW_NEGATIVE_STOCK:
        for item, qty in item_qtys.items():
            balance = StockBalance.objects.filter(site=from_site, item=item).first()
            available = balance.quantity if balance else Decimal("0")
            if qty > available:
                raise ValueError(f"Cannot transfer {qty} of {item.name} — only {available} in stock at {from_site.code}.")

    with transaction.atomic():
        transfer = StockTransfer.objects.create(
            from_site=from_site, to_site=to_site, vehicle_number=vehicle_number,
            dispatched_by=user, dispatched_at=timezone.now(),
            remarks=remarks, created_by=user,
        )
        for item, qty in item_qtys.items():
            StockTransferLine.objects.create(transfer=transfer, item=item, qty_dispatched=qty, created_by=user)
            write_stock_ledger_entry(
                site=from_site, item=item, txn_date=transfer.dispatched_at.date(),
                txn_type=StockLedger.TxnType.TRANSFER_OUT, qty=-qty,
                ref_doc_type="StockTransfer", ref_doc_id=transfer.pk,
            )
    return transfer


def stock_on_hand(site=None, item=None):
    from stores.models import StockBalance

    qs = StockBalance.objects.select_related("site", "item")
    if site:
        qs = qs.filter(site=site)
    if item:
        qs = qs.filter(item=item)
    return qs


def slow_moving_items(site=None, days=60):
    """Items with stock on hand but no issue in the last `days` days."""
    from django.utils import timezone

    from stores.models import StockBalance, StockLedger

    cutoff = timezone.localdate() - timezone.timedelta(days=days)
    qs = StockBalance.objects.filter(quantity__gt=0).select_related("site", "item")
    if site:
        qs = qs.filter(site=site)

    slow = []
    for balance in qs:
        recent_issue = StockLedger.objects.filter(
            site=balance.site, item=balance.item, txn_type=StockLedger.TxnType.ISSUE, txn_date__gte=cutoff,
        ).exists()
        if not recent_issue:
            slow.append(balance)
    return slow


def consumption_by_site_month():
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth

    from stores.models import StockLedger

    rows = (
        StockLedger.objects.filter(txn_type=StockLedger.TxnType.ISSUE)
        .annotate(month=TruncMonth("txn_date"))
        .values("site__code", "month")
        .annotate(total_qty=Sum("qty"))
        .order_by("site__code", "month")
    )
    return [
        {"site": r["site__code"], "month": r["month"].strftime("%b %Y"), "qty_issued": -r["total_qty"]}
        for r in rows
    ]
