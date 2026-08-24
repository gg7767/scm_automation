# SCM Automation System — Precast Company

This file is the project brief for Claude Code. Read it fully before making changes.
It defines the domain, data model, conventions, and build order. Do not deviate from
the data model without discussing it first.

## What this system is

A supply chain management (SCM) web application for a precast concrete company,
replacing paper and Excel for: indents, purchase orders, goods receipts, bills,
and payments. Built and rolled out in phases. **We are currently building Phase 1:
Vendor & Purchase Order module.**

- Users: ~2 SCM team members at head office, ~2 members at each of 4–10 sites,
  plus accounts. Total ~10–25 users.
- Site users will mostly be on mobile phones — every screen must work well on
  a small screen.
- Accounts runs Tally. This system is the source of truth for procurement;
  approved bills will later be pushed to Tally as purchase vouchers (Phase 3).
  Design GST and ledger-mapping fields with that in mind now.
- Currency is INR. GST applies. Dates in DD-MM-YYYY for display.

## Tech stack (fixed)

- Python 3.12+, Django 5.x, PostgreSQL 16
- Server-rendered Django templates + HTMX for interactivity. No React/SPA.
- Tailwind CSS (via CDN or django-tailwind) for styling; clean, dense,
  business-app look; large touch targets on mobile.
- WeasyPrint for PO PDF generation.
- Django's built-in auth with Groups for roles; django-simple-history (or
  equivalent) for audit trails on all business models.
- File uploads stored on local disk in dev (`MEDIA_ROOT`), S3-compatible
  storage in production.
- pytest + pytest-django for tests. Every workflow (PO approval, status
  transitions) must have tests.
- Single settings module with environment variables via django-environ.
  Never commit secrets.

## Roles (Django groups)

| Role | Can do |
|---|---|
| SCM Head | Everything, final approver above threshold |
| Purchase Officer (HO) | Manage masters, create/send POs, approve below threshold |
| Site Member | View POs for their site; (Phase 2: raise indents, log GRNs) |
| Accounts | Read-only on POs and vendor ledger; (Phase 3: bills & payments) |
| Admin | User management, approval rules, settings |

Every user is optionally linked to a Site. Site members only see data for
their own site.

## Phase 1 data model

Create these Django models (app names in parentheses). All models get
`created_at`, `updated_at`, `created_by`, and history tracking.

### masters app

**Site**
- name, code (short unique code e.g. "HYD-F1"), address, is_factory (bool), active

**Vendor**
- name, code (auto e.g. VEN-0001), gstin (validated format, optional for
  unregistered), pan, address, state (for GST place-of-supply),
  contact_person, phone, email
- bank_name, account_number, ifsc
- payment_terms_days (int, e.g. 30), categories (M2M to ItemCategory)
- tally_ledger_name (char, nullable — mapping for Phase 3 sync)
- status: choices [active, on_hold, blacklisted]
- documents: separate VendorDocument model (file, label)

**ItemCategory**
- name (e.g. Cement, Steel, Aggregates, Admixtures, Hardware, Machinery Hire,
  Transport), parent (nullable, for one level of nesting)

**Item**
- code (auto e.g. ITM-00001), name (canonical, unique), category (FK),
- unit (choices: BAG, KG, MT, NOS, CUM, SQM, LTR, TRIP, HOUR, DAY, SET),
- gst_rate (decimal %, e.g. 18.00), hsn_code (char, nullable), active
- aliases: separate ItemAlias model (alias_name) — used to map messy legacy
  Excel names to canonical items during data import

**RateContract**
- vendor (FK), item (FK), rate (decimal), valid_from, valid_to, remarks
- unique constraint on (vendor, item, valid_from)
- helper: `current_rate(vendor, item)` returns active contract rate or None

### purchase app

**PurchaseOrder**
- po_number (auto: PO/{site.code}/{FY}/{seq} e.g. PO/HYD-F1/26-27/0042;
  FY = Indian financial year Apr–Mar)
- vendor (FK), site (FK, delivery location), project_name (char, optional),
- status: choices [draft, pending_approval, approved, sent, partially_delivered,
  closed, cancelled] — enforce transitions in model methods, never by direct
  status assignment in views
- terms: payment_terms_days (defaulted from vendor, editable),
  delivery_terms (text), remarks (text)
