from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from masters.models import ItemCategory, Site

ROLE_GROUPS = [
    "SCM Head",
    "Purchase Officer (HO)",
    "Site Member",
    "Accounts",
    "Admin",
]

DEMO_CATEGORIES = [
    "Cement",
    "Steel",
    "Aggregates",
    "Admixtures",
    "Hardware",
    "Machinery Hire",
    "Transport",
]


class Command(BaseCommand):
    help = "Seed demo/reference data: role groups, a demo site, and standard item categories (idempotent)."

    def handle(self, *args, **options):
        for name in ROLE_GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created group: {name}"))
            else:
                self.stdout.write(f"Group already exists: {name}")

        for name in DEMO_CATEGORIES:
            category, created = ItemCategory.objects.get_or_create(name=name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created category: {name}"))
            else:
                self.stdout.write(f"Category already exists: {name}")

        site, created = Site.objects.get_or_create(
            code="HYD-F1",
            defaults={"name": "Hyderabad Factory 1", "is_factory": True},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created demo site: {site.code}"))
        else:
            self.stdout.write(f"Demo site already exists: {site.code}")

        self.stdout.write(self.style.SUCCESS("seed_demo complete."))
