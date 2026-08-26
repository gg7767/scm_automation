from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.models import Site, TimeStampedModel, Vendor
from masters.numbering import generate_document_number
from purchase.models import PurchaseOrder
from stores.models import GRN


class TransportTrip(TimeStampedModel):
    class BillableTo(models.TextChoices):
        COMPANY = "company", "Company"
        VENDOR = "vendor", "Vendor"
        CLIENT = "client", "Client"

    trip_number = models.CharField(max_length=40, unique=True, blank=True)
    trip_date = models.DateField(default=timezone.localdate)
    vehicle_number = models.CharField(max_length=30)
    transporter = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name="transport_trips",
        help_text="Vendor tagged with the Transport category.",
    )
    driver_name = models.CharField(max_length=255, blank=True)
    driver_phone = models.CharField(max_length=15, blank=True)

    from_location = models.CharField(max_length=255, help_text="Free text, or a site name for factory dispatches.")
    to_site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="inbound_trips")

    linked_po = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="trips",
        help_text="For inward material trips.",
    )
    linked_grn = models.ForeignKey(
        GRN, null=True, blank=True, on_delete=models.SET_NULL, related_name="trips",
    )
    element_description = models.CharField(
        max_length=255, blank=True, help_text="Free description for precast element deliveries (no PO/GRN link).",
    )

    freight_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    billable_to = models.CharField(max_length=10, choices=BillableTo.choices, default=BillableTo.COMPANY)
    remarks = models.TextField(blank=True)
    pod_photo = models.ImageField(upload_to="trip_pods/%Y/%m/", blank=True, null=True, help_text="Proof of delivery.")

    history = HistoricalRecords()

    class Meta:
        ordering = ["-trip_date", "-created_at"]

    def __str__(self):
        return self.trip_number or f"(draft trip #{self.pk})"

    def save(self, *args, **kwargs):
        if not self.trip_number:
            self.trip_number = generate_document_number(TransportTrip, "trip_number", "TRP", self.to_site.code, self.trip_date)
        super().save(*args, **kwargs)

    def clean(self):
        if self.linked_po_id and self.linked_grn_id and self.linked_grn.po_id != self.linked_po_id:
            raise ValidationError({"linked_grn": "This GRN does not belong to the linked PO."})
