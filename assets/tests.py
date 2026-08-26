import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from assets.models import Machine, MachineDeployment, MachineLog, MaintenanceSchedule
from masters.models import Site, Vendor


class MachineModelTests(TestCase):
    def test_code_auto_generated(self):
        m1 = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)
        m2 = Machine.objects.create(name="Crane", category=Machine.Category.CRANES, ownership=Machine.Ownership.OWNED)
        self.assertEqual(m1.code, "MC-0001")
        self.assertEqual(m2.code, "MC-0002")

    def test_hired_machine_requires_vendor(self):
        vendor = Vendor.objects.create(name="Crane Hire Co")
        machine = Machine(name="Hydra Crane", category=Machine.Category.CRANES, ownership=Machine.Ownership.HIRED)
        with self.assertRaises(ValidationError):
            machine.full_clean(exclude=["code"])
        machine.hire_vendor = vendor
        machine.full_clean(exclude=["code"])  # should not raise


class MachineDeploymentTests(TestCase):
    def setUp(self):
        self.site1 = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.site2 = Site.objects.create(name="Chennai Factory", code="CHN-F1")
        self.machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)

    def test_current_deployment(self):
        MachineDeployment.objects.create(machine=self.machine, site=self.site1, from_date=datetime.date(2026, 1, 1))
        self.assertEqual(self.machine.current_deployment.site, self.site1)

    def test_only_one_open_deployment_allowed(self):
        MachineDeployment.objects.create(machine=self.machine, site=self.site1, from_date=datetime.date(2026, 1, 1))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MachineDeployment.objects.create(machine=self.machine, site=self.site2, from_date=datetime.date(2026, 2, 1))

    def test_closing_deployment_allows_new_one(self):
        d1 = MachineDeployment.objects.create(machine=self.machine, site=self.site1, from_date=datetime.date(2026, 1, 1))
        d1.close(datetime.date(2026, 2, 1))
        d2 = MachineDeployment.objects.create(machine=self.machine, site=self.site2, from_date=datetime.date(2026, 2, 1))
        self.assertEqual(self.machine.current_deployment, d2)

    def test_to_date_before_from_date_rejected(self):
        deployment = MachineDeployment(machine=self.machine, site=self.site1, from_date=datetime.date(2026, 2, 1), to_date=datetime.date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            deployment.full_clean()


class MachineLogTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)

    def test_log_requires_active_deployment(self):
        log = MachineLog(machine=self.machine, hours_run=Decimal("8"))
        with self.assertRaises(ValidationError):
            log.save()

    def test_log_site_auto_from_deployment(self):
        MachineDeployment.objects.create(machine=self.machine, site=self.site, from_date=datetime.date(2026, 1, 1))
        log = MachineLog.objects.create(machine=self.machine, hours_run=Decimal("8"))
        self.assertEqual(log.site, self.site)


class MaintenanceScheduleTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)
        MachineDeployment.objects.create(machine=self.machine, site=self.site, from_date=datetime.date(2026, 1, 1))

    def test_requires_at_least_one_interval(self):
        schedule = MaintenanceSchedule(machine=self.machine)
        with self.assertRaises(ValidationError):
            schedule.full_clean()

    def test_due_when_never_done(self):
        schedule = MaintenanceSchedule.objects.create(machine=self.machine, every_n_days=30)
        self.assertTrue(schedule.is_due)

    def test_not_due_within_interval(self):
        schedule = MaintenanceSchedule.objects.create(
            machine=self.machine, every_n_days=30, last_done_date=datetime.date.today() - datetime.timedelta(days=10)
        )
        self.assertFalse(schedule.is_due)

    def test_due_past_interval(self):
        schedule = MaintenanceSchedule.objects.create(
            machine=self.machine, every_n_days=30, last_done_date=datetime.date.today() - datetime.timedelta(days=31)
        )
        self.assertTrue(schedule.is_due)

    def test_due_by_hours(self):
        schedule = MaintenanceSchedule.objects.create(machine=self.machine, every_n_hours=100, last_done_date=datetime.date(2026, 1, 1))
        MachineLog.objects.create(machine=self.machine, log_date=datetime.date(2026, 1, 5), hours_run=Decimal("60"))
        MachineLog.objects.create(machine=self.machine, log_date=datetime.date(2026, 1, 10), hours_run=Decimal("50"))
        self.assertTrue(schedule.is_due)

    def test_mark_done_resets(self):
        schedule = MaintenanceSchedule.objects.create(machine=self.machine, every_n_days=30)
        self.assertTrue(schedule.is_due)
        schedule.mark_done()
        self.assertFalse(schedule.is_due)


class CheckMaintenanceDueCommandTests(TestCase):
    def test_notifies_scm_head(self):
        from django.core.management import call_command
        from accounts_stub.models import Notification

        site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)
        MachineDeployment.objects.create(machine=machine, site=site, from_date=datetime.date(2026, 1, 1))
        MaintenanceSchedule.objects.create(machine=machine, every_n_days=30)

        head = User.objects.create_user(username="head", password="pass12345", email="head@example.com")
        head.groups.add(Group.objects.create(name="SCM Head"))

        call_command("check_maintenance_due")
        self.assertTrue(Notification.objects.filter(recipient=head).exists())


class AssetViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Hyderabad Factory 1", code="HYD-F1")
        self.user = User.objects.create_user(username="po", password="pass12345")
        self.user.groups.add(Group.objects.create(name="Purchase Officer (HO)"))
        self.client.login(username="po", password="pass12345")

    def test_register_machine_via_view(self):
        response = self.client.post(reverse("assets:machine_create"), {
            "name": "Transit Mixer", "category": "vehicles", "ownership": "owned",
            "hire_vendor": "", "hire_rate": "", "rate_unit": "", "purchase_date": "", "purchase_value": "",
            "status": "active",
        })
        machine = Machine.objects.get(name="Transit Mixer")
        self.assertRedirects(response, reverse("assets:machine_detail", args=[machine.pk]))

    def test_deploy_via_view(self):
        machine = Machine.objects.create(name="Batching Plant", category=Machine.Category.BATCHING, ownership=Machine.Ownership.OWNED)
        response = self.client.post(reverse("assets:machine_deploy", args=[machine.pk]), {
            "site": self.site.pk, "from_date": "2026-01-01", "remarks": "",
        })
        self.assertRedirects(response, reverse("assets:machine_detail", args=[machine.pk]))
        self.assertEqual(machine.current_deployment.site, self.site)

    def test_site_member_cannot_register_machine(self):
        member = User.objects.create_user(username="siteuser", password="pass12345")
        member.groups.add(Group.objects.create(name="Site Member"))
        self.client.login(username="siteuser", password="pass12345")
        response = self.client.get(reverse("assets:machine_create"))
        self.assertEqual(response.status_code, 403)

    def test_site_member_can_view_machine_list(self):
        member = User.objects.create_user(username="siteuser2", password="pass12345")
        member.groups.add(Group.objects.create(name="Site Member"))
        self.client.login(username="siteuser2", password="pass12345")
        response = self.client.get(reverse("assets:machine_list"))
        self.assertEqual(response.status_code, 200)
