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

## Phase 3: Bills, 3-way match, payments

- Tally sync: skipped entirely per the user's explicit decision (confirmed
  twice, including after being shown that the phase doc's golden-file XML
  tests would make it testable without a live Tally server). No XML/CSV
  export code exists; `Vendor.tally_ledger_name` is the only trace.
- 3-way match tolerances are exact (0% qty, 0% rate, ₹1 total rounding) via
  `BILL_MATCH_*` settings — matches both the user's answer and the phase
  doc's own stated defaults.
- `VendorBill._run_three_way_match` auto-advances straight to
  `approved_for_payment` on a full pass (no separate resting "matched"
  state in practice) and applies `qty_already_billed` + auto-closes the PO
  (if `delivery_complete` and every line fully billed) only on that
  transition — never on `override_mismatch`'s twin path re-does the same
  two side effects, so both roads to "approved" behave identically.
  Duplicate-invoice guard is a real `UniqueConstraint` on
  `(vendor, vendor_invoice_number, financial_year)`, not just a `clean()`
  check, so it's race-safe.
- **Found and fixed the same bug twice**: `VendorBillForm`/`PaymentAllocationForm`
  are validated via `form.is_valid()`, which calls `instance.full_clean()`
  and therefore the model's own `clean()` — but `clean()` reads
  `self.vendor`/`self.payment`, which aren't set until *after*
  `form.is_valid()` in the naive view pattern (`form.save(commit=False)`
  then assign FKs). Fixed both views by passing a pre-populated
  `instance=Model(fk=value)` into the form constructor so validation sees
  the FK. Caught only via live browser testing — the equivalent unit tests
  had built objects directly, bypassing the view layer entirely; added a
  view-level regression test for the payment-allocation case since the
  existing suite had a real gap there.
- `PaymentAllocation.clean()` computes remaining headroom as
  `unallocated_amount + original_stored_amount` (fetched from the DB on
  edit), not `+ the new proposed amount` — otherwise editing an existing
  allocation's amount would silently miscompute the available headroom.
- Vendor ledger (full) and payables aging are a new `billing` app view
  pair, deliberately separate from the existing `purchase` app's PO-based
  "lite" ledger (POs by status/value) — different questions, different
  audiences (Accounts vs. Purchase Officer), no reason to conflate them.
  `VendorOpeningBalance` (one row per vendor, admin-entered) seeds the
  ledger's running balance for pre-go-live dues; no CSV import for it was
  built since the phase doc didn't specify a format.
- Match-result JSON keys must not start with `_` — Django template
  variable lookup rejects underscore-prefixed attributes/keys outright
  (`TemplateSyntaxError`), not silently. Renamed the totals-comparison key
  from `_totals` to `totals` after hitting this in the bill-detail template.
- `GRNAccessRequiredMixin` (create/manage) doesn't include Accounts, but
  the debit-note-from-candidate workflow redirects to the GRN detail page
  — so `GRNListView`/`GRNDetailView` specifically now use a separate
  `GRNViewRequiredMixin` that adds Accounts as read-only, while
  create/submit/reversal stay on the stricter mixin.

## Phase 4: Inventory, transport, machinery, reporting, hardening

- Stayed with the CLAUDE.md app list exactly (`stores` owns inventory,
  `logistics` owns transport, `assets` owns machinery) rather than adding
  a `reports` app for the reporting suite — each report's service
  function/view lives in the app that owns its data (spend analysis in
  `billing`, lead time in `purchase`, vendor score in `masters`, stock
  reports in `stores`, freight in `logistics`, machinery utilisation in
  `assets`); `accounts_stub` hosts the monthly-pack command since it
  already owns cross-cutting email/notification infra.
- `StockLedger` is genuinely append-only (no update/delete views exist);
  `StockBalance` is a cache written in the same transaction as every
  ledger entry via `write_stock_ledger_entry()`, the single choke point
  every stock-moving action goes through. `reconcile_stock_balances`
  recomputes the cache from the ledger nightly and reports drift —
  this is also how `Phase 4 fills in stores.services.record_receipt`
  (the Phase 2 no-op stub) without touching GRN code at all.
  `MachineDeployment`'s "at most one open deployment per machine" rule
  is a real partial `UniqueConstraint` (`condition=Q(to_date__isnull=True)`),
  not just application-level validation.
- Freight bills from transporters needed `VendorBill.po` to become
  nullable and a new `transport_trips` M2M, with a 2-way match
  (`_run_two_way_match_for_trips`, trip freight vs bill total) alongside
  the existing 3-way PO/GRN/bill match — `submit_for_matching` branches
  on whether the bill has a PO or trips. Found and fixed a real bug this
  introduced: `_maybe_close_po` unconditionally did `self.po.refresh_from_db()`,
  which crashes for a trip-based bill with no PO; guarded with an early
  return. Trip-based bill entry is admin-only for now (no custom
  front-end flow) — the PO-based path is the primary, high-frequency one
  and already has full custom UI; this is a deliberate scope trim, not
  an oversight.
- Vendor performance score is shown on the vendor detail page as
  specified; it is **not** wired into the PO vendor picker (`<select>`)
  as the phase doc also asks for — doing that cleanly needs a custom
  option-rendering widget, and this was cut for time. Documented here
  rather than silently dropped.
- Hired-machinery bills validating against `MachineLog` totals (the
  phase doc's other 2-way match variant) was **not built** — only the
  transport-trip 2-way match exists. `Machine`/`MachineDeployment`/
  `MachineLog`/`MaintenanceSchedule` are otherwise complete (registration,
  deployment history, usage logs, maintenance-due tracking).
- Procurement lead time has no real "delivery complete" timestamp to
  read (`PurchaseOrder.delivery_complete` is a plain boolean, set in
  Phase 2) — approximated by the latest submitted GRN's `received_date`
  for that PO. Lead time is only computed for POs with a `source_indent`
  (indent-originated), per the phase doc's own stage definition
  ("indent approved → PO sent → ..."); direct HO-created POs aren't
  included in this particular report.
- Vendor performance score and procurement lead time are both plain
  Python loops over querysets rather than SQL aggregations (see
  `docs/DEPLOYMENT.md` performance notes) — deliberate, given the actual
  data volumes at this team's scale.
- Stock issue's item lines use a plain `modelformset_factory` with
  `extra=5` blank rows (no HTMX search-as-you-type), unlike PO/indent
  lines — simpler to implement, and issue lines aren't pre-filled from
  any source document the way GRN/bill lines are, so there was less
  benefit from the fancier pattern.
- In-app help (`/help/`) is text-only, no screenshots — screenshots
  would need re-capturing by hand on every UI change with no automated
  way to keep them in sync here, so a stale screenshot seemed worse than
  none.
- `docs/DEPLOYMENT.md` documents cron wiring, backup/restore commands,
  and Tally connectivity options as a plan to execute, not a completed
  operation — no cron has actually been scheduled, no backup has
  actually been taken/restored, and no Tally gateway exists to connect
  to, per the Phase 3 decision to skip Tally entirely.
