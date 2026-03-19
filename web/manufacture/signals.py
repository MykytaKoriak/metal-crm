from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from crm.models import OrderItem
from crm.services import sync_order_status_from_production

from .models import ProductionSlot, ProductionStage


DEFAULT_STAGE_FLOW = (
    (1, ProductionStage.StageType.INTAKE),
    (2, ProductionStage.StageType.PROCUREMENT),
    (3, ProductionStage.StageType.EXECUTION),
    (4, ProductionStage.StageType.PAINTING),
    (5, ProductionStage.StageType.READY_TO_SHIP),
)


@receiver(post_save, sender=OrderItem)
def create_default_production_stages(sender, instance, created, **kwargs):
    if not created or instance.production_stages.exists():
        return

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


@receiver(post_save, sender=ProductionStage)
def sync_order_status_after_stage_save(sender, instance, **kwargs):
    sync_order_status_from_production(instance.order, save=True)


@receiver(post_delete, sender=ProductionStage)
def sync_order_status_after_stage_delete(sender, instance, **kwargs):
    sync_order_status_from_production(instance.order, save=True)


@receiver(post_save, sender=ProductionSlot)
def sync_order_status_after_slot_save(sender, instance, **kwargs):
    sync_order_status_from_production(instance.order, save=True)


@receiver(post_delete, sender=ProductionSlot)
def sync_order_status_after_slot_delete(sender, instance, **kwargs):
    sync_order_status_from_production(instance.order, save=True)
