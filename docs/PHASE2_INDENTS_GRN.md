# Phase 2 — Indents & GRN (Goods Receipt)

Read CLAUDE.md first. This phase adds the site-facing half of procurement:
sites request material (indents), head office converts them to POs, and
deliveries are recorded against POs (GRNs). Both indent and GRN screens are
used on phones at sites — mobile-first is mandatory.

## indents app

**Indent**
- indent_number (auto IND/{site.code}/{FY}/{seq})
- site (FK), raised_by (user), project_name (optional)
- required_by_date, priority [normal, urgent], remarks
- status: [draft, pending_approval, approved, partially_ordered, ordered,
  rejected, cancelled] — transitions via model methods only
- approved_by/at, rejection_reason

**IndentLine**
- indent (FK), item (FK), quantity, unit (from item), purpose (short text)
- qty_ordered (Decimal, default 0 — incremented when converted to PO lines)
- line status derived: open / partially_ordered / ordered

Rules:
- Site Members can create/edit only their own site's draft indents.
- Approval reuses ApprovalRule with doc_type=indent. Indents have no amounts,
  so indent rules match on estimated value: line qty × latest rate-contract
  rate or last PO rate for that item (fallback 0 → lowest rule). Show the
  estimate on the approval screen, clearly labelled "estimated".
- Urgent indents notify approvers immediately (email now; keep a Notification
  model so WhatsApp/SMS can be added later without schema change).

## Indent → PO conversion (HO side)

- Purchase Officer opens an approved indent, selects lines (full or partial
  quantities), picks a vendor → system creates a draft PO pre-filled with
  those lines (rates from rate contract when available) and sets
  po.source_indent.
- Multiple indents from the same site for the same vendor can be merged into
  one PO; a PO line can reference which indent line it fulfils
  (POLine.indent_line FK, nullable).
- On PO approval, increment qty_ordered on the linked indent lines and roll
  up indent status (partially_ordered / ordered).
- Indent lines can be individually rejected with a reason ("use stock at
  site", "duplicate") which closes them without ordering.

## stores app — GRN

**GRN**
- grn_number (auto GRN/{site.code}/{FY}/{seq})
- po (FK), site (auto = po.site), received_by (user), received_date
- vehicle_number (optional), challan_number, challan_date
- challan_photo (image upload, required), remarks
- status: [draft, submitted] — submitted GRNs are immutable; corrections via
  a reversal GRN (negative quantities) created by HO only.

**GRNLine**
- grn (FK), po_line (FK), qty_received, qty_accepted, qty_rejected
  (rejected = damaged/short-supplied), rejection_reason
- Validation: cumulative qty_received across GRNs must not exceed
  po_line.quantity by more than the over-receipt tolerance (setting,
  default 2%); beyond that, block and tell the user to get the PO amended.

Effects of submitting a GRN:
- Update po_line.qty_received; recompute PO status: any line partially
  received → partially_delivered; all lines fully received → mark PO
  deliverable-complete (PO closes fully in Phase 3 after billing, so add
  boolean po.delivery_complete instead of forcing status=closed here).
- Write StockLedger entries in Phase 4 — for now, emit a signal/service call
  stub `record_receipt(grn)` so Phase 4 can hook in without touching GRN code.
- Rejected quantities create a pending "debit note candidate" record for
  Phase 3 (simple model DebitNoteCandidate: grn_line, qty, reason, resolved).

## UI requirements

- Site home screen: big buttons — "Raise Indent", "Record Delivery (GRN)",
  "My Indents", "Expected Deliveries" (approved/sent POs for this site with
  expected dates).
- GRN entry: pick from open POs for this site → lines pre-filled with
  remaining quantities → adjust received/accepted → snap challan photo →
  submit. Must be comfortable one-handed on a phone at a gate.
- Indent entry: item search with alias support, quantity, required-by date.
  Max 3 taps between opening the app and typing the first item.

## Dashboards added in this phase

- HO: open indents by site/age, indents awaiting conversion, POs past
  expected delivery with days overdue, over/short receipt log.
- Site: my indent statuses, expected deliveries this week.

## Tests that must exist

- Indent status machine (all legal/illegal transitions).
- Partial conversion math (qty_ordered roll-ups, merged indents).
- GRN over-receipt tolerance blocking.
- PO status roll-up from multiple partial GRNs.
- Permission walls: site A user cannot see/create anything for site B.
