from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import TelegramNotification, TelegramUpdateLog, UserProfile
from core.telegram.services import enqueue_deadline_notifications
from crm.models import Client, Contact, Order, OrderItem, Product, Tag, Task
from crm.services import sync_order_status_from_production
from manufacture.models import Machine, ProductionSlot, ProductionStage, ResourceDowntime, WorkUnit
from manufacture.services import find_next_available_window, planner_execution


class Command(BaseCommand):
    help = "Seed a rich demo dataset for CRM, production, reporting, and Telegram scenarios."

    seed_prefix = "[seed-demo]"
    demo_user_specs = (
        {
            "username": "demo_admin",
            "email": "demo_admin@example.com",
            "full_name": "Demo Administrator",
            "role": UserProfile.Role.ADMIN,
            "phone": "+380000100001",
            "telegram_chat_id": "",
            "telegram_username": "",
        },
        {
            "username": "demo_manager",
            "email": "demo_manager@example.com",
            "full_name": "Demo Sales Manager",
            "role": UserProfile.Role.SALES_MANAGER,
            "phone": "+380000100002",
            "telegram_chat_id": "91001",
            "telegram_username": "demo_manager_bot",
        },
        {
            "username": "demo_production",
            "email": "demo_production@example.com",
            "full_name": "Demo Production Lead",
            "role": UserProfile.Role.PRODUCTION,
            "phone": "+380000100003",
            "telegram_chat_id": "91002",
            "telegram_username": "demo_production_bot",
        },
        {
            "username": "demo_executive",
            "email": "demo_executive@example.com",
            "full_name": "Demo Executive",
            "role": UserProfile.Role.EXECUTIVE,
            "phone": "+380000100004",
            "telegram_chat_id": "91003",
            "telegram_username": "demo_executive_bot",
        },
    )

    def _seed_marker(self, key):
        return f"{self.seed_prefix}:{key}"

    def _seed_text(self, key, label):
        return f"{self._seed_marker(key)} {label}"

    def _business_date(self, day_offset):
        current = timezone.localdate()
        if day_offset == 0:
            while current.weekday() > 4:
                current += timedelta(days=1)
            return current

        step = 1 if day_offset > 0 else -1
        remaining = abs(day_offset)
        while remaining:
            current += timedelta(days=step)
            if current.weekday() <= 4:
                remaining -= 1
        return current

    def _make_dt(self, day_offset, hour, minute=0):
        current_date = self._business_date(day_offset)
        return timezone.make_aware(
            datetime.combine(current_date, time(hour, minute)),
            timezone.get_current_timezone(),
        )

    def _ensure_user(self, spec):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=spec["username"],
            defaults={
                "email": spec["email"],
                "is_active": True,
            },
        )
        updated_fields = []
        if user.email != spec["email"]:
            user.email = spec["email"]
            updated_fields.append("email")
        if not user.is_active:
            user.is_active = True
            updated_fields.append("is_active")
        if created:
            user.set_password("ChangeMe123!")
            updated_fields.append("password")
        if updated_fields:
            user.save(update_fields=updated_fields)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.full_name = spec["full_name"]
        profile.phone = spec["phone"]
        profile.role = spec["role"]
        profile.telegram_chat_id = spec["telegram_chat_id"]
        profile.telegram_username = spec["telegram_username"]
        profile.telegram_linked_at = timezone.now() if spec["telegram_chat_id"] else None
        profile.telegram_notifications_enabled = True
        profile.telegram_notify_new_tasks = True
        profile.telegram_notify_deadlines = True
        profile.telegram_notify_overdue = True
        profile.telegram_notify_order_updates = True
        profile.telegram_notify_production_events = True
        profile.save()
        return user

    def _ensure_client(self, **defaults):
        client, _ = Client.objects.update_or_create(
            name=defaults["name"],
            defaults=defaults,
        )
        return client

    def _ensure_contact(self, client, **defaults):
        contact, _ = Contact.objects.update_or_create(
            client=client,
            full_name=defaults["full_name"],
            defaults=defaults,
        )
        return contact

    def _ensure_product(self, **defaults):
        product, _ = Product.objects.update_or_create(
            sku=defaults["sku"],
            defaults=defaults,
        )
        return product

    def _ensure_machine(self, **defaults):
        machine, _ = Machine.objects.update_or_create(
            name=defaults["name"],
            defaults=defaults,
        )
        return machine

    def _ensure_workunit(self, **defaults):
        work_unit, _ = WorkUnit.objects.update_or_create(
            name=defaults["name"],
            defaults=defaults,
        )
        return work_unit

    def _cleanup_seed_runtime(self):
        demo_usernames = [spec["username"] for spec in self.demo_user_specs]
        demo_chat_ids = [spec["telegram_chat_id"] for spec in self.demo_user_specs if spec["telegram_chat_id"]]
        Task.objects.filter(comment__startswith=self.seed_prefix).delete()
        Order.objects.filter(comment__startswith=self.seed_prefix).delete()
        ResourceDowntime.objects.filter(comment__startswith=self.seed_prefix).delete()
        TelegramNotification.objects.filter(profile__user__username__in=demo_usernames).delete()
        TelegramUpdateLog.objects.filter(chat_id__in=demo_chat_ids).delete()

    def _create_order(self, *, key, contact, manager, items, priority, deadline, status=Order.Status.NEW, title_note=""):
        order = Order.objects.create(
            contact=contact,
            manager=manager,
            priority=priority,
            status=status,
            deadline=deadline,
            delivery_method=Order.DeliveryMethod.NOVA_POSHTA,
            shipping_address=f"{self._seed_marker(key)} Warehouse pickup point",
            recipient=contact.full_name,
            recipient_phone=contact.phone,
            tracking_number=f"SEED-{key.upper()[:10]}",
            payment_type=Order.PaymentType.PREPAY,
            payment_terms="50% upfront, 50% before shipment",
            payment_amount=Decimal("0.00"),
            comment=self._seed_text(key, title_note or f"Demo order {key}"),
        )
        total = Decimal("0.00")
        created_items = []
        for index, item_data in enumerate(items, start=1):
            order_item = OrderItem.objects.create(
                order=order,
                product=item_data["product"],
                quantity=item_data.get("quantity", 1),
                unit_price=item_data.get("unit_price", item_data["product"].base_price or Decimal("0.00")),
                comment=self._seed_text(f"{key}-item-{index}", "Demo order item"),
            )
            total += (order_item.unit_price or Decimal("0.00")) * order_item.quantity
            created_items.append(order_item)
        order.payment_amount = total
        order._changed_by = manager
        order.save(update_fields=["payment_amount"])
        order.refresh_title()
        return order, created_items

    def _stage(self, order_item, stage_type):
        return order_item.production_stages.get(stage_type=stage_type)

    def _create_slot(
        self,
        *,
        key,
        order,
        stage,
        resource,
        resource_field,
        start_from,
        duration,
        responsible,
        planning_mode,
        planning_source,
        is_locked,
        dispatcher_comment="",
        comment="",
        slot_type=ProductionSlot.SlotType.WORK,
        operation_type=None,
        purpose="",
    ):
        window = find_next_available_window(resource, start_from, duration)
        if not window:
            raise CommandError(f"Unable to find free window for resource '{resource}' in seed scenario '{key}'.")

        start_datetime, end_datetime = window
        slot = ProductionSlot(
            order=order,
            stage=stage,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            slot_type=slot_type,
            operation_type=operation_type or (stage.stage_type if stage else ProductionSlot.OperationType.OTHER),
            planning_mode=planning_mode,
            planning_source=planning_source,
            is_locked=is_locked,
            purpose=purpose or (f"{stage.order_item.product.name} / {stage.get_stage_type_display()}" if stage else ""),
            comment=self._seed_text(key, comment or "Demo production slot"),
            dispatcher_comment=dispatcher_comment,
        )
        setattr(slot, resource_field, resource)
        slot._planner_operation = True
        slot._history_source = "auto" if planning_mode == ProductionSlot.PlanningMode.AUTO else "system"
        slot._history_note = self._seed_text(key, "Seed generated slot")
        slot._changed_by = responsible
        slot.save()
        return slot, start_datetime, end_datetime

    def _mark_stage_done_without_slot(self, *, key, stage, responsible, start_datetime, end_datetime):
        stage.status = ProductionStage.Status.DONE
        stage.responsible = responsible
        stage.planned_start = start_datetime
        stage.planned_end = end_datetime
        stage.started_at = start_datetime
        stage.completed_at = end_datetime
        stage.comment = self._seed_text(key, "Completed in seed without dedicated slot")
        stage._changed_by = responsible
        stage.save(
            update_fields=[
                "status",
                "responsible",
                "planned_start",
                "planned_end",
                "started_at",
                "completed_at",
                "comment",
                "updated_at",
            ]
        )

    def _mark_stage_with_slot(
        self,
        *,
        key,
        order,
        stage,
        resource,
        resource_field,
        start_from,
        duration,
        responsible,
        status,
        planning_mode,
        planning_source,
        is_locked=False,
        dispatcher_comment="",
        comment="",
        slot_type=ProductionSlot.SlotType.WORK,
        operation_type=None,
        purpose="",
    ):
        slot, start_datetime, end_datetime = self._create_slot(
            key=key,
            order=order,
            stage=stage,
            resource=resource,
            resource_field=resource_field,
            start_from=start_from,
            duration=duration,
            responsible=responsible,
            planning_mode=planning_mode,
            planning_source=planning_source,
            is_locked=is_locked,
            dispatcher_comment=dispatcher_comment,
            comment=comment,
            slot_type=slot_type,
            operation_type=operation_type,
            purpose=purpose,
        )
        stage.status = status
        stage.responsible = responsible
        stage.planned_start = start_datetime
        stage.planned_end = end_datetime
        stage.comment = self._seed_text(key, comment or f"Stage configured as {status}")
        if status in {ProductionStage.Status.IN_PROGRESS, ProductionStage.Status.DONE, ProductionStage.Status.BLOCKED}:
            stage.started_at = start_datetime
        else:
            stage.started_at = None
        if status == ProductionStage.Status.DONE:
            stage.completed_at = end_datetime
        else:
            stage.completed_at = None
        stage._changed_by = responsible
        stage.save(
            update_fields=[
                "status",
                "responsible",
                "planned_start",
                "planned_end",
                "started_at",
                "completed_at",
                "comment",
                "updated_at",
            ]
        )
        return slot, start_datetime, end_datetime

    def _cancel_stage(self, *, key, stage, responsible):
        stage.status = ProductionStage.Status.CANCELLED
        stage.responsible = responsible
        stage.started_at = None
        stage.completed_at = timezone.now()
        stage.comment = self._seed_text(key, "Cancelled in seed")
        stage._changed_by = responsible
        stage.save(update_fields=["status", "responsible", "started_at", "completed_at", "comment", "updated_at"])

    def _sync_order(self, order, *, preserve_terminal=True):
        sync_order_status_from_production(order, save=True, preserve_terminal=preserve_terminal)
        order.refresh_from_db()
        return order

    def _create_demo_updates(self):
        payloads = (
            (
                990001,
                "91001",
                "demo_manager_bot",
                TelegramUpdateLog.Status.PROCESSED,
                {"message": {"text": "/link DEMO-CODE"}},
                "message",
                "",
            ),
            (
                990002,
                "91001",
                "demo_manager_bot",
                TelegramUpdateLog.Status.PROCESSED,
                {"message": {"text": "/tasks"}},
                "message",
                "",
            ),
            (
                990003,
                "91002",
                "demo_production_bot",
                TelegramUpdateLog.Status.IGNORED,
                {"message": {"text": "/unknown"}},
                "message",
                "Unsupported command",
            ),
        )
        for update_id, chat_id, username, status, payload, update_type, error_message in payloads:
            TelegramUpdateLog.objects.update_or_create(
                update_id=update_id,
                defaults={
                    "chat_id": chat_id,
                    "username": username,
                    "update_type": update_type,
                    "payload": payload,
                    "status": status,
                    "error_message": error_message,
                    "processed_at": timezone.now(),
                },
            )

    def _create_demo_notification_samples(self, *, manager_profile, production_profile, order, task, stage):
        TelegramNotification.objects.update_or_create(
            dedupe_key="seed-demo:sent-order-summary",
            defaults={
                "profile": manager_profile,
                "notification_type": TelegramNotification.Type.ORDER_STATUS,
                "message_text": "Seed demo: order summary already delivered.",
                "payload": {"source": "seed-demo"},
                "order": order,
                "status": TelegramNotification.Status.SENT,
                "scheduled_for": timezone.now() - timedelta(hours=1),
                "sent_at": timezone.now() - timedelta(minutes=30),
                "delivery_attempts": 1,
                "error_message": "",
            },
        )
        TelegramNotification.objects.update_or_create(
            dedupe_key="seed-demo:failed-production-event",
            defaults={
                "profile": production_profile,
                "notification_type": TelegramNotification.Type.PRODUCTION_EVENT,
                "message_text": "Seed demo: failed production event delivery.",
                "payload": {"source": "seed-demo"},
                "order": order,
                "stage": stage,
                "status": TelegramNotification.Status.FAILED,
                "scheduled_for": timezone.now() - timedelta(hours=2),
                "sent_at": None,
                "delivery_attempts": 1,
                "error_message": "Seed demo transport error.",
            },
        )
        TelegramNotification.objects.update_or_create(
            dedupe_key="seed-demo:pending-task-follow-up",
            defaults={
                "profile": manager_profile,
                "notification_type": TelegramNotification.Type.TASK_CREATED,
                "message_text": "Seed demo: pending task notification.",
                "payload": {"source": "seed-demo"},
                "task": task,
                "order": task.order,
                "status": TelegramNotification.Status.PENDING,
                "scheduled_for": timezone.now(),
                "sent_at": None,
                "delivery_attempts": 0,
                "error_message": "",
            },
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("CRM DEMO SEED START"))

        now = timezone.now()
        today = timezone.localdate()
        one_hour = timedelta(hours=1)
        ninety_minutes = timedelta(minutes=90)
        two_hours = timedelta(hours=2)
        three_hours = timedelta(hours=3)

        with planner_execution():
            self._cleanup_seed_runtime()

            users = {spec["username"]: self._ensure_user(spec) for spec in self.demo_user_specs}
            manager = users["demo_manager"]
            production_user = users["demo_production"]
            executive = users["demo_executive"]
            manager_profile = manager.profile
            production_profile = production_user.profile
            self.stdout.write(self.style.SUCCESS("Demo users and roles ready"))

            tags = {
                name: Tag.objects.get_or_create(name=name)[0]
                for name in ("seed-demo", "b2b", "b2c", "priority", "risk")
            }
            self.stdout.write(self.style.SUCCESS("Tags ready"))

            client_alpha = self._ensure_client(
                name="Alpha Power Systems",
                client_type=Client.ClientType.TOV,
                tax_code="12345678",
                phones="+380671111111",
                email="alpha@example.com",
                source=Client.Source.PROM,
                notes=self._seed_text("alpha-client", "B2B client for production demo"),
            )
            client_retail = self._ensure_client(
                name="Retail Home Customer",
                client_type=Client.ClientType.INDIVIDUAL,
                tax_code="",
                phones="+380672222222",
                email="retail@example.com",
                source=Client.Source.INSTAGRAM,
                notes=self._seed_text("retail-client", "B2C client for task board demo"),
            )
            client_gamma = self._ensure_client(
                name="Gamma Trade",
                client_type=Client.ClientType.FOP,
                tax_code="1234567890",
                phones="+380673333333",
                email="gamma@example.com",
                source=Client.Source.RECOMMENDATION,
                notes=self._seed_text("gamma-client", "Repeat customer with urgent orders"),
            )
            client_delta = self._ensure_client(
                name="Delta Energy",
                client_type=Client.ClientType.TOV,
                tax_code="87654321",
                phones="+380674444444",
                email="delta@example.com",
                source=Client.Source.OTHER,
                notes=self._seed_text("delta-client", "Used for ready and completed scenarios"),
            )
            client_alpha.tags.set([tags["seed-demo"], tags["b2b"]])
            client_retail.tags.set([tags["seed-demo"], tags["b2c"]])
            client_gamma.tags.set([tags["seed-demo"], tags["b2b"], tags["priority"]])
            client_delta.tags.set([tags["seed-demo"], tags["b2b"], tags["risk"]])

            contact_alpha = self._ensure_contact(
                client_alpha,
                full_name="Alice Buyer",
                position="Procurement",
                phone="+380671111112",
                email="alice.buyer@example.com",
                source=Contact.Source.PROM,
                notes=self._seed_text("alpha-contact", "Main B2B contact"),
            )
            contact_retail = self._ensure_contact(
                client_retail,
                full_name="Roman Retail",
                position="",
                phone="+380672222223",
                email="roman.retail@example.com",
                source=Contact.Source.INSTAGRAM,
                notes=self._seed_text("retail-contact", "Retail contact"),
            )
            contact_gamma = self._ensure_contact(
                client_gamma,
                full_name="Greg Owner",
                position="Owner",
                phone="+380673333334",
                email="greg.owner@example.com",
                source=Contact.Source.RECOMMENDATION,
                notes=self._seed_text("gamma-contact", "Urgent account owner"),
            )
            contact_delta = self._ensure_contact(
                client_delta,
                full_name="Diana Operations",
                position="Operations Manager",
                phone="+380674444445",
                email="diana.ops@example.com",
                source=Contact.Source.OTHER,
                notes=self._seed_text("delta-contact", "Operations contact"),
            )
            contact_alpha.tags.set([tags["seed-demo"], tags["b2b"]])
            contact_retail.tags.set([tags["seed-demo"], tags["b2c"]])
            contact_gamma.tags.set([tags["seed-demo"], tags["priority"]])
            contact_delta.tags.set([tags["seed-demo"], tags["risk"]])
            self.stdout.write(self.style.SUCCESS("Clients and contacts ready"))

            product_box = self._ensure_product(
                sku="DEMO-GEN-BOX",
                name="Generator Box",
                description="Protective housing for backup generator installations.",
                technical_description="Laser cutting, bending, welding, painting, and final assembly.",
                base_price=Decimal("7200.00"),
                prom_url="https://example.com/prom/demo-gen-box",
                rozetka_url="https://example.com/rozetka/demo-gen-box",
                olx_url="https://example.com/olx/demo-gen-box",
                site_url="https://example.com/products/demo-gen-box",
                photos_url="https://example.com/assets/demo-gen-box/photos",
                production_norms_url="https://example.com/assets/demo-gen-box/norms",
                is_active=True,
            )
            product_stand = self._ensure_product(
                sku="DEMO-GEN-STAND",
                name="Generator Stand",
                description="Support stand for generator box installations.",
                technical_description="Welded frame, anti-vibration pads, quick mounting kit.",
                base_price=Decimal("3400.00"),
                prom_url="https://example.com/prom/demo-stand",
                rozetka_url="https://example.com/rozetka/demo-stand",
                olx_url="https://example.com/olx/demo-stand",
                site_url="https://example.com/products/demo-stand",
                photos_url="https://example.com/assets/demo-stand/photos",
                production_norms_url="https://example.com/assets/demo-stand/norms",
                is_active=True,
            )
            product_canopy = self._ensure_product(
                sku="DEMO-SOLAR-CANOPY",
                name="Solar Canopy Frame",
                description="Frame set for rooftop canopy projects.",
                technical_description="Mixed laser, welding, painting, and assembly operations.",
                base_price=Decimal("12800.00"),
                prom_url="https://example.com/prom/demo-canopy",
                rozetka_url="https://example.com/rozetka/demo-canopy",
                olx_url="https://example.com/olx/demo-canopy",
                site_url="https://example.com/products/demo-canopy",
                photos_url="https://example.com/assets/demo-canopy/photos",
                production_norms_url="https://example.com/assets/demo-canopy/norms",
                is_active=True,
            )
            product_service = self._ensure_product(
                sku="DEMO-SERVICE-KIT",
                name="Service Mounting Kit",
                description="Small accessory item used for fast demo orders.",
                technical_description="Mostly storage and assembly operations.",
                base_price=Decimal("1200.00"),
                prom_url="https://example.com/prom/demo-service-kit",
                rozetka_url="https://example.com/rozetka/demo-service-kit",
                olx_url="https://example.com/olx/demo-service-kit",
                site_url="https://example.com/products/demo-service-kit",
                photos_url="https://example.com/assets/demo-service-kit/photos",
                production_norms_url="https://example.com/assets/demo-service-kit/norms",
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS("Products and production norm links ready"))

            laser_main = self._ensure_machine(
                name="Demo Laser Main",
                type=Machine.MachineType.LASER,
                is_active=True,
                available_weekdays="0,1,2,3,4",
                workday_start=time(8, 0),
                workday_end=time(17, 0),
                comment=self._seed_text("machine-main", "Primary laser resource"),
            )
            laser_overload = self._ensure_machine(
                name="Demo Laser Overload",
                type=Machine.MachineType.LASER,
                is_active=True,
                available_weekdays="0,1,2,3,4,5,6",
                workday_start=time(8, 0),
                workday_end=time(9, 0),
                comment=self._seed_text("machine-overload", "Short workday resource for overload report"),
            )
            paint_machine = self._ensure_machine(
                name="Demo Paint Booth",
                type=Machine.MachineType.PAINTING,
                is_active=True,
                available_weekdays="0,1,2,3,4",
                workday_start=time(10, 0),
                workday_end=time(18, 0),
                comment=self._seed_text("machine-paint", "Painting resource"),
            )
            self._ensure_machine(
                name="Demo Reserve Laser",
                type=Machine.MachineType.LASER,
                is_active=False,
                available_weekdays="0,1,2,3,4",
                workday_start=time(8, 0),
                workday_end=time(17, 0),
                comment=self._seed_text("machine-reserve", "Inactive resource to show availability constraints"),
            )
            storage_unit = self._ensure_workunit(
                name="Demo Storage",
                type=WorkUnit.UnitType.STORAGE,
                is_active=True,
                available_weekdays="0,1,2,3,4,5",
                workday_start=time(8, 0),
                workday_end=time(18, 0),
                comment=self._seed_text("unit-storage", "Storage and ready to ship operations"),
            )
            assembly_unit = self._ensure_workunit(
                name="Demo Assembly",
                type=WorkUnit.UnitType.ASSEMBLY,
                is_active=True,
                available_weekdays="0,1,2,3,4",
                workday_start=time(8, 0),
                workday_end=time(17, 0),
                comment=self._seed_text("unit-assembly", "Assembly workunit"),
            )
            self.stdout.write(self.style.SUCCESS("Production resources ready"))

            ResourceDowntime.objects.create(
                machine=laser_main,
                downtime_type=ResourceDowntime.DowntimeType.MAINTENANCE,
                start_datetime=self._make_dt(1, 9, 0),
                end_datetime=self._make_dt(1, 12, 0),
                is_blocking=True,
                comment=self._seed_text("downtime-main", "Laser maintenance window"),
            )
            ResourceDowntime.objects.create(
                work_unit=assembly_unit,
                downtime_type=ResourceDowntime.DowntimeType.MANUAL_BLOCK,
                start_datetime=self._make_dt(2, 14, 0),
                end_datetime=self._make_dt(2, 16, 0),
                is_blocking=True,
                comment=self._seed_text("downtime-assembly", "Assembly manual block"),
            )
            self.stdout.write(self.style.SUCCESS("Downtimes ready"))

            order_new, _ = self._create_order(
                key="new-order",
                contact=contact_retail,
                manager=manager,
                items=[{"product": product_service, "quantity": 1}],
                priority=Order.Priority.NORMAL,
                deadline=self._business_date(6),
                status=Order.Status.NEW,
                title_note="Fresh order without production slots yet",
            )
            order_new = self._sync_order(order_new)

            order_risk, risk_items = self._create_order(
                key="risk-order",
                contact=contact_gamma,
                manager=manager,
                items=[{"product": product_box, "quantity": 2}],
                priority=Order.Priority.URGENT,
                deadline=self._business_date(-1),
                status=Order.Status.NEW,
                title_note="Urgent overdue order with active production risk",
            )
            risk_item = risk_items[0]
            self._mark_stage_done_without_slot(
                key="risk-intake",
                stage=self._stage(risk_item, ProductionStage.StageType.INTAKE),
                responsible=production_user,
                start_datetime=self._make_dt(-4, 8, 0),
                end_datetime=self._make_dt(-4, 9, 0),
            )
            self._mark_stage_done_without_slot(
                key="risk-procurement",
                stage=self._stage(risk_item, ProductionStage.StageType.PROCUREMENT),
                responsible=production_user,
                start_datetime=self._make_dt(-3, 8, 0),
                end_datetime=self._make_dt(-3, 10, 0),
            )
            risk_stage = self._stage(risk_item, ProductionStage.StageType.EXECUTION)
            self._mark_stage_with_slot(
                key="risk-execution",
                order=order_risk,
                stage=risk_stage,
                resource=laser_main,
                resource_field="machine",
                start_from=self._make_dt(-2, 12, 0),
                duration=two_hours,
                responsible=production_user,
                status=ProductionStage.Status.IN_PROGRESS,
                planning_mode=ProductionSlot.PlanningMode.AUTO,
                planning_source=ProductionSlot.PlanningSource.PLANNER,
                comment="Execution started but is already overdue",
            )
            order_risk = self._sync_order(order_risk)

            order_blocked, blocked_items = self._create_order(
                key="blocked-order",
                contact=contact_alpha,
                manager=manager,
                items=[{"product": product_canopy, "quantity": 1}],
                priority=Order.Priority.HIGH,
                deadline=self._business_date(1),
                status=Order.Status.NEW,
                title_note="Blocked production order with manual slot",
            )
            blocked_item = blocked_items[0]
            self._mark_stage_done_without_slot(
                key="blocked-intake",
                stage=self._stage(blocked_item, ProductionStage.StageType.INTAKE),
                responsible=production_user,
                start_datetime=self._make_dt(-3, 8, 0),
                end_datetime=self._make_dt(-3, 9, 0),
            )
            self._mark_stage_done_without_slot(
                key="blocked-procurement",
                stage=self._stage(blocked_item, ProductionStage.StageType.PROCUREMENT),
                responsible=production_user,
                start_datetime=self._make_dt(-2, 8, 0),
                end_datetime=self._make_dt(-2, 10, 0),
            )
            blocked_stage = self._stage(blocked_item, ProductionStage.StageType.EXECUTION)
            self._mark_stage_with_slot(
                key="blocked-execution",
                order=order_blocked,
                stage=blocked_stage,
                resource=assembly_unit,
                resource_field="work_unit",
                start_from=self._make_dt(-1, 11, 0),
                duration=three_hours,
                responsible=production_user,
                status=ProductionStage.Status.BLOCKED,
                planning_mode=ProductionSlot.PlanningMode.MANUAL,
                planning_source=ProductionSlot.PlanningSource.DISPATCHER,
                is_locked=True,
                dispatcher_comment="Waiting for final technical approval",
                comment="Blocked after manual intervention",
            )
            order_blocked = self._sync_order(order_blocked)

            order_ready, ready_items = self._create_order(
                key="ready-order",
                contact=contact_delta,
                manager=manager,
                items=[{"product": product_stand, "quantity": 1}],
                priority=Order.Priority.NORMAL,
                deadline=self._business_date(2),
                status=Order.Status.NEW,
                title_note="Ready order with finished production chain",
            )
            ready_item = ready_items[0]
            self._mark_stage_done_without_slot(
                key="ready-intake",
                stage=self._stage(ready_item, ProductionStage.StageType.INTAKE),
                responsible=production_user,
                start_datetime=self._make_dt(-4, 8, 0),
                end_datetime=self._make_dt(-4, 9, 0),
            )
            self._mark_stage_done_without_slot(
                key="ready-procurement",
                stage=self._stage(ready_item, ProductionStage.StageType.PROCUREMENT),
                responsible=production_user,
                start_datetime=self._make_dt(-3, 8, 0),
                end_datetime=self._make_dt(-3, 10, 0),
            )
            _, _, ready_exec_end = self._mark_stage_with_slot(
                key="ready-execution",
                order=order_ready,
                stage=self._stage(ready_item, ProductionStage.StageType.EXECUTION),
                resource=laser_main,
                resource_field="machine",
                start_from=self._make_dt(-2, 8, 0),
                duration=ninety_minutes,
                responsible=production_user,
                status=ProductionStage.Status.DONE,
                planning_mode=ProductionSlot.PlanningMode.AUTO,
                planning_source=ProductionSlot.PlanningSource.PLANNER,
                comment="Execution finished automatically",
            )
            _, _, ready_paint_end = self._mark_stage_with_slot(
                key="ready-painting",
                order=order_ready,
                stage=self._stage(ready_item, ProductionStage.StageType.PAINTING),
                resource=paint_machine,
                resource_field="machine",
                start_from=ready_exec_end,
                duration=ninety_minutes,
                responsible=production_user,
                status=ProductionStage.Status.DONE,
                planning_mode=ProductionSlot.PlanningMode.MANUAL,
                planning_source=ProductionSlot.PlanningSource.DISPATCHER,
                is_locked=True,
                dispatcher_comment="Manual correction after paint queue review",
                comment="Painting finished on manual slot",
            )
            self._mark_stage_done_without_slot(
                key="ready-shipping",
                stage=self._stage(ready_item, ProductionStage.StageType.READY_TO_SHIP),
                responsible=production_user,
                start_datetime=ready_paint_end,
                end_datetime=ready_paint_end + one_hour,
            )
            order_ready = self._sync_order(order_ready)

            order_completed, completed_items = self._create_order(
                key="completed-order",
                contact=contact_alpha,
                manager=manager,
                items=[{"product": product_service, "quantity": 2}],
                priority=Order.Priority.NORMAL,
                deadline=self._business_date(-2),
                status=Order.Status.NEW,
                title_note="Completed order for conversion stats",
            )
            completed_item = completed_items[0]
            self._mark_stage_done_without_slot(
                key="completed-intake",
                stage=self._stage(completed_item, ProductionStage.StageType.INTAKE),
                responsible=production_user,
                start_datetime=self._make_dt(-5, 8, 0),
                end_datetime=self._make_dt(-5, 9, 0),
            )
            self._mark_stage_done_without_slot(
                key="completed-procurement",
                stage=self._stage(completed_item, ProductionStage.StageType.PROCUREMENT),
                responsible=production_user,
                start_datetime=self._make_dt(-4, 8, 0),
                end_datetime=self._make_dt(-4, 9, 30),
            )
            _, _, completed_exec_end = self._mark_stage_with_slot(
                key="completed-execution",
                order=order_completed,
                stage=self._stage(completed_item, ProductionStage.StageType.EXECUTION),
                resource=laser_main,
                resource_field="machine",
                start_from=self._make_dt(-4, 12, 0),
                duration=one_hour,
                responsible=production_user,
                status=ProductionStage.Status.DONE,
                planning_mode=ProductionSlot.PlanningMode.AUTO,
                planning_source=ProductionSlot.PlanningSource.PLANNER,
                comment="Completed historical execution stage",
            )
            self._mark_stage_done_without_slot(
                key="completed-painting",
                stage=self._stage(completed_item, ProductionStage.StageType.PAINTING),
                responsible=production_user,
                start_datetime=completed_exec_end,
                end_datetime=completed_exec_end + one_hour,
            )
            self._mark_stage_done_without_slot(
                key="completed-shipping",
                stage=self._stage(completed_item, ProductionStage.StageType.READY_TO_SHIP),
                responsible=production_user,
                start_datetime=completed_exec_end + one_hour,
                end_datetime=completed_exec_end + two_hours,
            )
            order_completed = self._sync_order(order_completed)
            order_completed.status = Order.Status.COMPLETED
            order_completed._changed_by = manager
            order_completed.save(update_fields=["status"])

            order_canceled, canceled_items = self._create_order(
                key="canceled-order",
                contact=contact_retail,
                manager=manager,
                items=[{"product": product_box, "quantity": 1}],
                priority=Order.Priority.LOW,
                deadline=self._business_date(4),
                status=Order.Status.NEW,
                title_note="Canceled order for dashboard conversion",
            )
            canceled_item = canceled_items[0]
            for stage_key, stage_type in (
                ("canceled-intake", ProductionStage.StageType.INTAKE),
                ("canceled-procurement", ProductionStage.StageType.PROCUREMENT),
                ("canceled-execution", ProductionStage.StageType.EXECUTION),
                ("canceled-painting", ProductionStage.StageType.PAINTING),
                ("canceled-shipping", ProductionStage.StageType.READY_TO_SHIP),
            ):
                self._cancel_stage(
                    key=stage_key,
                    stage=self._stage(canceled_item, stage_type),
                    responsible=manager,
                )
            order_canceled.status = Order.Status.CANCELED
            order_canceled._changed_by = manager
            order_canceled.save(update_fields=["status"])
            self.stdout.write(self.style.SUCCESS("Primary order scenarios ready"))

            overload_start = self._make_dt(0, 8, 0)
            for index in range(8):
                overload_order, overload_items = self._create_order(
                    key=f"overload-order-{index + 1}",
                    contact=contact_alpha if index % 2 == 0 else contact_gamma,
                    manager=manager,
                    items=[{"product": product_stand if index % 2 == 0 else product_box, "quantity": 1}],
                    priority=Order.Priority.HIGH if index < 4 else Order.Priority.NORMAL,
                    deadline=today + timedelta(days=index + 1),
                    status=Order.Status.NEW,
                    title_note=f"Overload scenario #{index + 1}",
                )
                overload_item = overload_items[0]
                self._mark_stage_done_without_slot(
                    key=f"overload-intake-{index + 1}",
                    stage=self._stage(overload_item, ProductionStage.StageType.INTAKE),
                    responsible=production_user,
                    start_datetime=self._make_dt(-2, 8, 0),
                    end_datetime=self._make_dt(-2, 9, 0),
                )
                self._mark_stage_done_without_slot(
                    key=f"overload-procurement-{index + 1}",
                    stage=self._stage(overload_item, ProductionStage.StageType.PROCUREMENT),
                    responsible=production_user,
                    start_datetime=self._make_dt(-1, 8, 0),
                    end_datetime=self._make_dt(-1, 9, 0),
                )
                self._mark_stage_with_slot(
                    key=f"overload-execution-{index + 1}",
                    order=overload_order,
                    stage=self._stage(overload_item, ProductionStage.StageType.EXECUTION),
                    resource=laser_overload,
                    resource_field="machine",
                    start_from=overload_start,
                    duration=one_hour,
                    responsible=production_user,
                    status=ProductionStage.Status.SCHEDULED,
                    planning_mode=ProductionSlot.PlanningMode.AUTO,
                    planning_source=ProductionSlot.PlanningSource.PLANNER,
                    comment=f"Reserved overload capacity #{index + 1}",
                )
                self._sync_order(overload_order)
            self.stdout.write(self.style.SUCCESS("Overload and queue scenarios ready"))

            tasks = [
                Task(
                    client=client_gamma,
                    contact=contact_gamma,
                    order=order_risk,
                    title="Call client about overdue urgent order",
                    assigned_by=manager,
                    assigned_to=manager,
                    date=today - timedelta(days=2),
                    status=Task.Status.NEW,
                    comment=self._seed_text("task-overdue-new", "Manager must explain delay"),
                ),
                Task(
                    client=client_alpha,
                    contact=contact_alpha,
                    order=order_blocked,
                    title="Prepare technical clarification for blocked order",
                    assigned_by=manager,
                    assigned_to=production_user,
                    date=today,
                    status=Task.Status.IN_PROGRESS,
                    comment=self._seed_text("task-in-progress", "Production follow-up"),
                ),
                Task(
                    client=client_alpha,
                    contact=contact_alpha,
                    order=order_blocked,
                    title="Await supplier confirmation",
                    assigned_by=manager,
                    assigned_to=manager,
                    date=today - timedelta(days=1),
                    status=Task.Status.WAITING,
                    comment=self._seed_text("task-waiting", "Waiting for external answer"),
                ),
                Task(
                    client=client_delta,
                    contact=contact_delta,
                    order=order_ready,
                    title="Send ready order summary to client",
                    assigned_by=manager,
                    assigned_to=manager,
                    date=today - timedelta(days=1),
                    status=Task.Status.DONE,
                    comment=self._seed_text("task-done", "Documentation sent"),
                ),
                Task(
                    client=client_delta,
                    contact=contact_delta,
                    order=order_new,
                    title="Review executive weekly risk report",
                    assigned_by=manager,
                    assigned_to=executive,
                    date=today + timedelta(days=1),
                    status=Task.Status.NEW,
                    comment=self._seed_text("task-executive", "Used for Telegram and dashboard demo"),
                ),
                Task(
                    client=client_retail,
                    contact=contact_retail,
                    order=order_new,
                    title="Confirm delivery details with retail customer",
                    assigned_by=manager,
                    assigned_to=manager,
                    date=today + timedelta(days=1),
                    status=Task.Status.IN_PROGRESS,
                    comment=self._seed_text("task-deadline", "Near-term reminder scenario"),
                ),
            ]
            for task in tasks:
                task._changed_by = task.assigned_by
                task.save()
            self.stdout.write(self.style.SUCCESS("Tasks across all statuses ready"))

            enqueue_deadline_notifications(now=now)
            self._create_demo_updates()
            self._create_demo_notification_samples(
                manager_profile=manager_profile,
                production_profile=production_profile,
                order=order_blocked,
                task=tasks[-1],
                stage=blocked_stage,
            )
            self.stdout.write(self.style.SUCCESS("Telegram scenarios ready"))

        self.stdout.write(
            self.style.SUCCESS(
                "Seed summary: "
                f"clients={Client.objects.count()} "
                f"contacts={Contact.objects.count()} "
                f"orders={Order.objects.count()} "
                f"tasks={Task.objects.count()} "
                f"slots={ProductionSlot.objects.count()}"
            )
        )
        self.stdout.write(self.style.SUCCESS("CRM DEMO SEED DONE"))
