from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts_stub.management.commands.seed_demo import DEMO_CATEGORIES, ROLE_GROUPS
from masters.models import ItemCategory, Site


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

    def test_idempotent(self):
        call_command("seed_demo")
        call_command("seed_demo")
        self.assertEqual(Group.objects.count(), len(ROLE_GROUPS))
        self.assertEqual(ItemCategory.objects.count(), len(DEMO_CATEGORIES))
        self.assertEqual(Site.objects.count(), 1)


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
