from datetime import timedelta
import hashlib

from django.utils import timezone

from core.access import get_user_role
from core.models import TelegramNotification, UserProfile
from crm.models import Order, Task
from manufacture.models import ProductionStage
from manufacture.services import build_orders_in_work_report

from .api import TelegramAPIError, send_message
from .formatters import (
    build_help_message,
    build_home_keyboard,
    build_home_message,
    build_order_comment_message,
    build_order_created_message,
    build_order_deadline_message,
    build_order_status_message,
    build_orders_message,
    build_profile_message,
    build_production_event_message,
    build_task_comment_message,
    build_task_created_message,
    build_task_deadline_message,
    build_tasks_message,
    notification_keyboard,
)


NOTIFICATION_PREFERENCE_MAP = {
    TelegramNotification.Type.TASK_CREATED: "telegram_notify_new_tasks",
    TelegramNotification.Type.TASK_COMMENT: "telegram_notify_comments",
    TelegramNotification.Type.ORDER_CREATED: "telegram_notify_new_orders",
    TelegramNotification.Type.ORDER_COMMENT: "telegram_notify_comments",
    TelegramNotification.Type.TASK_DEADLINE: "telegram_notify_deadlines",
    TelegramNotification.Type.ORDER_DEADLINE: "telegram_notify_deadlines",
    TelegramNotification.Type.TASK_OVERDUE: "telegram_notify_overdue",
    TelegramNotification.Type.ORDER_OVERDUE: "telegram_notify_overdue",
    TelegramNotification.Type.ORDER_STATUS: "telegram_notify_order_updates",
    TelegramNotification.Type.PRODUCTION_EVENT: "telegram_notify_production_events",
}

TRACKED_PRODUCTION_STATUSES = {
    ProductionStage.Status.IN_PROGRESS,
    ProductionStage.Status.BLOCKED,
    ProductionStage.Status.DONE,
    ProductionStage.Status.CANCELLED,
}


def get_profile_for_user(user):
    if not user or not getattr(user, "pk", None):
        return None
    profile = getattr(user, "profile", None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def get_profile_by_chat_id(chat_id):
    return UserProfile.objects.select_related("user").filter(telegram_chat_id=str(chat_id)).first()


def profile_allows_notification(profile, notification_type):
    if not profile or not profile.telegram_chat_id or not profile.telegram_notifications_enabled:
        return False
    field_name = NOTIFICATION_PREFERENCE_MAP.get(notification_type)
    return getattr(profile, field_name, True) if field_name else True


def link_profile_to_chat(code, chat_id, *, username=""):
    normalized_code = (code or "").strip().upper()
    if not normalized_code:
        return None
    profile = UserProfile.objects.select_related("user").filter(telegram_link_code=normalized_code).first()
    if not profile:
        return None

    now = timezone.now()
    UserProfile.objects.filter(telegram_chat_id=str(chat_id)).exclude(pk=profile.pk).update(
        telegram_chat_id="",
        telegram_username="",
        telegram_linked_at=None,
    )

    profile.telegram_chat_id = str(chat_id)
    profile.telegram_username = (username or "").strip()
    profile.telegram_linked_at = now
    profile.rotate_telegram_link_code(save=False)
    profile.save(
        update_fields=[
            "telegram_chat_id",
            "telegram_username",
            "telegram_linked_at",
            "telegram_link_code",
        ]
    )
    return profile


def unlink_profile_by_chat(chat_id):
    profile = get_profile_by_chat_id(chat_id)
    if not profile:
        return None
    profile.telegram_chat_id = ""
    profile.telegram_username = ""
    profile.telegram_linked_at = None
    profile.rotate_telegram_link_code(save=False)
    profile.save(
        update_fields=[
            "telegram_chat_id",
            "telegram_username",
            "telegram_linked_at",
            "telegram_link_code",
        ]
    )
    return profile


def build_home_response(profile):
    return build_home_message(profile), build_home_keyboard()


def build_help_response(profile=None):
    return build_help_message(profile), build_home_keyboard()


def build_profile_response(profile):
    return build_profile_message(profile), build_home_keyboard()


def get_task_queryset_for_profile(profile, *, scope="open"):
    today = timezone.localdate()
    queryset = (
        Task.objects.filter(assigned_to=profile.user)
        .select_related("client", "contact", "order", "assigned_to", "assigned_by")
        .order_by("date", "id")
    )
    if scope == "today":
        queryset = queryset.exclude(status=Task.Status.DONE).filter(date=today)
    elif scope == "overdue":
        queryset = queryset.exclude(status=Task.Status.DONE).filter(date__lt=today)
    else:
        queryset = queryset.exclude(status=Task.Status.DONE)
    return queryset


def get_order_rows_for_profile(profile, *, scope="active"):
    role = get_user_role(profile.user)
    manager_id = profile.user_id if role == UserProfile.Role.SALES_MANAGER else None
    risk_only = scope == "risk"
    if scope == "mine":
        manager_id = profile.user_id
    rows = build_orders_in_work_report(
        manager_id=manager_id,
        risk_only=risk_only,
        user=profile.user,
    )
    if role == UserProfile.Role.PRODUCTION and scope == "mine":
        rows = [
            row for row in rows if row["current_responsible"] and row["current_responsible"].pk == profile.user_id
        ]
    return rows


def build_tasks_response(profile, *, scope="open", page_number=1):
    return build_tasks_message(profile, get_task_queryset_for_profile(profile, scope=scope), scope=scope, page_number=page_number)


def build_orders_response(profile, *, scope="active", page_number=1):
    return build_orders_message(profile, get_order_rows_for_profile(profile, scope=scope), scope=scope, page_number=page_number)


def enqueue_notification(
    *,
    profile,
    notification_type,
    message_text,
    dedupe_key,
    scheduled_for=None,
    payload=None,
    task=None,
    order=None,
    stage=None,
):
    if not profile_allows_notification(profile, notification_type):
        return None, False

    notification, created = TelegramNotification.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "profile": profile,
            "notification_type": notification_type,
            "message_text": message_text,
            "payload": payload or {},
            "task": task,
            "order": order,
            "stage": stage,
            "scheduled_for": scheduled_for or timezone.now(),
        },
    )
    return notification, created


