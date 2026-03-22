from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from core.audit import serialize_for_audit, write_audit_log
from core.models import ChangeAuditLog
from crm.models import Order, Task
from manufacture.models import ProductionSlot, ProductionStage


AUDITED_MODELS = (Order, Task, ProductionStage, ProductionSlot)


def _prepare_previous_snapshot(sender, instance):
    if instance.pk:
        previous = sender.objects.filter(pk=instance.pk).first()
        instance._audit_before = serialize_for_audit(previous) if previous else {}
    else:
        instance._audit_before = {}


@receiver(pre_save, sender=Order)
@receiver(pre_save, sender=Task)
@receiver(pre_save, sender=ProductionStage)
@receiver(pre_save, sender=ProductionSlot)
def prepare_audit_snapshot(sender, instance, **kwargs):
    _prepare_previous_snapshot(sender, instance)


@receiver(post_save, sender=Order)
@receiver(post_save, sender=Task)
@receiver(post_save, sender=ProductionStage)
@receiver(post_save, sender=ProductionSlot)
def write_audit_snapshot(sender, instance, created, **kwargs):
    before = getattr(instance, "_audit_before", {})
    after = serialize_for_audit(instance)
    write_audit_log(
        instance,
        action=ChangeAuditLog.Action.CREATED if created else ChangeAuditLog.Action.UPDATED,
        before=before,
        after=after,
    )


@receiver(post_delete, sender=Order)
@receiver(post_delete, sender=Task)
@receiver(post_delete, sender=ProductionStage)
@receiver(post_delete, sender=ProductionSlot)
def write_audit_delete(sender, instance, **kwargs):
    write_audit_log(
        instance,
        action=ChangeAuditLog.Action.DELETED,
        before=serialize_for_audit(instance),
        after={},
    )


@receiver(pre_delete, sender=Order)
def detach_order_audit_links(sender, instance, **kwargs):
    ChangeAuditLog.objects.filter(order_id=instance.pk).update(order=None)


@receiver(pre_delete, sender=Task)
def detach_task_audit_links(sender, instance, **kwargs):
    ChangeAuditLog.objects.filter(task_id=instance.pk).update(task=None)


@receiver(pre_delete, sender=ProductionStage)
def detach_stage_audit_links(sender, instance, **kwargs):
    ChangeAuditLog.objects.filter(stage_id=instance.pk).update(stage=None)


@receiver(pre_delete, sender=ProductionSlot)
def detach_slot_audit_links(sender, instance, **kwargs):
    ChangeAuditLog.objects.filter(slot_id=instance.pk).update(slot=None)
