from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.numbering import generate_document_number
from masters.models import Site, TimeStampedModel
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
