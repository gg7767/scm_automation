import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from billing.models import (
    BillRemark,
    DebitNote,
    InvalidStatusTransition,
    Payment,
    PaymentAllocation,
    VendorBill,
    VendorBillLine,
    VendorOpeningBalance,
)
from billing.services import payables_aging, vendor_ledger_entries
from masters.models import Item, ItemCategory, Site, Vendor
from purchase.models import PurchaseOrder, PurchaseOrderLine
from stores.models import DebitNoteCandidate, GRN, GRNLine

_ONE_PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tiny_file(name="doc.png"):
    return SimpleUploadedFile(name, _ONE_PX_PNG, content_type="image/png")


class BillingTestBase(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders", payment_terms_days=30)
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG, gst_rate=Decimal("18.00"))
        self.user = User.objects.create_user(username="accountant", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Accounts"))

        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, status=PurchaseOrder.Status.SENT)
        self.po_line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("100"), rate=Decimal("10.00"), gst_rate=Decimal("18.00"),
        )
        self._receive(Decimal("100"), Decimal("100"))

    def _receive(self, received, accepted, rejected=Decimal("0")):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_file("challan.png"),
        )
        GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=received, qty_accepted=accepted, qty_rejected=rejected)
        grn.submit(self.user)
        return grn

    def _make_bill(self, qty_billed=Decimal("100"), rate_billed=Decimal("10.00"), gst_rate=Decimal("18.00"), invoice_number="INV-1"):
        bill = VendorBill.objects.create(
            vendor=self.vendor, po=self.po, vendor_invoice_number=invoice_number,
            vendor_invoice_date=datetime.date(2026, 1, 5), invoice_scan=_tiny_file("bill.png"), created_by=self.user,
        )
        VendorBillLine.objects.create(
            bill=bill, po_line=self.po_line, quantity_billed=qty_billed, rate_billed=rate_billed, gst_rate=gst_rate,
        )
        return bill


class DuplicateInvoiceTests(BillingTestBase):
    def test_duplicate_invoice_same_fy_blocked(self):
        self._make_bill(invoice_number="INV-100")
        with self.assertRaises(ValidationError):
            dup = VendorBill(
                vendor=self.vendor, po=self.po, vendor_invoice_number="INV-100",
                vendor_invoice_date=datetime.date(2026, 2, 1), invoice_scan=_tiny_file(), created_by=self.user,
            )
            dup.full_clean(exclude=["bill_number", "financial_year", "site"])

    def test_same_invoice_number_different_fy_allowed(self):
        self._make_bill(invoice_number="INV-100")
        other = VendorBill(
            vendor=self.vendor, po=self.po, vendor_invoice_number="INV-100",
            vendor_invoice_date=datetime.date(2025, 2, 1), invoice_scan=_tiny_file(), created_by=self.user,
        )
        other.full_clean(exclude=["bill_number", "financial_year", "site"])  # should not raise

    def test_different_vendor_same_invoice_number_allowed(self):
        self._make_bill(invoice_number="INV-100")
        vendor2 = Vendor.objects.create(name="XYZ Traders")
        other = VendorBill(
            vendor=vendor2, po=self.po, vendor_invoice_number="INV-100",
            vendor_invoice_date=datetime.date(2026, 2, 1), invoice_scan=_tiny_file(), created_by=self.user,
        )
        other.full_clean(exclude=["bill_number", "financial_year", "site"])  # should not raise


class OverBillingTests(BillingTestBase):
    def test_billing_within_available_qty_matches(self):
        bill = self._make_bill(qty_billed=Decimal("100"))
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_already_billed, Decimal("100.000"))

    def test_overbilling_beyond_accepted_qty_mismatches(self):
        bill = self._make_bill(qty_billed=Decimal("150"))  # only 100 accepted
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.MISMATCH)
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_already_billed, Decimal("0.000"))  # not applied

    def test_cannot_bill_same_delivery_twice(self):
        bill1 = self._make_bill(qty_billed=Decimal("100"), invoice_number="INV-1")
        bill1.submit_for_matching(self.user)
        # A second bill trying to claim the same (already-billed) quantity should mismatch.
        bill2 = self._make_bill(qty_billed=Decimal("50"), invoice_number="INV-2")
        bill2.submit_for_matching(self.user)
        self.assertEqual(bill2.status, VendorBill.Status.MISMATCH)


