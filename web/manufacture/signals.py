from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from core.request_context import get_current_user
from crm.deletion_state import is_order_deleting
from crm.interactions import log_stage_interaction
from crm.models import Order, OrderItem
from crm.services import sync_order_status_from_production

from .models import (
    Machine,
    ProductionSlot,
    ProductionSlotChangeLog,
    ProductionStage,
    ResourceDowntime,
    WorkUnit,
)
from .services import (
    planner_is_active,
    request_replan_open_orders,
    serialize_slot,
    sync_stage_schedule_from_slots,
)


DEFAULT_STAGE_FLOW = (
    (1, ProductionStage.StageType.INTAKE),
    (2, ProductionStage.StageType.PROCUREMENT),
    (3, ProductionStage.StageType.EXECUTION),
    (4, ProductionStage.StageType.PAINTING),
    (5, ProductionStage.StageType.READY_TO_SHIP),
)


def _slot_history_source(instance):
    if getattr(instance, "_history_source", None) == "auto":
        return ProductionSlotChangeLog.Source.AUTO
    if getattr(instance, "_history_source", None) == "manual":
        return ProductionSlotChangeLog.Source.MANUAL
    if instance.planning_mode == ProductionSlot.PlanningMode.AUTO:
        return ProductionSlotChangeLog.Source.AUTO
    if instance.planning_mode == ProductionSlot.PlanningMode.MANUAL or instance.is_locked:
        return ProductionSlotChangeLog.Source.MANUAL
    return ProductionSlotChangeLog.Source.SYSTEM


def _write_slot_history(instance, *, action, before=None, after=None):
    changed_by = getattr(instance, "_changed_by", None)
    if changed_by is None:
        current_user = get_current_user()
        if getattr(current_user, "is_authenticated", False):
            changed_by = current_user
    order = Order.objects.filter(pk=instance.order_id).first() if getattr(instance, "order_id", None) else None
    stage = (
        ProductionStage.objects.filter(pk=instance.stage_id).first() if getattr(instance, "stage_id", None) else None
    )
    if action == ProductionSlotChangeLog.Action.DELETED:
        order = None
        stage = None
    ProductionSlotChangeLog.objects.create(
        slot=instance if instance.pk and action != ProductionSlotChangeLog.Action.DELETED else None,
        slot_reference=instance.pk,
        order=order,
        stage=stage,
        action=action,
        source=_slot_history_source(instance),
        snapshot_before=before or {},
        snapshot_after=after or {},
        note=getattr(instance, "_history_note", ""),
        changed_by=changed_by,
    )


@receiver(post_save, sender=OrderItem)
def create_default_production_stages(sender, instance, created, **kwargs):
    if created and not instance.production_stages.exists():
        ProductionStage.objects.bulk_create(
            [
                ProductionStage(
                    order_item=instance,
                    stage_type=stage_type,
                    sequence=sequence,
                    status=ProductionStage.Status.NEW,
                )
                for sequence, stage_type in DEFAULT_STAGE_FLOW
            ]
        )
    sync_order_status_from_production(instance.order, save=True)
    request_replan_open_orders()


@receiver(post_save, sender=ProductionStage)
def sync_order_status_after_stage_save(sender, instance, created, **kwargs):
    if is_order_deleting(instance.order.pk):
        return
    sync_order_status_from_production(instance.order, save=True)
    log_stage_interaction(
        instance,
        created=created,
        previous=getattr(instance, "_interaction_previous_state", {}),
        actor=get_current_user(),
    )


@receiver(pre_save, sender=ProductionStage)
def store_stage_interaction_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._interaction_previous_state = {}
        return
    instance._interaction_previous_state = (
        sender.objects.filter(pk=instance.pk)
        .values("status", "responsible_id", "planned_start", "planned_end", "comment")
        .first()
        or {}
    )


@receiver(post_delete, sender=ProductionStage)
def sync_order_status_after_stage_delete(sender, instance, **kwargs):
    order_id = OrderItem.objects.filter(pk=instance.order_item_id).values_list("order_id", flat=True).first()
    if is_order_deleting(order_id):
        return
    order = Order.objects.filter(pk=order_id).first() if order_id else None
    should_sync_order = order is not None and OrderItem.objects.filter(order_id=order.pk).exists()
    if should_sync_order:
        sync_order_status_from_production(order, save=True)
        request_replan_open_orders()


@receiver(pre_save, sender=ProductionSlot)
def prepare_slot_history(sender, instance, **kwargs):
    if instance.pk:
        previous = sender.objects.filter(pk=instance.pk).first()
        instance._history_before = serialize_slot(previous) if previous else {}
    else:
        instance._history_before = {}

    if planner_is_active() or getattr(instance, "_planner_operation", False):
        return

    instance.planning_mode = ProductionSlot.PlanningMode.MANUAL
    instance.planning_source = ProductionSlot.PlanningSource.DISPATCHER
    instance.is_locked = True
    instance._history_source = "manual"


@receiver(pre_delete, sender=Order)
def detach_slot_history_order_links(sender, instance, **kwargs):
    ProductionSlotChangeLog.objects.filter(order_id=instance.pk).update(order=None)


@receiver(pre_delete, sender=ProductionStage)
def detach_slot_history_stage_links(sender, instance, **kwargs):
    ProductionSlotChangeLog.objects.filter(stage_id=instance.pk).update(stage=None)


@receiver(pre_delete, sender=ProductionSlot)
def detach_slot_history_slot_links(sender, instance, **kwargs):
    ProductionSlotChangeLog.objects.filter(slot_id=instance.pk).update(slot=None)


@receiver(post_save, sender=ProductionSlot)
def sync_after_slot_save(sender, instance, created, **kwargs):
    if is_order_deleting(instance.order_id):
        return
    if instance.stage_id:
        sync_stage_schedule_from_slots(instance.stage, save=True)
    sync_order_status_from_production(instance.order, save=True)

    before = getattr(instance, "_history_before", {})
    after = serialize_slot(instance)
    if created or before != after:
        _write_slot_history(
            instance,
            action=ProductionSlotChangeLog.Action.CREATED if created else ProductionSlotChangeLog.Action.UPDATED,
            before=before,
            after=after,
        )

    if not planner_is_active() and not getattr(instance, "_planner_operation", False):
        request_replan_open_orders()


@receiver(post_delete, sender=ProductionSlot)
def sync_after_slot_delete(sender, instance, **kwargs):
    if is_order_deleting(instance.order_id):
        return
    stage = ProductionStage.objects.filter(pk=instance.stage_id).first() if instance.stage_id else None
    order = Order.objects.filter(pk=instance.order_id).first() if instance.order_id else None
    if stage is not None:
        sync_stage_schedule_from_slots(stage, save=True)
    should_sync_order = order is not None and OrderItem.objects.filter(order_id=order.pk).exists()
    if should_sync_order:
        sync_order_status_from_production(order, save=True)
    _write_slot_history(
        instance,
        action=ProductionSlotChangeLog.Action.DELETED,
        before=serialize_slot(instance),
        after={},
    )
    if should_sync_order and not planner_is_active():
        request_replan_open_orders()


@receiver(post_save, sender=Machine)
@receiver(post_save, sender=WorkUnit)
@receiver(post_save, sender=ResourceDowntime)
def replan_after_resource_save(sender, instance, **kwargs):
    request_replan_open_orders()


@receiver(post_delete, sender=Machine)
@receiver(post_delete, sender=WorkUnit)
@receiver(post_delete, sender=ResourceDowntime)
def replan_after_resource_delete(sender, instance, **kwargs):
    request_replan_open_orders()
