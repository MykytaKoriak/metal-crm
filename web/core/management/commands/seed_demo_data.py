from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from crm.models import (
    Client,
    Contact,
    Order,
    OrderItem,
    Product,
    Tag,
    Task,
)
from manufacture.models import Machine, ProductionSlot, ProductionStage, WorkUnit
from manufacture.services import find_next_available_window, planner_execution


class Command(BaseCommand):
    help = "Seed demo data for CRM (Client / Contact / Orders / Production)"

    def _create_fixed_slot(
        self,
        *,
        order,
        stage,
        resource,
        resource_field,
        start_from,
        duration,
        responsible,
        comment,
    ):
        window = find_next_available_window(resource, start_from, duration)
        if not window:
            raise CommandError(f"Не вдалося знайти вільне вікно для ресурсу '{resource}'.")

        start_datetime, end_datetime = window
        stage.status = ProductionStage.Status.SCHEDULED
        stage.responsible = responsible
        stage.planned_start = start_datetime
        stage.planned_end = end_datetime
        stage.save(
            update_fields=["status", "responsible", "planned_start", "planned_end", "updated_at"]
        )

        slot = ProductionSlot(
            order=order,
            stage=stage,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            comment=comment,
            planning_mode=ProductionSlot.PlanningMode.MANUAL,
            is_locked=True,
        )
        setattr(slot, resource_field, resource)
        slot._planner_operation = True
        slot._history_source = "system"
        slot._history_note = "Створено seed-командою."
        slot.save()
        return start_datetime, end_datetime

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("CRM DEMO SEED START"))

        now = timezone.now()
        execution_duration = timedelta(hours=2)
        painting_duration = timedelta(hours=2)
        User = get_user_model()

        with planner_execution():
            manager, _ = User.objects.get_or_create(
                username="demo_manager",
                defaults={
                    "email": "demo_manager@example.com",
                    "first_name": "Demo",
                    "last_name": "Manager",
                    "is_staff": True,
                },
            )

            tag_names = ["гарячий лід", "холодний лід", "B2B", "B2C"]
            tags = {name: Tag.objects.get_or_create(name=name)[0] for name in tag_names}
            self.stdout.write(self.style.SUCCESS("Tags ready"))

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
                client_data = block["client"]
                client, _ = Client.objects.get_or_create(
                    name=client_data["name"],
                    defaults={
                        "client_type": client_data["client_type"],
                        "tax_code": client_data["tax_code"],
                        "phones": client_data["phones"],
                        "email": client_data["email"],
                        "source": client_data["source"],
                        "notes": client_data["notes"],
                    },
                )
                client.tags.set(tags[name] for name in client_data["tags"])

                for contact_data in block["contacts"]:
                    contact, _ = Contact.objects.get_or_create(
                        client=client,
                        full_name=contact_data["full_name"],
                        defaults={
                            "position": contact_data["position"],
                            "phone": contact_data["phone"],
                            "email": contact_data["email"],
                            "source": contact_data["source"],
                        },
                    )
                    contact.tags.set(tags[name] for name in contact_data["tags"])
                    contacts.append(contact)

            self.stdout.write(self.style.SUCCESS("Clients & Contacts ready"))

            products_spec = [
                ("GEN-BOX-KS", "Кожух генератора KS", 9500),
                ("GEN-BOX-UNI", "Універсальний бокс 3–5 кВт", 11500),
                ("GEN-TABLE", "Підставка для генератора", 3500),
            ]
            products = []
            for sku, name, price in products_spec:
                product, _ = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "name": name,
                        "base_price": price,
                        "is_active": True,
                    },
                )
                products.append(product)

            self.stdout.write(self.style.SUCCESS("Products ready"))

            laser_machine, _ = Machine.objects.get_or_create(
                name="Laser Demo",
                defaults={"type": Machine.MachineType.LASER},
            )
            painting_unit, _ = WorkUnit.objects.get_or_create(
                name="Painting Demo",
                defaults={"type": WorkUnit.UnitType.PAINTING},
            )

            self.stdout.write(self.style.SUCCESS("Production resources ready"))

            for idx, contact in enumerate(contacts):
                order = Order.objects.create(
                    contact=contact,
                    manager=manager,
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
                order_start_from = now + timedelta(days=idx)
                for product in products[:2]:
                    item = OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=1,
                        unit_price=product.base_price,
                    )
                    total += product.base_price

                    execution_stage = item.production_stages.get(
                        stage_type=ProductionStage.StageType.EXECUTION
                    )
                    _, execution_end = self._create_fixed_slot(
                        order=order,
                        stage=execution_stage,
                        resource=laser_machine,
                        resource_field="machine",
                        start_from=order_start_from,
                        duration=execution_duration,
                        responsible=manager,
                        comment="Demo execution slot",
                    )

                    painting_stage = item.production_stages.get(
                        stage_type=ProductionStage.StageType.PAINTING
                    )
                    _, painting_end = self._create_fixed_slot(
                        order=order,
                        stage=painting_stage,
                        resource=painting_unit,
                        resource_field="work_unit",
                        start_from=execution_end,
                        duration=painting_duration,
                        responsible=manager,
                        comment="Demo painting slot",
                    )
                    order_start_from = painting_end

                order.payment_amount = total
                order.save(update_fields=["payment_amount"])

            self.stdout.write(self.style.SUCCESS("Orders ready"))

            for contact in contacts:
                Task.objects.get_or_create(
                    client=contact.client,
                    contact=contact,
                    title="Контакт з клієнтом",
                    date=now.date(),
                    defaults={
                        "assigned_by": manager,
                        "assigned_to": manager,
                        "status": Task.Status.NEW,
                        "comment": "Демо задача",
                    },
                )

            self.stdout.write(self.style.SUCCESS("Tasks ready"))

        self.stdout.write(self.style.SUCCESS("CRM DEMO SEED FINISHED"))
