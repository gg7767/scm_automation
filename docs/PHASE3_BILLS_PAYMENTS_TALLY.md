# Phase 3 — Bills, 3-Way Match, Payments & Tally Sync

Read CLAUDE.md first. This phase closes the money loop: vendor invoices are
entered, matched against PO + GRN, approved, paid, and pushed to Tally.
Primary users: Accounts role, with SCM Head approving mismatches.

## billing app

**VendorBill**
- bill_number (auto BILL/{site.code}/{FY}/{seq}) — internal number
- vendor_invoice_number, vendor_invoice_date (the vendor's own numbering)
- vendor (FK), po (FK), site (auto = po.site)
- invoice_scan (file, required)
- amounts: subtotal, gst_amount (with cgst/sgst/igst split), other_charges
  (freight/loading if on invoice), round_off, grand_total
- status: [draft, matched, mismatch, approved_for_payment, partially_paid,
  paid, cancelled]
- match_result (JSON: per-line comparison snapshot), matched_at
- approved_by/at (for mismatch overrides), override_reason
- due_date = vendor_invoice_date + vendor.payment_terms_days (editable)
- Duplicate guard: unique (vendor, vendor_invoice_number, FY) — block
  duplicate invoice entry outright with a clear message.

**VendorBillLine**
- bill (FK), po_line (FK), quantity_billed, rate_billed, gst_rate, line_total

**Payment**
- payment_number (auto PAY/HO/{FY}/{seq})
- vendor (FK), amount, payment_date, mode [neft, rtgs, imps, cheque, upi, cash],
  reference_number (UTR/cheque no), remarks
- type: [against_bills, advance]
- PaymentAllocation: payment (FK), bill (FK), amount — one payment can cover
  several bills; partial allocations allowed; unallocated advance sits on the
  vendor and can be allocated to future bills.
- tds_amount (nullable) if the company deducts TDS; net = amount − tds.

**DebitNote**
- from DebitNoteCandidate records (Phase 2 rejected quantities) or manual
- vendor, linked grn_line/bill, amount, reason, status [open, adjusted]
- Adjusted debit notes reduce the payable on allocation screens.

## 3-way match engine

For each bill line, compare against its po_line and cumulative accepted GRN
quantity:

| Check | Rule |
|---|---|
| Quantity | quantity_billed ≤ (qty_accepted − qty_already_billed) + qty tolerance |
| Rate | rate_billed within rate tolerance of po_line.rate |
| GST | gst_rate equals po_line.gst_rate |
| Totals | recomputed total equals bill.grand_total within ₹1 rounding |

- Tolerances are settings (defaults: qty 0%, rate 0%, i.e. exact; configurable
  per company later). Store qty_already_billed on po_line, updated on bill
  approval, so a vendor cannot bill the same delivery twice.
- All checks pass → status=matched → auto-advance to approved_for_payment.
- Any failure → status=mismatch with a side-by-side review screen
  (PO says / GRN says / Bill says, differences highlighted). SCM Head can
  override with a mandatory reason, or Accounts sends it back to the vendor
  (status stays mismatch; add a remarks thread on the bill).
- When all lines of a PO are fully billed and delivery_complete is true,
  auto-close the PO (status=closed).

## Vendor ledger (full) & aging

- Ledger per vendor: opening balance (imported), bills (+), debit notes (−),
  payments (−), advances, running balance. Exportable to Excel.
- Payables aging report: buckets 0–30 / 31–60 / 61–90 / 90+ by due_date,
  filter by site and category. This is the SCM Head's Monday-morning screen.

## Tally sync

Design: this system is the source of truth; Tally receives accounting entries.
Push, never pull. Tally's HTTP gateway accepts XML envelopes
(<ENVELOPE><HEADER><TALLYREQUEST>Import Data ... with Purchase and Payment
vouchers).

**TallySyncLog**
- doc_type [bill, payment, debit_note], doc_id, payload (XML text),
  status [pending, sent, accepted, failed], response_text, attempts,
  last_attempt_at

Rules:
- A bill becomes eligible for sync when approved_for_payment; a payment when
  saved. Eligible docs are queued (management command `tally_push`, run
  manually or via cron; also a "Push to Tally" button per doc and "Push all
  pending" for Accounts).
- Vendor mapping: vendor.tally_ledger_name must be set before sync; the
  mapping screen lists vendors missing it. Also map GST ledgers and purchase
  ledgers via a small TallyLedgerMap settings model (our category/gst-rate →
  Tally ledger name).
- Failures never block the app: log, show in a sync-status screen, allow
  retry. Include a "mark as manually entered" escape hatch (accounts typed it
  into Tally by hand) so the queue never wedges.
- Fallback exporter: same queue can emit a CSV/XML file for manual Tally
  import if the gateway is unreachable from the server (common when Tally
  runs on an office desktop — document the ngrok/static-IP options in
  docs/DEPLOYMENT.md).

## Tests that must exist

- Duplicate invoice blocking; over-billing blocked via qty_already_billed.
- Match engine: each failure mode independently (qty, rate, gst, total).
- Partial payments and multi-bill allocation math; advance allocation.
- Aging bucket math around boundary dates.
- Tally XML generation golden-file tests (stable expected XML per doc type).
