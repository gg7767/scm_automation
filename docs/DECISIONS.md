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
- Shared `TimeStampedModel` abstract base (created_at/updated_at/created_by)
  lives in `masters/models.py` since masters is the first/foundational app;
  `purchase` will import it from there rather than duplicating it or adding
  a separate `core` app not named in the brief.
- `Vendor.code` / `Item.code` auto-generate as `{PREFIX}-{N}` by scanning
  the highest existing numeric suffix (wrapped in `select_for_update`), not
  a dedicated sequence table — simple and sufficient at this team's scale
  (10-25 users); the same pattern will be reused for `PurchaseOrder.po_number`.
- `Vendor.state` uses a fixed Indian states/UTs choice list
  (`masters/constants.py`) rather than free text, since it drives the
  CGST/SGST vs IGST decision on PO PDFs in build step 7.
- Front-end masters CRUD (as opposed to Django admin) is restricted to
  Purchase Officer (HO) / SCM Head / Admin via `MastersManagerRequiredMixin`
  (`masters/permissions.py`) — Site Member and Accounts have no masters
  UI access in Phase 1, matching the roles table in CLAUDE.md.
- Item aliases and vendor documents are managed via small inline
  add/remove forms on the detail page (plain POST, no formsets/HTMX) —
  HTMX is reserved for PO line editing per build step 5.
