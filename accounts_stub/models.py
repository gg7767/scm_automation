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
