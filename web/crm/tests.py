from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ChangeAuditLog, TelegramNotification
from core.models import UserProfile
from manufacture.models import Machine, ProductionStage, WorkUnit

from .models import Client, ClientInteraction, Contact, Order, OrderItem, Product, ProductProductionNorm, Tag, Task


class CrmWorkspaceMixin:
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

    def create_product(self, name_prefix="Product"):
        return Product.objects.create(
            name=f"{name_prefix} {uuid4().hex[:6]}",
            sku=f"SKU-{uuid4().hex[:10]}",
            base_price="250.00",
        )

    def create_order(self, contact, manager):
        product = self.create_product()
        order = Order.objects.create(
            contact=contact,
            manager=manager,
            status=Order.Status.IN_PROGRESS,
            deadline=timezone.localdate() + timedelta(days=3),
            payment_amount="250.00",
        )
        OrderItem.objects.create(order=order, product=product, quantity=2, unit_price="250.00")
        order.refresh_title()
        return order

    def build_order_payload(self, *, contact, manager, product, order=None):
        today = timezone.localdate()
        payload = {
            "contact": str(contact.id),
            "manager": str(manager.id),
            "status": Order.Status.NEW,
            "deadline": (today + timedelta(days=7)).isoformat(),
            "comment": "Workspace order",
            "delivery_method": Order.DeliveryMethod.NOVA_POSHTA,
            "shipping_address": "Kyiv, branch 1",
            "recipient": "Receiver Name",
            "recipient_phone": "+380671234567",
            "tracking_number": "TTN-001",
            "payment_type": Order.PaymentType.PREPAY,
            "payment_terms": "100% prepaid",
            "payment_amount": "300.00",
        }
        if order is None:
            payload.update(
                {
                    "items-TOTAL_FORMS": "1",
                    "items-INITIAL_FORMS": "0",
                    "items-MIN_NUM_FORMS": "0",
                    "items-MAX_NUM_FORMS": "1000",
                    "items-0-product": str(product.id),
                    "items-0-quantity": "2",
                    "items-0-unit_price": "150.00",
                    "items-0-comment": "Main item",
                }
            )
        else:
            item = order.items.get()
            payload.update(
                {
                    "status": Order.Status.IN_PROGRESS,
                    "items-TOTAL_FORMS": "1",
                    "items-INITIAL_FORMS": "1",
                    "items-MIN_NUM_FORMS": "0",
                    "items-MAX_NUM_FORMS": "1000",
                    "items-0-id": str(item.id),
                    "items-0-order": str(order.id),
                    "items-0-product": str(product.id),
                    "items-0-quantity": "3",
                    "items-0-unit_price": "175.00",
                    "items-0-comment": "Updated item",
                }
            )
        return payload

    def build_product_payload(self, *, name, sku, norm_stage, norm_time_value, norm_version="v1", product=None, norm=None):
        payload = {
            "name": name,
            "sku": sku,
            "description": "Product description",
            "technical_description": "Technical description",
            "base_price": "99.99",
            "prom_url": "",
            "rozetka_url": "",
            "olx_url": "",
            "site_url": "",
            "photos_url": "",
            "production_norms_url": "",
            "is_active": "on",
            "norms-TOTAL_FORMS": "1",
            "norms-INITIAL_FORMS": "0",
            "norms-MIN_NUM_FORMS": "0",
            "norms-MAX_NUM_FORMS": "1000",
            "norms-0-stage_type": norm_stage,
            "norms-0-time_value": norm_time_value,
            "norms-0-time_unit": ProductProductionNorm.TimeUnit.HOURS,
            "norms-0-material_value": "1.250",
            "norms-0-material_unit": ProductProductionNorm.MaterialUnit.SQUARE_METER,
            "norms-0-version": norm_version,
            "norms-0-comment": "Norm comment",
            "norms-0-is_active": "on",
        }
        if product is not None and norm is not None:
            payload.update(
                {
                    "norms-INITIAL_FORMS": "1",
                    "norms-0-id": str(norm.id),
                    "norms-0-product": str(product.id),
                }
            )
        return payload


