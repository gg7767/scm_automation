from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.models import TimeStampedModel, Vendor
from masters.numbering import financial_year_label, generate_document_number
from purchase.models import PurchaseOrder, PurchaseOrderLine
from stores.models import GRNLine


class InvalidStatusTransition(Exception):
    pass


class VendorBill(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        MATCHED = "matched", "Matched"
        MISMATCH = "mismatch", "Mismatch"
        APPROVED_FOR_PAYMENT = "approved_for_payment", "Approved for payment"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    bill_number = models.CharField(max_length=40, unique=True, blank=True)
    vendor_invoice_number = models.CharField(max_length=100)
    vendor_invoice_date = models.DateField()
    financial_year = models.CharField(max_length=5, blank=True, editable=False)

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="bills")
    po = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="bills", null=True, blank=True,
        help_text="Leave blank for a freight bill linked to transport trips instead.",
    )
    transport_trips = models.ManyToManyField(
        "logistics.TransportTrip", blank=True, related_name="bills",
        help_text="For transporter freight bills, in place of a PO — uses a 2-way match (trip freight vs bill total).",
    )
    site = models.ForeignKey("masters.Site", on_delete=models.PROTECT, related_name="bills", blank=True)

    invoice_scan = models.FileField(upload_to="vendor_bills/%Y/%m/")

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    gst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    other_charges = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    round_off = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    status = models.CharField(max_length=25, choices=Status.choices, default=Status.DRAFT)
    match_result = models.JSONField(null=True, blank=True)
    matched_at = models.DateTimeField(null=True, blank=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    override_reason = models.TextField(blank=True)

    due_date = models.DateField(null=True, blank=True)

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "vendor_invoice_number", "financial_year"],
                name="unique_vendor_invoice_per_fy",
            )
        ]

    def __str__(self):
        return self.bill_number or f"(draft bill #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.site_id:
            if self.po_id:
                self.site = self.po.site
            else:
                raise ValueError(
                    "A trip-based bill (no PO) must have `site` set explicitly before the "
                    "first save, since transport_trips (M2M) isn't available until after save()."
                )
        if not self.financial_year:
            self.financial_year = financial_year_label(self.vendor_invoice_date)
        if not self.bill_number:
            self.bill_number = generate_document_number(
                VendorBill, "bill_number", "BILL", self.site.code, self.vendor_invoice_date
            )
        if not self.due_date:
            self.due_date = self.vendor_invoice_date + timezone.timedelta(days=self.vendor.payment_terms_days)
        super().save(*args, **kwargs)

    def clean(self):
        existing = VendorBill.objects.filter(
            vendor=self.vendor, vendor_invoice_number=self.vendor_invoice_number,
            financial_year=financial_year_label(self.vendor_invoice_date),
        ).exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                f"Invoice {self.vendor_invoice_number} from this vendor has already been entered this financial year."
            )

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def recompute_totals(self, save=True):
        subtotal = Decimal("0.00")
        gst_amount = Decimal("0.00")
        for line in self.lines.all():
            subtotal += line.line_total
            gst_amount += (line.line_total * line.gst_rate / Decimal("100")).quantize(Decimal("0.01"))
        self.subtotal = subtotal
        self.gst_amount = gst_amount
        self.grand_total = subtotal + gst_amount + self.other_charges + self.round_off
        if save:
            VendorBill.objects.filter(pk=self.pk).update(
                subtotal=self.subtotal, gst_amount=self.gst_amount, grand_total=self.grand_total
            )

    # --- payment rollup -------------------------------------------------

    @property
    def total_paid(self):
        return sum((a.amount for a in self.allocations.all()), Decimal("0.00"))

    @property
    def balance_due(self):
        return self.grand_total - self.total_paid

    def refresh_payment_status(self):
        if self.status not in (
            self.Status.APPROVED_FOR_PAYMENT, self.Status.PARTIALLY_PAID, self.Status.PAID,
        ):
            return
        paid = self.total_paid
        if paid <= 0:
            new_status = self.Status.APPROVED_FOR_PAYMENT
        elif paid < self.grand_total:
            new_status = self.Status.PARTIALLY_PAID
        else:
            new_status = self.Status.PAID
        if new_status != self.status:
            self.status = new_status
            self.save(update_fields=["status", "updated_at"])

    # --- 3-way match ------------------------------------------------

    def submit_for_matching(self, user):
        if self.status != self.Status.DRAFT:
            raise InvalidStatusTransition(f"Cannot match a bill in '{self.status}' status.")
        if self.po_id:
            if not self.lines.exists():
                raise InvalidStatusTransition("Cannot match a bill with no lines.")
            self._run_three_way_match(user)
        elif self.transport_trips.exists():
            self._run_two_way_match_for_trips(user)
        else:
            raise InvalidStatusTransition("A bill must be linked to a PO or at least one transport trip.")

    def _run_two_way_match_for_trips(self, user):
        """Freight bills from transporters: 2-way check (trip freight vs
        bill total) in place of the full 3-way PO/GRN/bill match, since
        there's no PO or GRN involved."""
        from django.conf import settings as django_settings

        total_tolerance = Decimal(str(django_settings.BILL_MATCH_TOTAL_TOLERANCE_RUPEES))
        total_freight = sum((trip.freight_amount for trip in self.transport_trips.all()), Decimal("0.00"))
        ok = abs(total_freight - self.grand_total) <= total_tolerance

        self.match_result = {
            "trip_match": {
                "total_freight": str(total_freight), "bill_grand_total": str(self.grand_total), "ok": ok,
            }
        }
        self.matched_at = timezone.now()
        if ok:
            self.status = self.Status.APPROVED_FOR_PAYMENT
            self.approved_at = timezone.now()
            self.save(update_fields=["match_result", "matched_at", "status", "approved_at", "updated_at"])
        else:
            self.status = self.Status.MISMATCH
            self.save(update_fields=["match_result", "matched_at", "status", "updated_at"])

    def _run_three_way_match(self, user):
        from django.conf import settings as django_settings

        qty_tolerance = Decimal(str(django_settings.BILL_MATCH_QTY_TOLERANCE_PERCENT))
        rate_tolerance = Decimal(str(django_settings.BILL_MATCH_RATE_TOLERANCE_PERCENT))
        total_tolerance = Decimal(str(django_settings.BILL_MATCH_TOTAL_TOLERANCE_RUPEES))

        result = {}
        all_ok = True
        recomputed_subtotal = Decimal("0.00")
        recomputed_gst = Decimal("0.00")

        for line in self.lines.select_related("po_line"):
            po_line = line.po_line
            available = po_line.qty_received - po_line.qty_already_billed
            max_allowed_qty = available * (Decimal("1") + qty_tolerance / Decimal("100"))
            qty_ok = line.quantity_billed <= max_allowed_qty

            if po_line.rate:
                rate_diff_pct = abs(line.rate_billed - po_line.rate) / po_line.rate * Decimal("100")
            else:
                rate_diff_pct = Decimal("0") if line.rate_billed == 0 else Decimal("100")
            rate_ok = rate_diff_pct <= rate_tolerance

            gst_ok = line.gst_rate == po_line.gst_rate

            line_ok = qty_ok and rate_ok and gst_ok
            all_ok = all_ok and line_ok

            recomputed_subtotal += line.line_total
            recomputed_gst += (line.line_total * line.gst_rate / Decimal("100")).quantize(Decimal("0.01"))

            result[str(line.pk)] = {
                "item": po_line.item.name,
                "po_says": {"rate": str(po_line.rate), "gst_rate": str(po_line.gst_rate), "quantity": str(po_line.quantity)},
                "grn_says": {"qty_accepted": str(po_line.qty_received), "qty_already_billed": str(po_line.qty_already_billed)},
                "bill_says": {"quantity_billed": str(line.quantity_billed), "rate_billed": str(line.rate_billed), "gst_rate": str(line.gst_rate)},
                "qty_ok": qty_ok, "rate_ok": rate_ok, "gst_ok": gst_ok, "line_ok": line_ok,
            }

        recomputed_total = recomputed_subtotal + recomputed_gst + self.other_charges + self.round_off
        total_ok = abs(recomputed_total - self.grand_total) <= total_tolerance
        all_ok = all_ok and total_ok
        result["totals"] = {
            "recomputed_total": str(recomputed_total), "bill_grand_total": str(self.grand_total), "total_ok": total_ok,
        }

        self.match_result = result
        self.matched_at = timezone.now()

        if all_ok:
            self.status = self.Status.APPROVED_FOR_PAYMENT
            self.approved_at = timezone.now()
            self.save(update_fields=["match_result", "matched_at", "status", "approved_at", "updated_at"])
            self._apply_qty_already_billed()
            self._maybe_close_po(user)
        else:
            self.status = self.Status.MISMATCH
            self.save(update_fields=["match_result", "matched_at", "status", "updated_at"])

    def override_mismatch(self, user, reason):
        if self.status != self.Status.MISMATCH:
            raise InvalidStatusTransition(f"Cannot override a bill in '{self.status}' status.")
        if not reason:
            raise InvalidStatusTransition("An override reason is required.")
        self.status = self.Status.APPROVED_FOR_PAYMENT
        self.approved_by = user
        self.approved_at = timezone.now()
        self.override_reason = reason
        self.save(update_fields=["status", "approved_by", "approved_at", "override_reason", "updated_at"])
        self._apply_qty_already_billed()
        self._maybe_close_po(user)

    def _apply_qty_already_billed(self):
        for line in self.lines.select_related("po_line"):
            line.po_line.qty_already_billed = line.po_line.qty_already_billed + line.quantity_billed
            line.po_line.save(update_fields=["qty_already_billed"])

    def _maybe_close_po(self, user):
        if not self.po_id:
            return  # trip-based bill, no PO to close
        po = self.po
        po.refresh_from_db()
        if not po.delivery_complete:
            return
        fully_billed = all(line.qty_already_billed >= line.quantity for line in po.lines.all())
        if fully_billed:
            try:
                po.close(user)
            except Exception:
                pass

    def cancel(self, user, reason):
        if self.status in (self.Status.PAID, self.Status.CANCELLED):
            raise InvalidStatusTransition(f"Cannot cancel a bill in '{self.status}' status.")
        if not reason:
            raise InvalidStatusTransition("A cancellation reason is required.")
        self.status = self.Status.CANCELLED
        self.cancelled_by = user
        self.cancelled_at = timezone.now()
        self.cancelled_reason = reason
        self.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancelled_reason", "updated_at"])


