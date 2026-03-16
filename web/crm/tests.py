from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import UserProfile

from .models import Client, Contact, Order, OrderItem, Product, Tag, Task


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
            contact=self.contact,
            title="Call the client",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate() - timedelta(days=1),
            status=False,
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
        self.assertEqual(response.context["stats"]["contacts_count"], 1)
        self.assertEqual(response.context["stats"]["orders_count"], 1)
        self.assertEqual(response.context["stats"]["tasks_count"], 1)
        self.assertEqual(response.context["stats"]["overdue_tasks_count"], 1)

    def test_read_only_role_can_view_but_not_get_quick_create_actions(self):
        self.client.force_login(self.read_only_user)
        response = self.client.get(reverse("client_details", args=[self.client_obj.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Client Workspace")
        self.assertNotContains(response, reverse("crm_contact_create"))
        self.assertNotContains(response, reverse("crm_order_create"))
        self.assertNotContains(response, reverse("crm_task_create"))


class TestClientDetailsWithoutContacts(CrmWorkspaceMixin, TestCase):
    def test_order_and_task_actions_are_blocked_without_contact(self):
        user = self.create_user_with_role("sales-no-contact@example.com", UserProfile.Role.SALES_MANAGER)
        client_obj = Client.objects.create(name="No Contact Client")

        self.client.force_login(user)
        response = self.client.get(reverse("client_details", args=[client_obj.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Для замовлення потрібен контакт")
        self.assertContains(response, "Для задачі потрібен контакт")


class TestCustomCrmWorkspacePages(CrmWorkspaceMixin, TestCase):
    def setUp(self):
        self.user = self.create_user_with_role("sales-workspace@example.com", UserProfile.Role.SALES_MANAGER)
        client_obj = Client.objects.create(name="Workspace Client", phones="+380670000000")
        contact = Contact.objects.create(client=client_obj, full_name="Workspace Contact")
        self.create_order(contact, manager=self.user)
        Task.objects.create(
            contact=contact,
            title="Workspace task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=timezone.localdate(),
            status=False,
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
            {
                "name": "Workspace Product",
                "sku": "WORKSPACE-001",
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
            },
        )
        product = Product.objects.get(sku="WORKSPACE-001")
        self.assertRedirects(create_response, reverse("crm_products"))

        update_response = self.client.post(
            reverse("crm_product_update", args=[product.id]),
            {
                "name": "Workspace Product Updated",
                "sku": "WORKSPACE-001",
                "description": "Updated description",
                "technical_description": "Updated technical description",
                "base_price": "149.99",
                "prom_url": "",
                "rozetka_url": "",
                "olx_url": "",
                "site_url": "",
                "photos_url": "",
                "production_norms_url": "",
                "is_active": "on",
            },
        )
        product.refresh_from_db()
        self.assertEqual(product.name, "Workspace Product Updated")
        self.assertRedirects(update_response, reverse("crm_products"))

        delete_response = self.client.post(reverse("crm_product_delete", args=[product.id]))
        self.assertRedirects(delete_response, reverse("crm_products"))
        self.assertFalse(Product.objects.filter(id=product.id).exists())

    def test_task_crud_flow(self):
        create_response = self.client.post(
            reverse("crm_task_create"),
            {
                "contact": str(self.base_contact.id),
                "title": "Workspace Task",
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
                "contact": str(self.base_contact.id),
                "title": "Workspace Task Updated",
                "assigned_by": str(self.user.id),
                "assigned_to": str(self.user.id),
                "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "status": "on",
                "comment": "Task completed",
            },
        )
        task.refresh_from_db()
        self.assertEqual(task.title, "Workspace Task Updated")
        self.assertTrue(task.status)
        self.assertRedirects(update_response, reverse("client_details", args=[self.base_client.id]))

        delete_response = self.client.post(reverse("crm_task_delete", args=[task.id]))
        self.assertRedirects(delete_response, reverse("client_details", args=[self.base_client.id]))
        self.assertFalse(Task.objects.filter(id=task.id).exists())

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
