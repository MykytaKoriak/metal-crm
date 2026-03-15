from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Client, Contact, Task
from .models import UserProfile


class TestUserProfile(TestCase):
    def test_profile_created_automatically(self):
        user = get_user_model().objects.create_user(
            username="profile@example.com",
            email="profile@example.com",
            password="secret123",
        )

        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertTrue(user.profile.telegram_link_code)


class TestEmailLogin(TestCase):
    def test_user_can_authenticate_with_email(self):
        user = get_user_model().objects.create_user(
            username="email-login@example.com",
            email="email-login@example.com",
            password="secret123",
        )

        authenticated = authenticate(username="email-login@example.com", password="secret123")

        self.assertEqual(authenticated, user)


class TestMyAccountView(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="account@example.com",
            email="account@example.com",
            password="secret123",
            is_active=True,
        )
        self.user.profile.full_name = "Account User"
        self.user.profile.role = UserProfile.Role.SALES_MANAGER
        self.user.profile.save()

        client = Client.objects.create(name="Client", email="client@example.com")
        self.contact = Contact.objects.create(client=client, full_name="Primary Contact")

    def test_login_required(self):
        response = self.client.get(reverse("my_account"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_my_account_groups_tasks_and_stats(self):
        today = timezone.localdate()
        Task.objects.create(
            contact=self.contact,
            title="Current task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today + timedelta(days=1),
            status=False,
        )
        Task.objects.create(
            contact=self.contact,
            title="Overdue task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today - timedelta(days=1),
            status=False,
        )
        Task.objects.create(
            contact=self.contact,
            title="Completed task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today,
            status=True,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("my_account"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account User")
        self.assertEqual(response.context["current_tasks"].count(), 1)
        self.assertEqual(response.context["overdue_tasks"].count(), 1)
        self.assertEqual(response.context["completed_tasks"].count(), 1)
        self.assertEqual(response.context["stats"]["total_tasks"], 3)
        self.assertEqual(response.context["stats"]["completed_tasks"], 1)
        self.assertEqual(response.context["stats"]["overdue_tasks"], 1)


class TestRoleAccessMatrix(TestCase):
    def create_user_with_role(self, email, role):
        user = get_user_model().objects.create_user(
            username=email,
            email=email,
            password="secret123",
            is_active=True,
        )
        user.profile.role = role
        user.profile.save()
        user.refresh_from_db()
        return user

    def test_administrator_has_full_access(self):
        user = self.create_user_with_role("admin-role@example.com", UserProfile.Role.ADMIN)

        self.assertTrue(user.is_staff)
        self.assertTrue(user.has_perm("crm.change_client"))
        self.assertTrue(user.has_perm("manufacture.delete_productionslot"))
        self.assertTrue(user.has_perm("auth.change_user"))

    def test_sales_manager_has_crm_and_read_only_production(self):
        user = self.create_user_with_role("sales-role@example.com", UserProfile.Role.SALES_MANAGER)

        self.assertTrue(user.has_perm("crm.add_client"))
        self.assertTrue(user.has_perm("crm.change_order"))
        self.assertTrue(user.has_perm("manufacture.view_machine"))
        self.assertFalse(user.has_perm("manufacture.change_productionslot"))
        self.assertFalse(user.has_perm("auth.view_user"))

    def test_production_has_production_crud_and_crm_read_only(self):
        user = self.create_user_with_role("production-role@example.com", UserProfile.Role.PRODUCTION)

        self.assertTrue(user.has_perm("manufacture.change_productionslot"))
        self.assertTrue(user.has_perm("manufacture.add_machine"))
        self.assertTrue(user.has_perm("crm.view_order"))
        self.assertFalse(user.has_perm("crm.change_order"))

    def test_executive_has_read_only_access(self):
        user = self.create_user_with_role("executive-role@example.com", UserProfile.Role.EXECUTIVE)

        self.assertTrue(user.has_perm("crm.view_order"))
        self.assertTrue(user.has_perm("manufacture.view_machine"))
        self.assertFalse(user.has_perm("manufacture.change_machine"))
        self.assertFalse(user.has_perm("crm.add_client"))


class TestProtectedProductionViews(TestCase):
    def create_user_with_role(self, email, role):
        user = get_user_model().objects.create_user(
            username=email,
            email=email,
            password="secret123",
            is_active=True,
        )
        user.profile.role = role
        user.profile.save()
        return user

    def test_machine_load_report_requires_login(self):
        response = self.client.get(reverse("machine_load_report"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_machine_load_report_is_available_for_internal_role(self):
        user = self.create_user_with_role("manager-report@example.com", UserProfile.Role.SALES_MANAGER)

        self.client.force_login(user)
        response = self.client.get(reverse("machine_load_report"))

        self.assertEqual(response.status_code, 200)


class TestCreateRoleAccountsCommand(TestCase):
    def test_command_creates_all_role_accounts(self):
        call_command("create_role_accounts", password="RolePass123!")

        user_model = get_user_model()
        self.assertEqual(
            user_model.objects.filter(
                username__in=[
                    "admin@mkcrm.local",
                    "sales.manager@mkcrm.local",
                    "production@mkcrm.local",
                    "executive@mkcrm.local",
                ]
            ).count(),
            4,
        )

        admin_user = user_model.objects.get(username="admin@mkcrm.local")
        self.assertTrue(admin_user.check_password("RolePass123!"))
        self.assertEqual(admin_user.profile.role, UserProfile.Role.ADMIN)

        sales_user = user_model.objects.get(username="sales.manager@mkcrm.local")
        self.assertEqual(sales_user.profile.role, UserProfile.Role.SALES_MANAGER)

    def test_command_is_idempotent(self):
        call_command("create_role_accounts", password="RolePass123!")
        call_command("create_role_accounts", password="OtherPass123!")

        user_model = get_user_model()
        self.assertEqual(user_model.objects.count(), 4)
        admin_user = user_model.objects.get(username="admin@mkcrm.local")
        self.assertTrue(admin_user.check_password("RolePass123!"))
