from django.utils import timezone

from core.request_context import get_current_user

from .models import ClientInteraction


def _current_actor(explicit_user=None):
    if explicit_user is not None and getattr(explicit_user, "is_authenticated", False):
        return explicit_user
    user = get_current_user()
    if getattr(user, "is_authenticated", False):
        return user
    return None


def _user_label(user):
    if not user:
        return "—"
    try:
        profile = getattr(user, "profile", None)
        display_name = getattr(profile, "display_name", "")
    except Exception:
        display_name = ""
    return display_name or user.get_full_name() or user.email or user.username or str(user.pk)


def _format_date(value):
    if not value:
        return "—"
    if hasattr(value, "date") and hasattr(value, "hour"):
        value = timezone.localtime(value)
        return value.strftime("%d.%m.%Y %H:%M")
    return value.strftime("%d.%m.%Y")


def create_client_interaction(
    *,
    client,
    title,
    description="",
    event_type=ClientInteraction.EventType.SYSTEM,
    source=ClientInteraction.Source.SYSTEM,
    contact=None,
    order=None,
    task=None,
    created_by=None,
    event_at=None,
    payload=None,
):
    interaction = ClientInteraction(
        client=client,
        contact=contact,
        order=order,
        task=task,
        title=title,
        description=description,
        event_type=event_type,
        source=source,
        created_by=_current_actor(created_by),
        event_at=event_at or timezone.now(),
        payload=payload or {},
    )
    interaction.full_clean()
    interaction.save()
    return interaction


def log_contact_interaction(contact, *, created, previous=None, actor=None):
    if created:
        create_client_interaction(
            client=contact.client,
            contact=contact,
            title=f"Створено контакт: {contact.full_name}",
            description="\n".join(
                part
                for part in [
                    f"Посада: {contact.position}" if contact.position else "",
                    f"Телефон: {contact.phone}" if contact.phone else "",
                    f"Електронна пошта: {contact.email}" if contact.email else "",
                ]
                if part
            ),
            event_type=ClientInteraction.EventType.CONTACT,
            source=ClientInteraction.Source.AUTO,
            created_by=actor,
            event_at=contact.created_at,
            payload={"action": "created"},
        )
        return

    previous = previous or {}
    changed = []
    for field_name, label in (
        ("full_name", "ПІБ"),
        ("position", "Посада"),
        ("phone", "Телефон"),
        ("email", "Електронна пошта"),
        ("source", "Джерело"),
        ("notes", "Нотатки"),
    ):
        old_value = previous.get(field_name) or ""
        new_value = getattr(contact, field_name) or ""
        if old_value != new_value:
            changed.append(f"{label}: {old_value or '—'} -> {new_value or '—'}")

    if not changed:
        return

    create_client_interaction(
        client=contact.client,
        contact=contact,
        title=f"Оновлено контакт: {contact.full_name}",
        description="\n".join(changed),
        event_type=ClientInteraction.EventType.CONTACT,
        source=ClientInteraction.Source.AUTO,
        created_by=actor,
        payload={"action": "updated", "changed_fields": [item.split(":", 1)[0] for item in changed]},
    )


def log_order_interaction(order, *, created, previous=None, actor=None):
    if created:
        create_client_interaction(
            client=order.contact.client,
            contact=order.contact,
            order=order,
            title=f"Створено замовлення: {order.title or f'Замовлення #{order.pk}'}",
            description="\n".join(
                [
                    f"Статус: {order.get_status_display()}",
                    f"Дедлайн: {_format_date(order.deadline) if order.deadline else '—'}",
                    f"Менеджер: {_user_label(order.manager)}",
                ]
            ),
            event_type=ClientInteraction.EventType.ORDER,
            source=ClientInteraction.Source.AUTO,
            created_by=actor,
            event_at=order.created_at,
            payload={"action": "created"},
        )
        return

    previous = previous or {}
    changed = []
    changed_fields = []
    if previous.get("status") != order.status:
        changed_fields.append("status")
        changed.append(f"Статус: {order.get_status_display()}")
    if previous.get("deadline") != order.deadline:
        changed_fields.append("deadline")
        changed.append(f"Дедлайн: {_format_date(previous.get('deadline'))} -> {_format_date(order.deadline)}")
    if previous.get("manager_id") != order.manager_id:
        changed_fields.append("manager")
        changed.append(f"Менеджер: {_user_label(order.manager)}")
    if (previous.get("comment") or "") != (order.comment or ""):
        changed_fields.append("comment")
        changed.append("Оновлено коментар до замовлення.")

    if not changed_fields:
        return

    create_client_interaction(
        client=order.contact.client,
        contact=order.contact,
        order=order,
        title=f"Оновлено замовлення: {order.title or f'Замовлення #{order.pk}'}",
        description="\n".join(changed),
        event_type=ClientInteraction.EventType.COMMENT
        if changed_fields == ["comment"]
        else ClientInteraction.EventType.ORDER,
        source=ClientInteraction.Source.AUTO,
        created_by=actor,
        payload={"action": "updated", "changed_fields": changed_fields},
    )


