from decimal import Decimal

from django.contrib.auth.models import Group

from accounts_stub.notifications import notify
from masters.models import RateContract


def estimated_unit_rate(item):
    """Best-effort per-unit rate for indent approval routing: latest active
    rate contract for this item (any vendor), else the most recent PO line
    rate for it, else 0 (routes to the lowest approval band)."""
    latest_contract = (
        RateContract.objects.filter(item=item).order_by("-valid_from").first()
    )
    if latest_contract:
        return latest_contract.rate

    from purchase.models import PurchaseOrderLine
    last_line = PurchaseOrderLine.objects.filter(item=item).order_by("-created_at").first()
    if last_line:
        return last_line.rate

    return Decimal("0.00")


def convert_indent_lines_to_po(line_qtys, vendor, site, user, line_rates=None):
    """Create a draft PO from one or more approved indent lines (possibly
    from different indents, as long as they're all for `site`). Each
    resulting PO line links back to the indent line it fulfils; qty_ordered
    on those indent lines is only incremented when the PO is later approved
    (see PurchaseOrder.approve())."""
    from masters.models import RateContract
    from purchase.models import PurchaseOrder, PurchaseOrderLine

    line_rates = line_rates or {}

    first_indent = next(iter(line_qtys)).indent
    po = PurchaseOrder.objects.create(
        vendor=vendor, site=site, source_indent=first_indent,
        payment_terms_days=vendor.payment_terms_days, created_by=user,
    )
    for indent_line, qty in line_qtys.items():
        if indent_line in line_rates and line_rates[indent_line] is not None:
            rate = line_rates[indent_line]
        else:
            # 1. Check last PO line for this vendor & item
            last_po_line = PurchaseOrderLine.objects.filter(
                po__vendor=vendor, item=indent_line.item
            ).order_by("-created_at").first()
            if last_po_line:
                rate = last_po_line.rate
            else:
                # 2. Check rate contract
                contract_rate = RateContract.current_rate(vendor, indent_line.item)
                rate = contract_rate if contract_rate is not None else Decimal("0.00")

        PurchaseOrderLine.objects.create(
            po=po, item=indent_line.item, quantity=qty, rate=rate,
            indent_line=indent_line, created_by=user,
        )
    return po


def notify_urgent_indent(indent):
    from purchase.models import ApprovalRule

    group = ApprovalRule.approver_group_for_amount(indent.estimated_value(), doc_type=ApprovalRule.DocType.INDENT)
    if group is None:
        group = Group.objects.filter(name="SCM Head").first()
    if group is None:
        return

    subject = f"Urgent indent {indent.indent_number} needs approval"
    body = (
        f"{indent.raised_by} raised an urgent indent for {indent.site} "
        f"(estimated value ₹{indent.estimated_value()}). Please review: {indent.indent_number}"
    )
    for user in group.user_set.filter(is_active=True):
        notify(user, subject, body)
