from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from simple_history.models import HistoricalRecords

from masters.constants import INDIAN_STATES
from masters.validators import gstin_validator, ifsc_validator, pan_validator


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


def next_sequential_code(model, prefix, width):
    """Return the next `{prefix}{n:0{width}}` code for `model`, based on the
    highest existing numeric suffix among codes with that prefix."""
    with transaction.atomic():
        last = (
            model.objects.select_for_update()
            .filter(code__startswith=prefix)
            .order_by("-code")
            .first()
        )
        next_num = 1
        if last:
            suffix = last.code[len(prefix):]
            if suffix.isdigit():
                next_num = int(suffix) + 1
        return f"{prefix}{next_num:0{width}d}"


class Site(TimeStampedModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20, unique=True, help_text='Short unique code, e.g. "HYD-F1"')
    address = models.TextField(blank=True)
    is_factory = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class ItemCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "item categories"

    def __str__(self):
        return self.name

    def clean(self):
        if self.parent_id and self.parent.parent_id:
            raise ValidationError({"parent": "Only one level of category nesting is allowed."})
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "A category cannot be its own parent."})


class Item(TimeStampedModel):
    class Unit(models.TextChoices):
        BAG = "BAG", "Bag"
        KG = "KG", "Kilogram"
        MT = "MT", "Metric Tonne"
        NOS = "NOS", "Numbers"
        CUM = "CUM", "Cubic Metre"
        SQM = "SQM", "Square Metre"
        LTR = "LTR", "Litre"
        TRIP = "TRIP", "Trip"
        HOUR = "HOUR", "Hour"
        DAY = "DAY", "Day"
        SET = "SET", "Set"

    code = models.CharField(max_length=20, unique=True, blank=True, help_text="Auto-generated, e.g. ITM-00001")
    name = models.CharField(max_length=255, unique=True)
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, related_name="items")
    unit = models.CharField(max_length=4, choices=Unit.choices)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    hsn_code = models.CharField(max_length=20, blank=True)
    active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_sequential_code(Item, "ITM-", 5)
        super().save(*args, **kwargs)


class ItemAlias(TimeStampedModel):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="aliases")
    alias_name = models.CharField(max_length=255, unique=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["alias_name"]
        verbose_name_plural = "item aliases"

    def __str__(self):
        return f"{self.alias_name} → {self.item.name}"


class Vendor(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        BLACKLISTED = "blacklisted", "Blacklisted"

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20, unique=True, blank=True, help_text="Auto-generated, e.g. VEN-0001")
    gstin = models.CharField(
        max_length=15, blank=True, validators=[gstin_validator],
        help_text="Leave blank for unregistered vendors.",
    )
    pan = models.CharField(max_length=10, blank=True, validators=[pan_validator])
    address = models.TextField(blank=True)
    state = models.CharField(max_length=2, choices=INDIAN_STATES, blank=True, help_text="GST place of supply.")
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)

    bank_name = models.CharField(max_length=255, blank=True)
    account_number = models.CharField(max_length=30, blank=True)
    ifsc = models.CharField(max_length=11, blank=True, validators=[ifsc_validator])

    payment_terms_days = models.PositiveIntegerField(default=30)
    categories = models.ManyToManyField(ItemCategory, blank=True, related_name="vendors")
    tally_ledger_name = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_sequential_code(Vendor, "VEN-", 4)
        super().save(*args, **kwargs)


class VendorDocument(TimeStampedModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to="vendor_documents/%Y/%m/")
    label = models.CharField(max_length=255, help_text='e.g. "Vendor quotation", "GST certificate"')

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.label} ({self.vendor.code})"
