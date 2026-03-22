from django.conf import settings
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone

from core.access import get_user_role
from crm.models import Order
from manufacture.models import ProductionStage
from manufacture.services import build_order_row


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
        return "no deadline"
    return date_value.strftime("%d.%m.%Y")


def _task_context_line(task):
    parts = []
    if getattr(task, "client", None):
        parts.append(f"Client: {task.client.name}")
    if getattr(task, "order_id", None):
        title = task.order.title or f"Order #{task.order_id}"
        parts.append(title)
    return " | ".join(parts)


def _order_context_line(row):
    order = row["order"]
    manager_name = order.manager.profile.display_name if getattr(order.manager, "profile", None) else ""
    context = [f"Contact: {order.contact.full_name}"]
    if manager_name:
        context.append(f"Manager: {manager_name}")
    if row["current_stage"]:
        context.append(f"Stage: {row['current_stage'].get_stage_type_display()}")
    if row["current_resource_label"]:
        context.append(row["current_resource_label"])
    return " | ".join(context)


def build_home_message(profile) -> str:
    account_line = "linked" if profile.telegram_is_linked else "not linked"
    return "\n".join(
        [
            f"CRM Telegram bot",
            f"User: {profile.display_name}",
            f"Status: {account_line}",
            "",
            "Commands:",
            "/link CODE",
            "/unlink",
            "/tasks",
            "/orders",
        ]
    )


def build_home_keyboard():
    keyboard = [
        [
            {"text": "Tasks", "callback_data": "tasks:open:1"},
            {"text": "Orders", "callback_data": "orders:active:1"},
        ]
    ]
    account_button = _build_url_button("Open account", reverse("my_account"))
    if account_button:
        keyboard.append([account_button])
    return {"inline_keyboard": keyboard}


def build_tasks_message(profile, queryset, *, scope="open", page_number=1):
    paginator = Paginator(queryset, TASK_PAGE_SIZE)
    page_number = max(1, min(page_number, paginator.num_pages or 1))
    page = paginator.get_page(page_number)

    scope_label = {
        "open": "Open tasks",
        "today": "Today",
        "overdue": "Overdue",
    }.get(scope, "Tasks")

    lines = [f"{scope_label} ({page.number}/{max(paginator.num_pages, 1)})", ""]
    if not page.object_list:
        lines.append("No tasks found.")
    else:
        for index, task in enumerate(page.object_list, start=1 + (page.number - 1) * TASK_PAGE_SIZE):
            lines.append(
                f"{index}. {_format_deadline(task.date)} | {task.get_status_display()} | {task.title}"
            )
            context_line = _task_context_line(task)
            if context_line:
                lines.append(f"   {context_line}")
            if task.comment:
                lines.append(f"   {task.comment[:140]}")
            lines.append("")

    keyboard = [
        [
            {"text": "Open", "callback_data": "tasks:open:1"},
            {"text": "Today", "callback_data": "tasks:today:1"},
            {"text": "Overdue", "callback_data": "tasks:overdue:1"},
        ]
    ]
    nav_row = []
    if page.has_previous():
        nav_row.append({"text": "Prev", "callback_data": f"tasks:{scope}:{page.previous_page_number()}"})
    if page.has_next():
        nav_row.append({"text": "Next", "callback_data": f"tasks:{scope}:{page.next_page_number()}"})
    if nav_row:
        keyboard.append(nav_row)

    tasks_button = _build_url_button("Open task board", reverse("crm_tasks_kanban"))
    if tasks_button:
        keyboard.append([tasks_button])
    keyboard.append([{"text": "Orders", "callback_data": "orders:active:1"}])
    return "\n".join(lines).strip(), {"inline_keyboard": keyboard}


