from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from crm.models import Order, Task
from manufacture.models import ProductionStage

from .telegram.services import (
    queue_order_comment_notification,
    queue_order_created_notification,
    queue_order_status_notification,
    queue_production_event_notification,
    queue_task_comment_notification,
    queue_task_created_notification,
)


@receiver(pre_save, sender=Task)
def store_task_telegram_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._telegram_previous_assigned_to_id = None
        instance._telegram_previous_comment = ""
        return
    previous = sender.objects.filter(pk=instance.pk).values("assigned_to_id", "comment").first() or {}
    instance._telegram_previous_assigned_to_id = previous.get("assigned_to_id")
    instance._telegram_previous_comment = previous.get("comment") or ""


@receiver(post_save, sender=Task)
def queue_task_notifications(sender, instance, created, **kwargs):
    previous_assigned_to_id = getattr(instance, "_telegram_previous_assigned_to_id", None)
    previous_comment = getattr(instance, "_telegram_previous_comment", "")

    def _load_task():
        return (
            Task.objects.select_related("client", "contact", "order", "assigned_to", "assigned_to__profile")
            .filter(pk=instance.pk)
            .first()
        )

    def _queue_created():
        if not instance.assigned_to_id:
            return
        if not created and previous_assigned_to_id == instance.assigned_to_id:
            return
        task = _load_task()
        if task:
            queue_task_created_notification(task)

    def _queue_comment():
        if created:
            return
        if (previous_comment or "").strip() == (instance.comment or "").strip():
            return
        if not instance.assigned_to_id or not (instance.comment or "").strip():
            return
        task = _load_task()
        if task:
            queue_task_comment_notification(task)

    transaction.on_commit(_queue_created)
    transaction.on_commit(_queue_comment)


@receiver(pre_save, sender=Order)
def store_order_telegram_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._telegram_previous_status = None
        instance._telegram_previous_comment = ""
        return
    previous = sender.objects.filter(pk=instance.pk).values("status", "comment").first() or {}
    instance._telegram_previous_status = previous.get("status")
    instance._telegram_previous_comment = previous.get("comment") or ""


@receiver(post_save, sender=Order)
def queue_order_notifications(sender, instance, created, **kwargs):
    previous_status = getattr(instance, "_telegram_previous_status", None)
    previous_comment = getattr(instance, "_telegram_previous_comment", "")

    def _load_order():
        return Order.objects.select_related("contact", "manager", "manager__profile").filter(pk=instance.pk).first()

    def _queue_created():
        if not created:
            return
        order = _load_order()
        if order:
            queue_order_created_notification(order)

    def _queue_status():
        if created or previous_status == instance.status:
            return
        order = _load_order()
        if order:
            queue_order_status_notification(order, previous_status=previous_status)

    def _queue_comment():
        if created:
            return
        if (previous_comment or "").strip() == (instance.comment or "").strip():
            return
        if not (instance.comment or "").strip():
            return
        order = _load_order()
        if order:
            queue_order_comment_notification(order)

    transaction.on_commit(_queue_created)
    transaction.on_commit(_queue_status)
    transaction.on_commit(_queue_comment)


@receiver(pre_save, sender=ProductionStage)
def store_stage_telegram_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._telegram_previous_status = None
        return
    previous = sender.objects.filter(pk=instance.pk).values("status").first() or {}
    instance._telegram_previous_status = previous.get("status")


@receiver(post_save, sender=ProductionStage)
def queue_stage_notifications(sender, instance, created, **kwargs):
    previous_status = getattr(instance, "_telegram_previous_status", None)
    if created or previous_status == instance.status:
        return

    def _enqueue():
        stage = (
            ProductionStage.objects.select_related(
                "order_item",
                "order_item__order",
                "order_item__order__contact",
                "order_item__order__manager",
                "order_item__order__manager__profile",
                "responsible",
                "responsible__profile",
            )
            .filter(pk=instance.pk)
            .first()
        )
        if stage:
            queue_production_event_notification(stage, previous_status=previous_status)

    transaction.on_commit(_enqueue)
