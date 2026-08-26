import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from assets.models import Machine, MachineDeployment, MachineLog
from assets.services import machinery_utilization
from billing.models import VendorBill, VendorBillLine
from billing.services import spend_analysis
from logistics.models import TransportTrip
from logistics.services import freight_by_site_transporter_month
from masters.models import Item, ItemCategory, Site, Vendor
from masters.services import vendor_performance_score
from purchase.models import PurchaseOrder, PurchaseOrderLine
from purchase.services import procurement_lead_time
from stores.models import GRN, GRNLine, StockLedger, write_stock_ledger_entry
from stores.services import consumption_by_site_month, slow_moving_items

_ONE_PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tiny_file(name="doc.png"):
    return SimpleUploadedFile(name, _ONE_PX_PNG, content_type="image/png")


class SpendAnalysisTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG, gst_rate=Decimal("18.00"))
        self.user = User.objects.create_user(username="accountant", password="pass12345")

        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, status=PurchaseOrder.Status.SENT)
        self.po_line = PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("10"), rate=Decimal("100"))
        grn = GRN.objects.create(po=self.po, received_by=self.user, challan_number="C1", challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_file())
        GRNLine.objects.create(grn=grn, po_line=self.po_line, qty_received=Decimal("10"), qty_accepted=Decimal("10"))
        grn.submit(self.user)

        self.bill = VendorBill.objects.create(
            vendor=self.vendor, po=self.po, vendor_invoice_number="INV-1", vendor_invoice_date=datetime.date(2026, 1, 5),
            invoice_scan=_tiny_file(), created_by=self.user,
        )
        VendorBillLine.objects.create(bill=self.bill, po_line=self.po_line, quantity_billed=Decimal("10"), rate_billed=Decimal("100"), gst_rate=Decimal("18"))
        self.bill.submit_for_matching(self.user)

    def test_spend_by_vendor_includes_matched_bill(self):
        rows = spend_analysis(group_by="vendor")
        self.assertEqual(rows[0]["label"], "ABC Traders")
        self.assertEqual(rows[0]["total"], self.bill.grand_total)

    def test_spend_by_month(self):
        rows = spend_analysis(group_by="month")
        self.assertEqual(len(rows), 1)

    def test_draft_bill_excluded(self):
        VendorBill.objects.create(
            vendor=self.vendor, po=self.po, vendor_invoice_number="INV-2", vendor_invoice_date=datetime.date(2026, 1, 6),
            invoice_scan=_tiny_file(), created_by=self.user,
        )
        rows = spend_analysis(group_by="vendor")
        self.assertEqual(len(rows), 1)  # only the matched bill counted


class ProcurementLeadTimeTests(TestCase):
    def test_lead_time_computed_for_indent_originated_po(self):
        from indents.models import Indent, IndentLine

        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        vendor = Vendor.objects.create(name="ABC Traders")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        user = User.objects.create_user(username="po", password="pass12345")

        indent = Indent.objects.create(site=site, raised_by=user, status=Indent.Status.APPROVED, approved_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        IndentLine.objects.create(indent=indent, item=item, quantity=Decimal("10"))

        po = PurchaseOrder.objects.create(vendor=vendor, site=site, source_indent=indent, sent_at=datetime.datetime(2026, 1, 5, tzinfo=datetime.timezone.utc))
        po_line = PurchaseOrderLine.objects.create(po=po, item=item, quantity=Decimal("10"), rate=Decimal("10"))
        grn = GRN.objects.create(
            po=po, received_by=user, challan_number="C1", challan_date=datetime.date(2026, 1, 10),
            received_date=datetime.date(2026, 1, 10), challan_photo=_tiny_file(),
        )
        GRNLine.objects.create(grn=grn, po_line=po_line, qty_received=Decimal("10"), qty_accepted=Decimal("10"))
        grn.submit(user)

        data = procurement_lead_time()
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row["indent_to_sent_days"], 4)
        self.assertEqual(row["sent_to_first_grn_days"], 5)
        self.assertEqual(row["sent_to_complete_days"], 5)

    def test_no_rows_when_no_indent_originated_pos(self):
        data = procurement_lead_time()
        self.assertEqual(data["rows"], [])
        self.assertIsNone(data["overall_median_sent_to_first_grn"])


class VendorPerformanceScoreTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="po", password="pass12345")

    def test_no_history_returns_none_score(self):
        result = vendor_performance_score(self.vendor)
        self.assertIsNone(result["score"])

    def test_on_time_delivery_computed(self):
        po = PurchaseOrder.objects.create(
            vendor=self.vendor, site=self.site, expected_delivery_date=datetime.date(2026, 1, 10),
        )
        po_line = PurchaseOrderLine.objects.create(po=po, item=self.item, quantity=Decimal("10"), rate=Decimal("10"))
        grn = GRN.objects.create(po=po, received_by=self.user, challan_number="C1", challan_date=datetime.date(2026, 1, 5), challan_photo=_tiny_file(), received_date=datetime.date(2026, 1, 5))
        GRNLine.objects.create(grn=grn, po_line=po_line, qty_received=Decimal("10"), qty_accepted=Decimal("10"))
        grn.submit(self.user)

        result = vendor_performance_score(self.vendor)
        self.assertEqual(result["on_time_delivery_pct"], 100)
        self.assertIsNotNone(result["score"])


class StockReportTests(TestCase):
    def test_slow_moving_item_flagged(self):
        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        write_stock_ledger_entry(site=site, item=item, txn_date=datetime.date(2026, 1, 1), txn_type=StockLedger.TxnType.OPENING, qty=Decimal("50"))

        slow = slow_moving_items(days=30)
        self.assertEqual(len(slow), 1)
        self.assertEqual(slow[0].item, item)

    def test_recently_issued_item_not_flagged(self):
        from django.utils import timezone
        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        write_stock_ledger_entry(site=site, item=item, txn_date=datetime.date(2026, 1, 1), txn_type=StockLedger.TxnType.OPENING, qty=Decimal("50"))
        write_stock_ledger_entry(site=site, item=item, txn_date=timezone.localdate(), txn_type=StockLedger.TxnType.ISSUE, qty=Decimal("-5"))

        slow = slow_moving_items(days=30)
        self.assertEqual(len(slow), 0)

    def test_consumption_by_site_month(self):
        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        write_stock_ledger_entry(site=site, item=item, txn_date=datetime.date(2026, 1, 15), txn_type=StockLedger.TxnType.ISSUE, qty=Decimal("-10"))
        rows = consumption_by_site_month()
        self.assertEqual(rows[0]["qty_issued"], Decimal("10"))


class FreightReportServiceTests(TestCase):
    def test_freight_by_site_transporter_month(self):
        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Transport")
        transporter = Vendor.objects.create(name="Fast Movers")
        transporter.categories.add(category)
        user = User.objects.create_user(username="po", password="pass12345")
        TransportTrip.objects.create(
            vehicle_number="V1", transporter=transporter, from_location="A", to_site=site,
            trip_date=datetime.date(2026, 1, 15), freight_amount=Decimal("1000.00"), created_by=user,
        )
        rows = freight_by_site_transporter_month()
        self.assertEqual(rows[0]["total_freight"], Decimal("1000.00"))


class MachineryUtilizationTests(TestCase):
    def test_utilization_computed(self):
        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)
        MachineDeployment.objects.create(machine=machine, site=site, from_date=datetime.date(2026, 1, 1))
        MachineLog.objects.create(machine=machine, log_date=datetime.date(2026, 1, 5), hours_run=Decimal("8"), fuel_litres=Decimal("40"))

        rows = machinery_utilization()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hours_run"], Decimal("8"))
        self.assertEqual(rows[0]["fuel_per_hour"], Decimal("5"))


class MonthlyScmPackTests(TestCase):
    def test_workbook_builds_without_error(self):
        from accounts_stub.management.commands.send_monthly_scm_pack import build_monthly_pack_workbook

        wb = build_monthly_pack_workbook()
        self.assertIn("Spend by vendor", wb.sheetnames)
        self.assertIn("Machinery utilisation", wb.sheetnames)

    def test_command_sends_email_to_scm_head(self):
        from django.core import mail
        from django.core.management import call_command

        head = User.objects.create_user(username="head", password="pass12345", email="head@example.com")
        head.groups.add(Group.objects.create(name="SCM Head"))

        call_command("send_monthly_scm_pack")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["head@example.com"])
        self.assertEqual(len(mail.outbox[0].attachments), 1)

    def test_command_skips_send_with_no_recipients(self):
        from django.core import mail
        from django.core.management import call_command

        call_command("send_monthly_scm_pack")
        self.assertEqual(len(mail.outbox), 0)
