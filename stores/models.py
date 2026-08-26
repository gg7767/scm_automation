from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.numbering import generate_document_number
from masters.models import Item, Site, TimeStampedModel
from purchase.models import PurchaseOrder, PurchaseOrderLine


class InvalidStatusTransition(Exception):
    pass


class GRN(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"

    grn_number = models.CharField(max_length=40, unique=True, blank=True)
    po = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="grns")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="grns", blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    received_date = models.DateField(default=timezone.localdate)

    vehicle_number = models.CharField(max_length=30, blank=True)
    challan_number = models.CharField(max_length=100)
    challan_date = models.DateField()
    challan_photo = models.ImageField(upload_to="grn_challans/%Y/%m/")
    remarks = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    is_reversal = models.BooleanField(default=False)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reversed_by")

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.grn_number or f"(draft GRN #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.site_id:
            self.site = self.po.site
        if not self.grn_number:
            self.grn_number = generate_document_number(
                GRN, "grn_number", "GRN", self.site.code, self.received_date or timezone.localdate()
            )
        super().save(*args, **kwargs)

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def submit(self, user):
        if self.status != self.Status.DRAFT:
            raise InvalidStatusTransition(f"Cannot submit a GRN in '{self.status}' status.")
        lines = list(self.lines.select_related("po_line"))
        if not lines:
            raise InvalidStatusTransition("Cannot submit a GRN with no lines.")

        if not self.is_reversal:
            tolerance = Decimal(str(settings.GRN_OVER_RECEIPT_TOLERANCE_PERCENT))
            for line in lines:
                already = (
                    GRNLine.objects.filter(po_line=line.po_line, grn__status=self.Status.SUBMITTED)
                    .aggregate(total=Sum("qty_received"))["total"] or Decimal("0")
                )
                max_allowed = line.po_line.quantity * (Decimal("1") + tolerance / Decimal("100"))
                if already + line.qty_received > max_allowed:
                    raise InvalidStatusTransition(
                        f"Receiving {line.qty_received} for {line.po_line.item.name} would exceed the "
                        f"ordered quantity ({line.po_line.quantity}) beyond the {tolerance}% tolerance. "
                        "Amend the PO to increase the ordered quantity instead."
                    )

        with transaction.atomic():
            self.status = self.Status.SUBMITTED
            self.save(update_fields=["status", "updated_at"])
            for line in lines:
                line.po_line.qty_received = line.po_line.qty_received + line.qty_accepted
                line.po_line.save(update_fields=["qty_received"])
                if line.qty_rejected:
                    DebitNoteCandidate.objects.create(
                        grn_line=line, qty=line.qty_rejected, reason=line.rejection_reason,
                    )
            self.po.refresh_delivery_status()

        from stores.services import record_receipt
        record_receipt(self)


class GRNLine(TimeStampedModel):
    grn = models.ForeignKey(GRN, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="grn_lines")
    qty_received = models.DecimalField(max_digits=12, decimal_places=3)
    qty_accepted = models.DecimalField(max_digits=12, decimal_places=3)
    qty_rejected = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))
    rejection_reason = models.CharField(max_length=255, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.po_line.item.name}: {self.qty_received}"

    def clean(self):
        if not self.grn_id or self.grn.is_reversal:
            return
        if self.qty_received < 0 or self.qty_accepted < 0 or self.qty_rejected < 0:
            raise ValidationError("Quantities cannot be negative.")
        if self.qty_accepted + self.qty_rejected > self.qty_received:
            raise ValidationError("Accepted + rejected quantity cannot exceed received quantity.")


class DebitNoteCandidate(TimeStampedModel):
    """Raised automatically when a GRN line rejects (damaged/short) qty.
    Resolved later in Phase 3 billing (converted into an actual DebitNote,
    or waived)."""

    grn_line = models.ForeignKey(GRNLine, on_delete=models.CASCADE, related_name="debit_note_candidates")
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=255, blank=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.grn_line.po_line.item.name}: {self.qty} rejected"


# --- Inventory (Phase 4) --------------------------------------------------
# Stock is never edited directly, only moved by documents — every quantity
# change goes through write_stock_ledger_entry(), which appends to
# StockLedger and updates the cached StockBalance in the same transaction.

class StockLedger(TimeStampedModel):
    """Append-only. Never update or delete a row — corrections are a new
    entry (e.g. txn_type=adjustment), same as GRN reversals."""

    class TxnType(models.TextChoices):
        OPENING = "opening", "Opening balance"
        GRN_RECEIPT = "grn_receipt", "GRN receipt"
        ISSUE = "issue", "Issue"
        TRANSFER_OUT = "transfer_out", "Transfer out"
        TRANSFER_IN = "transfer_in", "Transfer in"
        ADJUSTMENT = "adjustment", "Adjustment"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="stock_ledger_entries")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_ledger_entries")
    txn_date = models.DateField()
    txn_type = models.CharField(max_length=15, choices=TxnType.choices)
    qty = models.DecimalField(max_digits=12, decimal_places=3, help_text="Signed: negative for issues/transfers out.")
    ref_doc_type = models.CharField(max_length=30, blank=True)
    ref_doc_id = models.PositiveIntegerField(null=True, blank=True)
    rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-txn_date", "-created_at"]
        indexes = [models.Index(fields=["site", "item"])]

    def __str__(self):
        return f"{self.site.code}/{self.item.code}: {self.qty} ({self.txn_type})"


