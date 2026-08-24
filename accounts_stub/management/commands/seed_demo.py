from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

ROLE_GROUPS = [
    "SCM Head",
    "Purchase Officer (HO)",
    "Site Member",
    "Accounts",
    "Admin",
]


class Command(BaseCommand):
    help = "Seed demo/reference data: role groups (idempotent)."

    def handle(self, *args, **options):
        for name in ROLE_GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created group: {name}"))
            else:
                self.stdout.write(f"Group already exists: {name}")

        self.stdout.write(self.style.SUCCESS("seed_demo complete."))