- expected_delivery_date
- totals: subtotal, gst_amount, grand_total — computed, stored, recomputed on
  line changes
- approved_by, approved_at, sent_at, sent_via (choices: email, whatsapp, manual)
- amendment tracking: revision_number (int, starts 0); any edit after approval
  requires creating a revision and re-approval

**PurchaseOrderLine**
- po (FK), item (FK), description_override (char, optional), quantity, unit
  (copied from item), rate, gst_rate (copied from item, editable),
  line_total (computed)
- rate_flag: if a RateContract exists and entered rate differs, mark
  `deviates_from_contract=True` and show a warning badge

**ApprovalRule**
- min_amount, max_amount (nullable = no cap), approver_role (FK to Group)
- Engine: on submit, find the matching rule for grand_total; users holding
  that role (or SCM Head) can approve. Keep it table-driven and simple.

**POAttachment**
- po (FK), file, label (e.g. "Vendor quotation")

## Phase 1 workflows

1. **Masters CRUD** — vendors, items, categories, sites, rate contracts.
   Use Django admin for Admin role, plus clean front-end list/detail/edit
   pages for Purchase Officers (search, filters, pagination).
2. **PO lifecycle** — create draft (line items with item search-as-you-type
   via HTMX, rates auto-filled from rate contract), submit for approval,
   approve/reject with comment, generate PDF on letterhead, mark as sent
   (log channel), amend with revision + re-approval, cancel with reason.
3. **PO PDF** — professional A4 layout: company letterhead placeholder,
   vendor & delivery details, line table with GST breakup (CGST/SGST if
   vendor state == company state, else IGST), amount in words (Indian
   numbering: lakh/crore), terms, authorized signatory block.
4. **Vendor ledger (lite)** — per vendor: list of POs with status and value;
   total open PO value. (True ledger with bills/payments arrives in Phase 3.)
5. **Dashboard (lite)** — pending approvals (for the logged-in approver),
   recent POs, PO count/value this month, POs past expected delivery date.
6. **Legacy data import** — management command to import vendors and items
   from CSV (exported from existing Excel), using ItemAlias for name mapping.
   Import must be idempotent (re-running does not duplicate).

## Conventions Claude Code must follow

- Fat models / thin views: status transitions, numbering, and total
  computation live on models or service functions, covered by tests.
- Use Django messages framework for user feedback; never silent failures.
- All money as Decimal; never float. Quantities as Decimal(12,3).
- Every list view: search + filter + pagination. Every destructive or
  state-changing action: POST with confirmation.
- Permissions checked server-side on every view (mixins/decorators), not
  just hidden buttons.
- Migrations must always be committed. Seed data (units, categories, a demo
  site, approval rules) via a management command `seed_demo`.
- Write tests alongside each feature, not at the end.
- Keep a `docs/DECISIONS.md` log — one line per notable design decision.

## Later phases (do NOT build yet, but don't block them)

- Phase 2: Indent model (site → HO), indent-to-PO conversion, GRN against PO
  lines with challan photo upload, partial deliveries updating PO status.
- Phase 3: VendorBill, 3-way match (PO vs GRN vs bill within tolerance),
  payment recording, payables aging, Tally push (XML over HTTP gateway;
  fallback CSV) using `tally_ledger_name` mapping.
- Phase 4: Inventory per site, transport trips & freight, machinery/asset
  register, richer dashboards & exports.

## Build order (work through these one at a time)

1. Project scaffold: Django project, apps (masters, purchase, accounts_stub),
   auth, roles/groups, base template with responsive nav, settings via env,
   PostgreSQL, pytest wiring, seed_demo command.
2. Masters: Site, ItemCategory, Item (+aliases), Vendor (+documents) —
   models, admin, front-end CRUD, tests.
3. RateContract + current-rate lookup.
4. PurchaseOrder + lines: models, numbering, totals, status machine, tests.
5. PO create/edit UI with HTMX line editing and rate-contract autofill.
6. Approval flow: ApprovalRule engine, submit/approve/reject UI, pending-
   approvals dashboard widget.
7. PO PDF generation + mark-as-sent.
8. Amendments (revisions) + cancellation.
9. Vendor ledger (lite) + dashboard (lite).
10. CSV import command for legacy vendors/items; polish, permissions audit,
    deployment notes (gunicorn + nginx or Railway/Render, daily pg_dump backup).
