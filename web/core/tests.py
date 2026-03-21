from datetime import datetime, time, timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Client, Contact, Order, OrderItem, Product, Task
from manufacture.models import Machine, ProductionSlot, ProductionStage
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


class TestProjectSettings(TestCase):
    def test_project_uses_kyiv_timezone(self):
        self.assertEqual(settings.TIME_ZONE, "Europe/Kyiv")


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
            client=self.contact.client,
            contact=self.contact,
            title="Current task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today + timedelta(days=1),
            status=Task.Status.IN_PROGRESS,
        )
        Task.objects.create(
            client=self.contact.client,
            contact=self.contact,
            title="Overdue task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today - timedelta(days=1),
            status=Task.Status.WAITING,
        )
        Task.objects.create(
            client=self.contact.client,
            contact=self.contact,
            title="Completed task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today,
            status=Task.Status.DONE,
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
        self.assertTrue(user.has_perm("manufacture.change_productionstage"))
        self.assertTrue(user.has_perm("manufacture.delete_productionslot"))
        self.assertTrue(user.has_perm("auth.change_user"))

    def test_sales_manager_has_crm_and_read_only_production(self):
        user = self.create_user_with_role("sales-role@example.com", UserProfile.Role.SALES_MANAGER)

        self.assertTrue(user.has_perm("crm.add_client"))
        self.assertTrue(user.has_perm("crm.change_order"))
        self.assertTrue(user.has_perm("manufacture.view_productionstage"))
        self.assertTrue(user.has_perm("manufacture.view_machine"))
        self.assertFalse(user.has_perm("manufacture.change_productionslot"))
        self.assertFalse(user.has_perm("auth.view_user"))

    def test_production_has_production_crud_and_crm_read_only(self):
        user = self.create_user_with_role("production-role@example.com", UserProfile.Role.PRODUCTION)

        self.assertTrue(user.has_perm("manufacture.change_productionstage"))
        self.assertTrue(user.has_perm("manufacture.change_productionslot"))
        self.assertTrue(user.has_perm("manufacture.add_machine"))
        self.assertTrue(user.has_perm("crm.view_order"))
        self.assertFalse(user.has_perm("crm.change_order"))

    def test_executive_has_read_only_access(self):
        user = self.create_user_with_role("executive-role@example.com", UserProfile.Role.EXECUTIVE)

        self.assertTrue(user.has_perm("crm.view_order"))
        self.assertTrue(user.has_perm("manufacture.view_productionstage"))
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


class DashboardTestMixin:
    def create_user_with_role(self, email, role):
        user = get_user_model().objects.create_user(
            username=email,
            email=email,
            password="secret123",
            is_active=True,
        )
        user.profile.role = role
        user.profile.full_name = email.split("@", 1)[0]
        user.profile.save()
        return user

    def create_order(self, contact, manager=None, status=Order.Status.NEW, deadline=None, quantity=1, unit_price="100.00"):
        product = Product.objects.create(name=f"Product {contact.full_name}", sku=f"SKU-{uuid4().hex[:12]}")
        order = Order.objects.create(
            contact=contact,
            manager=manager,
            status=status,
            deadline=deadline,
            payment_amount=unit_price,
        )
        OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price=unit_price)
        order.refresh_title()
        return order


class TestDashboardRouting(DashboardTestMixin, TestCase):
    def test_dashboard_router_redirects_by_role(self):
        user = self.create_user_with_role("sales-dashboard@example.com", UserProfile.Role.SALES_MANAGER)

        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(response, reverse("sales_dashboard"))

    def test_admin_can_open_all_dashboards(self):
        user = self.create_user_with_role("admin-dashboard@example.com", UserProfile.Role.ADMIN)

        self.client.force_login(user)
        for route_name in ("admin_dashboard", "sales_dashboard", "production_dashboard", "executive_dashboard"):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)

    def test_cross_role_access_is_denied_for_non_admin(self):
        user = self.create_user_with_role("sales-only@example.com", UserProfile.Role.SALES_MANAGER)

        self.client.force_login(user)
        response = self.client.get(reverse("executive_dashboard"))

        self.assertEqual(response.status_code, 403)


