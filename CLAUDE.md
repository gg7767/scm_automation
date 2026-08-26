# SCM Automation System — Precast Company

This file is the project brief for Claude Code. Read it fully before making changes.
Detailed specs for later phases live in `docs/PHASE2_INDENTS_GRN.md`,
`docs/PHASE3_BILLS_PAYMENTS_TALLY.md`, and `docs/PHASE4_INVENTORY_TRANSPORT_MACHINERY.md`.
Read the relevant phase doc before building that phase. Do not deviate from the
data models without discussing first.

## What this system is

A supply chain management (SCM) web application for a precast concrete company,
replacing paper and Excel for: indents, purchase orders, goods receipts, bills,
and payments.

- Users: ~2 SCM team members at head office, ~2 members at each of 4–10 sites,
  plus accounts. Total ~10–25 users.
- Site users are mostly on mobile phones — every screen must work on a small screen.
- Accounts runs Tally. This system is the source of truth for procurement;
  approved bills are pushed to Tally as purchase vouchers (Phase 3).
- Currency INR. GST applies. Display dates DD-MM-YYYY. Indian financial year Apr–Mar.

## Tech stack (fixed)

- Python 3.12+, Django 5.x, PostgreSQL 16
- Server-rendered Django templates + HTMX. No React/SPA.
- Tailwind CSS; clean, dense business-app look; large touch targets on mobile.
- WeasyPrint for PDF generation (POs, GRNs, payment advices).
- Django auth + Groups for roles; django-simple-history on all business models.
- Uploads: local disk in dev, S3-compatible in production.
- pytest + pytest-django. Every status transition and computation has tests.
- Settings via django-environ. Never commit secrets.

## Roles (Django groups)

| Role | Can do |
|---|---|
| SCM Head | Everything; final approver above threshold |
| Purchase Officer (HO) | Masters, POs, approvals below threshold, indent conversion |
| Site Member | Raise indents, log GRNs, view POs/stock for own site |
| Accounts | Bills, 3-way match review, payments, Tally sync, read-only POs |
| Admin | Users, approval rules, settings |

Users are optionally linked to a Site; site members only see their own site's data.

## Apps

- `masters` — Site, Vendor, ItemCategory, Item(+aliases), RateContract
- `purchase` — PurchaseOrder(+lines), ApprovalRule, attachments
- `indents` — Indent(+lines) (Phase 2)
- `stores` — GRN(+lines), StockLedger, StockIssue (Phases 2 & 4)
- `billing` — VendorBill(+lines), Payment, TallySyncLog (Phase 3)
- `logistics` — TransportTrip (Phase 4)
- `assets` — Machine, MachineDeployment, MachineLog (Phase 4)

## Phase 1 data model (masters + purchase)

All models get created_at, updated_at, created_by, and history tracking.

**Site**: name, code (unique, e.g. HYD-F1), address, is_factory, active

**Vendor**: name, code (auto VEN-0001), gstin (validated, optional), pan, address,
state, contact_person, phone, email, bank_name, account_number, ifsc,
payment_terms_days, categories (M2M ItemCategory), tally_ledger_name (nullable),
status [active, on_hold, blacklisted]. VendorDocument(file, label).

**ItemCategory**: name, parent (nullable, one level)

**Item**: code (auto ITM-00001), name (canonical, unique), category,
unit [BAG, KG, MT, NOS, CUM, SQM, LTR, TRIP, HOUR, DAY, SET],
gst_rate, hsn_code (nullable), active. ItemAlias(alias_name) for legacy names.

**RateContract**: vendor, item, rate, valid_from, valid_to, remarks;
unique (vendor, item, valid_from); helper current_rate(vendor, item).

**PurchaseOrder**: po_number (auto PO/{site.code}/{FY}/{seq}), vendor, site,
project_name, status [draft, pending_approval, approved, sent,
partially_delivered, closed, cancelled] — transitions only via model methods,
source_indent (FK Indent, nullable — set in Phase 2), payment_terms_days,
delivery_terms, remarks, expected_delivery_date, subtotal, gst_amount,
grand_total (computed+stored), approved_by/at, sent_at,
sent_via [email, whatsapp, manual], revision_number (edits after approval
create a revision and require re-approval).

**PurchaseOrderLine**: po, item, description_override, quantity, unit, rate,
gst_rate, line_total, deviates_from_contract flag,
qty_received (Decimal, default 0 — updated by GRNs in Phase 2).

