from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from crm.models import OrderItem
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
    ProductionSlotChangeLog.objects.create(
        slot=instance if instance.pk and action != ProductionSlotChangeLog.Action.DELETED else None,
        slot_reference=instance.pk,
        order=instance.order if getattr(instance, "order_id", None) else None,
        stage=instance.stage if getattr(instance, "stage_id", None) else None,
        action=action,
        source=_slot_history_source(instance),
        snapshot_before=before or {},
        snapshot_after=after or {},
        note=getattr(instance, "_history_note", ""),
        changed_by=getattr(instance, "_changed_by", None),
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
def sync_order_status_after_stage_save(sender, instance, **kwargs):
    sync_order_status_from_production(instance.order, save=True)


@receiver(post_delete, sender=ProductionStage)
def sync_order_status_after_stage_delete(sender, instance, **kwargs):
    sync_order_status_from_production(instance.order, save=True)
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


@receiver(post_save, sender=ProductionSlot)
def sync_after_slot_save(sender, instance, created, **kwargs):
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
    if instance.stage_id:
        sync_stage_schedule_from_slots(instance.stage, save=True)
    sync_order_status_from_production(instance.order, save=True)
    _write_slot_history(
        instance,
        action=ProductionSlotChangeLog.Action.DELETED,
        before=serialize_slot(instance),
        after={},
    )
    if not planner_is_active():
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
