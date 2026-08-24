import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from accounts_stub.models import UserProfile
from masters.models import Item, ItemCategory, RateContract, Site, Vendor
from purchase.models import ApprovalRule, InvalidStatusTransition, POAttachment, PurchaseOrder, PurchaseOrderLine
from purchase.numbering import financial_year_label


class FinancialYearLabelTests(TestCase):
    def test_april_starts_new_fy(self):
        self.assertEqual(financial_year_label(datetime.date(2026, 4, 1)), "26-27")

    def test_march_is_end_of_previous_fy(self):
        self.assertEqual(financial_year_label(datetime.date(2026, 3, 31)), "25-26")

    def test_mid_year(self):
        self.assertEqual(financial_year_label(datetime.date(2026, 12, 25)), "26-27")


class PurchaseOrderNumberingTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")

    def test_number_format(self):
        po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        self.assertTrue(po.po_number.startswith(f"PO/{self.site.code}/"))
        self.assertTrue(po.po_number.endswith("0001"))

    def test_numbers_increment_per_site(self):
        po1 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        po2 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        self.assertNotEqual(po1.po_number, po2.po_number)
        self.assertTrue(po2.po_number.endswith("0002"))

    def test_numbers_independent_per_site(self):
        site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        po1 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        po2 = PurchaseOrder.objects.create(vendor=self.vendor, site=site2)
        self.assertTrue(po1.po_number.endswith("0001"))
        self.assertTrue(po2.po_number.endswith("0001"))

    def test_number_preserved_on_resave(self):
        po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        original_number = po.po_number
        po.remarks = "updated"
        po.save()
        self.assertEqual(po.po_number, original_number)


class PurchaseOrderLineTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(
            name="OPC 53", category=self.category, unit=Item.Unit.BAG, gst_rate=Decimal("28.00")
        )
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)

    def test_line_total_computed(self):
        line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00")
        )
        self.assertEqual(line.line_total, Decimal("3800.00"))

    def test_unit_and_gst_rate_copied_from_item(self):
        line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00")
        )
        self.assertEqual(line.unit, Item.Unit.BAG)
        self.assertEqual(line.gst_rate, Decimal("28.00"))

    def test_po_totals_recomputed_on_line_save(self):
        PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00")
        )
        self.po.refresh_from_db()
        self.assertEqual(self.po.subtotal, Decimal("3800.00"))
        self.assertEqual(self.po.gst_amount, Decimal("1064.00"))
        self.assertEqual(self.po.grand_total, Decimal("4864.00"))

    def test_po_totals_recomputed_on_line_delete(self):
        line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00")
        )
        line.delete()
        self.po.refresh_from_db()
        self.assertEqual(self.po.subtotal, Decimal("0.00"))
        self.assertEqual(self.po.grand_total, Decimal("0.00"))

    def test_multiple_lines_sum_correctly(self):
        item2 = Item.objects.create(name="Steel rebar", category=self.category, unit=Item.Unit.KG, gst_rate=Decimal("18.00"))
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00"))
        PurchaseOrderLine.objects.create(po=self.po, item=item2, quantity=Decimal("100.000"), rate=Decimal("60.00"))
        self.po.refresh_from_db()
        self.assertEqual(self.po.subtotal, Decimal("9800.00"))

    def test_deviates_from_contract_flag_set(self):
        RateContract.objects.create(
            vendor=self.vendor, item=self.item, rate=Decimal("350.00"),
            valid_from=datetime.date(2020, 1, 1),
        )
        line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00")
        )
        self.assertTrue(line.deviates_from_contract)

    def test_deviates_from_contract_flag_not_set_when_rate_matches(self):
        RateContract.objects.create(
            vendor=self.vendor, item=self.item, rate=Decimal("380.00"),
            valid_from=datetime.date(2020, 1, 1),
        )
        line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("380.00")
        )
        self.assertFalse(line.deviates_from_contract)

    def test_no_flag_when_no_contract_exists(self):
        line = PurchaseOrderLine.objects.create(
            po=self.po, item=self.item, quantity=Decimal("10.000"), rate=Decimal("999.00")
        )
        self.assertFalse(line.deviates_from_contract)


class PurchaseOrderStatusMachineTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        self.user = User.objects.create_user(username="po_officer", password="pass12345")

    def test_cannot_submit_without_lines(self):
        with self.assertRaises(InvalidStatusTransition):
            self.po.submit_for_approval(self.user)

    def test_submit_moves_to_pending_approval(self):
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1.000"), rate=Decimal("10.00"))
        self.po.submit_for_approval(self.user)
        self.assertEqual(self.po.status, PurchaseOrder.Status.PENDING_APPROVAL)
        self.assertEqual(self.po.approval_actions.count(), 1)

    def test_cannot_submit_twice(self):
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1.000"), rate=Decimal("10.00"))
        self.po.submit_for_approval(self.user)
        with self.assertRaises(InvalidStatusTransition):
            self.po.submit_for_approval(self.user)

    def test_approve_sets_approver_and_timestamp(self):
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1.000"), rate=Decimal("10.00"))
        self.po.submit_for_approval(self.user)
        self.po.approve(self.user, comment="Looks good")
        self.assertEqual(self.po.status, PurchaseOrder.Status.APPROVED)
        self.assertEqual(self.po.approved_by, self.user)
        self.assertIsNotNone(self.po.approved_at)

    def test_cannot_approve_a_draft(self):
        with self.assertRaises(InvalidStatusTransition):
            self.po.approve(self.user)

    def test_reject_returns_to_draft(self):
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1.000"), rate=Decimal("10.00"))
        self.po.submit_for_approval(self.user)
        self.po.reject(self.user, comment="Wrong vendor")
        self.assertEqual(self.po.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(self.po.approval_actions.last().comment, "Wrong vendor")

    def test_mark_sent_requires_approved_status(self):
        with self.assertRaises(InvalidStatusTransition):
            self.po.mark_sent(self.user, PurchaseOrder.SentVia.EMAIL)

    def test_mark_sent_succeeds_after_approval(self):
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1.000"), rate=Decimal("10.00"))
        self.po.submit_for_approval(self.user)
        self.po.approve(self.user)
        self.po.mark_sent(self.user, PurchaseOrder.SentVia.WHATSAPP)
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)
        self.assertEqual(self.po.sent_via, PurchaseOrder.SentVia.WHATSAPP)
        self.assertIsNotNone(self.po.sent_at)

    def test_cancel_requires_reason(self):
        with self.assertRaises(InvalidStatusTransition):
            self.po.cancel(self.user, reason="")

    def test_cancel_from_draft(self):
        self.po.cancel(self.user, reason="Project shelved")
        self.assertEqual(self.po.status, PurchaseOrder.Status.CANCELLED)
        self.assertEqual(self.po.cancelled_reason, "Project shelved")

    def test_cannot_cancel_closed_po(self):
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1.000"), rate=Decimal("10.00"))
        self.po.submit_for_approval(self.user)
        self.po.approve(self.user)
        self.po.mark_sent(self.user, PurchaseOrder.SentVia.EMAIL)
        self.po.close(self.user)
        with self.assertRaises(InvalidStatusTransition):
            self.po.cancel(self.user, reason="too late")

    def test_close_requires_sent_status(self):
        with self.assertRaises(InvalidStatusTransition):
            self.po.close(self.user)


class ApprovalRuleEngineTests(TestCase):
    def setUp(self):
        self.po_group = Group.objects.create(name="Purchase Officer (HO)")
        self.scm_head_group = Group.objects.create(name="SCM Head")
        ApprovalRule.objects.create(min_amount=Decimal("0.00"), max_amount=Decimal("100000.00"), approver_role=self.po_group)
        ApprovalRule.objects.create(min_amount=Decimal("100000.01"), max_amount=None, approver_role=self.scm_head_group)

    def test_matches_lower_band(self):
        group = ApprovalRule.approver_group_for_amount(Decimal("50000.00"))
        self.assertEqual(group, self.po_group)

    def test_matches_uncapped_upper_band(self):
        group = ApprovalRule.approver_group_for_amount(Decimal("5000000.00"))
        self.assertEqual(group, self.scm_head_group)

    def test_no_rule_matches_returns_none(self):
        ApprovalRule.objects.all().delete()
        group = ApprovalRule.approver_group_for_amount(Decimal("1000.00"))
        self.assertIsNone(group)

    def test_scm_head_can_always_approve(self):
        user = User.objects.create_user(username="head", password="pass12345")
        user.groups.add(self.scm_head_group)
        self.assertTrue(ApprovalRule.can_user_approve(user, Decimal("1.00")))

    def test_purchase_officer_can_approve_within_band(self):
        user = User.objects.create_user(username="po", password="pass12345")
        user.groups.add(self.po_group)
        self.assertTrue(ApprovalRule.can_user_approve(user, Decimal("50000.00")))

    def test_purchase_officer_cannot_approve_above_band(self):
        user = User.objects.create_user(username="po2", password="pass12345")
        user.groups.add(self.po_group)
        self.assertFalse(ApprovalRule.can_user_approve(user, Decimal("5000000.00")))

    def test_superuser_can_always_approve(self):
        user = User.objects.create_superuser(username="admin", password="pass12345")
        self.assertTrue(ApprovalRule.can_user_approve(user, Decimal("5000000.00")))


