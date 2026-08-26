# Deployment notes

This system has only run in local dev so far (this file is written from that
position — treat every step below as a plan to execute and verify, not a
report of something already done in production).

## Server setup

- **App server**: gunicorn behind nginx, or a platform that runs
  `gunicorn config.wsgi` directly (Railway/Render/etc.).
  ```
  gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
  ```
- **Database**: managed PostgreSQL 16. Run migrations on every deploy:
  ```
  python manage.py migrate
  python manage.py seed_demo   # idempotent — roles/categories/demo site
  ```
  Set real `ApprovalRule` thresholds via the admin after `seed_demo` —
  it only seeds a placeholder PO/indent rule for dev.
- **Static files**: `python manage.py collectstatic --noinput`, served by
  nginx or whitenoise. Tailwind/HTMX are CDN-loaded (no JS build step).
- **Media/uploads**: switch `MEDIA_ROOT`-based `FileField`/`ImageField`
  storage (vendor documents, PO/bill attachments, challan/POD photos) to
  an S3-compatible bucket via `django-storages` in production — local
  disk does not survive redeploys on most PaaS hosts. Set
  `DEFAULT_FILE_STORAGE` / `STORAGES["default"]` accordingly; no model
  code needs to change (all uploads already go through Django's
  storage API).
- **WeasyPrint** (PO PDF generation): the Linux deploy target needs
  `libpango-1.0-0`, `libpangocairo-1.0-0`, `libgdk-pixbuf2.0-0`, `libffi`
  (Debian/Ubuntu package names) installed at the OS level. The
  `DYLD_LIBRARY_PATH` workaround in `config/settings.py` is guarded by
  `platform.system() == "Darwin"` and is a no-op on Linux — nothing to
  change there.

## Environment variables

All required env vars are read via `django-environ` in
`config/settings.py`; see `.env.example` for the full list. Never commit
`.env`. At minimum, production needs:

- `DJANGO_SECRET_KEY` — a real random value (`django.core.management.utils.get_random_secret_key()`)
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS` — the real domain(s)
- `DATABASE_URL` — the managed Postgres connection string
- `COMPANY_NAME` / `COMPANY_ADDRESS` / `COMPANY_GSTIN` / `COMPANY_STATE` — real letterhead details for PO PDFs
- `EMAIL_BACKEND` / `EMAIL_HOST` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` — real SMTP (defaults to the console backend, which only prints to stdout — fine in dev, useless in production)
- Business-policy tuning, all optional with sensible defaults:
  `GRN_OVER_RECEIPT_TOLERANCE_PERCENT` (2.0), `BILL_MATCH_QTY_TOLERANCE_PERCENT` (0.0),
  `BILL_MATCH_RATE_TOLERANCE_PERCENT` (0.0), `BILL_MATCH_TOTAL_TOLERANCE_RUPEES` (1.0),
  `ALLOW_NEGATIVE_STOCK` (False)

## Tally connectivity

**Not built** — this was an explicit, deliberate scope decision (see
`docs/DECISIONS.md`, Phase 3 section): no XML/CSV export or push code
exists. `Vendor.tally_ledger_name` is the only field in place for a future
integration.

If/when it's built, the phase doc's design (Tally's HTTP XML gateway,
push-only, `TallySyncLog` queue with retry) will need one of:

- **Static IP / port-forward** to the office desktop running Tally with
  its XML gateway enabled (Gateway of Tally → F12 → Advanced Configuration),
  reachable from wherever this app is deployed.
- **ngrok** (or similar tunnel) from the Tally desktop outward, if the
  office network can't accept inbound connections — simpler to stand up,
  but the tunnel URL must be kept current in this app's config and the
  tunnel process must stay running.
- **Fallback**: a CSV/XML file export for manual import into Tally, for
  whenever the gateway is unreachable — this doesn't need any network
  path at all, just a scheduled export + someone picking up the file.

## Cron jobs

All nightly commands are plain Django management commands — wire them up
with system cron, a platform's scheduled-jobs feature, or Celery beat if
one gets introduced later. None of them have been run on a real schedule
yet; run each manually first and read its output before trusting it
unattended.

```cron
# Stock balance cache reconciliation (source of truth: StockLedger)
0 1 * * * cd /path/to/app && ./venv/bin/python manage.py reconcile_stock_balances

# Min-stock check -> drafts (never submits) a replenishment indent per site
0 2 * * * cd /path/to/app && ./venv/bin/python manage.py check_min_stock

# Maintenance due/overdue -> notifies SCM Head
0 3 * * * cd /path/to/app && ./venv/bin/python manage.py check_maintenance_due

# Monthly SCM pack (spend/lead-time/aging/stock/freight/machinery workbook) -> emails SCM Head
0 6 1 * * cd /path/to/app && ./venv/bin/python manage.py send_monthly_scm_pack
```

## Backups

**Not yet performed in this environment** — the commands below are the
plan, not a completed drill. Before going live, actually run a restore
from a fresh backup onto a scratch database and confirm the app boots
against it; a backup that has never been restored is unverified.

```bash
# Nightly dump (adjust connection details / retention to your setup)
pg_dump "$DATABASE_URL" -Fc -f "backup_$(date +%Y%m%d).dump"

# Sync dumps + the media/ directory to off-server storage (S3, etc.)
aws s3 sync ./backups s3://your-backup-bucket/scm_automation/db/
aws s3 sync ./media s3://your-backup-bucket/scm_automation/media/
```

Restore drill (run this for real at least once before relying on backups):
```bash
createdb scm_automation_restore_test
pg_restore -d scm_automation_restore_test backup_YYYYMMDD.dump
DATABASE_URL=postgres:///scm_automation_restore_test python manage.py check
```

## Upgrade procedure

1. `git pull`, install any new/updated packages from `requirements.txt`.
2. `python manage.py migrate` — all migrations are committed to the repo;
   never hand-edit the schema.
3. Restart the app server (gunicorn workers) so code changes take effect.
4. Smoke-test the golden paths after every deploy: log in, view the
   dashboard, open a PO, open a bill. `pytest` should already be green in
   CI before merge — this is a live sanity check, not a substitute for it.

## Performance notes

- Every list view added across all four phases paginates (20/page) and
  filters via indexed/FK fields (`site`, `status`, dates) — none load an
  unbounded queryset.
- `StockLedger` has an explicit composite index on `(site, item)` since
  every stock-on-hand lookup filters on that pair.
- Dashboard/report views that iterate querysets in Python (lead-time
  medians, vendor performance score) do so over small per-tenant datasets
  (tens to low hundreds of rows at this team's scale — 10-25 users,
  4-10 sites) — deliberately not optimized into raw SQL aggregations,
  since at this volume the simpler Python code is easier to verify and
  the difference is not measurable. Re-visit if the row counts grow by
  an order of magnitude.
- No profiling (django-debug-toolbar / nplusone) has actually been run
  against this codebase — the above is a design intention, not a
  measured result. Worth doing before go-live if list views start feeling
  slow with real data volumes.

## In-app help

A short "how it works" page per major workflow lives at `/help/` (text
only, no screenshots — screenshots would need to be re-captured by hand
every time a screen changes, and there's no automated way here to keep
them in sync with the actual UI).
