from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from accounts_stub.notifications import notify
from assets.models import MaintenanceSchedule


class Command(BaseCommand):
    help = (
        "Nightly check: flags machines with maintenance due/overdue "
        "(MaintenanceSchedule.is_due) and notifies SCM Head."
    )

    def handle(self, *args, **options):
        due = [s for s in MaintenanceSchedule.objects.select_related("machine") if s.is_due]
        if not due:
            self.stdout.write(self.style.SUCCESS("check_maintenance_due: nothing due."))
            return

        scm_head_group = Group.objects.filter(name="SCM Head").first()
        if scm_head_group:
            body_lines = [f"- {s.machine.code} ({s.machine.name})" for s in due]
            body = "Machines due for maintenance:\n" + "\n".join(body_lines)
            for user in scm_head_group.user_set.filter(is_active=True):
                notify(user, f"{len(due)} machine(s) due for maintenance", body)

        self.stdout.write(self.style.SUCCESS(f"check_maintenance_due: {len(due)} machine(s) flagged."))
