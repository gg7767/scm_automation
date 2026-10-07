import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from masters.models import Item, ItemCategory, Site, Vendor
from purchase.models import PurchaseOrder, PurchaseOrderLine
from stores.models import (
    DebitNoteCandidate,
    GRN,
    GRNLine,
    InvalidStatusTransition,
    SiteItemSetting,
    StockBalance,
    StockIssue,
    StockIssueLine,
    StockLedger,
    StockTransfer,
)
from stores.services import dispatch_transfer, populate_lines_from_po


# A real 1x1 transparent PNG — Django's ImageField validates actual image
# content via Pillow, so fake bytes get rejected as "not an image".
_ONE_PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tiny_photo():
    return SimpleUploadedFile("challan.png", _ONE_PX_PNG, content_type="image/png")


class GRNSubmitTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")

        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        self.po_line = PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("100"), rate=Decimal("10"))
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=["status"])

    def _make_grn(self, received_qty, accepted_qty, rejected_qty=Decimal("0")):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        GRNLine.objects.create(
            grn=grn, po_line=self.po_line, qty_received=received_qty, qty_accepted=accepted_qty, qty_rejected=rejected_qty,
        )
        return grn

    def test_grn_number_format(self):
        grn = self._make_grn(Decimal("50"), Decimal("50"))
        self.assertTrue(grn.grn_number.startswith(f"GRN/{self.site.code}/"))

    def test_submit_updates_po_line_qty_received(self):
        grn = self._make_grn(Decimal("50"), Decimal("50"))
        grn.submit(self.user)
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("50.000"))

    def test_partial_receipt_moves_po_to_partially_delivered(self):
        grn = self._make_grn(Decimal("50"), Decimal("50"))
        grn.submit(self.user)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.PARTIALLY_DELIVERED)
        self.assertFalse(self.po.delivery_complete)

    def test_full_receipt_sets_delivery_complete(self):
        grn = self._make_grn(Decimal("100"), Decimal("100"))
        grn.submit(self.user)
        self.po.refresh_from_db()
        self.assertTrue(self.po.delivery_complete)

    def test_multiple_partial_grns_accumulate(self):
        grn1 = self._make_grn(Decimal("40"), Decimal("40"))
        grn1.submit(self.user)
        self.po.refresh_from_db()
        self.assertFalse(self.po.delivery_complete)

        grn2 = self._make_grn(Decimal("60"), Decimal("60"))
        grn2.submit(self.user)
        self.po_line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("100.000"))
        self.assertTrue(self.po.delivery_complete)

    def test_cannot_submit_twice(self):
        grn = self._make_grn(Decimal("50"), Decimal("50"))
        grn.submit(self.user)
        with self.assertRaises(InvalidStatusTransition):
            grn.submit(self.user)

    def test_cannot_submit_grn_with_no_lines(self):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        with self.assertRaises(InvalidStatusTransition):
            grn.submit(self.user)

    def test_rejected_qty_creates_debit_note_candidate(self):
        grn = self._make_grn(Decimal("50"), Decimal("40"), Decimal("10"))
        grn.lines.first().rejection_reason = "damaged bags"
        grn.lines.first().save()
        grn.submit(self.user)
        self.assertEqual(DebitNoteCandidate.objects.count(), 1)
        candidate = DebitNoteCandidate.objects.first()
        self.assertEqual(candidate.qty, Decimal("10.000"))
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("40.000"))  # only accepted counts