def _content_hash(value):
    return hashlib.sha1((value or "").strip().encode("utf-8")).hexdigest()[:12]


def queue_task_created_notification(task):
    if not task.assigned_to_id:
        return None
    profile = get_profile_for_user(task.assigned_to)
    if not profile:
        return None
    return enqueue_notification(
        profile=profile,
        notification_type=TelegramNotification.Type.TASK_CREATED,
        message_text=build_task_created_message(task),
        dedupe_key=f"task-created:{task.pk}:{task.assigned_to_id}:{timezone.localdate().isoformat()}",
        task=task,
        order=task.order,
    )


def queue_task_comment_notification(task):
    if not task.assigned_to_id or not (task.comment or "").strip():
        return None
    profile = get_profile_for_user(task.assigned_to)
    if not profile:
        return None
    return enqueue_notification(
        profile=profile,
        notification_type=TelegramNotification.Type.TASK_COMMENT,
        message_text=build_task_comment_message(task),
        dedupe_key=f"task-comment:{task.pk}:{task.assigned_to_id}:{_content_hash(task.comment)}",
        task=task,
        order=task.order,
    )


def queue_order_created_notification(order):
    if not order.manager_id:
        return None
    profile = get_profile_for_user(order.manager)
    if not profile:
        return None
    return enqueue_notification(
        profile=profile,
        notification_type=TelegramNotification.Type.ORDER_CREATED,
        message_text=build_order_created_message(order),
        dedupe_key=f"order-created:{order.pk}:{order.manager_id}",
        order=order,
    )


def queue_order_status_notification(order, *, previous_status=None):
    if not order.manager_id or previous_status == order.status:
        return None
    profile = get_profile_for_user(order.manager)
    if not profile:
        return None
    return enqueue_notification(
        profile=profile,
        notification_type=TelegramNotification.Type.ORDER_STATUS,
        message_text=build_order_status_message(order, previous_status=previous_status),
        dedupe_key=f"order-status:{order.pk}:{previous_status}:{order.status}:{timezone.localdate().isoformat()}",
        order=order,
    )


def queue_order_comment_notification(order):
    if not order.manager_id or not (order.comment or "").strip():
        return None
    profile = get_profile_for_user(order.manager)
    if not profile:
        return None
    return enqueue_notification(
        profile=profile,
        notification_type=TelegramNotification.Type.ORDER_COMMENT,
        message_text=build_order_comment_message(order),
        dedupe_key=f"order-comment:{order.pk}:{order.manager_id}:{_content_hash(order.comment)}",
        order=order,
    )


def _production_recipients(stage):
    recipients = []
    if stage.responsible_id:
        recipients.append(stage.responsible)
    if stage.order.manager_id and stage.order.manager_id != stage.responsible_id:
        recipients.append(stage.order.manager)
    return recipients


def queue_production_event_notification(stage, *, previous_status=None):
    if stage.status not in TRACKED_PRODUCTION_STATUSES or previous_status == stage.status:
        return []

    notifications = []
    marker = stage.updated_at.isoformat() if stage.updated_at else timezone.now().isoformat()
    for user in _production_recipients(stage):
        profile = get_profile_for_user(user)
        if not profile:
            continue
        notification, created = enqueue_notification(
            profile=profile,
            notification_type=TelegramNotification.Type.PRODUCTION_EVENT,
            message_text=build_production_event_message(stage, previous_status=previous_status),
            dedupe_key=f"production-event:{stage.pk}:{stage.status}:{user.pk}:{marker}",
            order=stage.order,
            stage=stage,
        )
        if created:
            notifications.append(notification)
    return notifications


