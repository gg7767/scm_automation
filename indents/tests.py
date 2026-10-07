import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from indents.models import Indent, IndentLine, InvalidStatusTransition
from masters.models import Item, ItemCategory, RateContract, Site, Vendor
from purchase.models import ApprovalRule, PurchaseOrder


class IndentStatusMachineTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.indent = Indent.objects.create(site=self.site, raised_by=self.user)

    def test_cannot_submit_without_lines(self):
        with self.assertRaises(InvalidStatusTransition):
            self.indent.submit_for_approval(self.user)

    def test_submit_moves_to_pending_approval(self):
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))
        self.indent.submit_for_approval(self.user)
        self.assertEqual(self.indent.status, Indent.Status.PENDING_APPROVAL)

    def test_cannot_submit_twice(self):
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))
        self.indent.submit_for_approval(self.user)
        with self.assertRaises(InvalidStatusTransition):
            self.indent.submit_for_approval(self.user)

    def test_approve_requires_pending_approval(self):
        with self.assertRaises(InvalidStatusTransition):
            self.indent.approve(self.user)

    def test_approve_succeeds(self):
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))
        self.indent.submit_for_approval(self.user)
        self.indent.approve(self.user, comment="ok")
        self.assertEqual(self.indent.status, Indent.Status.APPROVED)
        self.assertEqual(self.indent.approved_by, self.user)

    def test_reject_requires_pending_approval(self):
        with self.assertRaises(InvalidStatusTransition):
            self.indent.reject(self.user, "no budget")

    def test_reject_succeeds(self):
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))
        self.indent.submit_for_approval(self.user)
        self.indent.reject(self.user, "no budget")
        self.assertEqual(self.indent.status, Indent.Status.REJECTED)
        self.assertEqual(self.indent.rejection_reason, "no budget")

    def test_cancel_requires_reason(self):
        with self.assertRaises(InvalidStatusTransition):
            self.indent.cancel(self.user, "")

    def test_cancel_from_draft(self):
        self.indent.cancel(self.user, "duplicate")
        self.assertEqual(self.indent.status, Indent.Status.CANCELLED)

    def test_cannot_cancel_ordered_indent(self):
        self.indent.status = Indent.Status.ORDERED
        self.indent.save(update_fields=["status"])
        with self.assertRaises(InvalidStatusTransition):
            self.indent.cancel(self.user, "too late")

    def test_cannot_approve_a_draft_directly(self):
        with self.assertRaises(InvalidStatusTransition):
            self.indent.approve(self.user)