class MatchEngineFailureModeTests(BillingTestBase):
    """Each failure mode (qty, rate, gst, total) independently."""

    def test_qty_failure(self):
        bill = self._make_bill(qty_billed=Decimal("101"))  # 1 more than accepted
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.MISMATCH)
        line_result = list(bill.match_result.values())[0]
        self.assertFalse(line_result["qty_ok"])
        self.assertTrue(line_result["rate_ok"])
        self.assertTrue(line_result["gst_ok"])

    def test_rate_failure(self):
        bill = self._make_bill(rate_billed=Decimal("11.00"))  # po rate is 10.00
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.MISMATCH)
        line_result = list(bill.match_result.values())[0]
        self.assertFalse(line_result["rate_ok"])

    def test_gst_failure(self):
        bill = self._make_bill(gst_rate=Decimal("28.00"))  # po gst_rate is 18.00
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.MISMATCH)
        line_result = list(bill.match_result.values())[0]
        self.assertFalse(line_result["gst_ok"])

    def test_total_failure(self):
        bill = self._make_bill()
        bill.round_off = Decimal("50.00")  # throws off the recomputed total by more than tolerance
        bill.save()
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.MISMATCH)
        self.assertFalse(bill.match_result["totals"]["total_ok"])

    def test_all_checks_pass_matches_and_approves(self):
        bill = self._make_bill()
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)
        for key, line in bill.match_result.items():
            if key != "totals":
                self.assertTrue(line["line_ok"])


class MismatchOverrideTests(BillingTestBase):
    def test_override_requires_reason(self):
        bill = self._make_bill(rate_billed=Decimal("20.00"))
        bill.submit_for_matching(self.user)
        with self.assertRaises(InvalidStatusTransition):
            bill.override_mismatch(self.user, "")

    def test_override_approves_and_applies_billed_qty(self):
        bill = self._make_bill(rate_billed=Decimal("20.00"))
        bill.submit_for_matching(self.user)
        bill.override_mismatch(self.user, "vendor confirmed rate change verbally")
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_already_billed, Decimal("100.000"))

    def test_cannot_override_non_mismatch(self):
        bill = self._make_bill()
        with self.assertRaises(InvalidStatusTransition):
            bill.override_mismatch(self.user, "reason")


class BillCancelTests(BillingTestBase):
    def test_cancel_requires_reason(self):
        bill = self._make_bill()
        with self.assertRaises(InvalidStatusTransition):
            bill.cancel(self.user, "")

    def test_cancel_from_draft(self):
        bill = self._make_bill()
        bill.cancel(self.user, "duplicate entry")
        self.assertEqual(bill.status, VendorBill.Status.CANCELLED)

    def test_cannot_cancel_paid_bill(self):
        bill = self._make_bill()
        bill.status = VendorBill.Status.PAID
        bill.save(update_fields=["status"])
        with self.assertRaises(InvalidStatusTransition):
            bill.cancel(self.user, "reason")


class POClosureOnFullBillingTests(BillingTestBase):
    def test_po_closes_when_fully_delivered_and_billed(self):
        self.po.refresh_from_db()
        self.assertTrue(self.po.delivery_complete)
        bill = self._make_bill(qty_billed=Decimal("100"))
        bill.submit_for_matching(self.user)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)

    def test_po_not_closed_if_not_fully_billed(self):
        bill = self._make_bill(qty_billed=Decimal("60"))
        bill.submit_for_matching(self.user)
        self.po.refresh_from_db()
        self.assertNotEqual(self.po.status, PurchaseOrder.Status.CLOSED)


