from django.conf import settings
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone

from core.access import get_user_role
from crm.models import Order, Task
from manufacture.models import ProductionStage
from manufacture.services import build_order_row, build_orders_in_work_report


TASK_PAGE_SIZE = 5
ORDER_PAGE_SIZE = 5


def build_absolute_url(path: str) -> str:
    base_url = (getattr(settings, "APP_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}{path}"


def _build_url_button(text: str, path: str):
    url = build_absolute_url(path)
    if not url:
        return None
    return {"text": text, "url": url}


def _format_deadline(date_value):
    if not date_value:
        return "без дедлайну"
    return date_value.strftime("%d.%m.%Y")


def _truncate_comment(value, limit=200):
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _task_context_line(task):
    parts = []
    if getattr(task, "client", None):
        parts.append(f"Клієнт: {task.client.name}")
    if getattr(task, "order_id", None):
        title = task.order.title or f"Замовлення #{task.order_id}"
        parts.append(title)
    return " | ".join(parts)


def _order_context_line(row):
    order = row["order"]
    manager_name = order.manager.profile.display_name if getattr(order.manager, "profile", None) else ""
    context = [f"Контакт: {order.contact.full_name}"]
    if manager_name:
        context.append(f"Менеджер: {manager_name}")
    if row["current_stage"]:
        context.append(f"Етап: {row['current_stage'].get_stage_type_display()}")
    if row["current_resource_label"]:
        context.append(row["current_resource_label"])
    return " | ".join(context)


def build_home_message(profile) -> str:
    account_line = "прив’язано" if profile.telegram_is_linked else "не прив’язано"
    return "\n".join(
        [
            "Telegram-бот CRM",
            f"Користувач: {profile.display_name}",
            f"Статус: {account_line}",
            "",
            "Команди:",
            "/link КОД",
            "/unlink",
            "/me",
            "/help",
            "/tasks",
            "/orders",
        ]
    )


def build_home_keyboard():
    keyboard = [
        [
            {"text": "Задачі", "callback_data": "tasks:open:1"},
            {"text": "Замовлення", "callback_data": "orders:active:1"},
        ],
        [
            {"text": "Профіль", "callback_data": "profile"},
            {"text": "Допомога", "callback_data": "help"},
        ],
    ]
    account_button = _build_url_button("Відкрити акаунт", reverse("my_account"))
    if account_button:
        keyboard.append([account_button])
    return {"inline_keyboard": keyboard}


def build_help_message(profile=None) -> str:
    lines = [
        "Довідка Telegram-бота CRM",
        "",
        "/link КОД - прив’язати цей чат до акаунта CRM",
        "/unlink - від’єднати цей чат",
        "/me - показати короткий профіль CRM",
        "/tasks - відкрити ваш список задач",
        "/orders - відкрити активні замовлення",
        "/help - показати цю довідку",
    ]
    if profile:
        lines.extend(
            [
                "",
                f"Прив’язаний користувач: {profile.display_name}",
                f"Роль: {profile.get_role_display()}",
            ]
        )
    return "\n".join(lines)


def build_profile_message(profile) -> str:
    today = timezone.localdate()
    open_tasks = Task.objects.filter(assigned_to=profile.user).exclude(status=Task.Status.DONE).count()
    overdue_tasks = (
        Task.objects.filter(assigned_to=profile.user)
        .exclude(status=Task.Status.DONE)
        .filter(date__lt=today)
        .count()
    )
    order_rows = build_orders_in_work_report(user=profile.user)
    my_order_rows = [row for row in order_rows if row["order"].manager_id == profile.user_id]
    at_risk_rows = [row for row in my_order_rows if row["risk_level"] in {"high", "critical"}]
    lines = [
        "Профіль CRM",
        f"Користувач: {profile.display_name}",
        f"Роль: {profile.get_role_display()}",
        f"Telegram: {'прив’язано' if profile.telegram_is_linked else 'не прив’язано'}",
        f"Сповіщення: {'увімкнено' if profile.telegram_notifications_enabled else 'вимкнено'}",
        "",
        f"Відкриті задачі: {open_tasks}",
        f"Прострочені задачі: {overdue_tasks}",
        f"Мої активні замовлення: {len(my_order_rows)}",
        f"Мої ризикові замовлення: {len(at_risk_rows)}",
    ]
    if profile.telegram_linked_at:
        lines.insert(4, f"Прив’язано: {timezone.localtime(profile.telegram_linked_at).strftime('%d.%m.%Y %H:%M')}")
    return "\n".join(lines)


def build_tasks_message(profile, queryset, *, scope="open", page_number=1):
    paginator = Paginator(queryset, TASK_PAGE_SIZE)
    page_number = max(1, min(page_number, paginator.num_pages or 1))
    page = paginator.get_page(page_number)

    scope_label = {
        "open": "Відкриті задачі",
        "today": "На сьогодні",
        "overdue": "Прострочені",
    }.get(scope, "Задачі")

    lines = [f"{scope_label} ({page.number}/{max(paginator.num_pages, 1)})", ""]
    if not page.object_list:
        lines.append("Задач не знайдено.")
    else:
        for index, task in enumerate(page.object_list, start=1 + (page.number - 1) * TASK_PAGE_SIZE):
            lines.append(
                f"{index}. {_format_deadline(task.date)} | {task.get_status_display()} | {task.title}"
            )
            context_line = _task_context_line(task)
            if context_line:
                lines.append(f"   {context_line}")
            lines.append(f"   Пріоритет: {task.get_priority_display()}")
            if getattr(task, "description", ""):
                lines.append(f"   {_truncate_comment(task.description, 140)}")
            if task.comment:
                lines.append(f"   Коментар: {_truncate_comment(task.comment, 140)}")
            lines.append("")

    keyboard = [
        [
            {"text": "Відкриті", "callback_data": "tasks:open:1"},
            {"text": "Сьогодні", "callback_data": "tasks:today:1"},
            {"text": "Прострочені", "callback_data": "tasks:overdue:1"},
        ]
    ]
    nav_row = []
    if page.has_previous():
        nav_row.append({"text": "Назад", "callback_data": f"tasks:{scope}:{page.previous_page_number()}"})
    if page.has_next():
        nav_row.append({"text": "Далі", "callback_data": f"tasks:{scope}:{page.next_page_number()}"})
    if nav_row:
        keyboard.append(nav_row)

    tasks_button = _build_url_button("Відкрити дошку задач", reverse("crm_tasks_kanban"))
    if tasks_button:
        keyboard.append([tasks_button])
    keyboard.append([{"text": "Замовлення", "callback_data": "orders:active:1"}])
    return "\n".join(lines).strip(), {"inline_keyboard": keyboard}


def build_orders_message(profile, order_rows, *, scope="active", page_number=1):
    paginator = Paginator(order_rows, ORDER_PAGE_SIZE)
    page_number = max(1, min(page_number, paginator.num_pages or 1))
    page = paginator.get_page(page_number)

    scope_label = {
        "active": "Замовлення в роботі",
        "risk": "Замовлення з ризиком",
        "mine": "Мої замовлення",
    }.get(scope, "Замовлення")

    lines = [f"{scope_label} ({page.number}/{max(paginator.num_pages, 1)})", ""]
    if not page.object_list:
        lines.append("Замовлень не знайдено.")
    else:
        for index, row in enumerate(page.object_list, start=1 + (page.number - 1) * ORDER_PAGE_SIZE):
            order = row["order"]
            lines.append(
                f"{index}. {_format_deadline(order.deadline)} | {order.get_status_display()} | {order.title or f'Замовлення #{order.pk}'}"
            )
            lines.append(f"   {_order_context_line(row)}")
            lines.append(
                f"   Готовність: {row['progress_percent']}% | Ризик: {row['risk_label']}"
            )
            if row["risk_reasons"]:
                lines.append(f"   {row['risk_reasons'][0]}")
            lines.append("")

    keyboard = [
        [
            {"text": "Активні", "callback_data": "orders:active:1"},
            {"text": "З ризиком", "callback_data": "orders:risk:1"},
        ]
    ]
    if get_user_role(profile.user) == profile.Role.SALES_MANAGER:
        keyboard[0].append({"text": "Мої", "callback_data": "orders:mine:1"})

    nav_row = []
    if page.has_previous():
        nav_row.append({"text": "Назад", "callback_data": f"orders:{scope}:{page.previous_page_number()}"})
    if page.has_next():
        nav_row.append({"text": "Далі", "callback_data": f"orders:{scope}:{page.next_page_number()}"})
    if nav_row:
        keyboard.append(nav_row)

    orders_button = _build_url_button("Відкрити замовлення", reverse("crm_orders"))
    if orders_button:
        keyboard.append([orders_button])
    report_button = _build_url_button("Звіт по замовленнях", reverse("production_orders_in_work_report"))
    if report_button:
        keyboard.append([report_button])
    keyboard.append([{"text": "Задачі", "callback_data": "tasks:open:1"}])
    return "\n".join(lines).strip(), {"inline_keyboard": keyboard}


def build_task_created_message(task):
    lines = [
        "Нова задача",
        task.title,
        f"Дедлайн: {_format_deadline(task.date)}",
        f"Пріоритет: {task.get_priority_display()}",
        f"Статус: {task.get_status_display()}",
    ]
    context_line = _task_context_line(task)
    if context_line:
        lines.append(context_line)
    if getattr(task, "description", ""):
        lines.append(f"Опис: {_truncate_comment(task.description)}")
    return "\n".join(lines)


def build_task_comment_message(task):
    lines = [
        "Оновлено коментар до задачі",
        task.title,
        f"Дедлайн: {_format_deadline(task.date)}",
        f"Статус: {task.get_status_display()}",
    ]
    context_line = _task_context_line(task)
    if context_line:
        lines.append(context_line)
    comment = _truncate_comment(task.comment)
    if comment:
        lines.append(f"Коментар: {comment}")
    return "\n".join(lines)


def build_task_deadline_message(task, *, overdue=False):
    label = "Задача прострочена" if overdue else "Наближається дедлайн задачі"
    lines = [
        label,
        task.title,
        f"Дедлайн: {_format_deadline(task.date)}",
        f"Пріоритет: {task.get_priority_display()}",
        f"Статус: {task.get_status_display()}",
    ]
    context_line = _task_context_line(task)
    if context_line:
        lines.append(context_line)
    if getattr(task, "description", ""):
        lines.append(f"Опис: {_truncate_comment(task.description)}")
    return "\n".join(lines)


def build_order_status_message(order, *, previous_status=None):
    lines = [
        "Змінено статус замовлення",
        order.title or f"Замовлення #{order.pk}",
        f"Новий статус: {order.get_status_display()}",
    ]
    if previous_status:
        previous_label = Order.Status(previous_status).label if previous_status in Order.Status.values else previous_status
        lines.append(f"Попередній статус: {previous_label}")
    lines.append(f"Дедлайн: {_format_deadline(order.deadline)}")
    lines.append(f"Контакт: {order.contact.full_name}")
    return "\n".join(lines)


def build_order_created_message(order):
    lines = [
        "Нове замовлення",
        order.title or f"Замовлення #{order.pk}",
        f"Статус: {order.get_status_display()}",
        f"Дедлайн: {_format_deadline(order.deadline)}",
        f"Пріоритет: {order.get_priority_display()}",
        f"Контакт: {order.contact.full_name}",
    ]
    comment = _truncate_comment(order.comment)
    if comment:
        lines.append(f"Коментар: {comment}")
    return "\n".join(lines)


def build_order_comment_message(order):
    lines = [
        "Оновлено коментар до замовлення",
        order.title or f"Замовлення #{order.pk}",
        f"Статус: {order.get_status_display()}",
        f"Дедлайн: {_format_deadline(order.deadline)}",
        f"Контакт: {order.contact.full_name}",
    ]
    comment = _truncate_comment(order.comment)
    if comment:
        lines.append(f"Коментар: {comment}")
    return "\n".join(lines)


def build_order_deadline_message(order, *, overdue=False):
    label = "Замовлення прострочене" if overdue else "Наближається дедлайн замовлення"
    row = build_order_row(order, now=timezone.now(), today=timezone.localdate())
    lines = [
        label,
        order.title or f"Замовлення #{order.pk}",
        f"Дедлайн: {_format_deadline(order.deadline)}",
        f"Статус: {order.get_status_display()}",
        f"Готовність: {row['progress_percent']}%",
    ]
    if row["current_stage"]:
        lines.append(f"Поточний етап: {row['current_stage'].get_stage_type_display()}")
    if row["risk_reasons"]:
        lines.append(f"Ризик: {row['risk_reasons'][0]}")
    return "\n".join(lines)


def build_production_event_message(stage, *, previous_status=None):
    order = stage.order
    lines = [
        "Подія виробництва",
        order.title or f"Замовлення #{order.pk}",
        f"Етап: {stage.get_stage_type_display()}",
        f"Статус: {stage.get_status_display()}",
    ]
    if previous_status:
        previous_label = (
            ProductionStage.Status(previous_status).label
            if previous_status in ProductionStage.Status.values
            else previous_status
        )
        lines.append(f"Попередній статус: {previous_label}")
    if stage.responsible:
        lines.append(f"Відповідальний: {stage.responsible.profile.display_name if hasattr(stage.responsible, 'profile') else stage.responsible}")
    return "\n".join(lines)


def notification_keyboard(notification):
    buttons = []
    if notification.task_id:
        task_board_button = _build_url_button("Відкрити дошку задач", reverse("crm_tasks_kanban"))
        if task_board_button:
            buttons.append([task_board_button])
    if notification.order_id:
        orders_button = _build_url_button("Відкрити замовлення", reverse("crm_orders"))
        if orders_button:
            buttons.append([orders_button])
    if notification.stage_id:
        report_button = _build_url_button("Звіт по замовленнях", reverse("production_orders_in_work_report"))
        if report_button:
            buttons.append([report_button])
    return {"inline_keyboard": buttons} if buttons else None
