from decimal import Decimal

from django.utils import timezone

from core.models import ChangeAuditLog
from core.request_context import get_current_user
from crm.models import Order, OrderItem, Task
from manufacture.models import ProductionSlot, ProductionStage


AUDITED_FIELDS = {
    Order: (
        "status",
        "deadline",
        "manager_id",
        "priority",
        "comment",
        "delivery_method",
        "shipping_address",
        "recipient",
        "recipient_phone",
        "tracking_number",
        "payment_type",
        "payment_terms",
        "payment_amount",
    ),
    Task: (
        "title",
        "status",
        "date",
        "assigned_by_id",
        "assigned_to_id",
        "client_id",
        "contact_id",
        "order_id",
        "comment",
    ),
    ProductionStage: (
        "status",
        "sequence",
        "responsible_id",
        "planned_start",
        "planned_end",
        "started_at",
        "completed_at",
        "comment",
    ),
    ProductionSlot: (
        "order_id",
        "stage_id",
        "slot_type",
        "operation_type",
        "machine_id",
        "work_unit_id",
        "start_datetime",
        "end_datetime",
        "planning_mode",
        "planning_source",
        "is_locked",
        "purpose",
        "comment",
        "dispatcher_comment",
    ),
}

ENTITY_TYPES = {
    Order: ChangeAuditLog.EntityType.ORDER,
    Task: ChangeAuditLog.EntityType.TASK,
    ProductionStage: ChangeAuditLog.EntityType.PRODUCTION_STAGE,
    ProductionSlot: ChangeAuditLog.EntityType.PRODUCTION_SLOT,
}


def _normalize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def safe_object_label(instance):
    try:
        return str(instance)
    except Exception:
        return f"{type(instance).__name__} #{getattr(instance, 'pk', 'unknown')}"


def serialize_for_audit(instance):
    return {
        field_name: _normalize_value(getattr(instance, field_name))
        for field_name in AUDITED_FIELDS[type(instance)]
    }


def resolve_audit_actor(instance):
    explicit_user = getattr(instance, "_changed_by", None)
    if explicit_user is not None:
        return explicit_user
    current_user = get_current_user()
    if getattr(current_user, "is_authenticated", False):
        return current_user
    return None


def build_audit_payload(instance):
    payload = {
        "entity_type": ENTITY_TYPES[type(instance)],
        "object_id": instance.pk,
        "object_label": safe_object_label(instance),
        "changed_by": resolve_audit_actor(instance),
        "note": getattr(instance, "_audit_note", ""),
    }
    if isinstance(instance, Order):
        payload["order"] = instance
    elif isinstance(instance, Task):
        payload["task"] = instance
        payload["order"] = Order.objects.filter(pk=instance.order_id).first() if instance.order_id else None
    elif isinstance(instance, ProductionStage):
        order_id = OrderItem.objects.filter(pk=instance.order_item_id).values_list("order_id", flat=True).first()
        payload["stage"] = instance
        payload["order"] = Order.objects.filter(pk=order_id).first() if order_id else None
    elif isinstance(instance, ProductionSlot):
        payload["slot"] = instance if getattr(instance, "pk", None) else None
        payload["order"] = Order.objects.filter(pk=instance.order_id).first() if instance.order_id else None
        payload["stage"] = ProductionStage.objects.filter(pk=instance.stage_id).first() if instance.stage_id else None
    return payload


def write_audit_log(instance, *, action, before=None, after=None):
    before = before or {}
    after = after or {}
    changed_fields = sorted(
        {
            field_name
            for field_name in set(before.keys()) | set(after.keys())
            if before.get(field_name) != after.get(field_name)
        }
    )
    if action == ChangeAuditLog.Action.UPDATED and not changed_fields:
        return None

    payload = build_audit_payload(instance)
    if action == ChangeAuditLog.Action.DELETED:
        payload.update(
            {
                "order": None,
                "task": None,
                "stage": None,
                "slot": None,
            }
        )
    payload.update(
        {
            "action": action,
            "changed_fields": changed_fields,
            "snapshot_before": before,
            "snapshot_after": after,
        }
    )
    return ChangeAuditLog.objects.create(**payload)