# --- Views --------------------------------------------------------------

class POListViewPermissionTests(TestCase):
    def setUp(self):
        self.site1 = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.po1 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site1)
        self.po2 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site2)

        self.po_officer = User.objects.create_user(username="po", password="pass12345")
        self.po_officer.groups.add(Group.objects.create(name="Purchase Officer (HO)"))

        self.site_member = User.objects.create_user(username="siteuser", password="pass12345")
        self.site_member.groups.add(Group.objects.create(name="Site Member"))
        self.site_member.profile.site = self.site1
        self.site_member.profile.save()

        self.accounts_user = User.objects.create_user(username="accountant", password="pass12345")
        self.accounts_user.groups.add(Group.objects.create(name="Accounts"))

    def test_purchase_officer_sees_all_sites(self):
        self.client.login(username="po", password="pass12345")
        response = self.client.get(reverse("purchase:po_list"))
        self.assertContains(response, self.po1.po_number)
        self.assertContains(response, self.po2.po_number)

    def test_site_member_sees_only_own_site(self):
        self.client.login(username="siteuser", password="pass12345")
        response = self.client.get(reverse("purchase:po_list"))
        self.assertContains(response, self.po1.po_number)
        self.assertNotContains(response, self.po2.po_number)

    def test_site_member_without_site_sees_nothing(self):
        unassigned = User.objects.create_user(username="nosite", password="pass12345")
        unassigned.groups.add(Group.objects.get(name="Site Member"))
        self.client.login(username="nosite", password="pass12345")
        response = self.client.get(reverse("purchase:po_list"))
        self.assertNotContains(response, self.po1.po_number)
        self.assertNotContains(response, self.po2.po_number)

    def test_accounts_role_has_read_only_access(self):
        self.client.login(username="accountant", password="pass12345")
        response = self.client.get(reverse("purchase:po_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "+ New PO")

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("purchase:po_list"))
        self.assertEqual(response.status_code, 302)

    def test_site_member_cannot_view_other_site_po_detail(self):
        self.client.login(username="siteuser", password="pass12345")
        response = self.client.get(reverse("purchase:po_detail", args=[self.po2.pk]))
        self.assertEqual(response.status_code, 404)

    def test_site_member_cannot_create_po(self):
        self.client.login(username="siteuser", password="pass12345")
        response = self.client.get(reverse("purchase:po_create"))
        self.assertEqual(response.status_code, 403)


class POCreateAndLineManagementViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders", payment_terms_days=45)
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG, gst_rate=Decimal("28.00"))
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")

    def test_create_po(self):
        response = self.client.post(reverse("purchase:po_create"), {
            "vendor": self.vendor.pk, "site": self.site.pk, "project_name": "",
            "payment_terms_days": 45, "delivery_terms": "", "remarks": "", "expected_delivery_date": "",
        })
        po = PurchaseOrder.objects.get(vendor=self.vendor)
        self.assertRedirects(response, reverse("purchase:po_detail", args=[po.pk]))
        self.assertEqual(po.created_by, self.user)
        self.assertTrue(po.po_number.startswith("PO/HYD-F1/"))

    def test_add_line_via_htmx_endpoint(self):
        po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        response = self.client.post(reverse("purchase:po_line_create", args=[po.pk]), {
            "item": self.item.pk, "description_override": "", "quantity": "10", "rate": "380.00", "gst_rate": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(po.lines.count(), 1)
        self.assertContains(response, "3800.00")

    def test_cannot_add_line_after_po_is_no_longer_draft(self):
        po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        PurchaseOrderLine.objects.create(po=po, item=self.item, quantity=Decimal("1"), rate=Decimal("10"))
        po.submit_for_approval(self.user)
        self.client.post(reverse("purchase:po_line_create", args=[po.pk]), {
            "item": self.item.pk, "description_override": "", "quantity": "5", "rate": "10.00", "gst_rate": "",
        })
        self.assertEqual(po.lines.count(), 1)

    def test_delete_line(self):
        po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        line = PurchaseOrderLine.objects.create(po=po, item=self.item, quantity=Decimal("1"), rate=Decimal("10"))
        self.client.post(reverse("purchase:po_line_delete", args=[po.pk, line.pk]))
        self.assertEqual(po.lines.count(), 0)

    def test_item_search_returns_matches(self):
        response = self.client.get(reverse("purchase:item_search"), {"q": "OPC"})
        self.assertContains(response, "OPC 53")

    def test_item_search_includes_contract_rate(self):
        RateContract.objects.create(
            vendor=self.vendor, item=self.item, rate=Decimal("380.00"), valid_from=datetime.date(2020, 1, 1)
        )
        response = self.client.get(reverse("purchase:item_search"), {"q": "OPC", "vendor": self.vendor.pk})
        self.assertContains(response, "380.00")


class POStatusTransitionViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)

        po_group = Group.objects.create(name="Purchase Officer (HO)")
        ApprovalRule.objects.create(min_amount=Decimal("0"), max_amount=None, approver_role=po_group)

        self.officer = User.objects.create_user(username="po", password="pass12345")
        self.officer.groups.add(po_group)
        self.client.login(username="po", password="pass12345")

        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("1"), rate=Decimal("100"))

    def test_full_lifecycle_via_views(self):
        self.client.post(reverse("purchase:po_submit", args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.PENDING_APPROVAL)

        self.client.post(reverse("purchase:po_approve", args=[self.po.pk]), {"text": "ok"})
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.APPROVED)

        self.client.post(reverse("purchase:po_send", args=[self.po.pk]), {"channel": "email"})
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

        self.client.post(reverse("purchase:po_amend", args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(self.po.revision_number, 1)

    def test_reject_via_view(self):
        self.client.post(reverse("purchase:po_submit", args=[self.po.pk]))
        self.client.post(reverse("purchase:po_reject", args=[self.po.pk]), {"text": "wrong vendor"})
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.DRAFT)

    def test_cancel_via_view_requires_reason(self):
        response = self.client.post(reverse("purchase:po_cancel", args=[self.po.pk]), {"text": ""})
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.DRAFT)

    def test_cancel_via_view_with_reason(self):
        self.client.post(reverse("purchase:po_cancel", args=[self.po.pk]), {"text": "project shelved"})
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CANCELLED)

    def test_user_below_approval_threshold_cannot_approve(self):
        scm_head = Group.objects.create(name="SCM Head")
        ApprovalRule.objects.all().delete()
        ApprovalRule.objects.create(min_amount=Decimal("1000000"), max_amount=None, approver_role=scm_head)
        self.client.post(reverse("purchase:po_submit", args=[self.po.pk]))
        response = self.client.post(reverse("purchase:po_approve", args=[self.po.pk]), {"text": ""})
        self.assertEqual(response.status_code, 403)


class POPdfViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders", state="TG")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG, gst_rate=Decimal("18.00"))
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        PurchaseOrderLine.objects.create(po=self.po, item=self.item, quantity=Decimal("10"), rate=Decimal("100"))

    def test_pdf_renders(self):
        response = self.client.get(reverse("purchase:po_pdf", args=[self.po.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


class VendorLedgerViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")

    def test_open_value_excludes_closed_and_cancelled(self):
        po1 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        po1.subtotal = po1.gst_amount = Decimal("0")
        po1.grand_total = Decimal("1000.00")
        po1.save()
        po2 = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)
        po2.grand_total = Decimal("500.00")
        po2.status = PurchaseOrder.Status.CANCELLED
        po2.save()

        response = self.client.get(reverse("purchase:vendor_ledger", args=[self.vendor.pk]))
        self.assertContains(response, "1000.00")
        self.assertContains(response, po1.po_number)
        self.assertContains(response, po2.po_number)


class POAttachmentViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")
        self.po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site)

    def test_upload_and_delete_attachment(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.client.post(reverse("purchase:po_attachment_create", args=[self.po.pk]), {
            "label": "Vendor quotation",
            "file": SimpleUploadedFile("quote.pdf", b"dummy"),
        })
        self.assertRedirects(response, reverse("purchase:po_detail", args=[self.po.pk]))
        attachment = POAttachment.objects.get(po=self.po)

        response = self.client.post(reverse("purchase:po_attachment_delete", args=[self.po.pk, attachment.pk]))
        self.assertRedirects(response, reverse("purchase:po_detail", args=[self.po.pk]))
        self.assertFalse(POAttachment.objects.filter(pk=attachment.pk).exists())


class HomeDashboardTests(TestCase):
    def test_dashboard_shows_for_po_viewer(self):
        user = User.objects.create_user(username="po", password="pass12345")
        user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "POs this month")

    def test_dashboard_hidden_for_users_without_po_access(self):
        user = User.objects.create_user(username="nobody", password="pass12345")
        self.client.login(username="nobody", password="pass12345")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "POs this month")
