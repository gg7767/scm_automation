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
  who have `can_view_purchase orders`.
- `import_legacy_csv` (build step 10) lives in `masters` (owns Vendor/
  Item/ItemAlias) and takes `--vendors`/`--items` CSV paths independently.
  Vendors match by GSTIN when present, else case-insensitive name — both
  create-or-update, so re-running a corrected export is safe. Items
  support an `alias_of` column: a row with it set only registers an
  `ItemAlias` onto an existing canonical item (matched by name or an
  existing alias) instead of creating a new item — this is the "map messy
  legacy Excel names onto one canonical item" mechanism from CLAUDE.md.
  Each row is validated and saved in its own savepoint (nested
  `transaction.atomic()`), so one bad row is skipped and reported without
  aborting the rest of the file.
- Permissions audit (step 10) found and fixed one real gap: vendor
  ledgers aggregate a vendor's POs across *all* sites, but
  `POViewRequiredMixin` (used for POs themselves) allows Site Member —
  which would have let a Site Member see other sites' PO data through the
  ledger route. Added `VendorLedgerViewRequiredMixin`, narrower than
  `POViewRequiredMixin`, that excludes site-restricted users; nav links
  and the vendor-detail "PO ledger" link now gate on a matching
  `can_view_vendor_ledgers` context flag instead of the broader
  `can_view_po`.

## Phase 2+ scope decisions (made in chat, not in CLAUDE.md)

Before building Phases 2-4, the user chose: skip Tally sync entirely (no
XML/CSV export/push code, just the existing `tally_ledger_name` field),
exact-match (0% tolerance) 3-way matching for bills, a full stock ledger
with consumption tracking in Phase 4, and both Excel + PDF exports for
reports. Infra-dependent Phase 4 items (cron, email, backups) get real
code + `docs/DEPLOYMENT.md`, not a live restore drill or real SMTP send.

- Document numbering (`PO/`, `IND/`, `GRN/`, ...) was generalized from
  `purchase/numbering.py` into `masters/numbering.py`
  (`generate_document_number(model, field_name, prefix, scope_code, on_date)`);
  `purchase.numbering.generate_po_number` is now a thin wrapper so existing
  imports/tests didn't need to change.
- `ApprovalRule` gained a `doc_type` [po, indent, bill] field (default
  `po`, so existing PO call sites are unaffected) instead of a separate
  rule table per document type — one engine, one admin screen, reused by
  indents (Phase 2) and bills (Phase 3).
- `Notification` (recipient, subject, body, channel, sent_at) lives in
  `accounts_stub` — logs every alert and actually sends email (console
  backend in dev); WhatsApp/SMS channels are logged only, no gateway yet,
  per the phase doc's "schema ready, gateway later" instruction.
- New apps `indents`, `stores`, `billing`, `logistics`, `assets` match the
  CLAUDE.md app list exactly (Phase 2: indents + stores/GRN; Phase 3:
  billing; Phase 4: stores/inventory + logistics + assets).
- Indent→PO conversion: `PurchaseOrderLine.indent_line` (nullable FK) and
  `indent_qty_recorded` track how much of a line has already been rolled
  onto its indent line. Rollup only happens in `PurchaseOrder.approve()`
  (delta-based: `quantity - indent_qty_recorded`), never at PO creation —
  matches the phase doc ("on PO approval, increment qty_ordered") and
  makes `amend()` + re-approve safe against double-counting.
- Indent estimated value (for `ApprovalRule` routing, since indents carry
  no money) uses the latest rate contract for the item across *any*
  vendor, falling back to the most recent `PurchaseOrderLine.rate` for
  that item, then 0 — this is an estimate only and is always labelled as
  such in the UI.
- `PurchaseOrder.delivery_complete` is a plain boolean set by
  `refresh_delivery_status()` (called from GRN submission), not a new
  status value — status still only reaches `partially_delivered` on any
  receipt (matching the phase doc literally); actual PO `closed` still
  only happens via the existing manual `close()` (or, from Phase 3
  onward, once billing completes too).
- GRN corrections are a second GRN with `is_reversal=True` (negative
  quantities, HO-only), not an edit to a submitted GRN — submitted GRNs
  are immutable per the phase doc. The over-receipt tolerance check
  (default 2%, `GRN_OVER_RECEIPT_TOLERANCE_PERCENT`) is skipped entirely
  for reversals.
- Rejected GRN quantities create a `DebitNoteCandidate` automatically
  (grn_line, qty, reason, resolved) — a placeholder Phase 3 will turn
  into a real `DebitNote` or resolve/waive.
- `stores.services.record_receipt(grn)` is a deliberate no-op stub today;
  Phase 4 fills it in to write `StockLedger` entries, so GRN submission
  code never needs to change when that lands.