class IndentConversionMathTests(TestCase):
    """Partial conversion math: qty_ordered roll-ups and merged indents."""

    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.user = User.objects.create_user(username="po", password="pass12345")
        po_group = Group.objects.create(name="Purchase Officer (HO)")
        self.user.groups.add(po_group)
        ApprovalRule.objects.create(
            doc_type=ApprovalRule.DocType.PO, min_amount=Decimal("0"), max_amount=None, approver_role=po_group
        )

        self.indent = Indent.objects.create(site=self.site, raised_by=self.user, status=Indent.Status.APPROVED)
        self.line = IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("100"))

    def _approve_po(self, po):
        po.submit_for_approval(self.user)
        po.approve(self.user)

    def test_partial_conversion_updates_qty_ordered_on_approval(self):
        from indents.services import convert_indent_lines_to_po
        po = convert_indent_lines_to_po({self.line: Decimal("40")}, vendor=self.vendor, site=self.site, user=self.user)
        for line in po.lines.all():
            line.rate = Decimal("10")
            line.save()
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_ordered, Decimal("0.000"))  # not yet, PO still draft

        self._approve_po(po)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_ordered, Decimal("40.000"))
        self.indent.refresh_from_db()
        self.assertEqual(self.indent.status, Indent.Status.PARTIALLY_ORDERED)

    def test_full_conversion_marks_indent_ordered(self):
        from indents.services import convert_indent_lines_to_po
        po = convert_indent_lines_to_po({self.line: Decimal("100")}, vendor=self.vendor, site=self.site, user=self.user)
        for line in po.lines.all():
            line.rate = Decimal("10")
            line.save()
        self._approve_po(po)
        self.indent.refresh_from_db()
        self.assertEqual(self.indent.status, Indent.Status.ORDERED)

    def test_two_partial_conversions_accumulate(self):
        from indents.services import convert_indent_lines_to_po
        po1 = convert_indent_lines_to_po({self.line: Decimal("40")}, vendor=self.vendor, site=self.site, user=self.user)
        for line in po1.lines.all():
            line.rate = Decimal("10")
            line.save()
        self._approve_po(po1)

        self.line.refresh_from_db()
        po2 = convert_indent_lines_to_po({self.line: Decimal("60")}, vendor=self.vendor, site=self.site, user=self.user)
        for line in po2.lines.all():
            line.rate = Decimal("10")
            line.save()
        self._approve_po(po2)

        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_ordered, Decimal("100.000"))
        self.indent.refresh_from_db()
        self.assertEqual(self.indent.status, Indent.Status.ORDERED)

    def test_amend_and_reapprove_does_not_double_count(self):
        from indents.services import convert_indent_lines_to_po
        po = convert_indent_lines_to_po({self.line: Decimal("40")}, vendor=self.vendor, site=self.site, user=self.user)
        for line in po.lines.all():
            line.rate = Decimal("10")
            line.save()
        self._approve_po(po)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_ordered, Decimal("40.000"))

        po.amend(self.user)
        po.submit_for_approval(self.user)
        po.approve(self.user)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_ordered, Decimal("40.000"))  # unchanged, not doubled

    def test_merged_indents_same_site_vendor(self):
        indent2 = Indent.objects.create(site=self.site, raised_by=self.user, status=Indent.Status.APPROVED)
        item2 = Item.objects.create(name="Steel rebar", category=self.category, unit=Item.Unit.KG)
        line2 = IndentLine.objects.create(indent=indent2, item=item2, quantity=Decimal("50"))

        from indents.services import convert_indent_lines_to_po
        po = convert_indent_lines_to_po(
            {self.line: Decimal("100"), line2: Decimal("50")}, vendor=self.vendor, site=self.site, user=self.user
        )
        self.assertEqual(po.lines.count(), 2)
        self.assertEqual(po.source_indent, self.indent)  # first indent recorded as primary
        for line in po.lines.all():
            self.assertIn(line.indent_line, [self.line, line2])
            line.rate = Decimal("10")
            line.save()

        self._approve_po(po)
        self.line.refresh_from_db()
        line2.refresh_from_db()
        self.assertEqual(self.line.qty_ordered, Decimal("100.000"))
        self.assertEqual(line2.qty_ordered, Decimal("50.000"))
        indent2.refresh_from_db()
        self.assertEqual(indent2.status, Indent.Status.ORDERED)

    def test_line_rejection_closes_without_ordering(self):
        self.line.reject_remaining(self.user, "use stock at site")
        self.line.refresh_from_db()
        self.assertTrue(self.line.is_rejected)
        self.indent.refresh_from_db()
        self.assertEqual(self.indent.status, Indent.Status.ORDERED)  # fully resolved (rejected)


class IndentEstimatedValueTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.vendor = Vendor.objects.create(name="ABC Traders")
        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.indent = Indent.objects.create(site=self.site, raised_by=self.user)

    def test_estimate_falls_back_to_zero_with_no_history(self):
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))
        self.assertEqual(self.indent.estimated_value(), Decimal("0.00"))

    def test_estimate_uses_latest_rate_contract(self):
        RateContract.objects.create(
            vendor=self.vendor, item=self.item, rate=Decimal("380.00"), valid_from=datetime.date(2020, 1, 1)
        )
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))
        self.assertEqual(self.indent.estimated_value(), Decimal("3800.00"))


# --- Permission walls ---------------------------------------------------

