from django.conf import settings
from django.db import models

from masters.models import Site, TimeStampedModel


class UserProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Restricts Site Members to this site's data.",
    )

    def __str__(self):
        return f"{self.user.username} profile"


class Notification(TimeStampedModel):
    """A logged alert to a user (urgent indents, maintenance/min-stock
    alerts, ...). `channel` is kept separate from delivery so WhatsApp/SMS
    can be wired in later without a schema change — only 'email' actually
    sends today; other channels are logged only."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    subject = models.CharField(max_length=255)
    body = models.TextField()
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.EMAIL)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} -> {self.recipient}"