class OverReceiptToleranceTests(TestCase):
    """Default tolerance is 2% per settings.GRN_OVER_RECEIPT_TOLERANCE_PERCENT."""

    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, status=PurchaseOrder.Status.SENT)
        self.po_line = PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("100"), rate=Decimal("10"))

    def _grn_with(self, qty):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=qty, qty_accepted=qty)
        return grn

    def test_within_tolerance_allowed(self):
        grn = self._grn_with(Decimal("102"))  # 2% over 100 = exactly at the boundary
        grn.submit(self.user)  # should not raise
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("102.000"))

    def test_beyond_tolerance_blocked(self):
        grn = self._grn_with(Decimal("105"))  # 5% over — beyond the 2% tolerance
        with self.assertRaises(InvalidStatusTransition):
            grn.submit(self.user)
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("0.000"))  # nothing applied

    def test_tolerance_applies_cumulatively_across_grns(self):
        grn1 = self._grn_with(Decimal("100"))
        grn1.submit(self.user)
        grn2 = self._grn_with(Decimal("5"))  # would bring total to 105, beyond 102 allowed
        with self.assertRaises(InvalidStatusTransition):
            grn2.submit(self.user)

    def test_reversal_bypasses_tolerance(self):
        grn1 = self._grn_with(Decimal("100"))
        grn1.submit(self.user)
        reversal = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-REV", is_reversal=True, reverses=grn1,
            challan_date=datetime.date(2026, 1, 2), challan_photo=_tiny_photo(),
        )
        GRNLine.objects.create(grn=reversal, po_line=self.po_line, qty_received=Decimal("-10"), qty_accepted=Decimal("-10"))
        reversal.submit(self.user)  # should not raise despite negative qty
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("90.000"))


class PopulateLinesFromPOTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, status=PurchaseOrder.Status.SENT)
        self.po_line = PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("100"), rate=Decimal("10"))

    def test_populates_pending_quantity(self):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        populate_lines_from_po(grn)
        line = grn.lines.first()
        self.assertEqual(line.qty_received, Decimal("100.000"))

    def test_fully_received_line_not_populated(self):
        self.po_line.qty_received = Decimal("100")
        self.po_line.save()
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        populate_lines_from_po(grn)
        self.assertEqual(grn.lines.count(), 0)


# --- Permission walls ---------------------------------------------------

class GRNPermissionTests(TestCase):
    def setUp(self):
        self.site1 = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")

        self.po1 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site1, status=PurchaseOrder.Status.SENT)
        self.po2 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site2, status=PurchaseOrder.Status.SENT)

        self.site1_user = User.objects.create_user(username="site1user", password="pass12345")
        self.site1_user.groups.add(Group.objects.create(name="Site Member"))
        self.site1_user.profile.site = self.site1
        self.site1_user.profile.save()

        self.site2_user = User.objects.create_user(username="site2user", password="pass12345")
        self.site2_user.groups.add(Group.objects.get(name="Site Member"))
        self.site2_user.profile.site = self.site2
        self.site2_user.profile.save()

        self.grn_site1 = GRN.objects.create(
            po=self.po1, received_by=self.site1_user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )

    def test_site_a_user_cannot_see_site_b_grn(self):
        self.client.login(username="site2user", password="pass12345")
        response = self.client.get(reverse("stores:grn_detail", args=[self.grn_site1.pk]))
        self.assertEqual(response.status_code, 404)

    def test_site_a_user_po_picker_excludes_site_b_pos(self):
        self.client.login(username="site1user", password="pass12345")
        response = self.client.get(reverse("stores:po_picker"))
        self.assertContains(response, self.po1.po_number)
        self.assertNotContains(response, self.po2.po_number)

    def test_site_member_cannot_create_reversal(self):
        self.client.login(username="site1user", password="pass12345")
        response = self.client.get(reverse("stores:grn_reversal_create") + f"?po={self.po1.pk}")
        self.assertEqual(response.status_code, 403)

    def test_purchase_officer_can_create_reversal(self):
        officer = User.objects.create_user(username="po", password="pass12345")
        officer.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")
        response = self.client.get(reverse("stores:grn_reversal_create") + f"?po={self.po1.pk}")
        self.assertEqual(response.status_code, 200)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("stores:grn_list"))
        self.assertEqual(response.status_code, 302)


class GRNViewFlowTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, status=PurchaseOrder.Status.SENT)
        self.po_line = PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("100"), rate=Decimal("10"))

        self.user = User.objects.create_user(username="site1user", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Site Member"))
        self.user.profile.site = self.site
        self.user.profile.save()
        self.client.login(username="site1user", password="pass12345")

    def test_create_grn_via_view(self):
        response = self.client.post(reverse("stores:grn_create") + f"?po={self.po.pk}", {
            "po": self.po.pk, "vehicle_number": "AP09AB1234", "challan_number": "CH-100",
            "challan_date": "2026-01-01", "challan_photo": _tiny_photo(), "remarks": "",
        })
        grn = GRN.objects.get(po=self.po)
        self.assertRedirects(response, reverse("stores:grn_detail", args=[grn.pk]))
        self.assertEqual(grn.lines.count(), 1)
        self.assertEqual(grn.lines.first().qty_received, Decimal("100.000"))

    def test_submit_via_view(self):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=Decimal("100"), qty_accepted=Decimal("100"))
        self.client.post(reverse("stores:grn_submit", args=[grn.pk]))
        grn.refresh_from_db()
        self.assertEqual(grn.status, GRN.Status.SUBMITTED)

    def test_submit_via_view_with_formset_data_saves_partial_qty(self):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        line = GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=Decimal("100"), qty_accepted=Decimal("100"))

        formset_data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": line.pk,
            "form-0-qty_received": "20.000",
            "form-0-qty_accepted": "20.000",
            "form-0-qty_rejected": "0.000",
            "form-0-rejection_reason": "",
        }
        response = self.client.post(reverse("stores:grn_submit", args=[grn.pk]), formset_data)
        self.assertRedirects(response, reverse("stores:grn_detail", args=[grn.pk]))

        line.refresh_from_db()
        self.assertEqual(line.qty_received, Decimal("20.000"))
        self.assertEqual(line.qty_accepted, Decimal("20.000"))

        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.qty_received, Decimal("20.000"))

        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.PARTIALLY_DELIVERED)
        self.assertFalse(self.po.delivery_complete)


# --- Phase 4: Inventory --------------------------------------------------

class GRNReceiptWritesStockLedgerTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, status=PurchaseOrder.Status.SENT)
        self.po_line = PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("100"), rate=Decimal("10"))

    def test_grn_submission_writes_ledger_and_updates_balance(self):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=Decimal("40"), qty_accepted=Decimal("40"))
        grn.submit(self.user)

        entry = StockLedger.objects.get(ref_doc_type="GRN", ref_doc_id=grn.pk)
        self.assertEqual(entry.qty, Decimal("40.000"))
        self.assertEqual(entry.txn_type, StockLedger.TxnType.GRN_RECEIPT)

        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("40.000"))

    def test_rejected_qty_not_added_to_stock(self):
        grn = GRN.objects.create(
            po=self.po, received_by=self.user, challan_number="CH-1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_photo(),
        )
        GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=Decimal("40"), qty_accepted=Decimal("30"), qty_rejected=Decimal("10"))
        grn.submit(self.user)
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("30.000"))


class StockIssueTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        from stores.models import write_stock_ledger_entry
        write_stock_ledger_entry(
            site=self.site, item=self.item, txn_date=datetime.date(2026, 1, 1),
            txn_type=StockLedger.TxnType.OPENING, qty=Decimal("50"),
        )

    def test_issue_number_format(self):
        issue = StockIssue.objects.create(site=self.site, issued_by=self.user, purpose=StockIssue.Purpose.PRODUCTION)
        self.assertTrue(issue.issue_number.startswith(f"ISS/{self.site.code}/"))

    def test_issue_within_stock_succeeds(self):
        issue = StockIssue.objects.create(site=self.site, issued_by=self.user, purpose=StockIssue.Purpose.PRODUCTION)
        StockIssueLine.objects.create(issue=issue, item=self.item, qty=Decimal("20"))
        issue.submit(self.user)
        self.assertEqual(issue.status, StockIssue.Status.SUBMITTED)
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("30.000"))

    def test_issue_beyond_stock_blocked_by_default(self):
        issue = StockIssue.objects.create(site=self.site, issued_by=self.user, purpose=StockIssue.Purpose.PRODUCTION)
        StockIssueLine.objects.create(issue=issue, item=self.item, qty=Decimal("100"))
        with self.assertRaises(InvalidStatusTransition):
            issue.submit(self.user)
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("50.000"))  # unchanged

    def test_issue_beyond_stock_allowed_when_setting_enabled(self):
        with self.settings(ALLOW_NEGATIVE_STOCK=True):
            issue = StockIssue.objects.create(site=self.site, issued_by=self.user, purpose=StockIssue.Purpose.PRODUCTION)
            StockIssueLine.objects.create(issue=issue, item=self.item, qty=Decimal("100"))
            issue.submit(self.user)
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("-50.000"))

    def test_cannot_submit_issue_with_no_lines(self):
        issue = StockIssue.objects.create(site=self.site, issued_by=self.user, purpose=StockIssue.Purpose.OTHER)
        with self.assertRaises(InvalidStatusTransition):
            issue.submit(self.user)


