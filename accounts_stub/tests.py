from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts_stub import roles
from accounts_stub.management.commands.seed_demo import DEFAULT_APPROVAL_RULES, DEMO_CATEGORIES, ROLE_GROUPS
from accounts_stub.models import UserProfile
from masters.models import ItemCategory, Site
from purchase.models import ApprovalRule


class SeedDemoTests(TestCase):
    def test_creates_all_role_groups(self):
        call_command("seed_demo")
        group_names = set(Group.objects.values_list("name", flat=True))
        self.assertEqual(group_names, set(ROLE_GROUPS))

    def test_creates_demo_categories_and_site(self):
        call_command("seed_demo")
        category_names = set(ItemCategory.objects.values_list("name", flat=True))
        self.assertEqual(category_names, set(DEMO_CATEGORIES))
        self.assertTrue(Site.objects.filter(code="HYD-F1").exists())

    def test_creates_default_approval_rules(self):
        call_command("seed_demo")
        self.assertEqual(ApprovalRule.objects.count(), len(DEFAULT_APPROVAL_RULES))

    def test_idempotent(self):
        call_command("seed_demo")
        call_command("seed_demo")
        self.assertEqual(Group.objects.count(), len(ROLE_GROUPS))
        self.assertEqual(ItemCategory.objects.count(), len(DEMO_CATEGORIES))
        self.assertEqual(Site.objects.count(), 1)
        self.assertEqual(ApprovalRule.objects.count(), len(DEFAULT_APPROVAL_RULES))


class AuthAndHomeViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass12345")

    def test_home_requires_login(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_home_renders_for_logged_in_user(self):
        self.client.login(username="alice", password="pass12345")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alice")

    def test_login_view_renders(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)


class UserProfileSignalTests(TestCase):
    def test_profile_auto_created_on_user_creation(self):
        user = User.objects.create_user(username="bob", password="pass12345")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertIsNone(user.profile.site)

    def test_profile_not_duplicated_on_resave(self):
        user = User.objects.create_user(username="carol", password="pass12345")
        user.first_name = "Carol"
        user.save()
        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)


class RolesHelperTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")

    def test_site_member_is_site_restricted(self):
        user = User.objects.create_user(username="siteuser", password="pass12345")
        user.groups.add(Group.objects.create(name=roles.SITE_MEMBER))
        user.profile.site = self.site
        user.profile.save()
        self.assertTrue(roles.is_site_restricted(user))
        self.assertEqual(roles.user_site(user), self.site)

    def test_purchase_officer_is_not_site_restricted(self):
        user = User.objects.create_user(username="po", password="pass12345")
        user.groups.add(Group.objects.create(name=roles.PURCHASE_OFFICER))
        self.assertFalse(roles.is_site_restricted(user))

    def test_superuser_can_manage_and_view_purchase_orders(self):
        user = User.objects.create_superuser(username="admin", password="pass12345")
        self.assertTrue(roles.can_manage_purchase_orders(user))
        self.assertTrue(roles.can_view_purchase_orders(user))

    def test_accounts_role_can_view_but_not_manage(self):
        user = User.objects.create_user(username="accountant", password="pass12345")
        user.groups.add(Group.objects.create(name=roles.ACCOUNTS))
        self.assertTrue(roles.can_view_purchase_orders(user))
        self.assertFalse(roles.can_manage_purchase_orders(user))