class TestSalesDashboard(DashboardTestMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("sales-owner@example.com", UserProfile.Role.SALES_MANAGER)
        self.other_user = self.create_user_with_role("other-sales@example.com", UserProfile.Role.SALES_MANAGER)
        client = Client.objects.create(name="Sales Client", email="sales-client@example.com")
        self.contact = Contact.objects.create(client=client, full_name="Sales Contact")

    def test_sales_dashboard_shows_my_orders_tasks_and_deadlines(self):
        today = timezone.localdate()
        Task.objects.create(
            client=self.contact.client,
            contact=self.contact,
            title="Today task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today,
            status=Task.Status.NEW,
        )
        Task.objects.create(
            client=self.contact.client,
            contact=self.contact,
            title="Planned task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today + timedelta(days=2),
            status=Task.Status.IN_PROGRESS,
        )
        Task.objects.create(
            client=self.contact.client,
            contact=self.contact,
            title="Late task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today - timedelta(days=1),
            status=Task.Status.WAITING,
        )
        Task.objects.create(
            client=self.contact.client,
            contact=self.contact,
            title="Done task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today,
            status=Task.Status.DONE,
        )

        my_order = self.create_order(
            self.contact,
            manager=self.user,
            status=Order.Status.IN_PROGRESS,
            deadline=today + timedelta(days=3),
            unit_price="250.00",
        )
        self.create_order(
            self.contact,
            manager=self.user,
            status=Order.Status.NEW,
            deadline=today + timedelta(days=20),
            unit_price="300.00",
        )
        self.create_order(
            self.contact,
            manager=self.other_user,
            status=Order.Status.NEW,
            deadline=today + timedelta(days=2),
            unit_price="999.00",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("sales_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["orders_count"], 2)
        self.assertEqual(response.context["task_stats"]["open_tasks"], 3)
        self.assertEqual(response.context["task_stats"]["overdue_tasks"], 1)
        self.assertContains(response, "Робоче меню")
        self.assertContains(response, "Клієнти")
        self.assertContains(response, "Замовлення")
        self.assertContains(response, reverse("crm_tasks_kanban"))
        self.assertContains(response, my_order.title)
        self.assertEqual(len(response.context["nearest_deadlines"]), 1)


class TestProductionDashboard(DashboardTestMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("production-dashboard@example.com", UserProfile.Role.PRODUCTION)
        client = Client.objects.create(name="Production Client", email="prod-client@example.com")
        self.contact = Contact.objects.create(client=client, full_name="Production Contact")

    def test_production_dashboard_shows_queue_and_overdue_orders(self):
        today = timezone.localdate()
        machine = Machine.objects.create(name="Laser A", type=Machine.MachineType.LASER)
        active_order = self.create_order(
            self.contact,
            status=Order.Status.IN_PROGRESS,
            deadline=today + timedelta(days=1),
            unit_price="500.00",
        )
        overdue_order = self.create_order(
            self.contact,
            status=Order.Status.IN_PROGRESS,
            deadline=today - timedelta(days=2),
            unit_price="700.00",
        )
        slot_date = today
        while slot_date.weekday() > 4:
            slot_date += timedelta(days=1)
        slot_start = timezone.make_aware(datetime.combine(slot_date, time(10, 0)))
        slot_end = timezone.make_aware(datetime.combine(slot_date, time(12, 0)))
        stage = active_order.items.first().production_stages.get(
            stage_type=ProductionStage.StageType.EXECUTION
        )
        stage.status = ProductionStage.Status.IN_PROGRESS
        stage.planned_start = slot_start
        stage.planned_end = slot_end
        stage.save(update_fields=["status", "planned_start", "planned_end", "updated_at"])
        ProductionSlot.objects.create(
            order=active_order,
            stage=stage,
            machine=machine,
            start_datetime=slot_start,
            end_datetime=slot_end,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("production_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["active_queue"]), 1)
        self.assertEqual(len(response.context["overdue_orders"]), 1)
        self.assertContains(response, stage.get_stage_type_display())
        self.assertContains(response, overdue_order.title)

    def test_order_item_creates_default_stage_flow(self):
        order = self.create_order(self.contact, status=Order.Status.NEW, unit_price="150.00")

        stage_types = list(
            order.items.first().production_stages.order_by("sequence").values_list("stage_type", flat=True)
        )

        self.assertEqual(
            stage_types,
            [
                ProductionStage.StageType.INTAKE,
                ProductionStage.StageType.PROCUREMENT,
                ProductionStage.StageType.EXECUTION,
                ProductionStage.StageType.PAINTING,
                ProductionStage.StageType.READY_TO_SHIP,
            ],
        )


class TestExecutiveDashboard(DashboardTestMixin, TestCase):
    def test_executive_dashboard_shows_recent_entities_and_revenue(self):
        user = self.create_user_with_role("executive-dashboard@example.com", UserProfile.Role.EXECUTIVE)
        client = Client.objects.create(name="Executive Client", email="executive-client@example.com")
        contact = Contact.objects.create(client=client, full_name="Executive Contact")
        self.create_order(
            contact,
            status=Order.Status.COMPLETED,
            deadline=timezone.localdate() + timedelta(days=5),
            unit_price="420.00",
        )

        self.client.force_login(user)
        response = self.client.get(reverse("executive_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["new_clients_count"], 1)
        self.assertEqual(response.context["new_orders_count"], 1)
        self.assertEqual(response.context["monthly_revenue"][-1]["revenue"], 420)
        self.assertContains(response, "Executive Client")