class VendorBillLine(TimeStampedModel):
    bill = models.ForeignKey(VendorBill, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="bill_lines")
    quantity_billed = models.DecimalField(max_digits=12, decimal_places=3)
    rate_billed = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, editable=False, default=Decimal("0.00"))

    history = HistoricalRecords()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.po_line.item.name}: {self.quantity_billed}"

    def save(self, *args, **kwargs):
        self.line_total = (self.quantity_billed * self.rate_billed).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)
        self.bill.recompute_totals()


class BillRemark(TimeStampedModel):
    """Free-form remarks thread on a bill — used for 'sent back to vendor'
    notes on a mismatch, or any other commentary that isn't a status change."""

    bill = models.ForeignKey(VendorBill, on_delete=models.CASCADE, related_name="remarks")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    text = models.TextField()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.bill.bill_number}: {self.text[:40]}"


class DebitNote(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ADJUSTED = "adjusted", "Adjusted"

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="debit_notes")
    grn_line = models.ForeignKey(GRNLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="debit_notes")
    bill = models.ForeignKey(VendorBill, null=True, blank=True, on_delete=models.SET_NULL, related_name="debit_notes")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"DN {self.vendor.code}: ₹{self.amount} ({self.status})"

    def adjust(self):
        if self.status == self.Status.ADJUSTED:
            raise InvalidStatusTransition("Debit note is already adjusted.")
        self.status = self.Status.ADJUSTED
        self.save(update_fields=["status", "updated_at"])


