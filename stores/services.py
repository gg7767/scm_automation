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
    """Stub for Phase 4: writes StockLedger `grn_receipt` entries (accepted
    qty only) for every line of `grn`. Kept as a single hook so GRN
    submission code never needs to change when Phase 4 lands — see
    docs/PHASE4_INVENTORY_TRANSPORT_MACHINERY.md."""
    pass