def log_task_interaction(task, *, created, previous=None, actor=None):
    contact = task.contact if task.contact_id and task.contact.client_id == task.client_id else None
    order = task.order if task.order_id and task.order.contact.client_id == task.client_id else None

    if created:
        lines = [
            f"Статус: {task.get_status_display()}",
            f"Дедлайн: {_format_date(task.date)}",
            f"Пріоритет: {task.get_priority_display()}",
            f"Відповідальний: {_user_label(task.assigned_to)}",
        ]
        if task.description:
            lines.append(f"Опис: {task.description}")
        create_client_interaction(
            client=task.client,
            contact=contact,
            order=order,
            task=task,
            title=f"Створено задачу: {task.title}",
            description="\n".join(lines),
            event_type=ClientInteraction.EventType.TASK,
            source=ClientInteraction.Source.AUTO,
            created_by=actor,
            event_at=task.created_at,
            payload={"action": "created"},
        )
        return

    previous = previous or {}
    changed = []
    changed_fields = []
    if (previous.get("title") or "") != (task.title or ""):
        changed_fields.append("title")
        changed.append(f"Назва: {previous.get('title') or '—'} -> {task.title or '—'}")
    if (previous.get("description") or "") != (task.description or ""):
        changed_fields.append("description")
        changed.append("Оновлено опис задачі.")
    if previous.get("priority") != task.priority:
        changed_fields.append("priority")
        changed.append(f"Пріоритет: {task.get_priority_display()}")
    if previous.get("status") != task.status:
        changed_fields.append("status")
        changed.append(f"Статус: {task.get_status_display()}")
    if previous.get("date") != task.date:
        changed_fields.append("date")
        changed.append(f"Дедлайн: {_format_date(previous.get('date'))} -> {_format_date(task.date)}")
    if previous.get("assigned_to_id") != task.assigned_to_id:
        changed_fields.append("assigned_to")
        changed.append(f"Відповідальний: {_user_label(task.assigned_to)}")
    if previous.get("assigned_by_id") != task.assigned_by_id:
        changed_fields.append("assigned_by")
        changed.append(f"Ким створено: {_user_label(task.assigned_by)}")
    if (previous.get("comment") or "") != (task.comment or ""):
        changed_fields.append("comment")
        changed.append("Оновлено коментар до задачі.")

    if not changed_fields:
        return

    create_client_interaction(
        client=task.client,
        contact=contact,
        order=order,
        task=task,
        title=f"Оновлено задачу: {task.title}",
        description="\n".join(changed),
        event_type=ClientInteraction.EventType.COMMENT
        if changed_fields == ["comment"]
        else ClientInteraction.EventType.TASK,
        source=ClientInteraction.Source.AUTO,
        created_by=actor,
        payload={"action": "updated", "changed_fields": changed_fields},
    )


def log_stage_interaction(stage, *, created, previous=None, actor=None):
    order = stage.order
    client = order.contact.client
    if created:
        create_client_interaction(
            client=client,
            contact=order.contact,
            order=order,
            title=f"Додано виробничий етап: {stage.get_stage_type_display()}",
            description=f"Позиція: {stage.order_item.product.name}",
            event_type=ClientInteraction.EventType.PRODUCTION,
            source=ClientInteraction.Source.SYSTEM,
            created_by=actor,
            event_at=stage.created_at,
            payload={"action": "created", "stage_type": stage.stage_type},
        )
        return

    previous = previous or {}
    changed = []
    changed_fields = []
    if previous.get("status") != stage.status:
        changed_fields.append("status")
        changed.append(f"Статус: {stage.get_status_display()}")
    if previous.get("responsible_id") != stage.responsible_id:
        changed_fields.append("responsible")
        changed.append(f"Відповідальний: {_user_label(stage.responsible)}")
    if previous.get("planned_start") != stage.planned_start or previous.get("planned_end") != stage.planned_end:
        changed_fields.append("schedule")
        changed.append(
            f"План: {_format_date(stage.planned_start) if stage.planned_start else '—'} -> "
            f"{_format_date(stage.planned_end) if stage.planned_end else '—'}"
        )
    if (previous.get("comment") or "") != (stage.comment or ""):
        changed_fields.append("comment")
        changed.append("Оновлено коментар виробничого етапу.")

    if not changed_fields:
        return

    create_client_interaction(
        client=client,
        contact=order.contact,
        order=order,
        title=f"Оновлено виробничий етап: {stage.get_stage_type_display()}",
        description="\n".join(changed),
        event_type=ClientInteraction.EventType.PRODUCTION,
        source=ClientInteraction.Source.SYSTEM,
        created_by=actor,
        payload={"action": "updated", "changed_fields": changed_fields, "stage_type": stage.stage_type},
    )
