# SCM Automation — Precast Company

Full supply chain management system for a precast concrete company:
indents, purchase orders (draft → approval → send → amend/cancel → close),
goods receipts, vendor bills with 3-way matching, payments, vendor
ledgers, inventory (stock ledger, issues, transfers), transport trips,
machinery/asset tracking, and a reporting suite. See [CLAUDE.md](CLAUDE.md)
for the full project brief and data model, and `docs/PHASE2_*.md` /
`docs/PHASE3_*.md` / `docs/PHASE4_*.md` for the detailed specs each phase
was built against.

Tally sync was **not built** — a deliberate scope decision (see
`docs/DECISIONS.md`); `Vendor.tally_ledger_name` is the only trace of it.

## Stack

Django 5 + PostgreSQL 16, server-rendered templates + HTMX (no SPA/build
step), Tailwind via CDN, WeasyPrint for PDFs, openpyxl for Excel exports,
django-simple-history for audit trails, pytest for tests.

## Apps

`masters` (Site/Vendor/Item/RateContract), `purchase` (PurchaseOrder),
`indents` (Indent), `stores` (GRN + inventory: StockLedger/StockIssue/
StockTransfer), `billing` (VendorBill/Payment/DebitNote), `logistics`
(TransportTrip), `assets` (Machine/MachineDeployment/MachineLog/
MaintenanceSchedule), `accounts_stub` (roles, notifications, cross-cutting
management commands).

## Dev setup

Requires Python 3.12+ and PostgreSQL 16 (both installed via Homebrew in this
environment: `brew install python@3.12 postgresql@16`).

```bash
source venv/bin/activate
pip install -r requirements.txt
```

`.env` (already present in this checkout, not committed) configures the DB,
company letterhead details for PO PDFs, email, and business-policy
tolerances — see `.env.example` for the full list.

Start Postgres and create the DB (already done in this environment):

```bash
brew services start postgresql@16
createdb scm_automation
```

Run migrations and seed roles/categories/demo site/approval rules:

```bash
python manage.py migrate
python manage.py seed_demo
python manage.py createsuperuser
```

Run the dev server:

```bash
python manage.py runserver
```

Visit http://127.0.0.1:8000/ — you'll be redirected to log in. `/help/`
has a short how-to per workflow once logged in.

## Roles

Django Groups, seeded by `seed_demo`: **SCM Head** (everything, final
approver above threshold, overrides bill mismatches), **Purchase Officer
(HO)** (masters, POs, indent conversion, approvals below threshold, GRN
reversals), **Site Member** (raise indents, log GRNs/stock issues, view
POs/stock for their own site only — set via each user's Site in the
admin), **Accounts** (bills, payments, 3-way match review, read-only on
POs/GRNs/vendor ledgers), **Admin** (user management, approval rules,
masters). `ApprovalRule` (min/max amount + `doc_type` [po/indent/bill] →
approver group) drives who can approve what, editable in the admin.

## Management commands

```bash
# Legacy data import (idempotent) — see sample_data/ for column layout
python manage.py import_legacy_csv --vendors sample_data/vendors_sample.csv --items sample_data/items_sample.csv
python manage.py import_opening_stock path/to/opening_stock.csv

# Nightly jobs (see docs/DEPLOYMENT.md for suggested cron schedule)
python manage.py reconcile_stock_balances   # recompute StockBalance cache from StockLedger, report drift
python manage.py check_min_stock            # draft (never auto-submit) replenishment indents below SiteItemSetting.min_stock_qty
python manage.py check_maintenance_due      # notify SCM Head of due/overdue MaintenanceSchedules
python manage.py send_monthly_scm_pack      # build + email the monthly Excel workbook (spend/lead-time/aging/stock/freight/machinery)
```

## Tests

```bash
pytest
```

288 tests as of the last Phase 4 commit, covering every status machine,
numbering scheme, permission wall, and the reporting suite's service
functions — see `accounts_stub/test_permission_matrix.py` for the
cross-app permission matrix.

## Notes

- PDF generation (WeasyPrint) needs Pango/GDK-Pixbuf system libraries
  (`brew install pango gdk-pixbuf libffi` on macOS). `config/settings.py`
  sets `DYLD_LIBRARY_PATH` automatically on Darwin so this works out of
  the box in dev — no need to launch `manage.py` with it set manually.
- All money is `Decimal`; quantities are `Decimal(12,3)`. Dates render
  DD-MM-YYYY in templates; document numbering (`PO/`, `IND/`, `GRN/`,
  `BILL/`, `PAY/`, `TRP/`, `ISS/`, `TRF/`, `MC-`) uses the Indian
  financial year (Apr–Mar) via `masters/numbering.py`.
- Design decisions (including known scope trims) are logged in
  [docs/DECISIONS.md](docs/DECISIONS.md).
- Deployment (server setup, env vars, cron jobs, backups, Tally
  connectivity options, upgrade procedure) is documented in
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — written as a plan to
  execute, since this has only run in local dev so far.
