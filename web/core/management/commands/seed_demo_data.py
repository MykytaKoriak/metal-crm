from datetime import timedelta, time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model

from crm.models import (
    Tag,
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
    help = "Заповнює базу демо-даними для CRM та виробництва"

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Старт наповнення демо-даними..."))

        User = get_user_model()

        # --- Користувачі (для Task.assigned_by / assigned_to) ---
        user, _ = User.objects.get_or_create(
            username="demo_manager",
            defaults={
                "first_name": "Демо",
                "last_name": "Менеджер",
                "email": "demo_manager@example.com",
            },
        )

        # --- Теги ---
        tag_names = ["гарячий лід", "холодний лід", "B2B", "B2C"]
        tags = {}
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(name=name)
            tags[name] = tag
        self.stdout.write(self.style.SUCCESS(f"Створено/оновлено {len(tags)} тегів"))

        # --- Контакти ---
        contacts_data = [
            {
                "name": "ТОВ «ЕлектроСервіс»",
                "phone": "+380671234567",
                "email": "info@electroservice.ua",
                "source": Contact.Source.PROM,
                "tags": ["гарячий лід", "B2B"],
                "comment": "Цікавилися захисними кожухами для генераторів.",
            },
            {
                "name": "Іван Петров",
                "phone": "+380501112233",
                "email": "ivan.petrov@example.com",
                "source": Contact.Source.INSTAGRAM,
                "tags": ["B2C"],
                "comment": "Писав у директ щодо невеликого боксу для генератора.",
            },
            {
                "name": "ФОП «Світло в дім»",
                "phone": "+380931234567",
                "email": "office@svitlo.in.ua",
                "source": Contact.Source.RECOMMENDATION,
                "tags": ["гарячий лід", "B2B"],
                "comment": "Прийшли за рекомендацією старого клієнта.",
            },
        ]

        contacts = []
        for c in contacts_data:
            contact, _ = Contact.objects.get_or_create(
                name=c["name"],
                defaults={
                    "phone": c["phone"],
                    "email": c["email"],
                    "source": c["source"],
                    "comment": c["comment"],
                },
            )
            # Прив’язуємо теги
            contact.tags.set([tags[t] for t in c["tags"] if t in tags])
            contacts.append(contact)

        self.stdout.write(self.style.SUCCESS(f"Створено/оновлено {len(contacts)} контактів"))

        # --- Продукти ---
        products_data = [
            {
                "name": "Захисний кожух для генератора Konner & Sohnen",
                "sku": "GEN-BOX-KS",
                "description": "Металевий короб для генератора Konner & Sohnen, базовий захист від опадів.",
                "base_price": 9500.00,
            },
            {
                "name": "Універсальний бокс для генератора 3–5 кВт",
                "sku": "GEN-BOX-UNI-3-5",
                "description": "Універсальний антивандальний бокс для більшості побутових генераторів 3–5 кВт.",
                "base_price": 11500.00,
            },
            {
                "name": "Металевий стіл для генератора (підставка)",
                "sku": "GEN-TABLE-01",
                "description": "Металева підставка для генератора з антивібраційними опорами.",
                "base_price": 3500.00,
            },
        ]

        products = []
        for p in products_data:
            product, _ = Product.objects.get_or_create(
                sku=p["sku"],
                defaults={
                    "name": p["name"],
                    "description": p["description"],
                    "base_price": p["base_price"],
                    "is_active": True,
                },
            )
            products.append(product)

        self.stdout.write(self.style.SUCCESS(f"Створено/оновлено {len(products)} продуктів"))

        # --- Верстати (Machine) ---
        machines_data = [
            {
                "name": "Лазерний верстат 3 кВт",
                "type": Machine.MachineType.LASER,
                "workday_start": time(8, 0),
                "workday_end": time(17, 0),
                "comment": "Основний лазер для різки корпусів.",
            },
            {
                "name": "Гибочний верстат 160т",
                "type": Machine.MachineType.BENDING,
                "workday_start": time(8, 0),
                "workday_end": time(17, 0),
                "comment": "Гибка деталей для кожухів та столів.",
            },
            {
                "name": "Зварювальний пост №1",
                "type": Machine.MachineType.WELDING,
                "workday_start": time(9, 0),
                "workday_end": time(18, 0),
                "comment": "Основний зварювальний пост.",
            },
            {
                "name": "Фарбувальна камера",
                "type": Machine.MachineType.PAINTING,
                "workday_start": time(10, 0),
                "workday_end": time(19, 0),
                "comment": "Порошкове фарбування.",
            },
        ]

        machines = []
        for m in machines_data:
            machine, _ = Machine.objects.get_or_create(
                name=m["name"],
                defaults={
                    "type": m["type"],
                    "workday_start": m["workday_start"],
                    "workday_end": m["workday_end"],
                    "comment": m["comment"],
                },
            )
            machines.append(machine)

        self.stdout.write(self.style.SUCCESS(f"Створено/оновлено {len(machines)} верстатів"))

        # --- Виробничі дільниці (WorkUnit) ---
        work_units_data = [
            {
                "name": "Зварювальна дільниця №1",
                "type": WorkUnit.UnitType.WELDING,
                "comment": "Збірка каркасів кожухів.",
            },
            {
                "name": "Фарбувальна дільниця №1",
                "type": WorkUnit.UnitType.PAINTING,
                "comment": "Фарбування готових виробів.",
            },
            {
                "name": "Склад готової продукції",
                "type": WorkUnit.UnitType.STORAGE,
                "comment": "Тимчасове зберігання перед відправкою.",
            },
        ]

        work_units = []
        for wu in work_units_data:
            unit, _ = WorkUnit.objects.get_or_create(
                name=wu["name"],
                defaults={
                    "type": wu["type"],
                    "comment": wu["comment"],
                },
            )
            work_units.append(unit)

        self.stdout.write(self.style.SUCCESS(f"Створено/оновлено {len(work_units)} виробничих дільниць"))

        # --- Замовлення (Order) + позиції (OrderItem) ---
        now = timezone.now()

        orders_data = [
            {
                "contact": contacts[0],
                "status": Order.Status.IN_PROGRESS,
                "deadline": (now + timedelta(days=5)).date(),
                "shipping_address": "м. Київ, Нова Пошта №12",
                "recipient": "Петренко Олександр",
                "delivery_method": Order.DeliveryMethod.NOVA_POSHTA,
                "payment_type": Order.PaymentType.PREPAY,
                "items": [
                    {"product": products[0], "quantity": 2},
                    {"product": products[2], "quantity": 1},
                ],
                "comment": "Терміново, потрібно до кінця тижня.",
            },
            {
                "contact": contacts[1],
                "status": Order.Status.NEW,
                "deadline": (now + timedelta(days=10)).date(),
                "shipping_address": "м. Біла Церква, НП №5",
                "recipient": "Іван Петров",
                "delivery_method": Order.DeliveryMethod.NOVA_POSHTA,
                "payment_type": Order.PaymentType.COD,
                "items": [
                    {"product": products[1], "quantity": 1},
                ],
                "comment": "Можлива зміна моделі генератора, попросили уточнити.",
            },
            {
                "contact": contacts[2],
                "status": Order.Status.SHIPPED,
                "deadline": (now + timedelta(days=2)).date(),
                "shipping_address": "м. Львів, НП №3",
                "recipient": "ФОП «Світло в дім»",
                "delivery_method": Order.DeliveryMethod.NOVA_POSHTA,
                "payment_type": Order.PaymentType.CASHLESS,
                "items": [
                    {"product": products[1], "quantity": 3},
                    {"product": products[2], "quantity": 3},
                ],
                "comment": "Оплата по безготівці, виставлено рахунок.",
            },
        ]

        orders = []
        for idx, od in enumerate(orders_data, start=1):
            order, created = Order.objects.get_or_create(
                contact=od["contact"],
                created_at__date=now.date(),
                comment=od["comment"],
                defaults={
                    "status": od["status"],
                    "deadline": od["deadline"],
                    "shipping_address": od["shipping_address"],
                    "recipient": od["recipient"],
                    "delivery_method": od["delivery_method"],
                    "payment_type": od["payment_type"],
                    "payment_amount": 0,  # порахуємо нижче
                },
            )

            # Якщо замовлення вже існувало — очищаємо позиції й додаємо заново
            if not created:
                order.items.all().delete()

            total_amount = 0
            for item_data in od["items"]:
                product = item_data["product"]
                quantity = item_data["quantity"]
                unit_price = product.base_price or 0
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    unit_price=unit_price,
                    comment="",
                )
                total_amount += unit_price * quantity

            order.payment_amount = total_amount
            order.save()
            orders.append(order)

        self.stdout.write(self.style.SUCCESS(f"Створено/оновлено {len(orders)} замовлень з позиціями"))

        # --- Виробничі слоти (ProductionSlot) ---
        # Для простоти: по два слоти на перше замовлення на лазер і зварку, один на фарбування
        if machines and work_units and orders:
            # видалити старі демо-слоти для цих замовлень
            ProductionSlot.objects.filter(order__in=orders).delete()

            base_start = now.replace(minute=0, second=0, microsecond=0)

            slots_data = [
                # Лазер
                {
                    "order": orders[0],
                    "machine": machines[0],  # Лазер
                    "work_unit": None,
                    "start": base_start + timedelta(hours=2),
                    "end": base_start + timedelta(hours=4),
                    "comment": "Різка деталей корпусу.",
                },
                # Гибка
                {
                    "order": orders[0],
                    "machine": machines[1],  # Гибка
                    "work_unit": None,
                    "start": base_start + timedelta(hours=5),
                    "end": base_start + timedelta(hours=7),
                    "comment": "Гибка панелей корпусу.",
                },
                # Зварка
                {
                    "order": orders[0],
                    "machine": machines[2],  # Зварювальний пост
                    "work_unit": work_units[0],  # Зварювальна дільниця
                    "start": base_start + timedelta(days=1, hours=2),
                    "end": base_start + timedelta(days=1, hours=6),
                    "comment": "Зварка каркасів.",
                },
                # Фарбування для другого замовлення
                {
                    "order": orders[1],
                    "machine": machines[3],  # Фарбувальна камера
                    "work_unit": work_units[1],
                    "start": base_start + timedelta(days=2, hours=1),
                    "end": base_start + timedelta(days=2, hours=5),
                    "comment": "Фарбування та сушіння.",
                },
            ]

            for sd in slots_data:
                ProductionSlot.objects.create(
                    order=sd["order"],
                    machine=sd["machine"],
                    work_unit=sd["work_unit"],
                    start_datetime=sd["start"],
                    end_datetime=sd["end"],
                    comment=sd["comment"],
                )

            self.stdout.write(self.style.SUCCESS(f"Створено {len(slots_data)} виробничих слотів"))

        # --- Задачі (Task) ---
        tasks_data = [
            {
                "contact": contacts[0],
                "title": "Передзвонити щодо узгодження розмірів кожуха",
                "days_offset": 1,
                "status": False,
                "comment": "Уточнити марку генератора і габарити.",
            },
            {
                "contact": contacts[1],
                "title": "Нагадати про передоплату",
                "days_offset": 0,
                "status": False,
                "comment": "Відправити реквізити ще раз у Viber.",
            },
            {
                "contact": contacts[2],
                "title": "Уточнити адресу доставки для партії боків",
                "days_offset": 2,
                "status": False,
                "comment": "Можлива відправка на різні відділення НП.",
            },
        ]

        for td in tasks_data:
            task_date = (now + timedelta(days=td["days_offset"])).date()
            Task.objects.get_or_create(
                contact=td["contact"],
                title=td["title"],
                date=task_date,
                defaults={
                    "assigned_by": user,
                    "assigned_to": user,
                    "status": td["status"],
                    "comment": td["comment"],
                },
            )

        self.stdout.write(self.style.SUCCESS("Створено/оновлено задачі для контактів"))

        self.stdout.write(self.style.SUCCESS("Готово! База заповнена демо-даними."))
