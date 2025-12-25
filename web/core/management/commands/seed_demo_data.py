from datetime import timedelta, time

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from crm.models import (
    Tag,
    Client,
    Contact,
    Product,
    Order,
    OrderItem,
    Task,
)
from manufacture.models import (
    Machine,
    WorkUnit,
    ProductionSlot,
)


class Command(BaseCommand):
    help = "Seed demo data for CRM (Client / Contact / Orders / Production)"

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("▶ CRM DEMO SEED START"))

        now = timezone.now()
        User = get_user_model()

        # ------------------------------------------------------------------
        # USERS
        # ------------------------------------------------------------------
        manager, _ = User.objects.get_or_create(
            username="demo_manager",
            defaults={
                "email": "demo_manager@example.com",
                "first_name": "Demo",
                "last_name": "Manager",
                "is_staff": True,
            },
        )

        # ------------------------------------------------------------------
        # TAGS
        # ------------------------------------------------------------------
        tag_names = ["гарячий лід", "холодний лід", "B2B", "B2C"]
        tags = {
            name: Tag.objects.get_or_create(name=name)[0]
            for name in tag_names
        }

        self.stdout.write(self.style.SUCCESS("✔ Tags ready"))

        # ------------------------------------------------------------------
        # CLIENTS + CONTACTS
        # ------------------------------------------------------------------
        clients_spec = [
            {
                "client": {
                    "name": "ТОВ «ЕлектроСервіс»",
                    "client_type": Client.ClientType.TOV,
                    "tax_code": "12345678",
                    "phones": "+380671234567",
                    "email": "info@electroservice.ua",
                    "source": Client.Source.PROM,
                    "tags": ["B2B", "гарячий лід"],
                    "notes": "B2B клієнт, генераторні кожухи",
                },
                "contacts": [
                    {
                        "full_name": "Петренко Олександр",
                        "position": "Закупівлі",
                        "phone": "+380671234567",
                        "email": "petrenko@electroservice.ua",
                        "source": Contact.Source.PROM,
                        "tags": ["B2B"],
                    }
                ],
            },
            {
                "client": {
                    "name": "Іван Петров",
                    "client_type": Client.ClientType.INDIVIDUAL,
                    "tax_code": "",
                    "phones": "+380501112233",
                    "email": "ivan.petrov@example.com",
                    "source": Client.Source.INSTAGRAM,
                    "tags": ["B2C"],
                    "notes": "Приватний клієнт",
                },
                "contacts": [
                    {
                        "full_name": "Іван Петров",
                        "position": "",
                        "phone": "+380501112233",
                        "email": "ivan.petrov@example.com",
                        "source": Contact.Source.INSTAGRAM,
                        "tags": ["B2C"],
                    }
                ],
            },
            {
                "client": {
                    "name": "ФОП «Світло в дім»",
                    "client_type": Client.ClientType.FOP,
                    "tax_code": "1234567890",
                    "phones": "+380931234567",
                    "email": "office@svitlo.in.ua",
                    "source": Client.Source.RECOMMENDATION,
                    "tags": ["B2B"],
                    "notes": "ФОП, постійний клієнт",
                },
                "contacts": [
                    {
                        "full_name": "Власник ФОП",
                        "position": "Власник",
                        "phone": "+380931234567",
                        "email": "office@svitlo.in.ua",
                        "source": Contact.Source.RECOMMENDATION,
                        "tags": ["B2B"],
                    }
                ],
            },
        ]

        contacts = []

        for block in clients_spec:
            c = block["client"]

            client, _ = Client.objects.get_or_create(
                name=c["name"],
                defaults={
                    "client_type": c["client_type"],
                    "tax_code": c["tax_code"],
                    "phones": c["phones"],
                    "email": c["email"],
                    "source": c["source"],
                    "notes": c["notes"],
                },
            )

            client.tags.set(tags[t] for t in c["tags"])

            for cd in block["contacts"]:
                contact, _ = Contact.objects.get_or_create(
                    client=client,
                    full_name=cd["full_name"],
                    defaults={
                        "position": cd["position"],
                        "phone": cd["phone"],
                        "email": cd["email"],
                        "source": cd["source"],
                    },
                )
                contact.tags.set(tags[t] for t in cd["tags"])
                contacts.append(contact)

        self.stdout.write(self.style.SUCCESS("✔ Clients & Contacts ready"))

        # ------------------------------------------------------------------
        # PRODUCTS
        # ------------------------------------------------------------------
        products_spec = [
            ("GEN-BOX-KS", "Кожух генератора KS", 9500),
            ("GEN-BOX-UNI", "Універсальний бокс 3–5 кВт", 11500),
            ("GEN-TABLE", "Підставка для генератора", 3500),
        ]

        products = []
        for sku, name, price in products_spec:
            p, _ = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "base_price": price,
                    "is_active": True,
                },
            )
            products.append(p)

        self.stdout.write(self.style.SUCCESS("✔ Products ready"))

        # ------------------------------------------------------------------
        # ORDERS
        # ------------------------------------------------------------------
        orders = []
        for idx, contact in enumerate(contacts):
            order = Order.objects.create(
                contact=contact,
                status=Order.Status.NEW,
                deadline=(now + timedelta(days=5 + idx)).date(),
                delivery_method=Order.DeliveryMethod.NOVA_POSHTA,
                payment_type=Order.PaymentType.PREPAY,
                recipient=contact.full_name,
                shipping_address="Нова Пошта",
                payment_amount=0,
                comment="Демо-замовлення",
            )

            total = 0
            for product in products[:2]:
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=1,
                    unit_price=product.base_price,
                )
                total += product.base_price

            order.payment_amount = total
            order.save()
            orders.append(order)

        self.stdout.write(self.style.SUCCESS("✔ Orders ready"))

        # ------------------------------------------------------------------
        # TASKS
        # ------------------------------------------------------------------
        for contact in contacts:
            Task.objects.get_or_create(
                contact=contact,
                title="Контакт з клієнтом",
                date=now.date(),
                defaults={
                    "assigned_by": manager,
                    "assigned_to": manager,
                    "status": False,
                    "comment": "Демо задача",
                },
            )

        self.stdout.write(self.style.SUCCESS("✔ Tasks ready"))

        self.stdout.write(self.style.SUCCESS("▶ CRM DEMO SEED FINISHED"))
