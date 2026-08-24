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
- `UserProfile` (user ↔ Site, for "Site Member sees only their own site")
  lives in `accounts_stub`, not `masters`, specifically to avoid a
  masters↔accounts_stub import cycle: `accounts_stub.roles` (shared role
  constants/helpers) needs `UserProfile`, and `masters` doesn't need to
  import `accounts_stub` at all. A `post_save` signal on `User` auto-creates
  the profile. Role constants/helpers are centralized in
  `accounts_stub/roles.py` (`SCM_HEAD`, `PURCHASE_OFFICER`, etc.) so
  `masters`, `purchase`, and the nav context processor share one source of
  truth instead of re-hardcoding group name strings.
- Added a small `POApprovalAction` log model (submit/approve/reject/amend
  + actor + comment) — not in the original CLAUDE.md model list, but
  needed to satisfy "approve/reject with comment" and to show approval
  history on the PO detail page. It's the audit trail for the status
  machine, separate from django-simple-history's field-diff history.
- PO amendment (build step 8) is a single `PurchaseOrder.amend()` method:
  reopens an approved/sent/partially-delivered PO to `draft`, increments
  `revision_number`, clears approval/send state, and keeps the same
  `po_number` and existing lines (still editable). No separate "revision"
  model/table — simpler, and the approval-action log already gives an
  audit trail of who amended what and when.
- `vendor`/`site` on a PurchaseOrder are immutable after creation (excluded
  from the edit form) since `po_number` embeds `site.code` and rate-contract
  deviation flags are computed against `vendor` at line-save time; letting
  either change post-creation would desync the number or the flags.
- Item search-as-you-type (step 5) is a small HTMX GET endpoint
  (`purchase:item_search`) returning an HTML fragment, not a JSON API —
  keeps with "server-rendered templates + HTMX" and needed no extra JS
  framework. Rate-contract autofill piggybacks on the same fragment via
  `data`-carrying `onclick` handlers rather than a second round-trip.
- Company letterhead fields (`COMPANY_NAME`, `COMPANY_ADDRESS`,
  `COMPANY_GSTIN`, `COMPANY_STATE`) are env-configurable Django settings,
  not a model — there's exactly one company, and CLAUDE.md's data model
  doesn't list a Company model.
- WeasyPrint's Homebrew library path fix (`DYLD_LIBRARY_PATH`) was moved
  from a README instruction into `config/settings.py` itself (Darwin-only,
  guarded by `platform.system()`), since dyld reads that env var per
  `dlopen()` call — setting it at Django startup is enough, no need to
  launch `manage.py`/gunicorn with it pre-set.
- Dashboard (lite) lives on the existing home page (`config/views.py`)
  rather than a separate `/dashboard/` URL — it's the natural landing
  page and avoids a redundant route; it only renders PO widgets for users
  who have `can_view_purchase_orders`.
