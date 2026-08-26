from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from stores.models import StockBalance, StockLedger


class Command(BaseCommand):
    help = (
        "Nightly reconciliation: recomputes each (site, item) balance from "
        "StockLedger (the source of truth) and corrects StockBalance (the "
        "cache) if it has drifted, reporting every correction made."
    )

    def handle(self, *args, **options):
        totals = (
            StockLedger.objects.values("site_id", "item_id")
            .annotate(total=Sum("qty"))
        )
        seen = set()
        drift_count = 0

        for row in totals:
            site_id, item_id, computed = row["site_id"], row["item_id"], row["total"] or Decimal("0")
            seen.add((site_id, item_id))
            balance, _ = StockBalance.objects.get_or_create(site_id=site_id, item_id=item_id)
            if balance.quantity != computed:
                self.stdout.write(self.style.WARNING(
                    f"Drift: site={site_id} item={item_id} cached={balance.quantity} ledger={computed} — corrected."
                ))
                balance.quantity = computed
                balance.save(update_fields=["quantity"])
                drift_count += 1

        # Any StockBalance row with no ledger activity at all should be zero.
        for balance in StockBalance.objects.exclude(quantity=0):
            if (balance.site_id, balance.item_id) not in seen:
                self.stdout.write(self.style.WARNING(
                    f"Drift: site={balance.site_id} item={balance.item_id} cached={balance.quantity} ledger=0.000 — corrected."
                ))
                balance.quantity = Decimal("0")
                balance.save(update_fields=["quantity"])
                drift_count += 1

        self.stdout.write(self.style.SUCCESS(f"reconcile_stock_balances: {drift_count} correction(s) made."))
