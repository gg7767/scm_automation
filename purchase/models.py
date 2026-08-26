from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.models import Item, RateContract, Site, TimeStampedModel, Vendor
from purchase.numbering import generate_po_number


class InvalidStatusTransition(Exception):
    pass


class PurchaseOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        SENT = "sent", "Sent"
        PARTIALLY_DELIVERED = "partially_delivered", "Partially delivered"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class SentVia(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"
        MANUAL = "manual", "Manual"

    po_number = models.CharField(max_length=40, unique=True, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="purchase_orders")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="purchase_orders")
    project_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    source_indent = models.ForeignKey(
        "indents.Indent", null=True, blank=True, on_delete=models.SET_NULL, related_name="purchase_orders"
    )
    delivery_complete = models.BooleanField(
        default=False, editable=False,
        help_text="All lines fully received per GRNs; set by stores app, not by PO status alone.",
    )

    payment_terms_days = models.PositiveIntegerField(default=30)
    delivery_terms = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    gst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_via = models.CharField(max_length=10, choices=SentVia.choices, blank=True)

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True)

    revision_number = models.PositiveIntegerField(default=0)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.po_number or f"(draft PO #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.po_number:
            self.po_number = generate_po_number(PurchaseOrder, self.site, timezone.localdate())
        super().save(*args, **kwargs)

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
        self.grand_total = subtotal + gst_amount
        if save:
            PurchaseOrder.objects.filter(pk=self.pk).update(
                subtotal=self.subtotal, gst_amount=self.gst_amount, grand_total=self.grand_total
            )

    # --- status machine -----------------------------------------------
    # Transitions are the only way status may change; views must never
    # assign `.status` directly.

    def submit_for_approval(self, user):
        if self.status != self.Status.DRAFT:
            raise InvalidStatusTransition(f"Cannot submit a PO in '{self.status}' status.")
        if not self.lines.exists():
            raise InvalidStatusTransition("Cannot submit a PO with no line items.")
        self.status = self.Status.PENDING_APPROVAL
        self.save(update_fields=["status", "updated_at"])
        self.approval_actions.create(action=POApprovalAction.Action.SUBMITTED, actor=user)

    def approve(self, user, comment=""):
        if self.status != self.Status.PENDING_APPROVAL:
            raise InvalidStatusTransition(f"Cannot approve a PO in '{self.status}' status.")
        self.status = self.Status.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        self.approval_actions.create(action=POApprovalAction.Action.APPROVED, actor=user, comment=comment)

        for line in self.lines.select_related("indent_line"):
            if line.indent_line_id:
                # Delta-based: an amend()+re-approve cycle must not double-count
                # qty_ordered on the indent line if it's approved more than once.
                delta = line.quantity - line.indent_qty_recorded
                if delta != 0:
                    line.indent_line.record_conversion(delta)
                    line.indent_qty_recorded = line.quantity
                    line.save(update_fields=["indent_qty_recorded", "updated_at"])

    def reject(self, user, comment=""):
        if self.status != self.Status.PENDING_APPROVAL:
            raise InvalidStatusTransition(f"Cannot reject a PO in '{self.status}' status.")
        self.status = self.Status.DRAFT
        self.save(update_fields=["status", "updated_at"])
        self.approval_actions.create(action=POApprovalAction.Action.REJECTED, actor=user, comment=comment)

    def mark_sent(self, user, channel):
        if self.status != self.Status.APPROVED:
            raise InvalidStatusTransition(f"Cannot mark a PO in '{self.status}' status as sent.")
        if channel not in self.SentVia.values:
            raise InvalidStatusTransition(f"Unknown send channel '{channel}'.")
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.sent_via = channel
        self.save(update_fields=["status", "sent_at", "sent_via", "updated_at"])

    def close(self, user):
        if self.status not in (self.Status.SENT, self.Status.PARTIALLY_DELIVERED):
            raise InvalidStatusTransition(f"Cannot close a PO in '{self.status}' status.")
        self.status = self.Status.CLOSED
        self.save(update_fields=["status", "updated_at"])

    def cancel(self, user, reason):
        if self.status in (self.Status.CLOSED, self.Status.CANCELLED):
            raise InvalidStatusTransition(f"Cannot cancel a PO in '{self.status}' status.")
        if not reason:
            raise InvalidStatusTransition("A cancellation reason is required.")
        self.status = self.Status.CANCELLED
        self.cancelled_by = user
        self.cancelled_at = timezone.now()
        self.cancelled_reason = reason
        self.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancelled_reason", "updated_at"])

    def refresh_delivery_status(self):
        """Called by the stores app after a GRN is submitted. Moves SENT ->
        PARTIALLY_DELIVERED on first receipt and flags delivery_complete once
        every line is fully received — the PO itself still only closes via
        `close()` (post-billing in Phase 3), per docs/PHASE2_INDENTS_GRN.md."""
        lines = list(self.lines.all())
        total_ordered = sum((line.quantity for line in lines), Decimal("0"))
        total_received = sum((line.qty_received for line in lines), Decimal("0"))
        if total_received <= 0:
            return
        update_fields = []
        if self.status == self.Status.SENT:
            self.status = self.Status.PARTIALLY_DELIVERED
            update_fields.append("status")
        complete = total_received >= total_ordered
        if complete != self.delivery_complete:
            self.delivery_complete = complete
            update_fields.append("delivery_complete")
        if update_fields:
            update_fields.append("updated_at")
            self.save(update_fields=update_fields)

    def amend(self, user):
        """Reopen an approved/sent PO for editing as a new revision. Lines
        stay editable again and re-approval + re-send are required before
        it can go out again."""
        if self.status not in (self.Status.APPROVED, self.Status.SENT, self.Status.PARTIALLY_DELIVERED):
            raise InvalidStatusTransition(f"Cannot amend a PO in '{self.status}' status.")
        self.status = self.Status.DRAFT
        self.revision_number += 1
        self.approved_by = None
        self.approved_at = None
        self.sent_at = None
        self.sent_via = ""
        self.save(update_fields=[
            "status", "revision_number", "approved_by", "approved_at", "sent_at", "sent_via", "updated_at",
        ])
        self.approval_actions.create(action=POApprovalAction.Action.AMENDED, actor=user)


