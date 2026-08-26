# Phase 4 — Inventory, Transport, Machinery & Reporting

Read CLAUDE.md first. By this phase, all procurement transactions flow through
the system, so inventory and reports are largely derived data. Keep everything
ledger-driven: stock is never edited directly, only moved by documents.

## stores app — inventory

**StockLedger** (append-only)
- site (FK), item (FK), txn_date, txn_type [opening, grn_receipt, issue,
  transfer_out, transfer_in, adjustment], qty (signed Decimal),
  ref_doc_type/ref_doc_id (generic link), rate (for valuation), remarks
- Current stock = sum of qty per (site, item); maintain a cached StockBalance
  table updated in the same transaction for fast dashboards, with a nightly
  reconciliation command that recomputes from the ledger and reports drift.

**StockIssue**
- issue_number (auto ISS/{site.code}/{FY}/{seq}), site, issued_by, issue_date
- purpose: [production, project_consumption, maintenance, other] + free text
- lines: item, qty, remarks. Cannot issue below available stock
  (setting `allow_negative_stock`, default false).

**StockTransfer**
- transfer_number, from_site, to_site, vehicle_number, status
  [dispatched, received]; dispatch writes transfer_out, receipt (confirmed at
  destination with qty check) writes transfer_in; shortages in transit logged.

Hooks:
- Implement the `record_receipt(grn)` stub from Phase 2 to write grn_receipt
  entries (accepted qty only).
- Opening balances: CSV import per site (item, qty, rate) as txn_type=opening.
- Min-stock: Item gets min_stock_qty per site (SiteItemSetting model). Nightly
  command flags items below minimum and creates a *draft* indent per site
  (never auto-submits), notifying the site's members.

## logistics app

**TransportTrip**
- trip_number (auto TRP/{site.code}/{FY}/{seq})
- trip_date, vehicle_number, transporter (FK Vendor filtered to Transport
  category), driver_name/phone
- from_location, to_location (free text or Site FKs; both patterns occur:
  vendor→site for inward, factory→site for precast element delivery)
- linked_doc: optional generic link (PO or GRN for inward; free description
  for element deliveries)
- freight_amount, billable_to [company, vendor, client], remarks, pod_photo
  (proof of delivery upload)
- Freight bills from transporters flow through the normal Phase 3 billing
  path (transporter is a Vendor; a bill can link TransportTrips instead of a
  PO — add nullable bill.transport_trips M2M and relax the 3-way match to a
  2-way check, trip freight vs bill, for transport-category vendors).

Reports: monthly freight by route/site/transporter; cost per trip; trips
without PODs.

## assets app — machinery

**Machine**
- code (auto MC-0001), name (e.g. "Batching Plant 30cum", "Hydra Crane 14T"),
  category [batching, cranes, moulds, vehicles, tools, other],
  ownership [owned, hired], hire_vendor (FK, if hired), hire_rate + rate_unit
  [per_hour, per_day, per_month], purchase_date/value (if owned), status
  [active, under_maintenance, idle, disposed]

**MachineDeployment**
- machine (FK), site (FK), from_date, to_date (nullable = still there),
  remarks. Constraint: a machine has at most one open deployment. "Where is
  everything" screen = machines grouped by current site.

**MachineLog**
- machine, site (auto from deployment), log_date, hours_run or km,
  fuel_litres, operator_name, remarks. Simple daily entry from site phones.

**MaintenanceSchedule**
- machine, every_n_days or every_n_hours, last_done_date/hours, notes.
  Nightly command flags due/overdue maintenance; dashboard widget + email.

Hired machinery billing: vendor bills for hire link to a machine +
deployment period; validation compares billed period/hours against
MachineLog totals (2-way match variant, tolerance setting).

## Reporting suite

All reports filterable by date range, site, category, vendor; all exportable
to Excel (openpyxl) with the same numbers as on screen.

1. Spend analysis: by category / site / vendor / month (matched bills, not POs).
2. Procurement lead time: indent approved → PO sent → first GRN → delivery
   complete; medians by vendor and category.
3. Vendor performance score per vendor: on-time delivery %, short/damage %,
   rate-contract adherence, bill mismatch rate → simple weighted score shown
   on the vendor page and PO vendor picker.
4. Stock: current balances, slow-moving items (no issue in N days),
   consumption by site/month.
5. Freight: cost per site/route/month; % of material cost.
6. Machinery: utilisation (hours logged vs available), fuel per hour trend,
   hire cost by site.
7. Monthly SCM pack: one command/button that emails a single Excel workbook
   with the sheets above to SCM Head — the Monday-morning file.

## Hardening checklist (step 28)

- Permission matrix test: script that logs in as each role and asserts
  allowed/denied for every URL.
- Backup: nightly pg_dump + uploaded-files sync to off-server storage;
  documented restore drill actually performed once.
- Performance pass: list views paginated and indexed (site, status, dates);
  no N+1 queries on dashboards (assert with django-debug-toolbar/nplusone).
- In-app help: one short "how to" page per workflow with phone screenshots.
- docs/DEPLOYMENT.md finalised: server setup, env vars, Tally connectivity,
  backup cron, upgrade procedure.
