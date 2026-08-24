# SCM Automation — Precast Company (Phase 1)

Supply chain management system for a precast concrete company: vendor
masters, rate contracts, and the full purchase order lifecycle (draft →
approval → send → amend/cancel → close), with PDF generation and lite
vendor-ledger/dashboard views. See [CLAUDE.md](CLAUDE.md) for the full
project brief, data model, and build order this was built against.

## Stack

Django 5 + PostgreSQL 16, server-rendered templates + HTMX (no SPA/build
step), Tailwind via CDN, WeasyPrint for PDFs, django-simple-history for
audit trails, pytest for tests.

## Dev setup

Requires Python 3.12+ and PostgreSQL 16 (both installed via Homebrew in this
environment: `brew install python@3.12 postgresql@16`).

```bash
source venv/bin/activate
pip install -r requirements.txt
```

`.env` (already present in this checkout, not committed) configures the DB
and company letterhead details for PO PDFs:

```
DATABASE_URL=postgres:///scm_automation
COMPANY_NAME=Precast Co. Pvt. Ltd.
COMPANY_ADDRESS=Plot 1, Industrial Area, Hyderabad, Telangana
COMPANY_GSTIN=
COMPANY_STATE=TG
```

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

Visit http://127.0.0.1:8000/ — you'll be redirected to log in.

## Roles

Django Groups, seeded by `seed_demo`: **SCM Head** (everything, final
approver above threshold), **Purchase Officer (HO)** (manage masters,
create/send POs, approve below threshold), **Site Member** (view POs for
their own site only — set via each user's Site in the admin), **Accounts**
(read-only on POs and vendor ledgers), **Admin** (user management, approval
rules). Who can approve a given PO is driven by `ApprovalRule` (min/max
amount → approver group), editable in the admin.

## Legacy data import

Import vendors and/or items from CSV exports (e.g. from Excel). Idempotent
— re-running the same file updates existing rows instead of duplicating
them. See [sample_data/](sample_data/) for the expected column layout.

```bash
python manage.py import_legacy_csv --vendors sample_data/vendors_sample.csv --items sample_data/items_sample.csv
```

Items support an `alias_of` column: a row with `alias_of` set registers
`name` as an `ItemAlias` pointing at the existing canonical item named
`alias_of`, instead of creating a new item — this is how messy legacy
Excel item names get mapped onto one canonical item.

## Tests

```bash
pytest
```

## Notes

- PDF generation (WeasyPrint) needs Pango/GDK-Pixbuf system libraries
  (`brew install pango gdk-pixbuf libffi` on macOS). `config/settings.py`
  sets `DYLD_LIBRARY_PATH` automatically on Darwin so this works out of
  the box in dev — no need to launch `manage.py` with it set manually.
- All money is `Decimal`; quantities are `Decimal(12,3)`. Dates render
  DD-MM-YYYY in templates; PO numbering uses the Indian financial year
  (Apr–Mar).
- Design decisions are logged in [docs/DECISIONS.md](docs/DECISIONS.md).

## Deployment notes (not yet stood up)

This has only been run in local dev so far. For a real deployment:

- **App server**: gunicorn behind nginx (or a platform like Railway/Render
  that runs `gunicorn config.wsgi` directly) — `DEBUG=False`,
  `DJANGO_ALLOWED_HOSTS` set to the real domain, `DJANGO_SECRET_KEY`
  a real random value, all via environment variables (never commit `.env`).
- **Database**: managed PostgreSQL 16. Run `python manage.py migrate` on
  deploy; `seed_demo` once against production to get roles/categories/
  approval rules in place, then adjust approval-rule thresholds for real.
- **Media storage**: switch `MEDIA_ROOT`-based `FileField`s (vendor
  documents, PO attachments) to S3-compatible object storage in
  production — local disk won't survive redeploys on most PaaS hosts.
- **WeasyPrint**: the Linux deploy target needs `libpango-1.0-0`,
  `libpangocairo-1.0-0`, `libgdk-pixbuf2.0-0`, `libffi` (or the Debian/
  Ubuntu equivalents) installed at the OS level — no DYLD workaround
  needed there, that's macOS-only.
- **Backups**: daily `pg_dump` of the production database to
  off-site/object storage, with a tested restore procedure — there's no
  soft-delete/undo in this app for most models.
- **Static files**: `python manage.py collectstatic` (only `home.html`'s
  Tailwind/HTMX are CDN-loaded; local `STATICFILES_DIRS` is currently
  empty but wired up for future custom CSS/JS).