class StockBalance(TimeStampedModel):
    """Cached current stock per (site, item) for fast dashboards — always
    derivable from StockLedger; reconciled nightly by the
    reconcile_stock_balances management command."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="stock_balances")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_balances")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "item"], name="unique_site_item_balance")]

    def __str__(self):
        return f"{self.site.code}/{self.item.code}: {self.quantity}"

    @classmethod
    def apply_delta(cls, site, item, qty_delta):
        balance, _ = cls.objects.get_or_create(site=site, item=item)
        balance.quantity = balance.quantity + qty_delta
        balance.save(update_fields=["quantity"])
        return balance


def write_stock_ledger_entry(site, item, txn_date, txn_type, qty, ref_doc_type="", ref_doc_id=None, rate=None, remarks=""):
    with transaction.atomic():
        entry = StockLedger.objects.create(
            site=site, item=item, txn_date=txn_date, txn_type=txn_type, qty=qty,
            ref_doc_type=ref_doc_type, ref_doc_id=ref_doc_id, rate=rate, remarks=remarks,
        )
        StockBalance.apply_delta(site, item, qty)
    return entry


class SiteItemSetting(TimeStampedModel):
    """Per-site minimum stock threshold; below this, check_min_stock flags
    the item and drafts (never auto-submits) an indent."""

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="item_settings")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="site_settings")
    min_stock_qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "item"], name="unique_site_item_setting")]

    def __str__(self):
        return f"{self.site.code}/{self.item.code}: min {self.min_stock_qty}"


class StockIssue(TimeStampedModel):
    class Purpose(models.TextChoices):
        PRODUCTION = "production", "Production"
        PROJECT_CONSUMPTION = "project_consumption", "Project consumption"
        MAINTENANCE = "maintenance", "Maintenance"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"

    issue_number = models.CharField(max_length=40, unique=True, blank=True)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="stock_issues")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    issue_date = models.DateField(default=timezone.localdate)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    purpose_detail = models.CharField(max_length=255, blank=True)
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.issue_number or f"(draft issue #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.issue_number:
            self.issue_number = generate_document_number(StockIssue, "issue_number", "ISS", self.site.code, self.issue_date)
        super().save(*args, **kwargs)

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def submit(self, user):
        from django.conf import settings as django_settings

        if self.status != self.Status.DRAFT:
            raise InvalidStatusTransition(f"Cannot submit a stock issue in '{self.status}' status.")
        lines = list(self.lines.select_related("item"))
        if not lines:
            raise InvalidStatusTransition("Cannot submit a stock issue with no lines.")

        if not django_settings.ALLOW_NEGATIVE_STOCK:
            for line in lines:
                balance = StockBalance.objects.filter(site=self.site, item=line.item).first()
                available = balance.quantity if balance else Decimal("0")
                if line.qty > available:
                    raise InvalidStatusTransition(
                        f"Cannot issue {line.qty} of {line.item.name} — only {available} in stock at {self.site.code}."
                    )

        with transaction.atomic():
            self.status = self.Status.SUBMITTED
            self.save(update_fields=["status", "updated_at"])
            for line in lines:
                write_stock_ledger_entry(
                    site=self.site, item=line.item, txn_date=self.issue_date,
                    txn_type=StockLedger.TxnType.ISSUE, qty=-line.qty,
                    ref_doc_type="StockIssue", ref_doc_id=self.pk, remarks=line.remarks,
                )


class StockIssueLine(TimeStampedModel):
    issue = models.ForeignKey(StockIssue, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item.name}: {self.qty}"


class StockTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DISPATCHED = "dispatched", "Dispatched"
        RECEIVED = "received", "Received"

    transfer_number = models.CharField(max_length=40, unique=True, blank=True)
    from_site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="transfers_out")
    to_site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="transfers_in")
    vehicle_number = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DISPATCHED)

    dispatched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    received_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.transfer_number or f"(draft transfer #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.transfer_number:
            self.transfer_number = generate_document_number(
                StockTransfer, "transfer_number", "TRF", self.from_site.code, timezone.localdate()
            )
        super().save(*args, **kwargs)

    def receive(self, user, line_quantities):
        """line_quantities: {StockTransferLine: qty_received_decimal}."""
        if self.status != self.Status.DISPATCHED:
            raise InvalidStatusTransition(f"Cannot receive a transfer in '{self.status}' status.")
        with transaction.atomic():
            for line, qty_received in line_quantities.items():
                shortage = line.qty_dispatched - qty_received
                line.qty_received = qty_received
                if shortage > 0:
                    line.remarks = f"Shortage in transit: {shortage}"
                line.save(update_fields=["qty_received", "remarks", "updated_at"])
                write_stock_ledger_entry(
                    site=self.to_site, item=line.item, txn_date=timezone.localdate(),
                    txn_type=StockLedger.TxnType.TRANSFER_IN, qty=qty_received,
                    ref_doc_type="StockTransfer", ref_doc_id=self.pk,
                )
            self.status = self.Status.RECEIVED
            self.received_by = user
            self.received_at = timezone.now()
            self.save(update_fields=["status", "received_by", "received_at", "updated_at"])


class StockTransferLine(TimeStampedModel):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    qty_dispatched = models.DecimalField(max_digits=12, decimal_places=3)
    qty_received = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item.name}: {self.qty_dispatched}"
