# SCM Automation — Precast Company (Phase 1)

Supply chain management system: Vendor & Purchase Order module. See
[CLAUDE.md](CLAUDE.md) for the full project brief and build order.

## Dev setup

Requires Python 3.12+ and PostgreSQL 16 (both installed via Homebrew in this
environment: `brew install python@3.12 postgresql@16`).

```bash
source venv/bin/activate
pip install -r requirements.txt
```

`.env` (already present in this checkout, not committed) configures the DB:

```
DATABASE_URL=postgres:///scm_automation
```

Start Postgres and create the DB (already done in this environment):

```bash
brew services start postgresql@16
createdb scm_automation
```

Run migrations and seed the role groups:

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

## Tests

```bash
pytest
```

## Notes

- PDF generation (WeasyPrint, build step 7) needs Pango/GDK-Pixbuf system
  libraries. On Apple Silicon Homebrew, run with:
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib python manage.py ...`
