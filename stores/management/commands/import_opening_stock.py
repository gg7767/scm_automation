import csv
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from masters.models import Item, Site
from stores.models import StockLedger, write_stock_ledger_entry

REQUIRED_COLUMNS = {"site_code", "item", "qty"}


class Command(BaseCommand):
    help = (
        "Import per-site opening stock balances from a CSV export, as of go-live. "
        "Idempotent: a (site, item) pair that already has an 'opening' StockLedger "
        "entry is skipped, so re-running the same file never double-counts.\n\n"
        "CSV columns: site_code (required, matches Site.code), item (required, "
        "matches Item.code or Item.name), qty (required), rate (optional), "
        "as_of_date (optional, YYYY-MM-DD, defaults to today)."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **options):
        path = options["csv_path"]
        try:
            f = open(path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Could not open {path}: {exc}")

        created, skipped, errors = 0, 0, 0
        with f:
            reader = csv.DictReader(f)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"{path} is missing required column(s): {', '.join(sorted(missing))}")

            for line_no, row in enumerate(reader, start=2):
                try:
                    result = self._import_row(row)
                    if result:
                        created += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    errors += 1
                    self.stderr.write(self.style.ERROR(f"line {line_no}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Opening stock: {created} created, {skipped} skipped (already imported), {errors} errors."))

    def _import_row(self, row):
        site_code = row["site_code"].strip()
        site = Site.objects.filter(code=site_code).first()
        if not site:
            raise ValueError(f"unknown site_code '{site_code}'")

        item_ref = row["item"].strip()
        item = Item.objects.filter(code=item_ref).first() or Item.objects.filter(name__iexact=item_ref).first()
        if not item:
            raise ValueError(f"unknown item '{item_ref}'")

        if StockLedger.objects.filter(site=site, item=item, txn_type=StockLedger.TxnType.OPENING).exists():
            return False

        try:
            qty = Decimal(row["qty"].strip())
        except InvalidOperation:
            raise ValueError(f"invalid qty '{row['qty']}'")

        rate = None
        if (row.get("rate") or "").strip():
            try:
                rate = Decimal(row["rate"].strip())
            except InvalidOperation:
                raise ValueError(f"invalid rate '{row['rate']}'")

        as_of_date = (row.get("as_of_date") or "").strip()
        import datetime
        txn_date = datetime.date.fromisoformat(as_of_date) if as_of_date else datetime.date.today()

        write_stock_ledger_entry(
            site=site, item=item, txn_date=txn_date, txn_type=StockLedger.TxnType.OPENING,
            qty=qty, rate=rate, remarks="Opening balance import",
        )
        return True
