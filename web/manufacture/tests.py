from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Client, Contact, Order, OrderItem, Product
from manufacture.models import (
    Machine,
    ProductionSlot,
    ProductionSlotChangeLog,
    ProductionStage,
    ResourceDowntime,
    WorkUnit,
)


class ManufacturePlanningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="planner@example.com",
            email="planner@example.com",
            password="secret123",
            is_active=True,
        )
        client = Client.objects.create(name="Planner Client", email="planner-client@example.com")
        self.contact = Contact.objects.create(client=client, full_name="Planner Contact")
        self.product = Product.objects.create(name="Planner Product", sku="PLANNER-001")

        self.storage = WorkUnit.objects.create(name="Storage", type=WorkUnit.UnitType.STORAGE)
        self.assembly = WorkUnit.objects.create(name="Assembly", type=WorkUnit.UnitType.ASSEMBLY)
        self.laser = Machine.objects.create(name="Laser A", type=Machine.MachineType.LASER)
        self.paint = Machine.objects.create(name="Paint A", type=Machine.MachineType.PAINTING)

    def make_dt(self, day_offset, hour, minute=0):
        current_date = timezone.localdate() + timedelta(days=day_offset)
        while current_date.weekday() > 4:
            current_date += timedelta(days=1)
        return timezone.make_aware(datetime.combine(current_date, time(hour, minute)))

    def create_order(self, *, quantity=2, priority=Order.Priority.NORMAL, deadline=None):
        with self.captureOnCommitCallbacks(execute=True):
            order = Order.objects.create(
                contact=self.contact,
                manager=self.user,
                priority=priority,
                deadline=deadline or (timezone.localdate() + timedelta(days=5)),
            )
            OrderItem.objects.create(
                order=order,
                product=self.product,
                quantity=quantity,
                unit_price=Decimal("100.00"),
            )
        return Order.objects.get(pk=order.pk)

    def test_order_creation_auto_builds_slots_for_all_default_stages(self):
        order = self.create_order()

        stages = list(
            ProductionStage.objects.filter(order_item__order=order).order_by("sequence", "id")
        )
        slots = list(order.slots.select_related("stage").order_by("start_datetime", "id"))

        self.assertEqual(len(stages), 5)
        self.assertEqual(len(slots), 5)
        self.assertTrue(all(slot.planning_mode == ProductionSlot.PlanningMode.AUTO for slot in slots))
        self.assertTrue(all(not slot.is_locked for slot in slots))
        self.assertTrue(all(stage.status == ProductionStage.Status.SCHEDULED for stage in stages))
        self.assertEqual(order.status, Order.Status.IN_PRODUCTION)
        self.assertEqual(
            [stage.stage_type for stage in stages],
            [
                ProductionStage.StageType.INTAKE,
                ProductionStage.StageType.PROCUREMENT,
                ProductionStage.StageType.EXECUTION,
                ProductionStage.StageType.PAINTING,
                ProductionStage.StageType.READY_TO_SHIP,
            ],
        )
        self.assertTrue(all(slot.start_datetime < slot.end_datetime for slot in slots))

    def test_auto_slots_store_operation_metadata(self):
        order = self.create_order()

        slots = list(order.slots.select_related("stage", "stage__order_item__product").order_by("start_datetime", "id"))

        self.assertTrue(all(slot.slot_type == ProductionSlot.SlotType.WORK for slot in slots))
        self.assertTrue(all(slot.planning_source == ProductionSlot.PlanningSource.PLANNER for slot in slots))
        self.assertTrue(all(slot.operation_type == slot.stage.stage_type for slot in slots if slot.stage_id))
        self.assertTrue(all(slot.purpose for slot in slots))

    def test_planner_skips_blocked_resource_time(self):
        self.assembly.is_active = False
        self.assembly.save()
        ResourceDowntime.objects.create(
            machine=self.laser,
            downtime_type=ResourceDowntime.DowntimeType.MAINTENANCE,
            start_datetime=self.make_dt(0, 8),
            end_datetime=self.make_dt(3, 12),
            comment="Morning maintenance",
        )

        order = self.create_order()
        execution_stage = ProductionStage.objects.get(
            order_item__order=order,
            stage_type=ProductionStage.StageType.EXECUTION,
        )
        execution_slot = execution_stage.slots.get()

        self.assertGreaterEqual(timezone.localtime(execution_slot.start_datetime).time(), time(12, 0))

    def test_manual_slot_is_preserved_after_replanning_order_priority(self):
        order = self.create_order()
        execution_stage = ProductionStage.objects.get(
            order_item__order=order,
            stage_type=ProductionStage.StageType.EXECUTION,
        )
        execution_slot = execution_stage.slots.get()

        manual_start = self.make_dt(1, 13)
        manual_end = self.make_dt(1, 15)
        with self.captureOnCommitCallbacks(execute=True):
            execution_slot.start_datetime = manual_start
            execution_slot.end_datetime = manual_end
            execution_slot.comment = "Manual override"
            execution_slot.save()

        execution_slot.refresh_from_db()
        self.assertEqual(execution_slot.planning_mode, ProductionSlot.PlanningMode.MANUAL)
        self.assertTrue(execution_slot.is_locked)

        with self.captureOnCommitCallbacks(execute=True):
            order.priority = Order.Priority.URGENT
            order.save()

        execution_slot.refresh_from_db()
        painting_stage = ProductionStage.objects.get(
            order_item__order=order,
            stage_type=ProductionStage.StageType.PAINTING,
        )

        self.assertEqual(execution_slot.start_datetime, manual_start)
        self.assertEqual(execution_slot.end_datetime, manual_end)
        self.assertGreaterEqual(painting_stage.planned_start, manual_end)
        self.assertTrue(
            ProductionSlotChangeLog.objects.filter(
                slot_reference=execution_slot.pk,
                action=ProductionSlotChangeLog.Action.UPDATED,
            ).exists()
        )

    def test_slot_validation_rejects_overlap_outside_hours_and_downtime(self):
        order = self.create_order()
        validation_unit = WorkUnit.objects.create(name="Validation Unit", type=WorkUnit.UnitType.OTHER)
        base_slot = ProductionSlot(
            order=order,
            work_unit=validation_unit,
            start_datetime=self.make_dt(2, 9),
            end_datetime=self.make_dt(2, 10),
        )
        base_slot._planner_operation = True
        base_slot.save()

        overlapping_slot = ProductionSlot(
            order=order,
            work_unit=validation_unit,
            start_datetime=self.make_dt(2, 9, 30),
            end_datetime=self.make_dt(2, 10, 30),
        )
        with self.assertRaises(ValidationError):
            overlapping_slot.full_clean()

        outside_hours_slot = ProductionSlot(
            order=order,
            work_unit=validation_unit,
            start_datetime=self.make_dt(2, 7),
            end_datetime=self.make_dt(2, 8),
        )
        with self.assertRaises(ValidationError):
            outside_hours_slot.full_clean()

        ResourceDowntime.objects.create(
            work_unit=validation_unit,
            downtime_type=ResourceDowntime.DowntimeType.MANUAL_BLOCK,
            start_datetime=self.make_dt(2, 11),
            end_datetime=self.make_dt(2, 12),
            comment="Blocked window",
        )
        blocked_slot = ProductionSlot(
            order=order,
            work_unit=validation_unit,
            start_datetime=self.make_dt(2, 11, 15),
            end_datetime=self.make_dt(2, 11, 45),
        )
        with self.assertRaises(ValidationError):
            blocked_slot.full_clean()

    def test_slot_validation_enforces_stage_order(self):
        order = self.create_order()
        procurement_stage = ProductionStage.objects.get(
            order_item__order=order,
            stage_type=ProductionStage.StageType.PROCUREMENT,
        )
        execution_stage = ProductionStage.objects.get(
            order_item__order=order,
            stage_type=ProductionStage.StageType.EXECUTION,
        )

        ProductionSlot.objects.filter(order=order).delete()
        ProductionStage.objects.filter(order_item__order=order).update(
            planned_start=None,
            planned_end=None,
            status=ProductionStage.Status.NEW,
        )

        procurement_slot = ProductionSlot(
            order=order,
            stage=procurement_stage,
            work_unit=self.storage,
            start_datetime=self.make_dt(3, 9),
            end_datetime=self.make_dt(3, 11),
        )
        procurement_slot._planner_operation = True
        procurement_slot.save()

        early_execution_slot = ProductionSlot(
            order=order,
            stage=execution_stage,
            machine=self.laser,
            start_datetime=self.make_dt(3, 10),
            end_datetime=self.make_dt(3, 12),
        )
        with self.assertRaises(ValidationError):
            early_execution_slot.full_clean()


class ProductionCalendarViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="production@example.com",
            email="production@example.com",
            password="secret123",
            is_active=True,
        )
        self.user.profile.role = self.user.profile.Role.PRODUCTION
        self.user.profile.save()

        client = Client.objects.create(name="Calendar Client", email="calendar-client@example.com")
        contact = Contact.objects.create(client=client, full_name="Calendar Contact")
        product = Product.objects.create(name="Calendar Product", sku="CAL-001")
        self.machine = Machine.objects.create(name="Calendar Laser", type=Machine.MachineType.LASER)

        with self.captureOnCommitCallbacks(execute=True):
            self.order = Order.objects.create(contact=contact, manager=self.user)
            OrderItem.objects.create(order=self.order, product=product, quantity=1, unit_price=Decimal("50.00"))

        self.slot = self.order.slots.filter(machine=self.machine).first()
        if not self.slot:
            execution_stage = ProductionStage.objects.get(
                order_item__order=self.order,
                stage_type=ProductionStage.StageType.EXECUTION,
            )
            self.slot = ProductionSlot(
                order=self.order,
                stage=execution_stage,
                machine=self.machine,
                start_datetime=self.make_dt(1, 9),
                end_datetime=self.make_dt(1, 10),
            )
            self.slot._planner_operation = True
            self.slot.save()

        ResourceDowntime.objects.create(
            machine=self.machine,
            downtime_type=ResourceDowntime.DowntimeType.MAINTENANCE,
            start_datetime=self.slot.end_datetime + timedelta(hours=1),
            end_datetime=self.slot.end_datetime + timedelta(hours=2),
            comment="Calendar maintenance",
        )

    def make_dt(self, day_offset, hour, minute=0):
        current_date = timezone.localdate() + timedelta(days=day_offset)
        while current_date.weekday() > 4:
            current_date += timedelta(days=1)
        return timezone.make_aware(datetime.combine(current_date, time(hour, minute)))

    def test_calendar_events_include_resource_downtime(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("production_slot_events"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        kinds = {event["kind"] for event in payload}

        self.assertIn("slot", kinds)
        self.assertIn("downtime", kinds)


class ProductionWorkspaceViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="production-workspace@example.com",
            email="production-workspace@example.com",
            password="secret123",
            is_active=True,
        )
        self.user.profile.role = self.user.profile.Role.PRODUCTION
        self.user.profile.save()

        client = Client.objects.create(name="Workspace Client", email="workspace-client@example.com")
        contact = Contact.objects.create(client=client, full_name="Workspace Contact")
        product = Product.objects.create(name="Workspace Product", sku="WS-001")
        self.machine = Machine.objects.create(name="Workspace Laser", type=Machine.MachineType.LASER)

        with self.captureOnCommitCallbacks(execute=True):
            self.order = Order.objects.create(contact=contact, manager=self.user)
            OrderItem.objects.create(order=self.order, product=product, quantity=1, unit_price=Decimal("120.00"))

        self.execution_stage = ProductionStage.objects.get(
            order_item__order=self.order,
            stage_type=ProductionStage.StageType.EXECUTION,
        )
        self.execution_slot = self.execution_stage.slots.first()

    def test_stage_status_update_view_marks_stage_done(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("production_stage_status_update", args=[self.execution_stage.pk]),
            {
                "status": ProductionStage.Status.DONE,
                "next": reverse("production_dashboard"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.execution_stage.refresh_from_db()
        self.assertEqual(self.execution_stage.status, ProductionStage.Status.DONE)
        self.assertIsNotNone(self.execution_stage.completed_at)

    def test_stage_status_update_view_cancels_stage_and_clears_slots(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("production_stage_status_update", args=[self.execution_stage.pk]),
            {
                "status": ProductionStage.Status.CANCELLED,
                "next": reverse("production_dashboard"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.execution_stage.refresh_from_db()
        self.assertEqual(self.execution_stage.status, ProductionStage.Status.CANCELLED)
        self.assertEqual(self.execution_stage.slots.count(), 0)

    def test_free_slot_report_lists_windows_for_selected_machine(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("production_free_slot_report"),
            {
                "resource_kind": "machine",
                "resource_id": self.machine.pk,
                "date_from": timezone.localdate().isoformat(),
                "days": 3,
                "min_hours": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.machine.name)
        self.assertGreater(len(response.context["rows"]), 0)

    def test_overdue_stage_report_lists_overdue_stage(self):
        self.execution_stage.status = ProductionStage.Status.IN_PROGRESS
        self.execution_stage.planned_start = timezone.now() - timedelta(days=2)
        self.execution_stage.planned_end = timezone.now() - timedelta(hours=4)
        self.execution_stage.started_at = timezone.now() - timedelta(days=1)
        self.execution_stage.save(
            update_fields=["status", "planned_start", "planned_end", "started_at", "updated_at"]
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("production_overdue_stage_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.execution_stage.order_item.product.name)
        self.assertGreater(len(response.context["rows"]), 0)