**ApprovalRule**: min_amount, max_amount (nullable), approver_role.
On submit, match rule by grand_total; that role or SCM Head can approve.
Reused for indents and bills with a doc_type field
[po, indent, bill].

**POAttachment**: po, file, label.

## Phase 1 workflows

1. Masters CRUD (admin + front-end list/detail/edit with search, filters,
   pagination).
2. PO lifecycle: draft with HTMX line editing and rate-contract autofill →
   submit → approve/reject with comment → PDF on letterhead → mark sent →
   amendments (revision + re-approval) → cancel with reason.
3. PO PDF: A4, letterhead placeholder, vendor & delivery blocks, line table
   with CGST/SGST vs IGST (by vendor state vs company state), amount in words
   (Indian lakh/crore), terms, signatory block.
4. Vendor ledger (lite): POs per vendor with status and value; open PO total.
5. Dashboard (lite): my pending approvals, recent POs, month PO count/value,
   POs past expected delivery.
6. Legacy CSV import (vendors, items via aliases), idempotent management command.

## Conventions

- Fat models / thin views; transitions, numbering, totals in model/service
  functions with tests.
- Money and quantities as Decimal, never float. Quantities Decimal(12,3).
- Every list view: search + filter + pagination. Every state change: POST
  with confirmation. Django messages for feedback.
- Server-side permission checks on every view.
- Migrations always committed. `seed_demo` management command for units,
  categories, demo site, approval rules, demo users per role.
- Tests written alongside each feature.
- Keep `docs/DECISIONS.md` — one line per notable design decision.
- Document numbering: all docs follow {PREFIX}/{site.code}/{FY}/{seq}
  (PO, IND, GRN, BILL, PAY) with per-site-per-FY sequences generated
  race-safely (select_for_update on a Sequence table).

## Full build order

Work through these strictly one at a time. Stop after each step for review.

**Phase 1 — Vendors & POs**
1. Scaffold: project, apps, auth, roles, base responsive template, env
   settings, PostgreSQL, pytest, seed_demo. ✅ (done)
2. Masters: Site, ItemCategory, Item(+aliases), Vendor(+documents) — models,
   admin, front-end CRUD, tests.
3. RateContract + current-rate lookup.
4. PurchaseOrder + lines: numbering, totals, status machine, tests.
5. PO create/edit UI (HTMX lines, rate autofill).
6. Approval engine + submit/approve/reject UI + pending-approvals widget.
7. PO PDF + mark-as-sent.
8. Amendments + cancellation.
9. Vendor ledger (lite) + dashboard (lite).
10. Legacy CSV import; permissions audit; deployment notes + backup script.

**Phase 2 — Indents & GRN** (read docs/PHASE2_INDENTS_GRN.md first)
11. Indent + lines: models, numbering, status machine, tests.
12. Site-side indent UI (mobile-first) + approval flow (reuse engine).
13. Indent → PO conversion (single or merged; partial quantities).
14. GRN + lines: models, qty validation against PO, PO status updates, tests.
15. GRN UI (mobile-first) with challan photo upload; short/excess/damage flags.
16. Pending-deliveries and indent-status dashboards; PO closure rules.

**Phase 3 — Bills, payments & Tally** (read docs/PHASE3_BILLS_PAYMENTS_TALLY.md first)
17. VendorBill + lines: models, numbering, status machine, tests.
18. Bill entry UI with PO/GRN pickers; attachment of invoice scan.
19. 3-way match engine with tolerances; mismatch review screen.
20. Payments: record against bills (full/partial/advance), vendor ledger (full),
    payables aging.
21. Tally XML export/push + TallySyncLog + retry; ledger-name mapping screen.
22. Accounts dashboard; debit notes for short/damaged quantities.

**Phase 4 — Inventory, transport, machinery** (read docs/PHASE4_INVENTORY_TRANSPORT_MACHINERY.md first)
23. StockLedger driven by GRNs; opening balances import.
24. StockIssue (consumption) + inter-site transfer; min-stock alerts → draft
    indents.
25. TransportTrip: vehicle, route, linked PO/GRN or element delivery, freight
    cost; freight report.
26. Machine, MachineDeployment (which machine at which site), MachineLog
    (fuel/hours), maintenance due alerts.
27. Reporting suite: spend by category/site/vendor, lead-time report, vendor
    performance score, monthly Excel exports.
28. Hardening: permissions audit, backup/restore drill, performance pass,
    user manual pages.