class StockTransferTests(TestCase):
    def setUp(self):
        self.site1 = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        from stores.models import write_stock_ledger_entry
        write_stock_ledger_entry(
            site=self.site1, item=self.item, txn_date=datetime.date(2026, 1, 1),
            txn_type=StockLedger.TxnType.OPENING, qty=Decimal("100"),
        )

    def test_dispatch_writes_transfer_out(self):
        transfer = dispatch_transfer(self.site1, self.site2, "AP09AB1234", self.user, {self.item: Decimal("30")})
        self.assertEqual(transfer.status, StockTransfer.Status.DISPATCHED)
        balance = StockBalance.objects.get(site=self.site1, item=self.item)
        self.assertEqual(balance.quantity, Decimal("70.000"))

    def test_dispatch_beyond_stock_blocked(self):
        with self.assertRaises(ValueError):
            dispatch_transfer(self.site1, self.site2, "AP09AB1234", self.user, {self.item: Decimal("200")})

    def test_full_receipt_writes_transfer_in(self):
        transfer = dispatch_transfer(self.site1, self.site2, "AP09AB1234", self.user, {self.item: Decimal("30")})
        line = transfer.lines.first()
        transfer.receive(self.user, {line: Decimal("30")})
        self.assertEqual(transfer.status, StockTransfer.Status.RECEIVED)
        balance = StockBalance.objects.get(site=self.site2, item=self.item)
        self.assertEqual(balance.quantity, Decimal("30.000"))

    def test_shortage_in_transit_logged(self):
        transfer = dispatch_transfer(self.site1, self.site2, "AP09AB1234", self.user, {self.item: Decimal("30")})
        line = transfer.lines.first()
        transfer.receive(self.user, {line: Decimal("25")})
        line.refresh_from_db()
        self.assertIn("Shortage in transit: 5", line.remarks)
        balance = StockBalance.objects.get(site=self.site2, item=self.item)
        self.assertEqual(balance.quantity, Decimal("25.000"))


class ReconcileStockBalancesTests(TestCase):
    def test_corrects_drifted_balance(self):
        from django.core.management import call_command
        from stores.models import write_stock_ledger_entry

        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        write_stock_ledger_entry(site=site, item=item, txn_date=datetime.date(2026, 1, 1), txn_type=StockLedger.TxnType.OPENING, qty=Decimal("50"))

        # simulate drift: someone/something corrupted the cache
        balance = StockBalance.objects.get(site=site, item=item)
        balance.quantity = Decimal("999.000")
        balance.save()

        call_command("reconcile_stock_balances")
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, Decimal("50.000"))

    def test_no_drift_no_correction_reported(self):
        from io import StringIO
        from django.core.management import call_command
        from stores.models import write_stock_ledger_entry

        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        write_stock_ledger_entry(site=site, item=item, txn_date=datetime.date(2026, 1, 1), txn_type=StockLedger.TxnType.OPENING, qty=Decimal("50"))

        out = StringIO()
        call_command("reconcile_stock_balances", stdout=out)
        self.assertIn("0 correction(s)", out.getvalue())


class CheckMinStockTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.site_member = User.objects.create_user(username="siteuser", password="pass12345", email="siteuser@example.com")
        self.site_member.groups.add(Group.objects.create(name="Site Member"))
        self.site_member.profile.site = self.site
        self.site_member.profile.save()
        SiteItemSetting.objects.create(site=self.site, item=self.item, min_stock_qty=Decimal("50"))

    def test_creates_draft_indent_when_below_minimum(self):
        from django.core.management import call_command
        from indents.models import Indent

        call_command("check_min_stock")
        indent = Indent.objects.filter(site=self.site, status=Indent.Status.DRAFT).first()
        self.assertIsNotNone(indent)
        self.assertEqual(indent.lines.first().item, self.item)

    def test_notifies_site_members(self):
        from django.core.management import call_command
        from accounts_stub.models import Notification

        call_command("check_min_stock")
        self.assertTrue(Notification.objects.filter(recipient=self.site_member).exists())

    def test_does_not_duplicate_on_second_run(self):
        from django.core.management import call_command
        from indents.models import Indent

        call_command("check_min_stock")
        call_command("check_min_stock")
        self.assertEqual(Indent.objects.filter(site=self.site, status=Indent.Status.DRAFT).count(), 1)

    def test_no_flag_when_stock_sufficient(self):
        from django.core.management import call_command
        from indents.models import Indent
        from stores.models import write_stock_ledger_entry

        write_stock_ledger_entry(site=self.site, item=self.item, txn_date=datetime.date(2026, 1, 1), txn_type=StockLedger.TxnType.OPENING, qty=Decimal("100"))
        call_command("check_min_stock")
        self.assertEqual(Indent.objects.filter(site=self.site).count(), 0)


class ImportOpeningStockTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)

    def _write_csv(self, tmp_path, rows):
        import csv
        with open(tmp_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["site_code", "item", "qty", "rate"])
            writer.writerows(rows)

    def test_import_creates_opening_entry(self):
        import tempfile
        from django.core.management import call_command

        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as tmp:
            path = tmp.name
        self._write_csv(path, [[self.site.code, self.item.code, "75", "10.50"]])

        call_command("import_opening_stock", path)
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("75.000"))

    def test_reimport_is_idempotent(self):
        import tempfile
        from django.core.management import call_command

        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as tmp:
            path = tmp.name
        self._write_csv(path, [[self.site.code, self.item.code, "75", "10.50"]])

        call_command("import_opening_stock", path)
        call_command("import_opening_stock", path)
        self.assertEqual(StockLedger.objects.filter(txn_type=StockLedger.TxnType.OPENING).count(), 1)
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("75.000"))


class StockIssueViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Site Member"))
        self.user.profile.site = self.site
        self.user.profile.save()
        self.client.login(username="siteuser", password="pass12345")
        from stores.models import write_stock_ledger_entry
        write_stock_ledger_entry(
            site=self.site, item=self.item, txn_date=datetime.date(2026, 1, 1),
            txn_type=StockLedger.TxnType.OPENING, qty=Decimal("50"),
        )

    def test_create_stock_issue_via_view(self):
        response = self.client.get(reverse("stores:stock_issue_create"))
        self.assertEqual(response.status_code, 200)

        formset_data = {
            "site": self.site.pk, "purpose": "production", "purpose_detail": "", "remarks": "",
            "form-TOTAL_FORMS": "5", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-item": self.item.pk, "form-0-qty": "10", "form-0-remarks": "",
            "form-1-item": "", "form-1-qty": "", "form-1-remarks": "",
            "form-2-item": "", "form-2-qty": "", "form-2-remarks": "",
            "form-3-item": "", "form-3-qty": "", "form-3-remarks": "",
            "form-4-item": "", "form-4-qty": "", "form-4-remarks": "",
        }
        response = self.client.post(reverse("stores:stock_issue_create"), formset_data)
        issue = StockIssue.objects.get(site=self.site)
        self.assertRedirects(response, reverse("stores:stock_issue_detail", args=[issue.pk]))
        self.assertEqual(issue.lines.count(), 1)

    def test_submit_stock_issue_via_view_reduces_balance(self):
        issue = StockIssue.objects.create(site=self.site, issued_by=self.user, purpose=StockIssue.Purpose.PRODUCTION)
        StockIssueLine.objects.create(issue=issue, item=self.item, qty=Decimal("10"))
        response = self.client.post(reverse("stores:stock_issue_submit", args=[issue.pk]))
        self.assertRedirects(response, reverse("stores:stock_issue_detail", args=[issue.pk]))
        balance = StockBalance.objects.get(site=self.site, item=self.item)
        self.assertEqual(balance.quantity, Decimal("40.000"))

    def test_stock_balance_list_view_renders(self):
        response = self.client.get(reverse("stores:stock_balance_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OPC 53")
