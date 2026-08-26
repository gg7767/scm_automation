"""
Permission matrix: logs in as one user per role and asserts the expected
allowed/denied outcome for a representative URL from every app's read-only
list/detail views. This is a coarse safety net, not exhaustive per-action
coverage (each app's own tests already cover create/edit/transition
permissions in detail) — it exists to catch an accidentally-unguarded or
wrongly-guarded view at the "can this role even load the page" level.

docs/PHASE4_INVENTORY_TRANSPORT_MACHINERY.md, hardening checklist, item 1.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from assets.models import Machine
from indents.models import Indent
from masters.models import Item, ItemCategory, Site, Vendor
from purchase.models import PurchaseOrder, PurchaseOrderLine
from stores.models import GRN

_ONE_PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tiny_file(name="doc.png"):
    return SimpleUploadedFile(name, _ONE_PX_PNG, content_type="image/png")


class PermissionMatrixTests(TestCase):
    """ALLOWED means HTTP 200; DENIED means 302 (anonymous -> login redirect)
    or 403 (authenticated but not permitted)."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        cls.vendor = Vendor.objects.create(name="ABC Traders")
        cls.category = ItemCategory.objects.create(name="Cement")
        cls.item = Item.objects.create(name="OPC 53", category=cls.category, unit=Item.Unit.BAG)

        cls.po = PurchaseOrder.objects.create(vendor=cls.vendor, site=cls.site, status=PurchaseOrder.Status.SENT)
        PurchaseOrderLine.objects.create(po=cls.po, item=cls.item, quantity=Decimal("10"), rate=Decimal("10"))

        cls.indent = Indent.objects.create(site=cls.site, raised_by=User.objects.create_user(username="indent_raiser", password="x"))

        cls.grn = GRN.objects.create(
            po=cls.po, received_by=cls.indent.raised_by, challan_number="C1",
            challan_date=datetime.date(2026, 1, 1), challan_photo=_tiny_file(),
        )

        cls.machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)

        cls.users = {}
        for role in ["SCM Head", "Purchase Officer (HO)", "Site Member", "Accounts", "Admin"]:
            user = User.objects.create_user(username=role.split()[0].lower() + "_user", password="pass12345")
            user.groups.add(Group.objects.create(name=role))
            if role == "Site Member":
                user.profile.site = cls.site
                user.profile.save()
            cls.users[role] = user

    def _get(self, user, url_name, *args):
        client = self.client_class()
        if user:
            client.login(username=user.username, password="pass12345")
        return client.get(reverse(url_name, args=args))

    def _assert_matrix(self, url_name, args, expected):
        """expected: {role_or_None: True/False}. None = anonymous."""
        for role, should_allow in expected.items():
            user = self.users[role] if role else None
            response = self._get(user, url_name, *args)
            if should_allow:
                self.assertEqual(
                    response.status_code, 200,
                    f"{role or 'anonymous'} should be ALLOWED on {url_name} but got {response.status_code}",
                )
            else:
                self.assertIn(
                    response.status_code, (302, 403),
                    f"{role or 'anonymous'} should be DENIED on {url_name} but got {response.status_code}",
                )

    def test_masters_vendor_list(self):
        self._assert_matrix("masters:vendor_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": False, "Accounts": False, "Admin": True,
        })

    def test_purchase_order_list(self):
        self._assert_matrix("purchase:po_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": True, "Accounts": True, "Admin": True,
        })

    def test_purchase_order_create(self):
        self._assert_matrix("purchase:po_create", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": False, "Accounts": False, "Admin": False,
        })

    def test_indent_list(self):
        self._assert_matrix("indents:indent_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": True, "Accounts": False, "Admin": True,
        })

    def test_grn_list(self):
        self._assert_matrix("stores:grn_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": True, "Accounts": True, "Admin": True,
        })

    def test_grn_reversal_create_ho_only(self):
        url = reverse("stores:grn_reversal_create") + f"?po={self.po.pk}"
        for role, should_allow in {
            "SCM Head": True, "Purchase Officer (HO)": True, "Site Member": False, "Accounts": False,
        }.items():
            client = self.client_class()
            client.login(username=self.users[role].username, password="pass12345")
            response = client.get(url)
            if should_allow:
                self.assertEqual(response.status_code, 200, f"{role} should be ALLOWED on GRN reversal")
            else:
                self.assertIn(response.status_code, (302, 403), f"{role} should be DENIED on GRN reversal")

    def test_billing_bill_list_accounts_only(self):
        self._assert_matrix("billing:bill_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": False,
            "Site Member": False, "Accounts": True, "Admin": True,
        })

    def test_payments_list(self):
        self._assert_matrix("billing:payment_list", [], {
            None: False, "SCM Head": True, "Accounts": True, "Purchase Officer (HO)": False, "Site Member": False,
        })

    def test_transport_trip_list(self):
        self._assert_matrix("logistics:trip_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": True, "Accounts": False, "Admin": True,
        })

    def test_machine_list(self):
        self._assert_matrix("assets:machine_list", [], {
            None: False, "SCM Head": True, "Purchase Officer (HO)": True,
            "Site Member": True, "Accounts": False, "Admin": True,
        })

    def test_machine_create_manage_only(self):
        self._assert_matrix("assets:machine_create", [], {
            "SCM Head": True, "Purchase Officer (HO)": True, "Site Member": False, "Accounts": False,
        })

    def test_stock_balance_list(self):
        self._assert_matrix("stores:stock_balance_list", [], {
            None: False, "SCM Head": True, "Accounts": True, "Site Member": True,
        })

    def test_vendor_ledger_full_accounts_and_scm_head_only(self):
        self._assert_matrix("billing:vendor_ledger", [self.vendor.pk], {
            None: False, "SCM Head": True, "Accounts": True, "Purchase Officer (HO)": False, "Site Member": False,
        })

    def test_payables_aging(self):
        self._assert_matrix("billing:payables_aging", [], {
            None: False, "SCM Head": True, "Accounts": True, "Purchase Officer (HO)": False,
        })
