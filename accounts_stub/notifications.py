from django.core.mail import send_mail
from django.utils import timezone

from accounts_stub.models import Notification


def notify(recipient, subject, body, channel=Notification.Channel.EMAIL):
    """Log a Notification and, for email, actually send it. Delivery
    failures are logged on the record but never raise — a broken mail
    server must not block the workflow that triggered the notification."""
    notification = Notification.objects.create(
        recipient=recipient, subject=subject, body=body, channel=channel,
    )
    if channel == Notification.Channel.EMAIL and recipient.email:
        try:
            send_mail(subject, body, None, [recipient.email], fail_silently=False)
            notification.sent_at = timezone.now()
            notification.save(update_fields=["sent_at"])
        except Exception:
            pass
    return notification
