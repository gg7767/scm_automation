import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from billing.models import VendorBill
from logistics.models import TransportTrip
from masters.models import ItemCategory, Site, Vendor

_ONE_PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tiny_file(name="doc.png"):
    return SimpleUploadedFile(name, _ONE_PX_PNG, content_type="image/png")


class TransportTripModelTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Transport")
        self.transporter = Vendor.objects.create(name="Fast Movers")
        self.transporter.categories.add(self.category)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")

    def test_trip_number_format(self):
        trip = TransportTrip.objects.create(
            vehicle_number="AP09AB1234", transporter=self.transporter, from_location="Vendor warehouse",
            to_site=self.site, freight_amount=Decimal("1500.00"), created_by=self.user,
        )
        self.assertTrue(trip.trip_number.startswith(f"TRP/{self.site.code}/"))

    def test_default_billable_to_company(self):
        trip = TransportTrip.objects.create(
            vehicle_number="AP09AB1234", transporter=self.transporter, from_location="Factory",
            to_site=self.site, freight_amount=Decimal("500.00"), created_by=self.user,
        )
        self.assertEqual(trip.billable_to, TransportTrip.BillableTo.COMPANY)


class FreightReportTests(TestCase):
    def setUp(self):
        self.site1 = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        self.category = ItemCategory.objects.create(name="Transport")
        self.transporter = Vendor.objects.create(name="Fast Movers")
        self.transporter.categories.add(self.category)
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")

        TransportTrip.objects.create(
            vehicle_number="V1", transporter=self.transporter, from_location="A", to_site=self.site1,
            freight_amount=Decimal("1000.00"), created_by=self.user,
        )
        TransportTrip.objects.create(
            vehicle_number="V2", transporter=self.transporter, from_location="B", to_site=self.site2,
            freight_amount=Decimal("500.00"), created_by=self.user, pod_photo=_tiny_file(),
        )

    def test_freight_report_aggregates_by_site(self):
        response = self.client.get(reverse("logistics:freight_report"))
        self.assertContains(response, "1000.00")
        self.assertContains(response, "500.00")

    def test_trips_without_pod_listed(self):
        trip_without_pod = TransportTrip.objects.get(vehicle_number="V1")  # no pod_photo set in setUp
        response = self.client.get(reverse("logistics:freight_report"))
        self.assertContains(response, trip_without_pod.trip_number)

    def test_missing_pod_filter(self):
        response = self.client.get(reverse("logistics:trip_list"), {"without_pod": "1"})
        trips = list(response.context["trips"])
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0].to_site, self.site1)


class TripBasedBillTwoWayMatchTests(TestCase):
    """Freight bills flow through billing with a 2-way match (trip freight
    vs bill total), per docs/PHASE4_INVENTORY_TRANSPORT_MACHINERY.md."""

    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Transport")
        self.transporter = Vendor.objects.create(name="Fast Movers", payment_terms_days=15)
        self.transporter.categories.add(self.category)
        self.user = User.objects.create_user(username="accountant", password="pass12345")
        self.trip1 = TransportTrip.objects.create(
            vehicle_number="V1", transporter=self.transporter, from_location="A", to_site=self.site,
            freight_amount=Decimal("1000.00"), created_by=self.user,
        )
        self.trip2 = TransportTrip.objects.create(
            vehicle_number="V2", transporter=self.transporter, from_location="B", to_site=self.site,
            freight_amount=Decimal("500.00"), created_by=self.user,
        )

    def _make_bill(self, other_charges=Decimal("1500.00")):
        bill = VendorBill(
            vendor=self.transporter, site=self.site, vendor_invoice_number="FRT-1",
            vendor_invoice_date=datetime.date(2026, 1, 5), invoice_scan=_tiny_file(),
            other_charges=other_charges, created_by=self.user,
        )
        bill.save()
        bill.transport_trips.set([self.trip1, self.trip2])
        bill.recompute_totals()
        return bill

    def test_matching_freight_total_approves(self):
        bill = self._make_bill(other_charges=Decimal("1500.00"))
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)

    def test_mismatched_freight_total_flags_mismatch(self):
        bill = self._make_bill(other_charges=Decimal("2000.00"))  # trips only total 1500
        bill.submit_for_matching(self.user)
        self.assertEqual(bill.status, VendorBill.Status.MISMATCH)

    def test_bill_without_po_or_trips_cannot_be_matched(self):
        from billing.models import InvalidStatusTransition
        bill = VendorBill(
            vendor=self.transporter, site=self.site, vendor_invoice_number="FRT-2",
            vendor_invoice_date=datetime.date(2026, 1, 5), invoice_scan=_tiny_file(), created_by=self.user,
        )
        bill.save()
        with self.assertRaises(InvalidStatusTransition):
            bill.submit_for_matching(self.user)

    def test_trip_based_bill_does_not_crash_on_maybe_close_po(self):
        """Regression: _maybe_close_po must not assume self.po exists."""
        bill = self._make_bill()
        bill.submit_for_matching(self.user)  # should not raise AttributeError
        self.assertEqual(bill.status, VendorBill.Status.APPROVED_FOR_PAYMENT)
