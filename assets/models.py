from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords

from masters.models import Site, TimeStampedModel, Vendor, next_sequential_code


class Machine(TimeStampedModel):
    class Category(models.TextChoices):
        BATCHING = "batching", "Batching"
        CRANES = "cranes", "Cranes"
        MOULDS = "moulds", "Moulds"
        VEHICLES = "vehicles", "Vehicles"
        TOOLS = "tools", "Tools"
        OTHER = "other", "Other"

    class Ownership(models.TextChoices):
        OWNED = "owned", "Owned"
        HIRED = "hired", "Hired"

    class RateUnit(models.TextChoices):
        PER_HOUR = "per_hour", "Per hour"
        PER_DAY = "per_day", "Per day"
        PER_MONTH = "per_month", "Per month"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        UNDER_MAINTENANCE = "under_maintenance", "Under maintenance"
        IDLE = "idle", "Idle"
        DISPOSED = "disposed", "Disposed"

    code = models.CharField(max_length=20, unique=True, blank=True, help_text="Auto-generated, e.g. MC-0001")
    name = models.CharField(max_length=255, help_text='e.g. "Batching Plant 30cum", "Hydra Crane 14T"')
    category = models.CharField(max_length=20, choices=Category.choices)
    ownership = models.CharField(max_length=10, choices=Ownership.choices)

    hire_vendor = models.ForeignKey(
        Vendor, null=True, blank=True, on_delete=models.PROTECT, related_name="hired_machines",
    )
    hire_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rate_unit = models.CharField(max_length=10, choices=RateUnit.choices, blank=True)

    purchase_date = models.DateField(null=True, blank=True)
    purchase_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_sequential_code(Machine, "MC-", 4)
        super().save(*args, **kwargs)

    def clean(self):
        if self.ownership == self.Ownership.HIRED and not self.hire_vendor_id:
            raise ValidationError({"hire_vendor": "A hire vendor is required for hired machinery."})

    @property
    def current_deployment(self):
        return self.deployments.filter(to_date__isnull=True).first()


class MachineDeployment(TimeStampedModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="deployments")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="machine_deployments")
    from_date = models.DateField()
    to_date = models.DateField(null=True, blank=True, help_text="Leave blank — still at this site.")
    remarks = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-from_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["machine"], condition=models.Q(to_date__isnull=True),
                name="unique_open_deployment_per_machine",
            )
        ]

    def __str__(self):
        return f"{self.machine.code} @ {self.site.code} from {self.from_date}"

    def clean(self):
        if self.to_date and self.from_date and self.to_date < self.from_date:
            raise ValidationError({"to_date": "End date cannot be before start date."})

    def close(self, to_date=None):
        self.to_date = to_date or timezone.localdate()
        self.save(update_fields=["to_date", "updated_at"])


class MachineLog(TimeStampedModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="logs")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="machine_logs", blank=True)
    log_date = models.DateField(default=timezone.localdate)
    hours_run = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    km = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fuel_litres = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    operator_name = models.CharField(max_length=255, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-log_date"]

    def __str__(self):
        return f"{self.machine.code} log {self.log_date}"

    def save(self, *args, **kwargs):
        if not self.site_id:
            deployment = self.machine.current_deployment
            if not deployment:
                raise ValidationError(f"{self.machine.code} has no active site deployment — deploy it first.")
            self.site = deployment.site
        super().save(*args, **kwargs)


class MaintenanceSchedule(TimeStampedModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="maintenance_schedules")
    every_n_days = models.PositiveIntegerField(null=True, blank=True)
    every_n_hours = models.PositiveIntegerField(null=True, blank=True)
    last_done_date = models.DateField(null=True, blank=True)
    last_done_hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["machine"]

    def __str__(self):
        return f"{self.machine.code} maintenance schedule"

    def clean(self):
        if not self.every_n_days and not self.every_n_hours:
            raise ValidationError("Set at least one of every_n_days or every_n_hours.")

    @property
    def is_due(self):
        if self.every_n_days:
            if not self.last_done_date:
                return True
            if (timezone.localdate() - self.last_done_date).days >= self.every_n_days:
                return True
        if self.every_n_hours:
            logs = self.machine.logs.all()
            if self.last_done_date:
                logs = logs.filter(log_date__gt=self.last_done_date)
            hours_since = logs.aggregate(total=Sum("hours_run"))["total"] or Decimal("0")
            if hours_since >= self.every_n_hours:
                return True
        return False

    def mark_done(self, done_date=None, done_hours=None):
        self.last_done_date = done_date or timezone.localdate()
        if done_hours is not None:
            self.last_done_hours = done_hours
        self.save(update_fields=["last_done_date", "last_done_hours", "updated_at"])