def build_orders_message(profile, order_rows, *, scope="active", page_number=1):
    paginator = Paginator(order_rows, ORDER_PAGE_SIZE)
    page_number = max(1, min(page_number, paginator.num_pages or 1))
    page = paginator.get_page(page_number)

    scope_label = {
        "active": "Orders in work",
        "risk": "Orders at risk",
        "mine": "My orders",
    }.get(scope, "Orders")

    lines = [f"{scope_label} ({page.number}/{max(paginator.num_pages, 1)})", ""]
    if not page.object_list:
        lines.append("No orders found.")
    else:
        for index, row in enumerate(page.object_list, start=1 + (page.number - 1) * ORDER_PAGE_SIZE):
            order = row["order"]
            lines.append(
                f"{index}. {_format_deadline(order.deadline)} | {order.get_status_display()} | {order.title or f'Order #{order.pk}'}"
            )
            lines.append(f"   {_order_context_line(row)}")
            lines.append(
                f"   Progress: {row['progress_percent']}% | Risk: {row['risk_label']}"
            )
            if row["risk_reasons"]:
                lines.append(f"   {row['risk_reasons'][0]}")
            lines.append("")

    keyboard = [
        [
            {"text": "Active", "callback_data": "orders:active:1"},
            {"text": "At risk", "callback_data": "orders:risk:1"},
        ]
    ]
    if get_user_role(profile.user) == profile.Role.SALES_MANAGER:
        keyboard[0].append({"text": "Mine", "callback_data": "orders:mine:1"})

    nav_row = []
    if page.has_previous():
        nav_row.append({"text": "Prev", "callback_data": f"orders:{scope}:{page.previous_page_number()}"})
    if page.has_next():
        nav_row.append({"text": "Next", "callback_data": f"orders:{scope}:{page.next_page_number()}"})
    if nav_row:
        keyboard.append(nav_row)

    orders_button = _build_url_button("Open orders", reverse("crm_orders"))
    if orders_button:
        keyboard.append([orders_button])
    report_button = _build_url_button("Orders in work report", reverse("production_orders_in_work_report"))
    if report_button:
        keyboard.append([report_button])
    keyboard.append([{"text": "Tasks", "callback_data": "tasks:open:1"}])
    return "\n".join(lines).strip(), {"inline_keyboard": keyboard}


def build_task_created_message(task):
    lines = [
        "New task",
        task.title,
        f"Deadline: {_format_deadline(task.date)}",
        f"Status: {task.get_status_display()}",
    ]
    context_line = _task_context_line(task)
    if context_line:
        lines.append(context_line)
    return "\n".join(lines)


def build_task_deadline_message(task, *, overdue=False):
    label = "Task overdue" if overdue else "Task deadline is close"
    lines = [
        label,
        task.title,
        f"Deadline: {_format_deadline(task.date)}",
        f"Status: {task.get_status_display()}",
    ]
    context_line = _task_context_line(task)
    if context_line:
        lines.append(context_line)
    return "\n".join(lines)


def build_order_status_message(order, *, previous_status=None):
    lines = [
        "Order status changed",
        order.title or f"Order #{order.pk}",
        f"New status: {order.get_status_display()}",
    ]
    if previous_status:
        previous_label = Order.Status(previous_status).label if previous_status in Order.Status.values else previous_status
        lines.append(f"Previous status: {previous_label}")
    lines.append(f"Deadline: {_format_deadline(order.deadline)}")
    lines.append(f"Contact: {order.contact.full_name}")
    return "\n".join(lines)


def build_order_deadline_message(order, *, overdue=False):
    label = "Order overdue" if overdue else "Order deadline is close"
    row = build_order_row(order, now=timezone.now(), today=timezone.localdate())
    lines = [
        label,
        order.title or f"Order #{order.pk}",
        f"Deadline: {_format_deadline(order.deadline)}",
        f"Status: {order.get_status_display()}",
        f"Progress: {row['progress_percent']}%",
    ]
    if row["current_stage"]:
        lines.append(f"Current stage: {row['current_stage'].get_stage_type_display()}")
    if row["risk_reasons"]:
        lines.append(f"Risk: {row['risk_reasons'][0]}")
    return "\n".join(lines)


def build_production_event_message(stage, *, previous_status=None):
    order = stage.order
    lines = [
        "Production event",
        order.title or f"Order #{order.pk}",
        f"Stage: {stage.get_stage_type_display()}",
        f"Status: {stage.get_status_display()}",
    ]
    if previous_status:
        previous_label = (
            ProductionStage.Status(previous_status).label
            if previous_status in ProductionStage.Status.values
            else previous_status
        )
        lines.append(f"Previous status: {previous_label}")
    if stage.responsible:
        lines.append(f"Responsible: {stage.responsible.profile.display_name if hasattr(stage.responsible, 'profile') else stage.responsible}")
    return "\n".join(lines)


def notification_keyboard(notification):
    buttons = []
    if notification.task_id:
        task_board_button = _build_url_button("Open task board", reverse("crm_tasks_kanban"))
        if task_board_button:
            buttons.append([task_board_button])
    if notification.order_id:
        orders_button = _build_url_button("Open orders", reverse("crm_orders"))
        if orders_button:
            buttons.append([orders_button])
    if notification.stage_id:
        report_button = _build_url_button("Orders in work report", reverse("production_orders_in_work_report"))
        if report_button:
            buttons.append([report_button])
    return {"inline_keyboard": buttons} if buttons else None
