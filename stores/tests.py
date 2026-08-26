import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from masters.models import Item, ItemCategory, Site, Vendor
from purchase.models import PurchaseOrder, PurchaseOrderLine
from stores.models import DebitNoteCandidate, GRN, GRNLine, InvalidStatusTransition
from stores.services import populate_lines_from_po


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