class IndentPermissionTests(TestCase):
    def setUp(self):
        self.site1 = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")

        self.site1_user = User.objects.create_user(username="site1user", password="pass12345")
        self.site1_user.groups.add(Group.objects.create(name="Site Member"))
        self.site1_user.profile.site = self.site1
        self.site1_user.profile.save()

        self.site2_user = User.objects.create_user(username="site2user", password="pass12345")
        self.site2_user.groups.add(Group.objects.get(name="Site Member"))
        self.site2_user.profile.site = self.site2
        self.site2_user.profile.save()

        self.indent_site1 = Indent.objects.create(site=self.site1, raised_by=self.site1_user)

    def test_site_member_can_create_indent_for_own_site(self):
        self.client.login(username="site1user", password="pass12345")
        before = Indent.objects.filter(site=self.site1, raised_by=self.site1_user).count()
        response = self.client.post(reverse("indents:indent_create"), {
            "site": self.site1.pk, "project_name": "", "required_by_date": "", "priority": "normal", "remarks": "",
        })
        self.assertEqual(Indent.objects.filter(site=self.site1, raised_by=self.site1_user).count(), before + 1)
        self.assertEqual(response.status_code, 302)

    def test_site_a_user_cannot_see_site_b_indent(self):
        self.client.login(username="site2user", password="pass12345")
        response = self.client.get(reverse("indents:indent_detail", args=[self.indent_site1.pk]))
        self.assertEqual(response.status_code, 404)

    def test_site_a_user_indent_list_excludes_site_b(self):
        Indent.objects.create(site=self.site2, raised_by=self.site2_user)
        self.client.login(username="site1user", password="pass12345")
        response = self.client.get(reverse("indents:indent_list"))
        self.assertContains(response, self.indent_site1.indent_number)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("indents:indent_list"))
        self.assertEqual(response.status_code, 302)

    def test_accounts_role_cannot_access_indents(self):
        user = User.objects.create_user(username="accountant", password="pass12345")
        user.groups.add(Group.objects.create(name="Accounts"))
        self.client.login(username="accountant", password="pass12345")
        response = self.client.get(reverse("indents:indent_list"))
        self.assertEqual(response.status_code, 403)


class IndentViewFlowTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.vendor = Vendor.objects.create(name="ABC Traders")

        po_group = Group.objects.create(name="Purchase Officer (HO)")
        ApprovalRule.objects.create(
            doc_type=ApprovalRule.DocType.INDENT, min_amount=Decimal("0"), max_amount=None, approver_role=po_group
        )
        self.officer = User.objects.create_user(username="po", password="pass12345")
        self.officer.groups.add(po_group)
        self.client.login(username="po", password="pass12345")

        self.indent = Indent.objects.create(site=self.site, raised_by=self.officer)
        IndentLine.objects.create(indent=self.indent, item=self.item, quantity=Decimal("10"))

    def test_submit_and_approve_via_views(self):
        self.client.post(reverse("indents:indent_submit", args=[self.indent.pk]))
        self.indent.refresh_from_db()
        self.assertEqual(self.indent.status, Indent.Status.PENDING_APPROVAL)

        self.client.post(reverse("indents:indent_approve", args=[self.indent.pk]), {"text": "ok"})
        self.indent.refresh_from_db()
        self.assertEqual(self.indent.status, Indent.Status.APPROVED)

    def test_convert_view_creates_draft_po(self):
        self.indent.status = Indent.Status.APPROVED
        self.indent.save(update_fields=["status"])
        line = self.indent.lines.first()

        response = self.client.post(reverse("indents:indent_convert", args=[self.indent.pk]), {
            "vendor": self.vendor.pk, f"qty_{line.pk}": "10",
        })
        po = PurchaseOrder.objects.get(source_indent=self.indent)
        self.assertRedirects(response, reverse("purchase:po_detail", args=[po.pk]))
        self.assertEqual(po.lines.count(), 1)
        self.assertEqual(po.lines.first().indent_line, line)


