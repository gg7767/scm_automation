from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from accounts_stub.roles import ALL_ROLES as ROLE_GROUPS
from accounts_stub.roles import PURCHASE_OFFICER, SCM_HEAD
from masters.models import ItemCategory, Site

DEMO_CATEGORIES = [
    "Cement",
    "Steel",
    "Aggregates",
    "Admixtures",
    "Hardware",
    "Machinery Hire",
    "Transport",
]

# (min_amount, max_amount, approver_role) — max_amount=None means no cap.
DEFAULT_APPROVAL_RULES = [
    (Decimal("0.00"), Decimal("200000.00"), PURCHASE_OFFICER),
    (Decimal("200000.01"), None, SCM_HEAD),
]


class Command(BaseCommand):
    help = "Seed demo/reference data: role groups, a demo site, standard item categories, and default approval rules (idempotent)."

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

        # Import here: purchase app depends on masters, avoid a hard
        # top-level dependency from accounts_stub on purchase.
        from purchase.models import ApprovalRule

        for min_amount, max_amount, role_name in DEFAULT_APPROVAL_RULES:
            role = Group.objects.get(name=role_name)
            rule, created = ApprovalRule.objects.get_or_create(
                min_amount=min_amount, max_amount=max_amount, defaults={"approver_role": role}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created approval rule: {rule}"))
            else:
                self.stdout.write(f"Approval rule already exists: {rule}")

        self.stdout.write(self.style.SUCCESS("seed_demo complete."))
