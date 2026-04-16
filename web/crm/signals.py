from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from core.request_context import get_current_user
from crm.inventory import refresh_order_item_materials, reconcile_order_reservations, release_order_item_reservations
from manufacture.services import request_replan_open_orders

from .deletion_state import is_order_deleting, mark_order_deleting, unmark_order_deleting
from .interactions import log_contact_interaction, log_order_interaction, log_task_interaction
from .models import Contact, Order, OrderItem, ProductBOM, Task
from .services import sync_order_status_from_production, sync_order_title


@receiver(pre_save, sender=Contact)
def store_contact_interaction_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._interaction_previous_state = {}
        return
    instance._interaction_previous_state = (
        sender.objects.filter(pk=instance.pk)
        .values("full_name", "position", "phone", "email", "source", "notes")
        .first()
        or {}
    )


@receiver(post_save, sender=Contact)
def log_contact_history(sender, instance, created, **kwargs):
    log_contact_interaction(
        instance,
        created=created,
        previous=getattr(instance, "_interaction_previous_state", {}),
        actor=get_current_user(),
    )


@receiver(pre_save, sender=Order)
def store_order_interaction_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._interaction_previous_state = {}
        return
    instance._interaction_previous_state = (
        sender.objects.filter(pk=instance.pk)
        .values("status", "deadline", "manager_id", "comment")
        .first()
        or {}
    )


@receiver(post_save, sender=Order)
def request_replan_after_order_save(sender, instance, created, **kwargs):
    log_order_interaction(
        instance,
        created=created,
        previous=getattr(instance, "_interaction_previous_state", {}),
        actor=get_current_user(),
    )
    reconcile_order_reservations(instance, created_by=get_current_user())
    request_replan_open_orders()


@receiver(pre_delete, sender=Order)
def mark_deleting_order(sender, instance, **kwargs):
    mark_order_deleting(instance.pk)


@receiver(post_delete, sender=Order)
def unmark_deleted_order(sender, instance, **kwargs):
    unmark_order_deleting(instance.pk)


@receiver(post_save, sender=OrderItem)
def sync_order_after_item_save(sender, instance, **kwargs):
    if is_order_deleting(instance.order_id):
        return
    if not Order.objects.filter(pk=instance.order_id).exists():
        return
    refresh_order_item_materials(instance, save=True)
    sync_order_title(instance.order, save=True)
    sync_order_status_from_production(instance.order, save=True)
    reconcile_order_reservations(instance.order, created_by=get_current_user())
    request_replan_open_orders()


@receiver(pre_delete, sender=OrderItem)
def release_inventory_before_order_item_delete(sender, instance, **kwargs):
    if not instance.pk:
        return
    if not Order.objects.filter(pk=instance.order_id).exists():
        return
    release_order_item_reservations(instance, created_by=get_current_user())


@receiver(post_delete, sender=OrderItem)
def sync_order_after_item_delete(sender, instance, **kwargs):
    if is_order_deleting(instance.order_id):
        return
    if not Order.objects.filter(pk=instance.order_id).exists():
        return
    sync_order_title(instance.order, save=True)
    sync_order_status_from_production(instance.order, save=True)
    reconcile_order_reservations(instance.order, created_by=get_current_user())
    request_replan_open_orders()


@receiver(post_save, sender=ProductBOM)
def sync_order_items_after_bom_save(sender, instance, **kwargs):
    order_items = list(
        OrderItem.objects.filter(product=instance.product).select_related("order", "product")
    )
    touched_orders = set()
    for order_item in order_items:
        refresh_order_item_materials(order_item, save=True)
        touched_orders.add(order_item.order)
    for order in touched_orders:
        reconcile_order_reservations(order, created_by=get_current_user())


@receiver(post_delete, sender=ProductBOM)
def sync_order_items_after_bom_delete(sender, instance, **kwargs):
    order_items = list(
        OrderItem.objects.filter(product=instance.product).select_related("order", "product")
    )
    touched_orders = set()
    for order_item in order_items:
        refresh_order_item_materials(order_item, save=True)
        touched_orders.add(order_item.order)
    for order in touched_orders:
        reconcile_order_reservations(order, created_by=get_current_user())


@receiver(pre_save, sender=Task)
def store_task_interaction_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._interaction_previous_state = {}
        return
    instance._interaction_previous_state = (
        sender.objects.filter(pk=instance.pk)
        .values("title", "description", "priority", "status", "date", "assigned_to_id", "assigned_by_id", "comment")
        .first()
        or {}
    )


@receiver(post_save, sender=Task)
def log_task_history(sender, instance, created, **kwargs):
    log_task_interaction(
        instance,
        created=created,
        previous=getattr(instance, "_interaction_previous_state", {}),
        actor=get_current_user(),
    )
