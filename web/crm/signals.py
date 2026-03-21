from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from manufacture.services import request_replan_open_orders

from .models import Order, OrderItem
from .services import sync_order_status_from_production, sync_order_title


@receiver(post_save, sender=Order)
def request_replan_after_order_save(sender, instance, **kwargs):
    request_replan_open_orders()


@receiver(post_save, sender=OrderItem)
def sync_order_after_item_save(sender, instance, **kwargs):
    sync_order_title(instance.order, save=True)
    sync_order_status_from_production(instance.order, save=True)
    request_replan_open_orders()


@receiver(post_delete, sender=OrderItem)
def sync_order_after_item_delete(sender, instance, **kwargs):
    sync_order_title(instance.order, save=True)
    sync_order_status_from_production(instance.order, save=True)
    request_replan_open_orders()
