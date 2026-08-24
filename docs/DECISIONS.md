# Design decisions log

One line per notable decision, most recent last.

- Django project package is named `config` (not `scm_automation`) to avoid a
  clash between the repo-root package and the top-level `scm_automation`
  concept used in the domain; apps are `masters`, `purchase`, `accounts_stub`.
- Roles are plain `django.contrib.auth` Groups (no custom Role model),
  seeded via `seed_demo`: SCM Head, Purchase Officer (HO), Site Member,
  Accounts, Admin.
- `TIME_ZONE` set to `Asia/Kolkata` since all sites and users are in India;
  `USE_TZ` stays True (store UTC, display local).
- DB config goes through `django-environ`'s `DATABASE_URL`; local dev
  defaults to `postgres:///scm_automation` (peer auth via Unix socket, no
  password needed on a local Homebrew Postgres).
- Tailwind is loaded via CDN for now (per CLAUDE.md, CDN or django-tailwind
  both allowed) to keep the scaffold dependency-free; can switch to
  django-tailwind later if a build step is needed.
- WeasyPrint requires Pango/GDK-Pixbuf system libraries (installed via
  Homebrew: `pango`, `gdk-pixbuf`, `libffi`); on Apple Silicon it needs
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib` set at runtime — documented in
  README, not needed until PO PDF generation (build step 7).