class VendorOpeningBalance(TimeStampedModel):
    """Imported opening payable balance as of go-live, so the vendor ledger
    doesn't start at zero for vendors with pre-existing dues."""

    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE, related_name="opening_balance")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    as_of_date = models.DateField()

    def __str__(self):
        return f"{self.vendor.code} opening balance: ₹{self.amount}"


class Payment(TimeStampedModel):
    class Mode(models.TextChoices):
        NEFT = "neft", "NEFT"
        RTGS = "rtgs", "RTGS"
        IMPS = "imps", "IMPS"
        CHEQUE = "cheque", "Cheque"
        UPI = "upi", "UPI"
        CASH = "cash", "Cash"

    class PaymentType(models.TextChoices):
        AGAINST_BILLS = "against_bills", "Against bills"
        ADVANCE = "advance", "Advance"

    payment_number = models.CharField(max_length=40, unique=True, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    mode = models.CharField(max_length=10, choices=Mode.choices)
    reference_number = models.CharField(max_length=100, blank=True)
    remarks = models.TextField(blank=True)
    payment_type = models.CharField(max_length=15, choices=PaymentType.choices, default=PaymentType.AGAINST_BILLS)
    tds_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    history = HistoricalRecords()

    class Meta:
        ordering = ["-payment_date", "-created_at"]

    def __str__(self):
        return self.payment_number or f"(draft payment #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.payment_number:
            self.payment_number = generate_document_number(
                Payment, "payment_number", "PAY", "HO", self.payment_date
            )
        super().save(*args, **kwargs)

    @property
    def net_amount(self):
        return self.amount - self.tds_amount

    @property
    def allocated_total(self):
        return sum((a.amount for a in self.allocations.all()), Decimal("0.00"))

    @property
    def unallocated_amount(self):
        return self.net_amount - self.allocated_total


class PaymentAllocation(TimeStampedModel):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    bill = models.ForeignKey(VendorBill, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.payment.payment_number} -> {self.bill.bill_number}: ₹{self.amount}"

    def clean(self):
        if self.amount <= 0:
            raise ValidationError("Allocation amount must be positive.")
        # On an edit, add back this allocation's *original* stored amount
        # (not the new proposed one) before checking headroom.
        original_amount = Decimal("0")
        if self.pk:
            original_amount = PaymentAllocation.objects.get(pk=self.pk).amount
        payment_remaining = self.payment.unallocated_amount + original_amount
        if self.amount > payment_remaining:
            raise ValidationError(f"Only ₹{payment_remaining} is unallocated on this payment.")
        bill_remaining = self.bill.balance_due + original_amount
        if self.amount > bill_remaining:
            raise ValidationError(f"This bill only has ₹{bill_remaining} outstanding.")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.bill.refresh_payment_status()

    def delete(self, *args, **kwargs):
        bill = self.bill
        super().delete(*args, **kwargs)
        bill.refresh_payment_status()
