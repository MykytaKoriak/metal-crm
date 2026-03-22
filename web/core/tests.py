from datetime import datetime, time, timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import Client, Contact, Order, OrderItem, Product, Task
from manufacture.models import Machine, ProductionSlot, ProductionStage, ResourceDowntime, WorkUnit
from .models import ChangeAuditLog, TelegramNotification, TelegramUpdateLog, UserProfile


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
        self.assertTrue(user.has_perm("core.view_changeauditlog"))

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
        self.assertFalse(user.has_perm("core.view_changeauditlog"))


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

    def test_executive_dashboard_shows_problem_zones_and_conversion(self):
        user = self.create_user_with_role("executive-risk@example.com", UserProfile.Role.EXECUTIVE)
        manager = self.create_user_with_role("manager-risk@example.com", UserProfile.Role.SALES_MANAGER)
        client = Client.objects.create(name="Risk Client", email="risk-client@example.com")
        contact = Contact.objects.create(client=client, full_name="Risk Contact")
        today = timezone.localdate()

        risk_order = self.create_order(
            contact,
            manager=manager,
            status=Order.Status.IN_PRODUCTION,
            deadline=today + timedelta(days=1),
            unit_price="500.00",
        )
        ready_order = self.create_order(
            contact,
            manager=manager,
            status=Order.Status.READY,
            deadline=today + timedelta(days=3),
            unit_price="300.00",
        )

        risk_stage = risk_order.items.first().production_stages.get(
            stage_type=ProductionStage.StageType.EXECUTION
        )
        risk_stage.status = ProductionStage.Status.IN_PROGRESS
        risk_stage.planned_start = timezone.now() - timedelta(days=2)
        risk_stage.planned_end = timezone.now() - timedelta(hours=3)
        risk_stage.started_at = timezone.now() - timedelta(days=1)
        risk_stage.responsible = manager
        risk_stage.save(
            update_fields=["status", "planned_start", "planned_end", "started_at", "responsible", "updated_at"]
        )

        Task.objects.create(
            client=client,
            contact=contact,
            order=risk_order,
            title="Waiting executive task",
            assigned_by=user,
            assigned_to=manager,
            date=today - timedelta(days=2),
            status=Task.Status.WAITING,
        )

        critical_machine = Machine.objects.create(
            name="Critical Laser",
            type=Machine.MachineType.LASER,
            available_weekdays="0,1,2,3,4,5,6",
            workday_start=time(8, 0),
            workday_end=time(9, 0),
        )
        for offset in range(8):
            day = today + timedelta(days=offset)
            slot = ProductionSlot(
                order=ready_order,
                machine=critical_machine,
                start_datetime=timezone.make_aware(datetime.combine(day, time(8, 0))),
                end_datetime=timezone.make_aware(datetime.combine(day, time(9, 0))),
                slot_type=ProductionSlot.SlotType.RESERVATION,
                planning_mode=ProductionSlot.PlanningMode.MANUAL,
                planning_source=ProductionSlot.PlanningSource.ADMIN,
                is_locked=True,
                purpose="Executive load test",
            )
            slot._planner_operation = True
            slot.save()

        self.client.force_login(user)
        response = self.client.get(reverse("executive_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.context["executive_summary"]["at_risk_order_count"], 1)
        self.assertGreaterEqual(response.context["executive_summary"]["critical_resource_count"], 1)
        self.assertGreaterEqual(response.context["executive_summary"]["stalled_task_count"], 1)
        self.assertGreater(response.context["executive_summary"]["in_production_share"], 0)
        self.assertTrue(any(row["count"] for row in response.context["order_status_rows"]))
        self.assertContains(response, risk_order.title)
        self.assertContains(response, "Waiting executive task")


class TestTelegramWebhook(DashboardTestMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("telegram-link@example.com", UserProfile.Role.SALES_MANAGER)
        self.profile = self.user.profile
        self.profile.full_name = "Telegram Manager"
        self.profile.save()

    @patch("core.telegram.handlers.send_message")
    def test_webhook_links_profile_by_code(self, send_message_mock):
        payload = {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "text": f"/link {self.profile.telegram_link_code}",
                "chat": {"id": 555001},
                "from": {"id": 77, "username": "crm_user"},
            },
        }

        response = self.client.post(
            reverse("telegram_webhook"),
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.telegram_chat_id, "555001")
        self.assertEqual(self.profile.telegram_username, "crm_user")
        self.assertTrue(TelegramUpdateLog.objects.filter(update_id=1001).exists())
        send_message_mock.assert_called_once()

    @patch("core.telegram.handlers.send_message")
    def test_webhook_returns_tasks_for_linked_chat(self, send_message_mock):
        today = timezone.localdate()
        self.profile.telegram_chat_id = "555001"
        self.profile.save(update_fields=["telegram_chat_id"])

        client = Client.objects.create(name="Telegram Client", email="telegram-client@example.com")
        contact = Contact.objects.create(client=client, full_name="Telegram Contact")
        Task.objects.create(
            client=client,
            contact=contact,
            title="Telegram task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today,
            status=Task.Status.NEW,
        )

        payload = {
            "update_id": 1002,
            "message": {
                "message_id": 2,
                "text": "/tasks",
                "chat": {"id": 555001},
                "from": {"id": 77, "username": "crm_user"},
            },
        }

        response = self.client.post(
            reverse("telegram_webhook"),
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        send_args, send_kwargs = send_message_mock.call_args
        self.assertEqual(send_args[0], "555001")
        self.assertIn("Telegram task", send_args[1])
        self.assertIn("reply_markup", send_kwargs)


class TestTelegramNotifications(DashboardTestMixin, TestCase):
    def setUp(self):
        self.manager = self.create_user_with_role("telegram-manager@example.com", UserProfile.Role.SALES_MANAGER)
        self.manager.profile.telegram_chat_id = "70001"
        self.manager.profile.save(update_fields=["telegram_chat_id"])

        self.production_user = self.create_user_with_role("telegram-production@example.com", UserProfile.Role.PRODUCTION)
        self.production_user.profile.telegram_chat_id = "70002"
        self.production_user.profile.save(update_fields=["telegram_chat_id"])

        self.client_entity = Client.objects.create(name="Notify Client", email="notify@example.com")
        self.contact = Contact.objects.create(client=self.client_entity, full_name="Notify Contact")

    def test_task_creation_queues_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            task = Task.objects.create(
                client=self.client_entity,
                contact=self.contact,
                title="Notify task",
                assigned_by=self.manager,
                assigned_to=self.manager,
                date=timezone.localdate() + timedelta(days=1),
                status=Task.Status.NEW,
            )

        notification = TelegramNotification.objects.get(task=task, notification_type=TelegramNotification.Type.TASK_CREATED)
        self.assertEqual(notification.profile, self.manager.profile)
        self.assertEqual(notification.status, TelegramNotification.Status.PENDING)

    def test_order_status_change_queues_notification(self):
        order = self.create_order(
            self.contact,
            manager=self.manager,
            status=Order.Status.NEW,
            deadline=timezone.localdate() + timedelta(days=4),
            unit_price="320.00",
        )

        with self.captureOnCommitCallbacks(execute=True):
            order.status = Order.Status.IN_PRODUCTION
            order.save(update_fields=["status"])

        notification = TelegramNotification.objects.get(order=order, notification_type=TelegramNotification.Type.ORDER_STATUS)
        self.assertEqual(notification.profile, self.manager.profile)
        self.assertIn("IN_PRODUCTION".lower(), notification.dedupe_key)

    def test_stage_status_change_queues_production_notification(self):
        order = self.create_order(
            self.contact,
            manager=self.manager,
            status=Order.Status.IN_PRODUCTION,
            deadline=timezone.localdate() + timedelta(days=2),
            unit_price="410.00",
        )
        stage = order.items.first().production_stages.get(stage_type=ProductionStage.StageType.EXECUTION)
        stage.responsible = self.production_user
        stage.save(update_fields=["responsible", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            stage.status = ProductionStage.Status.IN_PROGRESS
            stage.started_at = timezone.now()
            stage.save(update_fields=["status", "started_at", "updated_at"])

        notifications = TelegramNotification.objects.filter(
            stage=stage,
            notification_type=TelegramNotification.Type.PRODUCTION_EVENT,
        )
        self.assertEqual(notifications.count(), 2)

    @patch("core.telegram.services.send_message")
    def test_process_notification_queue_sends_and_deduplicates_deadline_reminders(self, send_message_mock):
        task = Task.objects.create(
            client=self.client_entity,
            contact=self.contact,
            title="Deadline task",
            assigned_by=self.manager,
            assigned_to=self.manager,
            date=timezone.localdate() + timedelta(days=1),
            status=Task.Status.IN_PROGRESS,
        )
        order = self.create_order(
            self.contact,
            manager=self.manager,
            status=Order.Status.IN_PROGRESS,
            deadline=timezone.localdate() + timedelta(days=1),
            unit_price="150.00",
        )

        send_message_mock.return_value = {"message_id": 99}

        from core.telegram.services import process_notification_queue

        result_first = process_notification_queue(now=timezone.now(), limit=20)
        result_second = process_notification_queue(now=timezone.now(), limit=20)

        self.assertEqual(result_first["queued"], 2)
        self.assertEqual(result_first["delivered"], 2)
        self.assertEqual(result_second["queued"], 0)
        self.assertGreaterEqual(send_message_mock.call_count, 2)
        self.assertTrue(
            TelegramNotification.objects.filter(task=task, notification_type=TelegramNotification.Type.TASK_DEADLINE).exists()
        )
        self.assertTrue(
            TelegramNotification.objects.filter(order=order, notification_type=TelegramNotification.Type.ORDER_DEADLINE).exists()
        )

    def test_account_page_updates_telegram_preferences(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("update_telegram_preferences"),
            data={
                "telegram_notifications_enabled": "on",
                "telegram_notify_deadlines": "on",
                "telegram_notify_order_updates": "on",
            },
        )

        self.assertRedirects(response, reverse("my_account"))
        self.manager.profile.refresh_from_db()
        self.assertTrue(self.manager.profile.telegram_notifications_enabled)
        self.assertFalse(self.manager.profile.telegram_notify_new_tasks)
        self.assertTrue(self.manager.profile.telegram_notify_deadlines)
        self.assertFalse(self.manager.profile.telegram_notify_overdue)
        self.assertTrue(self.manager.profile.telegram_notify_order_updates)
        self.assertFalse(self.manager.profile.telegram_notify_production_events)


class TestChangeAuditLog(DashboardTestMixin, TestCase):
    def setUp(self):
        self.manager = self.create_user_with_role("audit-manager@example.com", UserProfile.Role.SALES_MANAGER)
        self.production_user = self.create_user_with_role("audit-production@example.com", UserProfile.Role.PRODUCTION)
        self.client_entity = Client.objects.create(name="Audit Client", email="audit-client@example.com")
        self.contact = Contact.objects.create(client=self.client_entity, full_name="Audit Contact")
        self.storage = WorkUnit.objects.create(name="Audit Storage", type=WorkUnit.UnitType.STORAGE)
        self.assembly = WorkUnit.objects.create(name="Audit Assembly", type=WorkUnit.UnitType.ASSEMBLY)
        self.laser = Machine.objects.create(name="Audit Laser", type=Machine.MachineType.LASER)
        self.paint = Machine.objects.create(name="Audit Paint", type=Machine.MachineType.PAINTING)

    def create_order_with_item(self):
        product = Product.objects.create(name=f"Audit Product {uuid4().hex[:6]}", sku=f"AUD-{uuid4().hex[:8]}")
        with self.captureOnCommitCallbacks(execute=True):
            order = Order.objects.create(
                contact=self.contact,
                manager=self.manager,
                status=Order.Status.NEW,
                deadline=timezone.localdate() + timedelta(days=3),
            )
            OrderItem.objects.create(order=order, product=product, quantity=1, unit_price="250.00")
        return Order.objects.get(pk=order.pk)

    def test_order_and_task_changes_are_logged_with_actor_and_fields(self):
        order = self.create_order_with_item()

        created_order_log = ChangeAuditLog.objects.filter(
            entity_type=ChangeAuditLog.EntityType.ORDER,
            action=ChangeAuditLog.Action.CREATED,
            object_id=order.pk,
        ).first()
        self.assertIsNotNone(created_order_log)
        self.assertEqual(created_order_log.order_id, order.pk)

        order._changed_by = self.manager
        order.priority = Order.Priority.URGENT
        order.comment = "Escalated to production"
        order.save(update_fields=["priority", "comment"])

        updated_order_log = ChangeAuditLog.objects.filter(
            entity_type=ChangeAuditLog.EntityType.ORDER,
            action=ChangeAuditLog.Action.UPDATED,
            object_id=order.pk,
        ).first()
        self.assertIsNotNone(updated_order_log)
        self.assertEqual(updated_order_log.changed_by, self.manager)
        self.assertIn("priority", updated_order_log.changed_fields)
        self.assertIn("comment", updated_order_log.changed_fields)

        task = Task(
            client=self.client_entity,
            contact=self.contact,
            order=order,
            title="Audit task",
            assigned_by=self.manager,
            assigned_to=self.manager,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )
        task._changed_by = self.manager
        task.save()

        created_task_log = ChangeAuditLog.objects.filter(
            entity_type=ChangeAuditLog.EntityType.TASK,
            action=ChangeAuditLog.Action.CREATED,
            object_id=task.pk,
        ).first()
        self.assertIsNotNone(created_task_log)
        self.assertEqual(created_task_log.changed_by, self.manager)

        task_id = task.pk
        task._changed_by = self.manager
        task.delete()

        deleted_task_log = ChangeAuditLog.objects.filter(
            entity_type=ChangeAuditLog.EntityType.TASK,
            action=ChangeAuditLog.Action.DELETED,
            object_id=task_id,
        ).first()
        self.assertIsNotNone(deleted_task_log)
        self.assertEqual(deleted_task_log.changed_by, self.manager)
        self.assertIn("status", deleted_task_log.snapshot_before)

    def test_stage_slot_and_cascade_deletes_are_logged(self):
        order = self.create_order_with_item()
        stage = order.items.first().production_stages.get(stage_type=ProductionStage.StageType.EXECUTION)
        slot = order.slots.order_by("id").first()
        self.assertIsNotNone(slot)

        stage._changed_by = self.production_user
        stage.status = ProductionStage.Status.IN_PROGRESS
        stage.started_at = timezone.now()
        stage.save(update_fields=["status", "started_at", "updated_at"])

        updated_stage_log = ChangeAuditLog.objects.filter(
            entity_type=ChangeAuditLog.EntityType.PRODUCTION_STAGE,
            action=ChangeAuditLog.Action.UPDATED,
            object_id=stage.pk,
        ).first()
        self.assertIsNotNone(updated_stage_log)
        self.assertEqual(updated_stage_log.changed_by, self.production_user)
        self.assertIn("status", updated_stage_log.changed_fields)

        slot._changed_by = self.production_user
        slot.dispatcher_comment = "Dispatcher note"
        slot.save()

        updated_slot_log = ChangeAuditLog.objects.filter(
            entity_type=ChangeAuditLog.EntityType.PRODUCTION_SLOT,
            action=ChangeAuditLog.Action.UPDATED,
            object_id=slot.pk,
        ).first()
        self.assertIsNotNone(updated_slot_log)
        self.assertEqual(updated_slot_log.changed_by, self.production_user)
        self.assertIn("dispatcher_comment", updated_slot_log.changed_fields)

        order_id = order.pk
        stage_ids = list(order.items.first().production_stages.values_list("id", flat=True))
        slot_ids = list(order.slots.values_list("id", flat=True))

        order._changed_by = self.manager
        order.delete()

        self.assertTrue(
            ChangeAuditLog.objects.filter(
                entity_type=ChangeAuditLog.EntityType.ORDER,
                action=ChangeAuditLog.Action.DELETED,
                object_id=order_id,
            ).exists()
        )
        self.assertEqual(
            ChangeAuditLog.objects.filter(
                entity_type=ChangeAuditLog.EntityType.PRODUCTION_STAGE,
                action=ChangeAuditLog.Action.DELETED,
                object_id__in=stage_ids,
            ).count(),
            len(stage_ids),
        )
        self.assertEqual(
            ChangeAuditLog.objects.filter(
                entity_type=ChangeAuditLog.EntityType.PRODUCTION_SLOT,
                action=ChangeAuditLog.Action.DELETED,
                object_id__in=slot_ids,
            ).count(),
            len(slot_ids),
        )


class TestSeedDemoDataCommand(TestCase):
    def run_seed(self):
        with self.captureOnCommitCallbacks(execute=True):
            call_command("seed_demo_data")

    def test_seed_demo_data_creates_rich_demo_scenarios(self):
        self.run_seed()

        self.assertTrue(get_user_model().objects.filter(username="demo_manager").exists())
        self.assertEqual(
            UserProfile.objects.get(user__username="demo_production").role,
            UserProfile.Role.PRODUCTION,
        )
        self.assertEqual(Order.objects.filter(comment__startswith="[seed-demo]").count(), 14)
        self.assertEqual(Task.objects.filter(comment__startswith="[seed-demo]").count(), 6)
        self.assertGreaterEqual(ProductionSlot.objects.count(), 13)
        self.assertTrue(
            ProductionStage.objects.filter(status=ProductionStage.Status.BLOCKED).exists()
        )
        self.assertTrue(
            ProductionStage.objects.filter(
                status=ProductionStage.Status.IN_PROGRESS,
                planned_end__lt=timezone.now(),
            ).exists()
        )
        self.assertTrue(
            ProductionSlot.objects.filter(
                planning_mode=ProductionSlot.PlanningMode.MANUAL,
                is_locked=True,
            ).exists()
        )
        self.assertTrue(
            ProductionSlot.objects.filter(
                planning_mode=ProductionSlot.PlanningMode.AUTO,
                planning_source=ProductionSlot.PlanningSource.PLANNER,
            ).exists()
        )
        self.assertTrue(Machine.objects.filter(is_active=False).exists())
        self.assertEqual(ResourceDowntime.objects.filter(comment__startswith="[seed-demo]").count(), 2)
        self.assertEqual(TelegramUpdateLog.objects.count(), 3)
        self.assertTrue(
            TelegramNotification.objects.filter(
                notification_type=TelegramNotification.Type.TASK_DEADLINE
            ).exists()
        )
        self.assertTrue(
            TelegramNotification.objects.filter(
                notification_type=TelegramNotification.Type.ORDER_OVERDUE
            ).exists()
        )
        self.assertTrue(
            TelegramNotification.objects.filter(status=TelegramNotification.Status.SENT).exists()
        )
        self.assertTrue(
            TelegramNotification.objects.filter(status=TelegramNotification.Status.FAILED).exists()
        )
        self.assertTrue(
            TelegramNotification.objects.filter(status=TelegramNotification.Status.PENDING).exists()
        )

    def test_seed_demo_data_is_idempotent_for_runtime_entities(self):
        self.run_seed()
        self.run_seed()

        self.assertEqual(Order.objects.filter(comment__startswith="[seed-demo]").count(), 14)
        self.assertEqual(Task.objects.filter(comment__startswith="[seed-demo]").count(), 6)
        self.assertEqual(ResourceDowntime.objects.filter(comment__startswith="[seed-demo]").count(), 2)
        self.assertEqual(TelegramUpdateLog.objects.count(), 3)
        self.assertEqual(
            get_user_model().objects.filter(
                username__in=["demo_admin", "demo_manager", "demo_production", "demo_executive"]
            ).count(),
            4,
        )


class TestTelegramManagementCommands(TestCase):
    @patch("core.management.commands.process_telegram_notifications.process_notification_queue")
    def test_process_telegram_notifications_command_delegates_to_service(self, process_mock):
        process_mock.return_value = {"queued": 3, "delivered": 2}
        stdout = StringIO()

        call_command("process_telegram_notifications", limit=25, stdout=stdout)

        process_mock.assert_called_once()
        self.assertEqual(process_mock.call_args.kwargs["limit"], 25)
        self.assertIn("now", process_mock.call_args.kwargs)
        self.assertIn("queued=3 delivered=2", stdout.getvalue())

    @patch("core.management.commands.telegram_pull_updates.pull_updates_and_process")
    def test_telegram_pull_updates_command_delegates_to_handler(self, pull_mock):
        pull_mock.return_value = {"processed": 4, "next_offset": 123}
        stdout = StringIO()

        call_command("telegram_pull_updates", offset=10, limit=5, timeout=20, stdout=stdout)

        pull_mock.assert_called_once_with(offset=10, limit=5, timeout=20)
        self.assertIn("processed=4 next_offset=123", stdout.getvalue())

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_run_telegram_bot_requires_token(self):
        with self.assertRaises(CommandError):
            call_command("run_telegram_bot", stdout=StringIO(), stderr=StringIO())

    @override_settings(TELEGRAM_BOT_TOKEN="token")
    @patch("core.management.commands.run_telegram_bot.close_old_connections")
    @patch("core.management.commands.run_telegram_bot.pull_updates_and_process")
    @patch("core.management.commands.run_telegram_bot.process_notification_queue")
    def test_run_telegram_bot_processes_cycle_and_stops_on_keyboard_interrupt(
        self,
        process_mock,
        pull_mock,
        close_connections_mock,
    ):
        process_mock.side_effect = [
            {"queued": 1, "delivered": 1},
            {"queued": 0, "delivered": 0},
        ]
        pull_mock.side_effect = [
            {"processed": 2, "next_offset": 55},
            KeyboardInterrupt(),
        ]
        stdout = StringIO()

        call_command(
            "run_telegram_bot",
            poll_timeout=1,
            notify_limit=10,
            retry_delay=1,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertGreaterEqual(close_connections_mock.call_count, 1)
        self.assertEqual(process_mock.call_args_list[0].kwargs["limit"], 10)
        self.assertEqual(pull_mock.call_args_list[0].kwargs, {"offset": None, "limit": 100, "timeout": 1})
        self.assertEqual(pull_mock.call_args_list[1].kwargs, {"offset": 55, "limit": 100, "timeout": 1})
        self.assertIn("Telegram bot loop started.", stdout.getvalue())
        self.assertIn("queued=1 delivered=1 updates=2 next_offset=55", stdout.getvalue())