class TestClientWorkspaceView(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("sales-client@example.com", UserProfile.Role.SALES_MANAGER)
        self.read_only_user = self.create_user_with_role("production-client@example.com", UserProfile.Role.PRODUCTION)
        self.tag = Tag.objects.create(name="VIP")
        self.client_obj = Client.objects.create(
            name="Client Workspace",
            client_type=Client.ClientType.TOV,
            tax_code="12345678",
            phones="+380671112233",
            email="client.workspace@example.com",
            notes="Key account",
            source=Client.Source.PROM,
        )
        self.client_obj.tags.add(self.tag)
        self.contact = Contact.objects.create(
            client=self.client_obj,
            full_name="Main Contact",
            position="Buyer",
            phone="+380500000001",
            email="contact@example.com",
            notes="Primary",
        )
        self.order = self.create_order(self.contact, manager=self.user)
        self.task = Task.objects.create(
            client=self.client_obj,
            contact=self.contact,
            title="Call the client",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate() - timedelta(days=1),
            status=Task.Status.NEW,
            comment="Need delivery details",
        )

    def test_client_details_requires_login(self):
        response = self.client.get(reverse("client_details", args=[self.client_obj.id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_client_details_displays_workspace_data_and_custom_actions(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("client_details", args=[self.client_obj.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Client Workspace")
        self.assertContains(response, "12345678")
        self.assertContains(response, "Main Contact")
        self.assertContains(response, self.order.title)
        self.assertContains(response, "Call the client")
        self.assertContains(response, "Робоче меню")
        self.assertContains(response, "Поточний клієнт")
        self.assertContains(response, reverse("crm_clients"))
        self.assertContains(response, reverse("crm_contacts"))
        self.assertContains(response, reverse("crm_orders"))
        self.assertContains(response, reverse("crm_tasks"))
        self.assertContains(response, reverse("crm_products"))
        self.assertContains(response, reverse("crm_client_update", args=[self.client_obj.id]))
        self.assertContains(response, reverse("crm_contact_create"))
        self.assertContains(response, reverse("crm_order_create"))
        self.assertContains(response, reverse("crm_task_create"))
        self.assertContains(response, reverse("crm_client_interaction_create", args=[self.client_obj.id]))
        self.assertContains(response, "Запит доставки")
        self.assertContains(response, "Історія взаємодії")
        self.assertEqual(response.context["stats"]["contacts_count"], 1)
        self.assertEqual(response.context["stats"]["orders_count"], 1)
        self.assertEqual(response.context["stats"]["tasks_count"], 1)
        self.assertEqual(response.context["stats"]["overdue_tasks_count"], 1)
        self.assertGreaterEqual(response.context["stats"]["interactions_count"], 3)

    def test_manual_client_interaction_can_be_created_from_workspace(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("crm_client_interaction_create", args=[self.client_obj.id]),
            {
                "event_type": ClientInteraction.EventType.CALL,
                "title": "Follow-up call",
                "description": "Discussed delivery timing",
                "event_at": timezone.localtime().replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M"),
                "contact": str(self.contact.id),
                "order": str(self.order.id),
                "task": str(self.task.id),
            },
        )

        self.assertRedirects(response, reverse("client_details", args=[self.client_obj.id]))
        interaction = ClientInteraction.objects.get(title="Follow-up call")
        self.assertEqual(interaction.client, self.client_obj)
        self.assertEqual(interaction.source, ClientInteraction.Source.MANUAL)
        self.assertEqual(interaction.created_by, self.user)

    def test_production_stage_updates_are_written_to_client_timeline(self):
        stage = self.order.items.first().production_stages.get(stage_type=ProductionStage.StageType.EXECUTION)

        with self.captureOnCommitCallbacks(execute=True):
            stage.status = ProductionStage.Status.IN_PROGRESS
            stage.comment = "Started on production floor"
            stage.save(update_fields=["status", "comment", "updated_at"])

        self.assertTrue(
            ClientInteraction.objects.filter(
                client=self.client_obj,
                event_type=ClientInteraction.EventType.PRODUCTION,
                title__contains=stage.get_stage_type_display(),
            ).exists()
        )

    def test_read_only_role_can_view_but_not_get_quick_create_actions(self):
        self.client.force_login(self.read_only_user)
        response = self.client.get(reverse("client_details", args=[self.client_obj.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Client Workspace")
        self.assertNotContains(response, reverse("crm_contact_create"))
        self.assertNotContains(response, reverse("crm_order_create"))
        self.assertNotContains(response, reverse("crm_task_create"))


class TestClientDetailsWithoutContacts(CrmWorkspaceMixin, TestCase):
    def test_order_is_blocked_but_task_creation_is_available_without_contact(self):
        user = self.create_user_with_role("sales-no-contact@example.com", UserProfile.Role.SALES_MANAGER)
        client_obj = Client.objects.create(name="No Contact Client")

        self.client.force_login(user)
        response = self.client.get(reverse("client_details", args=[client_obj.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Для замовлення потрібен контакт")
        self.assertContains(response, reverse("crm_task_create"))


class TestCustomCrmWorkspacePages(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("sales-workspace@example.com", UserProfile.Role.SALES_MANAGER)
        client_obj = Client.objects.create(name="Workspace Client", phones="+380670000000")
        contact = Contact.objects.create(client=client_obj, full_name="Workspace Contact")
        self.create_order(contact, manager=self.user)
        Task.objects.create(
            client=client_obj,
            contact=contact,
            title="Workspace task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )

    def test_custom_crm_pages_render_in_dashboard_style(self):
        self.client.force_login(self.user)

        route_expectations = (
            ("crm_clients", "Клієнтська база"),
            ("crm_contacts", "Контактні особи"),
            ("crm_orders", "Замовлення"),
            ("crm_tasks", "Задачі по CRM"),
            ("crm_products", "Каталог продуктів"),
        )

        for route_name, expected_title in route_expectations:
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected_title)
            self.assertContains(response, "Робоче меню")


class TestCrmCrudViews(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("sales-crud@example.com", UserProfile.Role.SALES_MANAGER)
        self.admin_user = self.create_user_with_role("admin-crud@example.com", UserProfile.Role.ADMIN)
        self.read_only_user = self.create_user_with_role("executive-crud@example.com", UserProfile.Role.EXECUTIVE)
        self.client.force_login(self.user)
        self.base_client = Client.objects.create(name="Base Client", phones="+380670010101")
        self.base_contact = Contact.objects.create(client=self.base_client, full_name="Base Contact")

    def test_read_only_role_cannot_access_crud_pages(self):
        self.client.force_login(self.read_only_user)
        protected_routes = (
            reverse("crm_client_create"),
            reverse("crm_contact_create"),
            reverse("crm_order_create"),
            reverse("crm_task_create"),
            reverse("crm_product_create"),
        )

        for route in protected_routes:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 403)

    def test_client_crud_flow(self):
        create_response = self.client.post(
            reverse("crm_client_create"),
            {
                "name": "New Client",
                "client_type": Client.ClientType.INDIVIDUAL,
                "tax_code": "",
                "phones": "+380670020202",
                "email": "new-client@example.com",
                "source": Client.Source.OTHER,
                "notes": "Created from workspace",
            },
        )
        created_client = Client.objects.get(name="New Client")
        self.assertRedirects(create_response, reverse("client_details", args=[created_client.id]))

        update_response = self.client.post(
            reverse("crm_client_update", args=[created_client.id]),
            {
                "name": "Updated Client",
                "client_type": Client.ClientType.INDIVIDUAL,
                "tax_code": "",
                "phones": "+380670030303",
                "email": "updated-client@example.com",
                "source": Client.Source.OTHER,
                "notes": "Updated from workspace",
            },
        )
        created_client.refresh_from_db()
        self.assertEqual(created_client.name, "Updated Client")
        self.assertRedirects(update_response, reverse("client_details", args=[created_client.id]))

        delete_response = self.client.post(reverse("crm_client_delete", args=[created_client.id]))
        self.assertRedirects(delete_response, reverse("crm_clients"))
        self.assertFalse(Client.objects.filter(id=created_client.id).exists())

    def test_contact_crud_flow(self):
        create_response = self.client.post(
            reverse("crm_contact_create"),
            {
                "client": str(self.base_client.id),
                "full_name": "Workspace Contact",
                "position": "Buyer",
                "phone": "+380670040404",
                "email": "workspace-contact@example.com",
                "source": Contact.Source.OTHER,
                "notes": "Created from custom CRM",
            },
        )
        contact = Contact.objects.get(full_name="Workspace Contact")
        self.assertRedirects(create_response, reverse("client_details", args=[self.base_client.id]))

        update_response = self.client.post(
            reverse("crm_contact_update", args=[contact.id]),
            {
                "client": str(self.base_client.id),
                "full_name": "Workspace Contact Updated",
                "position": "Head Buyer",
                "phone": "+380670050505",
                "email": "workspace-contact-updated@example.com",
                "source": Contact.Source.PHONE,
                "notes": "Updated from custom CRM",
            },
        )
        contact.refresh_from_db()
        self.assertEqual(contact.full_name, "Workspace Contact Updated")
        self.assertRedirects(update_response, reverse("client_details", args=[self.base_client.id]))

        delete_response = self.client.post(reverse("crm_contact_delete", args=[contact.id]))
        self.assertRedirects(delete_response, reverse("client_details", args=[self.base_client.id]))
        self.assertFalse(Contact.objects.filter(id=contact.id).exists())

    def test_product_crud_flow(self):
        self.client.force_login(self.admin_user)
        create_response = self.client.post(
            reverse("crm_product_create"),
            self.build_product_payload(
                name="Workspace Product",
                sku="WORKSPACE-001",
                norm_stage=ProductionStage.StageType.EXECUTION,
                norm_time_value="1.50",
            ),
        )
        product = Product.objects.get(sku="WORKSPACE-001")
        norm = product.production_norms.get()
        self.assertRedirects(create_response, reverse("crm_products"))
        self.assertEqual(str(norm.time_value), "1.50")

        update_response = self.client.post(
            reverse("crm_product_update", args=[product.id]),
            self.build_product_payload(
                name="Workspace Product Updated",
                sku="WORKSPACE-001",
                norm_stage=norm.stage_type,
                norm_time_value="2.25",
                product=product,
                norm=norm,
            ),
        )
        product.refresh_from_db()
        norm.refresh_from_db()
        self.assertEqual(product.name, "Workspace Product Updated")
        self.assertEqual(str(norm.time_value), "2.25")
        self.assertRedirects(update_response, reverse("crm_products"))

        delete_response = self.client.post(reverse("crm_product_delete", args=[product.id]))
        self.assertRedirects(delete_response, reverse("crm_products"))
        self.assertFalse(Product.objects.filter(id=product.id).exists())

    def test_task_crud_flow(self):
        create_response = self.client.post(
            reverse("crm_task_create"),
            {
                "client": str(self.base_client.id),
                "contact": str(self.base_contact.id),
                "title": "Workspace Task",
                "description": "Task description",
                "priority": Task.Priority.HIGH,
                "status": Task.Status.NEW,
                "assigned_by": str(self.user.id),
                "assigned_to": str(self.user.id),
                "date": timezone.localdate().isoformat(),
                "comment": "Initial task comment",
            },
        )
        task = Task.objects.get(title="Workspace Task")
        self.assertRedirects(create_response, reverse("client_details", args=[self.base_client.id]))

        update_response = self.client.post(
            reverse("crm_task_update", args=[task.id]),
            {
                "client": str(self.base_client.id),
                "contact": str(self.base_contact.id),
                "title": "Workspace Task Updated",
                "description": "Updated task description",
                "priority": Task.Priority.URGENT,
                "status": Task.Status.DONE,
                "assigned_by": str(self.user.id),
                "assigned_to": str(self.user.id),
                "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "comment": "Task completed",
            },
        )
        task.refresh_from_db()
        self.assertEqual(task.title, "Workspace Task Updated")
        self.assertEqual(task.description, "Updated task description")
        self.assertEqual(task.priority, Task.Priority.URGENT)
        self.assertEqual(task.status, Task.Status.DONE)
        self.assertRedirects(update_response, reverse("client_details", args=[self.base_client.id]))

        delete_response = self.client.post(reverse("crm_task_delete", args=[task.id]))
        self.assertRedirects(delete_response, reverse("client_details", args=[self.base_client.id]))
        self.assertFalse(Task.objects.filter(id=task.id).exists())

    def test_task_kanban_and_status_update_endpoint(self):
        order = self.create_order(self.base_contact, manager=self.user)
        task = Task.objects.create(
            client=self.base_client,
            order=order,
            title="Kanban Task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )

        kanban_response = self.client.get(reverse("crm_tasks_kanban"), {"assigned_to": "mine"})
        self.assertEqual(kanban_response.status_code, 200)
        self.assertContains(kanban_response, "Канбан-дошка задач")
        self.assertContains(kanban_response, "Kanban Task")

        update_response = self.client.post(
            reverse("crm_task_status_update", args=[task.id]),
            {"status": Task.Status.WAITING},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        task.refresh_from_db()
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["status"], Task.Status.WAITING)
        self.assertEqual(task.status, Task.Status.WAITING)
        self.assertEqual(task.contact, order.contact)

    def test_task_filters_support_assigned_by_mine_for_list_and_kanban(self):
        other_user = self.create_user_with_role("filters-other@example.com", UserProfile.Role.SALES_MANAGER)
        Task.objects.create(
            client=self.base_client,
            contact=self.base_contact,
            title="Created by me",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )
        Task.objects.create(
            client=self.base_client,
            contact=self.base_contact,
            title="Assigned to me by other user",
            assigned_by=other_user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )

        list_response = self.client.get(reverse("crm_tasks"), {"assigned_by": "mine"})
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Created by me")
        self.assertNotContains(list_response, "Assigned to me by other user")
        self.assertContains(list_response, "Призначені мною")

        kanban_response = self.client.get(reverse("crm_tasks_kanban"), {"assigned_by": "mine"})
        self.assertEqual(kanban_response.status_code, 200)
        self.assertContains(kanban_response, "Created by me")
        self.assertNotContains(kanban_response, "Assigned to me by other user")

    def test_order_crud_flow_with_items(self):
        product = self.create_product(name_prefix="Order Product")
        create_response = self.client.post(
            reverse("crm_order_create"),
            self.build_order_payload(contact=self.base_contact, manager=self.user, product=product),
        )
        order = Order.objects.get(contact=self.base_contact)
        self.assertRedirects(create_response, reverse("client_details", args=[self.base_client.id]))
        self.assertEqual(order.title, product.name)
        self.assertEqual(order.items.count(), 1)
        self.assertGreater(order.items.first().production_stages.count(), 0)

        update_response = self.client.post(
            reverse("crm_order_update", args=[order.id]),
            self.build_order_payload(contact=self.base_contact, manager=self.user, product=product, order=order),
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_PROGRESS)
        self.assertEqual(order.items.get().quantity, 3)
        self.assertRedirects(update_response, reverse("client_details", args=[self.base_client.id]))

        delete_response = self.client.post(reverse("crm_order_delete", args=[order.id]))
        self.assertRedirects(delete_response, reverse("client_details", args=[self.base_client.id]))
        self.assertFalse(Order.objects.filter(id=order.id).exists())


class TestOrderModuleBehavior(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("orders-sales@example.com", UserProfile.Role.SALES_MANAGER)
        self.other_user = self.create_user_with_role("orders-other@example.com", UserProfile.Role.SALES_MANAGER)
        self.client.force_login(self.user)
        self.client_obj = Client.objects.create(name="Orders Client", phones="+380671010101")
        self.contact = Contact.objects.create(client=self.client_obj, full_name="Orders Contact")

    def test_order_title_syncs_when_items_change_outside_admin(self):
        order = Order.objects.create(contact=self.contact, manager=self.user)
        first_product = self.create_product(name_prefix="Laser Panel")
        second_product = self.create_product(name_prefix="Painted Frame")

        first_item = OrderItem.objects.create(order=order, product=first_product, quantity=1, unit_price="100.00")
        order.refresh_from_db()
        self.assertEqual(order.title, first_product.name)

        second_item = OrderItem.objects.create(order=order, product=second_product, quantity=1, unit_price="150.00")
        order.refresh_from_db()
        self.assertEqual(order.title, f"{first_product.name}, {second_product.name}")

        first_item.product = second_product
        first_item.save()
        order.refresh_from_db()
        self.assertEqual(order.title, second_product.name)

        second_item.delete()
        order.refresh_from_db()
        self.assertEqual(order.title, second_product.name)

        first_item.delete()
        order.refresh_from_db()
        self.assertEqual(order.title, "")

    def test_order_moves_to_in_production_when_stage_is_scheduled(self):
        order = self.create_order(self.contact, manager=self.user)
        order.status = Order.Status.IN_PROGRESS
        order.save(update_fields=["status"])

        stage = order.items.first().production_stages.get(stage_type=ProductionStage.StageType.EXECUTION)
        stage.status = ProductionStage.Status.SCHEDULED
        stage.planned_start = timezone.now()
        stage.planned_end = timezone.now() + timedelta(hours=2)
        stage.save(update_fields=["status", "planned_start", "planned_end", "updated_at"])

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_PRODUCTION)

    def test_order_moves_to_ready_when_all_stages_are_done(self):
        order = self.create_order(self.contact, manager=self.user)
        item = order.items.first()
        finished_at = timezone.now()

        for stage in item.production_stages.all():
            stage.status = ProductionStage.Status.DONE
            stage.started_at = finished_at - timedelta(hours=1)
            stage.completed_at = finished_at
            stage.save(update_fields=["status", "started_at", "completed_at", "updated_at"])

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.READY)

    def test_orders_list_filters_by_manager_and_shows_delivery_and_payment_details(self):
        visible_order = Order.objects.create(
            contact=self.contact,
            manager=self.user,
            status=Order.Status.NEW,
            deadline=timezone.localdate() + timedelta(days=2),
            delivery_method=Order.DeliveryMethod.NOVA_POSHTA,
            shipping_address="Kyiv, branch 4",
            recipient="Visible Receiver",
            recipient_phone="+380671234567",
            tracking_number="TTN-VISIBLE",
            payment_type=Order.PaymentType.PREPAY,
            payment_terms="100% prepaid",
            payment_amount="420.00",
        )
        other_order = Order.objects.create(
            contact=self.contact,
            manager=self.other_user,
            status=Order.Status.READY,
            deadline=timezone.localdate() + timedelta(days=5),
            delivery_method=Order.DeliveryMethod.COURIER,
            recipient="Other Receiver",
            payment_type=Order.PaymentType.COD,
            payment_amount="150.00",
        )
        product = self.create_product(name_prefix="Order Filter Product")
        OrderItem.objects.create(order=visible_order, product=product, quantity=2, unit_price="210.00")
        OrderItem.objects.create(order=other_order, product=product, quantity=1, unit_price="150.00")

        response = self.client.get(
            reverse("crm_orders"),
            {
                "manager": "mine",
                "status": Order.Status.NEW,
                "delivery_method": Order.DeliveryMethod.NOVA_POSHTA,
                "payment_type": Order.PaymentType.PREPAY,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible Receiver")
        self.assertContains(response, "TTN-VISIBLE")
        self.assertContains(response, "100% prepaid")
        self.assertContains(response, "Запит доставки")
        self.assertContains(response, reverse("crm_order_update", args=[visible_order.id]))
        self.assertNotContains(response, reverse("crm_order_update", args=[other_order.id]))
        self.assertEqual(response.context["stats"]["total_orders"], 1)

    def test_order_update_page_shows_delivery_copy_block(self):
        order = self.create_order(self.contact, manager=self.user)

        response = self.client.get(reverse("crm_order_update", args=[order.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Запит на доставку")
        self.assertContains(response, "Скопіювати в буфер")


class TestRowLevelVisibility(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.manager = self.create_user_with_role("row-manager@example.com", UserProfile.Role.SALES_MANAGER)
        self.other_manager = self.create_user_with_role("row-other@example.com", UserProfile.Role.SALES_MANAGER)
        self.shared_client = Client.objects.create(name="Shared Client", phones="+380679999999")
        self.shared_contact = Contact.objects.create(client=self.shared_client, full_name="Shared Contact")

        self.my_order = self.create_order(self.shared_contact, manager=self.manager)
        self.other_order = self.create_order(self.shared_contact, manager=self.other_manager)

        self.my_task = Task.objects.create(
            client=self.shared_client,
            contact=self.shared_contact,
            order=self.my_order,
            title="My visible task",
            assigned_by=self.manager,
            assigned_to=self.manager,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )
        self.other_task = Task.objects.create(
            client=self.shared_client,
            contact=self.shared_contact,
            order=self.other_order,
            title="Other hidden task",
            assigned_by=self.other_manager,
            assigned_to=self.other_manager,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )

    def test_sales_manager_only_sees_owned_orders_and_related_tasks_in_lists_and_card(self):
        self.client.force_login(self.manager)

        orders_response = self.client.get(reverse("crm_orders"))
        self.assertEqual(orders_response.status_code, 200)
        self.assertContains(orders_response, self.my_order.title)
        self.assertNotContains(orders_response, self.other_order.title)

        tasks_response = self.client.get(reverse("crm_tasks"))
        self.assertEqual(tasks_response.status_code, 200)
        self.assertContains(tasks_response, "My visible task")
        self.assertNotContains(tasks_response, "Other hidden task")

        details_response = self.client.get(reverse("client_details", args=[self.shared_client.id]))
        self.assertEqual(details_response.status_code, 200)
        self.assertContains(details_response, self.my_order.title)
        self.assertNotContains(details_response, self.other_order.title)
        self.assertContains(details_response, "My visible task")
        self.assertNotContains(details_response, "Other hidden task")
        self.assertEqual(details_response.context["stats"]["orders_count"], 1)
        self.assertEqual(details_response.context["stats"]["tasks_count"], 1)

    def test_sales_manager_cannot_open_or_mutate_another_managers_objects(self):
        self.client.force_login(self.manager)

        order_response = self.client.get(reverse("crm_order_update", args=[self.other_order.id]))
        self.assertEqual(order_response.status_code, 404)

        task_response = self.client.get(reverse("crm_task_update", args=[self.other_task.id]))
        self.assertEqual(task_response.status_code, 404)

        status_response = self.client.post(
            reverse("crm_task_status_update", args=[self.other_task.id]),
            {"status": Task.Status.DONE},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(status_response.status_code, 404)


class TestTaskModelValidation(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("task-model@example.com", UserProfile.Role.SALES_MANAGER)
        self.client_a = Client.objects.create(name="Task Client A")
        self.contact_a = Contact.objects.create(client=self.client_a, full_name="Contact A")
        self.client_b = Client.objects.create(name="Task Client B")
        self.contact_b = Contact.objects.create(client=self.client_b, full_name="Contact B")
        self.order_b = self.create_order(self.contact_b, manager=self.user)

    def test_task_populates_client_and_contact_from_order(self):
        task = Task(
            order=self.order_b,
            title="Autofill task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )

        task.save()

        self.assertEqual(task.client, self.client_b)
        self.assertEqual(task.contact, self.contact_b)

    def test_task_rejects_mismatched_order_and_client(self):
        task = Task(
            client=self.client_a,
            contact=self.contact_a,
            order=self.order_b,
            title="Broken task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=Task.Status.NEW,
        )

        with self.assertRaises(ValidationError):
            task.full_clean()


class TestCrmEndToEndFlow(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.manager = self.create_user_with_role("flow-manager@example.com", UserProfile.Role.SALES_MANAGER)
        self.production_user = self.create_user_with_role("flow-production@example.com", UserProfile.Role.PRODUCTION)
        self.manager.profile.telegram_chat_id = "81001"
        self.manager.profile.save(update_fields=["telegram_chat_id"])
        self.client_entity = Client.objects.create(name="Flow Client", email="flow@example.com")
        self.contact = Contact.objects.create(client=self.client_entity, full_name="Flow Contact")
        self.product = self.create_product(name_prefix="Flow Product")
        self.storage = WorkUnit.objects.create(name="Flow Storage", type=WorkUnit.UnitType.STORAGE)
        self.assembly = WorkUnit.objects.create(name="Flow Assembly", type=WorkUnit.UnitType.ASSEMBLY)
        self.laser = Machine.objects.create(name="Flow Laser", type=Machine.MachineType.LASER)
        self.paint = Machine.objects.create(name="Flow Paint", type=Machine.MachineType.PAINTING)

    def test_client_to_ready_order_flow_creates_slots_audit_and_notifications(self):
        with self.captureOnCommitCallbacks(execute=True):
            order = Order.objects.create(
                contact=self.contact,
                manager=self.manager,
                status=Order.Status.NEW,
                deadline=timezone.localdate() + timedelta(days=5),
            )
            OrderItem.objects.create(order=order, product=self.product, quantity=1, unit_price="250.00")

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_PRODUCTION)
        self.assertEqual(order.items.first().production_stages.count(), 5)
        self.assertEqual(order.slots.count(), 5)

        with self.captureOnCommitCallbacks(execute=True):
            task = Task.objects.create(
                client=self.client_entity,
                order=order,
                title="Flow follow-up",
                assigned_by=self.manager,
                assigned_to=self.manager,
                date=timezone.localdate() + timedelta(days=1),
                status=Task.Status.NEW,
            )

        task.refresh_from_db()
        self.assertEqual(task.contact, self.contact)
        self.assertEqual(task.client, self.client_entity)
        self.assertTrue(
            TelegramNotification.objects.filter(
                task=task,
                notification_type=TelegramNotification.Type.TASK_CREATED,
            ).exists()
        )

        finished_at = timezone.now()
        for stage in order.items.first().production_stages.order_by("sequence", "id"):
            stage._changed_by = self.production_user
            stage.responsible = self.production_user
            stage.status = ProductionStage.Status.DONE
            stage.started_at = finished_at - timedelta(hours=1)
            stage.completed_at = finished_at
            stage.save(
                update_fields=["responsible", "status", "started_at", "completed_at", "updated_at"]
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.READY)
        self.assertTrue(
            ChangeAuditLog.objects.filter(
                entity_type=ChangeAuditLog.EntityType.ORDER,
                object_id=order.pk,
            ).exists()
        )
        self.assertTrue(
            ChangeAuditLog.objects.filter(
                entity_type=ChangeAuditLog.EntityType.TASK,
                object_id=task.pk,
            ).exists()
        )