def _schedule_task_deadline_notifications(now):
    today = timezone.localdate(now)
    due_soon_limit = today + timedelta(days=1)
    notifications = []
    tasks = (
        Task.objects.exclude(status=Task.Status.DONE)
        .filter(assigned_to__isnull=False)
        .select_related("client", "contact", "order", "assigned_to", "assigned_to__profile")
        .order_by("date", "id")
    )
    for task in tasks:
        profile = get_profile_for_user(task.assigned_to)
        if not profile:
            continue
        if task.date < today:
            notification, created = enqueue_notification(
                profile=profile,
                notification_type=TelegramNotification.Type.TASK_OVERDUE,
                message_text=build_task_deadline_message(task, overdue=True),
                dedupe_key=f"task-overdue:{task.pk}:{today.isoformat()}",
                scheduled_for=now,
                task=task,
                order=task.order,
            )
            if notification and created:
                notifications.append(notification)
        elif task.date <= due_soon_limit:
            notification, created = enqueue_notification(
                profile=profile,
                notification_type=TelegramNotification.Type.TASK_DEADLINE,
                message_text=build_task_deadline_message(task, overdue=False),
                dedupe_key=f"task-deadline:{task.pk}:{task.date.isoformat()}:{today.isoformat()}",
                scheduled_for=now,
                task=task,
                order=task.order,
            )
            if notification and created:
                notifications.append(notification)
    return notifications


def _schedule_order_deadline_notifications(now):
    today = timezone.localdate(now)
    due_soon_limit = today + timedelta(days=2)
    notifications = []
    orders = (
        Order.objects.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])
        .filter(manager__isnull=False, deadline__isnull=False)
        .select_related("contact", "manager", "manager__profile")
        .order_by("deadline", "id")
    )
    for order in orders:
        profile = get_profile_for_user(order.manager)
        if not profile:
            continue
        if order.deadline < today:
            notification, created = enqueue_notification(
                profile=profile,
                notification_type=TelegramNotification.Type.ORDER_OVERDUE,
                message_text=build_order_deadline_message(order, overdue=True),
                dedupe_key=f"order-overdue:{order.pk}:{today.isoformat()}",
                scheduled_for=now,
                order=order,
            )
            if notification and created:
                notifications.append(notification)
        elif order.deadline <= due_soon_limit:
            notification, created = enqueue_notification(
                profile=profile,
                notification_type=TelegramNotification.Type.ORDER_DEADLINE,
                message_text=build_order_deadline_message(order, overdue=False),
                dedupe_key=f"order-deadline:{order.pk}:{order.deadline.isoformat()}:{today.isoformat()}",
                scheduled_for=now,
                order=order,
            )
            if notification and created:
                notifications.append(notification)
    return notifications


def enqueue_deadline_notifications(*, now=None):
    now = now or timezone.now()
    created_notifications = []
    created_notifications.extend(_schedule_task_deadline_notifications(now))
    created_notifications.extend(_schedule_order_deadline_notifications(now))
    return created_notifications


def deliver_notification(notification):
    profile = notification.profile
    if not profile_allows_notification(profile, notification.notification_type):
        notification.status = TelegramNotification.Status.SKIPPED
        notification.error_message = "Telegram-сповіщення вимкнені або профіль не прив’язаний."
        notification.delivery_attempts += 1
        notification.save(update_fields=["status", "error_message", "delivery_attempts"])
        return False

    reply_markup = notification.payload.get("reply_markup") or notification_keyboard(notification)
    try:
        result = send_message(profile.telegram_chat_id, notification.message_text, reply_markup=reply_markup)
    except TelegramAPIError as exc:
        notification.status = TelegramNotification.Status.FAILED
        notification.error_message = str(exc)
        notification.delivery_attempts += 1
        notification.save(update_fields=["status", "error_message", "delivery_attempts"])
        return False

    notification.status = TelegramNotification.Status.SENT
    notification.sent_at = timezone.now()
    notification.delivery_attempts += 1
    notification.error_message = ""
    payload = dict(notification.payload or {})
    payload["telegram_message_id"] = result.get("message_id")
    notification.payload = payload
    notification.save(
        update_fields=[
            "status",
            "sent_at",
            "delivery_attempts",
            "error_message",
            "payload",
        ]
    )
    return True


def deliver_scheduled_notifications(*, now=None, limit=100):
    now = now or timezone.now()
    queryset = (
        TelegramNotification.objects.select_related("profile", "profile__user", "task", "order", "stage")
        .filter(status=TelegramNotification.Status.PENDING, scheduled_for__lte=now)
        .order_by("scheduled_for", "id")[:limit]
    )
    delivered = 0
    for notification in queryset:
        if deliver_notification(notification):
            delivered += 1
    return delivered


def process_notification_queue(*, now=None, limit=100):
    now = now or timezone.now()
    queued = enqueue_deadline_notifications(now=now)
    delivered = deliver_scheduled_notifications(now=now, limit=limit)
    return {
        "queued": len(queued),
        "delivered": delivered,
    }
