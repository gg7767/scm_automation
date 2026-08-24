from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from masters.models import Item, ItemAlias, ItemCategory, Site, Vendor, VendorDocument


class SiteModelTests(TestCase):
    def test_str(self):
        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.assertEqual(str(site), "HYD-F1 — Hyderabad Factory 1")

    def test_code_must_be_unique(self):
        Site.objects.create(name="Site A", code="HYD-F1")
        with self.assertRaises(Exception):
            Site.objects.create(name="Site B", code="HYD-F1")


class ItemCategoryModelTests(TestCase):
    def test_one_level_nesting_allowed(self):
        parent = ItemCategory.objects.create(name="Cement")
        child = ItemCategory(name="OPC 53 Grade", parent=parent)
        child.full_clean()  # should not raise

    def test_two_level_nesting_rejected(self):
        grandparent = ItemCategory.objects.create(name="Cement")
        parent = ItemCategory.objects.create(name="OPC", parent=grandparent)
        grandchild = ItemCategory(name="OPC 53", parent=parent)
        with self.assertRaises(ValidationError):
            grandchild.full_clean()


class ItemModelTests(TestCase):
    def setUp(self):
        self.category = ItemCategory.objects.create(name="Cement")

    def test_code_auto_generated_sequentially(self):
        item1 = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        item2 = Item.objects.create(name="PPC", category=self.category, unit=Item.Unit.BAG)
        self.assertEqual(item1.code, "ITM-00001")
        self.assertEqual(item2.code, "ITM-00002")

    def test_explicit_code_is_preserved(self):
        item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG, code="ITM-CUSTOM")
        self.assertEqual(item.code, "ITM-CUSTOM")

    def test_gst_rate_is_decimal(self):
        item = Item.objects.create(
            name="OPC 53", category=self.category, unit=Item.Unit.BAG, gst_rate=Decimal("28.00")
        )
        self.assertEqual(item.gst_rate, Decimal("28.00"))


class ItemAliasModelTests(TestCase):
    def test_alias_maps_to_item(self):
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53 Grade Cement", category=category, unit=Item.Unit.BAG)
        alias = ItemAlias.objects.create(item=item, alias_name="OPC53 CEMENT BAG")
        self.assertEqual(alias.item, item)
        self.assertIn(alias, item.aliases.all())


class VendorModelTests(TestCase):
    def test_code_auto_generated_sequentially(self):
        v1 = Vendor.objects.create(name="ABC Traders")
        v2 = Vendor.objects.create(name="XYZ Suppliers")
        self.assertEqual(v1.code, "VEN-0001")
        self.assertEqual(v2.code, "VEN-0002")

    def test_gstin_format_validated(self):
        vendor = Vendor(name="Bad GSTIN Co", gstin="INVALID")
        with self.assertRaises(ValidationError):
            vendor.full_clean()

    def test_valid_gstin_passes(self):
        vendor = Vendor(name="Good Co", gstin="36ABCDE1234F1Z5")
        vendor.full_clean()  # should not raise

    def test_blank_gstin_allowed_for_unregistered_vendor(self):
        vendor = Vendor(name="Unregistered Co", gstin="")
        vendor.full_clean()  # should not raise

    def test_default_status_active(self):
        vendor = Vendor.objects.create(name="Default Status Co")
        self.assertEqual(vendor.status, Vendor.Status.ACTIVE)


class VendorDocumentModelTests(TestCase):
    def test_document_linked_to_vendor(self):
        vendor = Vendor.objects.create(name="ABC Traders")
        doc = VendorDocument.objects.create(
            vendor=vendor, label="GST certificate",
            file=SimpleUploadedFile("gst.pdf", b"dummy content"),
        )
        self.assertIn(doc, vendor.documents.all())


# --- Views ------------------------------------------------------------

class MastersViewPermissionTests(TestCase):
    def setUp(self):
        self.site_member = User.objects.create_user(username="siteuser", password="pass12345")
        self.site_member.groups.add(Group.objects.create(name="Site Member"))

        self.purchase_officer = User.objects.create_user(username="po", password="pass12345")
        self.purchase_officer.groups.add(Group.objects.create(name="Purchase Officer (HO)"))

    def test_site_member_cannot_access_vendor_list(self):
        self.client.login(username="siteuser", password="pass12345")
        response = self.client.get(reverse("masters:vendor_list"))
        self.assertEqual(response.status_code, 403)

    def test_purchase_officer_can_access_vendor_list(self):
        self.client.login(username="po", password="pass12345")
        response = self.client.get(reverse("masters:vendor_list"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("masters:vendor_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class VendorCRUDViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")

    def test_create_vendor(self):
        response = self.client.post(reverse("masters:vendor_create"), {
            "name": "New Vendor Co",
            "gstin": "",
            "pan": "",
            "address": "",
            "state": "",
            "contact_person": "",
            "phone": "",
            "email": "",
            "bank_name": "",
            "account_number": "",
            "ifsc": "",
            "payment_terms_days": 30,
            "categories": [],
            "tally_ledger_name": "",
            "status": "active",
        })
        vendor = Vendor.objects.get(name="New Vendor Co")
        self.assertRedirects(response, reverse("masters:vendor_detail", args=[vendor.pk]))
        self.assertEqual(vendor.created_by, self.user)
        self.assertEqual(vendor.code, "VEN-0001")

    def test_vendor_list_search_by_name(self):
        Vendor.objects.create(name="Findable Traders")
        Vendor.objects.create(name="Other Co")
        response = self.client.get(reverse("masters:vendor_list"), {"q": "Findable"})
        self.assertContains(response, "Findable Traders")
        self.assertNotContains(response, "Other Co")

    def test_vendor_list_filter_by_status(self):
        Vendor.objects.create(name="Active Co", status=Vendor.Status.ACTIVE)
        Vendor.objects.create(name="Blacklisted Co", status=Vendor.Status.BLACKLISTED)
        response = self.client.get(reverse("masters:vendor_list"), {"status": "blacklisted"})
        self.assertContains(response, "Blacklisted Co")
        self.assertNotContains(response, "Active Co")


class ItemAliasViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)

    def test_add_alias(self):
        response = self.client.post(
            reverse("masters:item_alias_create", args=[self.item.pk]),
            {"alias_name": "OPC53 BAG"},
        )
        self.assertRedirects(response, reverse("masters:item_detail", args=[self.item.pk]))
        self.assertTrue(self.item.aliases.filter(alias_name="OPC53 BAG").exists())

    def test_remove_alias(self):
        alias = ItemAlias.objects.create(item=self.item, alias_name="OPC53 BAG")
        response = self.client.post(
            reverse("masters:item_alias_delete", args=[self.item.pk, alias.pk])
        )
        self.assertRedirects(response, reverse("masters:item_detail", args=[self.item.pk]))
        self.assertFalse(ItemAlias.objects.filter(pk=alias.pk).exists())
