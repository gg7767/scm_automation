from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.models import Item, Site, TimeStampedModel
from masters.numbering import generate_document_number


class InvalidStatusTransition(Exception):
    pass


class Indent(TimeStampedModel):
    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        PARTIALLY_ORDERED = "partially_ordered", "Partially ordered"
        ORDERED = "ordered", "Ordered"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    indent_number = models.CharField(max_length=40, unique=True, blank=True)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="indents")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    project_name = models.CharField(max_length=255, blank=True)
    required_by_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.indent_number or f"(draft indent #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.indent_number:
            self.indent_number = generate_document_number(Indent, "indent_number", "IND", self.site.code, timezone.localdate())
        super().save(*args, **kwargs)

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def estimated_value(self):
        return sum((line.estimated_value() for line in self.lines.all()), Decimal("0.00"))

    # --- status machine -----------------------------------------------

    def submit_for_approval(self, user):
        if self.status != self.Status.DRAFT:
            raise InvalidStatusTransition(f"Cannot submit an indent in '{self.status}' status.")
        if not self.lines.exists():
            raise InvalidStatusTransition("Cannot submit an indent with no line items.")
        self.status = self.Status.PENDING_APPROVAL
        self.save(update_fields=["status", "updated_at"])
        self.approval_actions.create(action=IndentApprovalAction.Action.SUBMITTED, actor=user)

        if self.priority == self.Priority.URGENT:
            from indents.services import notify_urgent_indent
            notify_urgent_indent(self)

    def approve(self, user, comment=""):
        if self.status != self.Status.PENDING_APPROVAL:
            raise InvalidStatusTransition(f"Cannot approve an indent in '{self.status}' status.")
        self.status = self.Status.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        self.approval_actions.create(action=IndentApprovalAction.Action.APPROVED, actor=user, comment=comment)

    def reject(self, user, reason):
        if self.status != self.Status.PENDING_APPROVAL:
            raise InvalidStatusTransition(f"Cannot reject an indent in '{self.status}' status.")
        if not reason:
            raise InvalidStatusTransition("A rejection reason is required.")
        self.status = self.Status.REJECTED
        self.rejection_reason = reason
        self.save(update_fields=["status", "rejection_reason", "updated_at"])
        self.approval_actions.create(action=IndentApprovalAction.Action.REJECTED, actor=user, comment=reason)

    def cancel(self, user, reason):
        if self.status in (self.Status.ORDERED, self.Status.REJECTED, self.Status.CANCELLED):
            raise InvalidStatusTransition(f"Cannot cancel an indent in '{self.status}' status.")
        if not reason:
            raise InvalidStatusTransition("A cancellation reason is required.")
        self.status = self.Status.CANCELLED
        self.cancelled_by = user
        self.cancelled_at = timezone.now()
        self.cancelled_reason = reason
        self.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancelled_reason", "updated_at"])
        self.approval_actions.create(action=IndentApprovalAction.Action.CANCELLED, actor=user, comment=reason)

    def recompute_rollup_status(self):
        """Recompute partially_ordered / ordered from line resolution.
        Only meaningful once the indent has been approved."""
        if self.status not in (self.Status.APPROVED, self.Status.PARTIALLY_ORDERED):
            return
        lines = list(self.lines.all())
        if not lines:
            return
        resolved = [line.is_resolved for line in lines]
        any_progress = any(line.is_rejected or line.qty_ordered > 0 for line in lines)
        if all(resolved):
            new_status = self.Status.ORDERED
        elif any_progress:
            new_status = self.Status.PARTIALLY_ORDERED
        else:
            new_status = self.Status.APPROVED
        if new_status != self.status:
            self.status = new_status
            self.save(update_fields=["status", "updated_at"])


class IndentLine(TimeStampedModel):
    indent = models.ForeignKey(Indent, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=4, choices=Item.Unit.choices, blank=True)
    present_stock = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"), null=True, blank=True)
    required_by_date = models.DateField(null=True, blank=True)
    purpose = models.CharField(max_length=255, blank=True)

    qty_ordered = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))
    is_rejected = models.BooleanField(default=False)
    rejection_reason = models.CharField(max_length=255, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item.name} x {self.quantity}"

    def save(self, *args, **kwargs):
        if not self.unit:
            self.unit = self.item.unit
        super().save(*args, **kwargs)

    @property
    def is_resolved(self):
        return self.is_rejected or self.qty_ordered >= self.quantity

    @property
    def remaining_quantity(self):
        return max(self.quantity - self.qty_ordered, Decimal("0.000"))

    @property
    def line_status(self):
        if self.is_rejected:
            return "rejected"
        if self.qty_ordered >= self.quantity:
            return "ordered"
        if self.qty_ordered > 0:
            return "partially_ordered"
        return "open"

    def estimated_value(self):
        from indents.services import estimated_unit_rate
        return (self.quantity * estimated_unit_rate(self.item)).quantize(Decimal("0.01"))

    def reject_remaining(self, user, reason):
        if self.indent.status not in (Indent.Status.APPROVED, Indent.Status.PARTIALLY_ORDERED):
            raise InvalidStatusTransition("Can only reject lines on an approved indent.")
        if not reason:
            raise InvalidStatusTransition("A rejection reason is required.")
        self.is_rejected = True
        self.rejection_reason = reason
        self.save(update_fields=["is_rejected", "rejection_reason", "updated_at"])
        self.indent.recompute_rollup_status()

    def record_conversion(self, qty):
        """Increment qty_ordered when a PO line is created against this
        indent line, then roll up the parent indent's status."""
        self.qty_ordered = self.qty_ordered + qty
        self.save(update_fields=["qty_ordered", "updated_at"])
        self.indent.recompute_rollup_status()


class IndentApprovalAction(TimeStampedModel):
    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    indent = models.ForeignKey(Indent, on_delete=models.CASCADE, related_name="approval_actions")
    action = models.CharField(max_length=10, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.indent.indent_number} {self.action} by {self.actor}"
