from decimal import Decimal

from django.utils import timezone

from billing.models import DebitNote, Payment, VendorBill

AGING_BUCKETS = ["0-30", "31-60", "61-90", "90+"]


def vendor_ledger_entries(vendor):
    """Chronological (date, type, ref, amount, running_balance) rows for a
    vendor: opening balance, then bills (+), adjusted debit notes (-),
    payments (-)."""
    from billing.models import VendorOpeningBalance

    rows = []
    opening = VendorOpeningBalance.objects.filter(vendor=vendor).first()
    running = Decimal("0.00")
    if opening:
        running = opening.amount
        rows.append({"date": opening.as_of_date, "type": "Opening balance", "ref": "", "amount": opening.amount, "running_balance": running})

    events = []
    for bill in VendorBill.objects.filter(vendor=vendor).exclude(status=VendorBill.Status.CANCELLED):
        events.append((bill.vendor_invoice_date, "Bill", bill.bill_number, bill.grand_total))
    for dn in DebitNote.objects.filter(vendor=vendor, status=DebitNote.Status.ADJUSTED):
        events.append((dn.updated_at.date(), "Debit note", f"DN-{dn.pk}", -dn.amount))
    for payment in Payment.objects.filter(vendor=vendor):
        events.append((payment.payment_date, "Payment", payment.payment_number, -payment.net_amount))

    for date, entry_type, ref, amount in sorted(events, key=lambda e: e[0]):
        running += amount
        rows.append({"date": date, "type": entry_type, "ref": ref, "amount": amount, "running_balance": running})

    return rows


def payables_aging(site=None, category=None):
    """Bucket outstanding bill balances by days-overdue from due_date."""
    today = timezone.localdate()
    qs = VendorBill.objects.filter(
        status__in=[VendorBill.Status.APPROVED_FOR_PAYMENT, VendorBill.Status.PARTIALLY_PAID],
    ).select_related("vendor", "site")
    if site:
        qs = qs.filter(site=site)
    if category:
        qs = qs.filter(vendor__categories=category)

    buckets = {b: Decimal("0.00") for b in AGING_BUCKETS}
    rows = []
    for bill in qs:
        balance = bill.balance_due
        if balance <= 0:
            continue
        days_overdue = (today - bill.due_date).days if bill.due_date else 0
        if days_overdue <= 30:
            bucket = "0-30"
        elif days_overdue <= 60:
            bucket = "31-60"
        elif days_overdue <= 90:
            bucket = "61-90"
        else:
            bucket = "90+"
        buckets[bucket] += balance
        rows.append({"bill": bill, "days_overdue": days_overdue, "bucket": bucket, "balance": balance})

    return {"buckets": buckets, "rows": rows, "total": sum(buckets.values(), Decimal("0.00"))}