class PurchaseOrderLine(TimeStampedModel):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    description_override = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=4, choices=Item.Unit.choices, blank=True)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, editable=False, default=Decimal("0.00"))
    deviates_from_contract = models.BooleanField(default=False, editable=False)

    indent_line = models.ForeignKey(
        "indents.IndentLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="po_lines"
    )
    indent_qty_recorded = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0.000"), editable=False,
        help_text="How much of this line's quantity has already been rolled up onto the indent line.",
    )
    qty_received = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0.000"), editable=False,
        help_text="Cumulative accepted quantity across GRNs (stores app).",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item.name} x {self.quantity}"

    @property
    def description(self):
        return self.description_override or self.item.name

    @property
    def pending_quantity(self):
        return self.quantity - self.qty_received

    def save(self, *args, **kwargs):
        if not self.unit:
            self.unit = self.item.unit
        if self.gst_rate is None:
            self.gst_rate = self.item.gst_rate
        self.line_total = (self.quantity * self.rate).quantize(Decimal("0.01"))

        contract_rate = RateContract.current_rate(self.po.vendor, self.item)
        self.deviates_from_contract = contract_rate is not None and contract_rate != self.rate

        super().save(*args, **kwargs)
        self.po.recompute_totals()

    def delete(self, *args, **kwargs):
        po = self.po
        super().delete(*args, **kwargs)
        po.recompute_totals()


class POApprovalAction(TimeStampedModel):
    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        AMENDED = "amended", "Amended"

    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="approval_actions")
    action = models.CharField(max_length=10, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.po.po_number} {self.action} by {self.actor}"


class ApprovalRule(TimeStampedModel):
    class DocType(models.TextChoices):
        PO = "po", "Purchase Order"
        INDENT = "indent", "Indent"
        BILL = "bill", "Vendor Bill"

    doc_type = models.CharField(max_length=10, choices=DocType.choices, default=DocType.PO)
    min_amount = models.DecimalField(max_digits=14, decimal_places=2)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Leave blank for no cap.")
    approver_role = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="approval_rules")

    history = HistoricalRecords()

    class Meta:
        ordering = ["doc_type", "min_amount"]

    def __str__(self):
        upper = f"{self.max_amount}" if self.max_amount is not None else "and above"
        return f"[{self.get_doc_type_display()}] ₹{self.min_amount}–{upper}: {self.approver_role.name}"

    def clean(self):
        if self.max_amount is not None and self.max_amount < self.min_amount:
            raise ValidationError({"max_amount": "Max amount must be greater than or equal to min amount."})

    @classmethod
    def approver_group_for_amount(cls, amount, doc_type=DocType.PO):
        rule = (
            cls.objects.filter(doc_type=doc_type, min_amount__lte=amount)
            .filter(models.Q(max_amount__isnull=True) | models.Q(max_amount__gte=amount))
            .order_by("min_amount")
            .first()
        )
        return rule.approver_role if rule else None

    @classmethod
    def can_user_approve(cls, user, amount, doc_type=DocType.PO):
        if user.is_superuser or user.groups.filter(name="SCM Head").exists():
            return True
        group = cls.approver_group_for_amount(amount, doc_type=doc_type)
        return bool(group and user.groups.filter(pk=group.pk).exists())


class POAttachment(TimeStampedModel):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="po_attachments/%Y/%m/")
    label = models.CharField(max_length=255, help_text='e.g. "Vendor quotation"')

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.label} ({self.po.po_number})"
