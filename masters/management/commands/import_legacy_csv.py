import csv
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from masters.models import Item, ItemAlias, ItemCategory, Vendor

VENDOR_REQUIRED_COLUMNS = {"name"}
ITEM_REQUIRED_COLUMNS = {"name", "category", "unit"}


class Command(BaseCommand):
    help = (
        "Import legacy vendors and/or items from CSV exports (e.g. from Excel). "
        "Idempotent: re-running does not create duplicates.\n\n"
        "vendors CSV columns: name (required), gstin, pan, address, state, "
        "contact_person, phone, email, bank_name, account_number, ifsc, "
        "payment_terms_days, tally_ledger_name. Matched by GSTIN if given, "
        "else by name (case-insensitive).\n\n"
        "items CSV columns: name (required), category (required), unit "
        "(required), gst_rate, hsn_code, alias_of. A row with alias_of set "
        "registers `name` as an ItemAlias pointing at the existing "
        "canonical item named `alias_of`, instead of creating a new item. "
        "Canonical items are matched by name (case-insensitive) or by an "
        "existing alias."
    )

    def add_arguments(self, parser):
        parser.add_argument("--vendors", dest="vendors_csv", help="Path to a vendors CSV file.")
        parser.add_argument("--items", dest="items_csv", help="Path to an items CSV file.")

    def handle(self, *args, **options):
        vendors_csv = options.get("vendors_csv")
        items_csv = options.get("items_csv")
        if not vendors_csv and not items_csv:
            raise CommandError("Pass at least one of --vendors or --items.")

        if vendors_csv:
            self.import_vendors(vendors_csv)
        if items_csv:
            self.import_items(items_csv)

    # --- vendors ------------------------------------------------------

    def import_vendors(self, path):
        rows = self._read_csv(path, VENDOR_REQUIRED_COLUMNS)
        created, updated, errors = 0, 0, 0

        for line_no, row in rows:
            try:
                with transaction.atomic():
                    is_new = self._import_vendor_row(row)
                created += is_new
                updated += not is_new
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(f"vendors.csv line {line_no}: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"Vendors: {created} created, {updated} updated, {errors} errors."
        ))

    def _import_vendor_row(self, row):
        name = row["name"].strip()
        if not name:
            raise ValueError("blank vendor name")
        gstin = (row.get("gstin") or "").strip().upper()

        lookup = {"gstin": gstin} if gstin else {"name__iexact": name}
        defaults = {
            "name": name,
            "gstin": gstin,
            "pan": (row.get("pan") or "").strip().upper(),
            "address": (row.get("address") or "").strip(),
            "state": (row.get("state") or "").strip().upper(),
            "contact_person": (row.get("contact_person") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "email": (row.get("email") or "").strip(),
            "bank_name": (row.get("bank_name") or "").strip(),
            "account_number": (row.get("account_number") or "").strip(),
            "ifsc": (row.get("ifsc") or "").strip().upper(),
            "tally_ledger_name": (row.get("tally_ledger_name") or "").strip(),
        }
        terms = (row.get("payment_terms_days") or "").strip()
        if terms:
            defaults["payment_terms_days"] = int(terms)

        vendor, created = Vendor.objects.update_or_create(defaults=defaults, **lookup)
        vendor.full_clean(exclude=["code"])
        vendor.save()
        return created

    # --- items ----------------------------------------------------------

    def import_items(self, path):
        rows = self._read_csv(path, ITEM_REQUIRED_COLUMNS)
        created, updated, aliased, errors = 0, 0, 0, 0

        for line_no, row in rows:
            try:
                with transaction.atomic():
                    result = self._import_item_row(row)
                if result == "created":
                    created += 1
                elif result == "updated":
                    updated += 1
                elif result == "aliased":
                    aliased += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(f"items.csv line {line_no}: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"Items: {created} created, {updated} updated, {aliased} aliases linked, {errors} errors."
        ))

    def _resolve_item(self, name):
        item = Item.objects.filter(name__iexact=name).first()
        if item:
            return item
        alias = ItemAlias.objects.filter(alias_name__iexact=name).select_related("item").first()
        return alias.item if alias else None

    def _import_item_row(self, row):
        name = row["name"].strip()
        if not name:
            raise ValueError("blank item name")
        alias_of = (row.get("alias_of") or "").strip()

        if alias_of:
            canonical = self._resolve_item(alias_of)
            if not canonical:
                raise ValueError(f"alias_of '{alias_of}' does not match any known item")
            _, created = ItemAlias.objects.get_or_create(item=canonical, alias_name=name)
            return "aliased" if created else None

        category_name = row["category"].strip()
        if not category_name:
            raise ValueError("blank category")
        category, _ = ItemCategory.objects.get_or_create(name=category_name)

        unit = row["unit"].strip().upper()
        if unit not in Item.Unit.values:
            raise ValueError(f"unknown unit '{unit}' (expected one of {', '.join(Item.Unit.values)})")

        defaults = {"category": category, "unit": unit, "hsn_code": (row.get("hsn_code") or "").strip()}
        gst_rate = (row.get("gst_rate") or "").strip()
        if gst_rate:
            try:
                defaults["gst_rate"] = Decimal(gst_rate)
            except InvalidOperation:
                raise ValueError(f"invalid gst_rate '{gst_rate}'")

        existing = self._resolve_item(name)
        if existing:
            for field, value in defaults.items():
                setattr(existing, field, value)
            existing.full_clean(exclude=["code"])
            existing.save()
            return "updated"

        item = Item(name=name, **defaults)
        item.full_clean(exclude=["code"])
        item.save()
        return "created"

    # --- shared -----------------------------------------------------------

    def _read_csv(self, path, required_columns):
        try:
            f = open(path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Could not open {path}: {exc}")

        with f:
            reader = csv.DictReader(f)
            missing = required_columns - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"{path} is missing required column(s): {', '.join(sorted(missing))}")
            return [(i, row) for i, row in enumerate(reader, start=2)]