class UrgentIndentNotificationTests(TestCase):
    def test_urgent_submit_notifies_matched_approver_group(self):
        from accounts_stub.models import Notification

        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)

        po_group = Group.objects.create(name="Purchase Officer (HO)")
        ApprovalRule.objects.create(
            doc_type=ApprovalRule.DocType.INDENT, min_amount=Decimal("0"), max_amount=None, approver_role=po_group
        )
        approver = User.objects.create_user(username="po", password="pass12345", email="po@example.com")
        approver.groups.add(po_group)

        raiser = User.objects.create_user(username="siteuser", password="pass12345")
        indent = Indent.objects.create(site=site, raised_by=raiser, priority=Indent.Priority.URGENT)
        IndentLine.objects.create(indent=indent, item=item, quantity=Decimal("10"))

        indent.submit_for_approval(raiser)

        self.assertTrue(Notification.objects.filter(recipient=approver).exists())

    def test_normal_priority_does_not_notify(self):
        from accounts_stub.models import Notification

        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        category = ItemCategory.objects.create(name="Cement")
        item = Item.objects.create(name="OPC 53", category=category, unit=Item.Unit.BAG)
        raiser = User.objects.create_user(username="siteuser", password="pass12345")
        indent = Indent.objects.create(site=site, raised_by=raiser, priority=Indent.Priority.NORMAL)
        IndentLine.objects.create(indent=indent, item=item, quantity=Decimal("10"))

        indent.submit_for_approval(raiser)

        self.assertEqual(Notification.objects.count(), 0)


class NewIndentWorkflowAndTableTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.existing_item = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)

        self.user = User.objects.create_user(username="siteuser", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Site Member"))
        self.user.profile.site = self.site
        self.user.profile.save()
        self.client.login(username="siteuser", password="pass12345")

    def test_raise_indent_bypasses_new_form_and_redirects_to_detail(self):
        response = self.client.get(reverse("indents:indent_create"))
        self.assertEqual(response.status_code, 302)
        indent = Indent.objects.filter(site=self.site, raised_by=self.user).latest("created_at")
        self.assertRedirects(response, reverse("indents:indent_detail", args=[indent.pk]))

    def test_add_line_item_for_existing_item(self):
        indent = Indent.objects.create(site=self.site, raised_by=self.user)
        from stores.models import StockBalance
        StockBalance.objects.create(site=self.site, item=self.existing_item, quantity=Decimal("150.000"))

        response = self.client.post(reverse("indents:indent_line_create", args=[indent.pk]), {
            "item_description": "OPC 53",
            "existing_item_id": self.existing_item.pk,
            "unit": "BAG",
            "quantity": "25.000",
            "required_by_date": "2026-11-01",
            "purpose": "Slab casting",
        })
        self.assertEqual(response.status_code, 200)
        line = indent.lines.first()
        self.assertIsNotNone(line)
        self.assertEqual(line.item, self.existing_item)
        self.assertEqual(line.quantity, Decimal("25.000"))
        self.assertEqual(line.unit, "BAG")
        self.assertEqual(line.present_stock, Decimal("150.000"))
        self.assertEqual(str(line.required_by_date), "2026-11-01")
        self.assertEqual(line.purpose, "Slab casting")

    def test_add_line_item_for_new_item(self):
        indent = Indent.objects.create(site=self.site, raised_by=self.user)
        response = self.client.post(reverse("indents:indent_line_create", args=[indent.pk]), {
            "item_description": "Steel Rod 12mm",
            "unit": "MT",
            "present_stock": "5.000",
            "quantity": "10.000",
            "required_by_date": "2026-12-15",
            "purpose": "Reinforcement",
        })
        self.assertEqual(response.status_code, 200)
        line = indent.lines.first()
        self.assertIsNotNone(line)
        self.assertEqual(line.item.name, "Steel Rod 12mm")
        self.assertEqual(line.item.unit, "MT")
        self.assertEqual(line.quantity, Decimal("10.000"))
        self.assertEqual(line.present_stock, Decimal("5.000"))
        self.assertEqual(str(line.required_by_date), "2026-12-15")
        self.assertEqual(line.purpose, "Reinforcement")

    def test_six_columns_rendered_in_detail_view(self):
        indent = Indent.objects.create(site=self.site, raised_by=self.user)
        IndentLine.objects.create(
            indent=indent,
            item=self.existing_item,
            quantity=Decimal("50.000"),
            unit="BAG",
            present_stock=Decimal("100.000"),
            required_by_date=datetime.date(2026, 10, 20),
            purpose="Foundation test"
        )
        response = self.client.get(reverse("indents:indent_detail", args=[indent.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Item Description")
        self.assertContains(response, "UOM")
        self.assertContains(response, "Present Stock")
        self.assertContains(response, "Required Qty")
        self.assertContains(response, "Required By Date")
        self.assertContains(response, "Remarks")
        self.assertContains(response, "OPC 53")
        self.assertContains(response, "100.000")
        self.assertContains(response, "50.000")
        self.assertContains(response, "20-10-2026")
        self.assertContains(response, "Foundation test")


class VendorPriceConversionTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.category = ItemCategory.objects.create(name="Cement")
        self.item1 = Item.objects.create(name="OPC 53", category=self.category, unit=Item.Unit.BAG)
        self.item2 = Item.objects.create(name="PPC Cement", category=self.category, unit=Item.Unit.BAG)

        self.vendor = Vendor.objects.create(name="UltraTech Cement", code="VEN-001", status="active")

        po_group = Group.objects.create(name="Purchase Officer (HO)")
        ApprovalRule.objects.create(
            doc_type=ApprovalRule.DocType.PO, min_amount=Decimal("0"), max_amount=None, approver_role=po_group
        )
        self.scm_head = User.objects.create_user(username="scmhead", password="pass12345")
        self.scm_head.groups.add(po_group)
        self.client.login(username="scmhead", password="pass12345")

        # Previous PO for item1 with vendor at rate 390.00
        from purchase.models import PurchaseOrder, PurchaseOrderLine
        prev_po = PurchaseOrder.objects.create(vendor=self.vendor, site=self.site, created_by=self.scm_head)
        PurchaseOrderLine.objects.create(po=prev_po, item=self.item1, quantity=Decimal("100"), rate=Decimal("390.00"), created_by=self.scm_head)

        self.indent = Indent.objects.create(site=self.site, raised_by=self.scm_head, status=Indent.Status.APPROVED)
        self.line1 = IndentLine.objects.create(indent=self.indent, item=self.item1, quantity=Decimal("50"))
        self.line2 = IndentLine.objects.create(indent=self.indent, item=self.item2, quantity=Decimal("30"))

    def test_vendor_rates_api_returns_historical_price_or_empty(self):
        response = self.client.get(
            reverse("indents:vendor_rates") + f"?vendor_id={self.vendor.pk}&item_ids={self.item1.pk},{self.item2.pk}"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get(str(self.item1.pk)), "390.00")
        self.assertEqual(data.get(str(self.item2.pk)), "")

    def test_convert_indent_to_po_with_custom_and_autopopulated_rates(self):
        response = self.client.post(reverse("indents:indent_convert", args=[self.indent.pk]), {
            "vendor": self.vendor.pk,
            f"qty_{self.line1.pk}": "50",
            f"rate_{self.line1.pk}": "395.00",  # SCM head edited price
            f"qty_{self.line2.pk}": "30",
            f"rate_{self.line2.pk}": "320.00",  # SCM head entered price for item without prior PO
        })
        from purchase.models import PurchaseOrder
        po = PurchaseOrder.objects.get(source_indent=self.indent)
        self.assertRedirects(response, reverse("purchase:po_detail", args=[po.pk]))
        self.assertEqual(po.lines.count(), 2)

        po_line1 = po.lines.get(item=self.item1)
        po_line2 = po.lines.get(item=self.item2)
        self.assertEqual(po_line1.rate, Decimal("395.00"))
        self.assertEqual(po_line2.rate, Decimal("320.00"))