class PaymentAllocationTests(BillingTestBase):
    def setUp(self):
        super().setUp()
        self.bill = self._make_bill(qty_billed=Decimal("100"))
        self.bill.submit_for_matching(self.user)  # -> approved_for_payment, grand_total = 100*10*1.18 = 1180

    def test_full_payment_marks_bill_paid(self):
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("1180.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        PaymentAllocation.objects.create(payment=payment, bill=self.bill, amount=Decimal("1180.00"))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, VendorBill.Status.PAID)
        self.assertEqual(self.bill.balance_due, Decimal("0.00"))

    def test_partial_payment_marks_bill_partially_paid(self):
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("500.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        PaymentAllocation.objects.create(payment=payment, bill=self.bill, amount=Decimal("500.00"))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, VendorBill.Status.PARTIALLY_PAID)
        self.assertEqual(self.bill.balance_due, Decimal("680.00"))

    def test_multi_bill_allocation_from_one_payment(self):
        bill2 = self._make_bill(qty_billed=Decimal("0"), invoice_number="INV-2")
        # give bill2 its own small line so it has a grand_total to pay against
        bill2.other_charges = Decimal("200.00")
        bill2.save()
        bill2.recompute_totals()
        bill2.status = VendorBill.Status.APPROVED_FOR_PAYMENT
        bill2.save(update_fields=["status"])

        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("1380.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        PaymentAllocation.objects.create(payment=payment, bill=self.bill, amount=Decimal("1180.00"))
        PaymentAllocation.objects.create(payment=payment, bill=bill2, amount=Decimal("200.00"))

        self.assertEqual(payment.unallocated_amount, Decimal("0.00"))
        self.bill.refresh_from_db()
        bill2.refresh_from_db()
        self.assertEqual(self.bill.status, VendorBill.Status.PAID)
        self.assertEqual(bill2.status, VendorBill.Status.PAID)

    def test_cannot_allocate_more_than_unallocated_payment(self):
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("100.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        allocation = PaymentAllocation(payment=payment, bill=self.bill, amount=Decimal("200.00"))
        with self.assertRaises(ValidationError):
            allocation.full_clean()

    def test_cannot_allocate_more_than_bill_balance(self):
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("5000.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        allocation = PaymentAllocation(payment=payment, bill=self.bill, amount=Decimal("2000.00"))
        with self.assertRaises(ValidationError):
            allocation.full_clean()

    def test_advance_payment_allocated_later(self):
        advance = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("1180.00"), payment_date=datetime.date(2026, 1, 1),
            mode=Payment.Mode.NEFT, payment_type=Payment.PaymentType.ADVANCE, created_by=self.user,
        )
        self.assertEqual(advance.unallocated_amount, Decimal("1180.00"))
        PaymentAllocation.objects.create(payment=advance, bill=self.bill, amount=Decimal("1180.00"))
        self.assertEqual(advance.unallocated_amount, Decimal("0.00"))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, VendorBill.Status.PAID)

    def test_tds_reduces_net_amount(self):
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("1180.00"), tds_amount=Decimal("18.00"),
            payment_date=datetime.date(2026, 2, 1), mode=Payment.Mode.NEFT, created_by=self.user,
        )
        self.assertEqual(payment.net_amount, Decimal("1162.00"))
        self.assertEqual(payment.unallocated_amount, Decimal("1162.00"))

    def test_deleting_allocation_reverts_bill_status(self):
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("1180.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        allocation = PaymentAllocation.objects.create(payment=payment, bill=self.bill, amount=Decimal("1180.00"))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, VendorBill.Status.PAID)
        allocation.delete()
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)


class AgingBucketTests(BillingTestBase):
    def _bill_due(self, days_ago, amount=Decimal("1000.00")):
        bill = self._make_bill(invoice_number=f"INV-{days_ago}")
        bill.due_date = datetime.date.today() - datetime.timedelta(days=days_ago)
        bill.status = VendorBill.Status.APPROVED_FOR_PAYMENT
        bill.grand_total = amount
        bill.save()
        return bill

    def test_bucket_0_30(self):
        self._bill_due(15)
        data = payables_aging()
        self.assertEqual(data["buckets"]["0-30"], Decimal("1000.00"))

    def test_bucket_boundary_30_vs_31(self):
        self._bill_due(30)
        data = payables_aging()
        self.assertEqual(data["buckets"]["0-30"], Decimal("1000.00"))
        self.assertEqual(data["buckets"]["31-60"], Decimal("0.00"))

    def test_bucket_31_60(self):
        self._bill_due(45)
        data = payables_aging()
        self.assertEqual(data["buckets"]["31-60"], Decimal("1000.00"))

    def test_bucket_61_90(self):
        self._bill_due(75)
        data = payables_aging()
        self.assertEqual(data["buckets"]["61-90"], Decimal("1000.00"))

    def test_bucket_90_plus(self):
        self._bill_due(120)
        data = payables_aging()
        self.assertEqual(data["buckets"]["90+"], Decimal("1000.00"))

    def test_paid_bills_excluded(self):
        bill = self._bill_due(45)
        bill.status = VendorBill.Status.PAID
        bill.save()
        data = payables_aging()
        self.assertEqual(data["total"], Decimal("0.00"))

    def test_site_filter(self):
        site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        po2 = PurchaseOrder.objects.create(vendor=self.vendor, site=site2, status=PurchaseOrder.Status.SENT)
        po_line2 = PurchaseOrderLine.objects.create(po=po2, item=self.item, quantity=Decimal("10"), rate=Decimal("10"), gst_rate=Decimal("18"))
        bill2 = VendorBill.objects.create(
            vendor=self.vendor, po=po2, vendor_invoice_number="INV-SITE2",
            vendor_invoice_date=datetime.date(2026, 1, 1), invoice_scan=_tiny_file(), created_by=self.user,
            status=VendorBill.Status.APPROVED_FOR_PAYMENT, grand_total=Decimal("500.00"),
            due_date=datetime.date.today() - datetime.timedelta(days=10),
        )
        self._bill_due(10)
        data = payables_aging(site=self.site)
        self.assertEqual(data["total"], Decimal("1000.00"))


class VendorLedgerTests(BillingTestBase):
    def test_ledger_includes_opening_balance(self):
        VendorOpeningBalance.objects.create(vendor=self.vendor, amount=Decimal("500.00"), as_of_date=datetime.date(2025, 4, 1))
        entries = vendor_ledger_entries(self.vendor)
        self.assertEqual(entries[0]["type"], "Opening balance")
        self.assertEqual(entries[0]["running_balance"], Decimal("500.00"))

    def test_ledger_running_balance_with_bill_and_payment(self):
        bill = self._make_bill()
        bill.submit_for_matching(self.user)
        payment = Payment.objects.create(
            vendor=self.vendor, amount=Decimal("500.00"), payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        PaymentAllocation.objects.create(payment=payment, bill=bill, amount=Decimal("500.00"))
        entries = vendor_ledger_entries(self.vendor)
        final_balance = entries[-1]["running_balance"]
        self.assertEqual(final_balance, bill.grand_total - Decimal("500.00"))

    def test_adjusted_debit_note_reduces_balance(self):
        bill = self._make_bill()
        bill.submit_for_matching(self.user)
        dn = DebitNote.objects.create(vendor=self.vendor, amount=Decimal("100.00"), reason="damaged goods", created_by=self.user)
        dn.adjust()
        entries = vendor_ledger_entries(self.vendor)
        types = [e["type"] for e in entries]
        self.assertIn("Debit note", types)

    def test_open_debit_note_excluded_from_ledger(self):
        DebitNote.objects.create(vendor=self.vendor, amount=Decimal("100.00"), reason="pending review", created_by=self.user)
        entries = vendor_ledger_entries(self.vendor)
        types = [e["type"] for e in entries]
        self.assertNotIn("Debit note", types)


class DebitNoteCandidateConversionTests(BillingTestBase):
    def test_create_debit_note_from_rejected_grn_candidate(self):
        # base setUp's po_line is already fully received (100/100, no
        # over-receipt headroom) — use a fresh line with room to receive.
        other_line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("50"), rate=Decimal("10.00"), gst_rate=Decimal("18.00"),
        )
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-2",
            challan_date=datetime.date(2026, 1, 2), challan_photo=_tiny_file("challan2.png"),
        )
        GRNLine.objects.create(grn=grn, po_line=other_line, qty_received=Decimal("10"), qty_accepted=Decimal("8"), qty_rejected=Decimal("2"))
        grn.submit(self.user)
        candidate = DebitNoteCandidate.objects.filter(grn_line__grn=grn).first()
        self.assertIsNotNone(candidate)
        self.assertFalse(candidate.resolved)

        self.client.login(username="accountant", password="pass12345")
        response = self.client.post(reverse("billing:debit_note_from_candidate", args=[candidate.pk]))
        self.assertRedirects(response, reverse("stores:grn_detail", args=[grn.pk]))
        candidate.refresh_from_db()
        self.assertTrue(candidate.resolved)
        self.assertEqual(DebitNote.objects.filter(vendor=self.vendor).count(), 1)


class BillPermissionTests(BillingTestBase):
    def test_purchase_officer_cannot_access_billing(self):
        officer = User.objects.create_user(username="po", password="pass12345")
        officer.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")
        response = self.client.get(reverse("billing:bill_list"))
        self.assertEqual(response.status_code, 403)

    def test_accounts_can_access_billing(self):
        self.client.login(username="accountant", password="pass12345")
        response = self.client.get(reverse("billing:bill_list"))
        self.assertEqual(response.status_code, 200)

    def test_accounts_cannot_override_mismatch(self):
        bill = self._make_bill(rate_billed=Decimal("20.00"))
        bill.submit_for_matching(self.user)
        self.client.login(username="accountant", password="pass12345")
        response = self.client.post(reverse("billing:bill_override", args=[bill.pk]), {"text": "reason"})
        self.assertEqual(response.status_code, 403)

    def test_scm_head_can_override_mismatch(self):
        bill = self._make_bill(rate_billed=Decimal("20.00"))
        bill.submit_for_matching(self.user)
        head = User.objects.create_user(username="head", password="pass12345")
        head.groups.add(Group.objects.create(name="SCM Head"))
        self.client.login(username="head", password="pass12345")
        response = self.client.post(reverse("billing:bill_override", args=[bill.pk]), {"text": "confirmed with vendor"})
        self.assertEqual(response.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)

    def test_anonymous_redirected(self):
        response = self.client.get(reverse("billing:bill_list"))
        self.assertEqual(response.status_code, 302)


class BillViewFlowTests(BillingTestBase):
    def setUp(self):
        super().setUp()
        self.client.login(username="accountant", password="pass12345")

    def test_create_bill_via_view(self):
        response = self.client.post(reverse("billing:bill_create") + f"?po={self.po.pk}", {
            "po": self.po.pk, "vendor_invoice_number": "INV-VIEW-1", "vendor_invoice_date": "2026-01-05",
            "invoice_scan": _tiny_file(), "other_charges": "0", "round_off": "0",
        })
        bill = VendorBill.objects.get(vendor_invoice_number="INV-VIEW-1")
        self.assertRedirects(response, reverse("billing:bill_detail", args=[bill.pk]))
        self.assertEqual(bill.lines.count(), 1)
        self.assertEqual(bill.lines.first().quantity_billed, Decimal("100.000"))

    def test_match_via_view(self):
        bill = self._make_bill()
        self.client.post(reverse("billing:bill_match", args=[bill.pk]))
        bill.refresh_from_db()
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)

    def test_remark_via_view(self):
        bill = self._make_bill()
        self.client.post(reverse("billing:bill_remark_create", args=[bill.pk]), {"text": "sent back to vendor for correction"})
        self.assertEqual(BillRemark.objects.filter(bill=bill).count(), 1)

    def test_allocate_payment_via_view(self):
        """Regression: PaymentAllocationCreateView must set `payment` on the
        form's instance before validation, since PaymentAllocation.clean()
        reads self.payment.unallocated_amount."""
        bill = self._make_bill()
        bill.submit_for_matching(self.user)
        payment = Payment.objects.create(
            vendor=self.vendor, amount=bill.grand_total, payment_date=datetime.date(2026, 2, 1),
            mode=Payment.Mode.NEFT, created_by=self.user,
        )
        response = self.client.post(reverse("billing:payment_allocate", args=[payment.pk]), {
            "bill": bill.pk, "amount": str(bill.grand_total),
        })
        self.assertRedirects(response, reverse("billing:payment_detail", args=[payment.pk]))
        bill.refresh_from_db()
        self.assertEqual(bill.status, VendorBill.Status.PAID)
