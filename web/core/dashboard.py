from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from crm.models import Client, Order, OrderItem, Task
from manufacture.models import Machine, ProductionSlot, ProductionStage, WorkUnit

from .access import ROLE_GROUP_NAMES


MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)
ZERO_MONEY = Value(Decimal("0.00"), output_field=MONEY_FIELD)
DEFAULT_WORKDAY_START = time(8, 0)
DEFAULT_WORKDAY_END = time(17, 0)


def _resource_day_length_hours(resource):
    workday_start = getattr(resource, "workday_start", None) or DEFAULT_WORKDAY_START
    workday_end = getattr(resource, "workday_end", None) or DEFAULT_WORKDAY_END
    delta = datetime.combine(date.today(), workday_end) - datetime.combine(date.today(), workday_start)
    return max(delta.total_seconds() / 3600, 0)


def _resource_busy_seconds(resource, field_name, start, end):
    busy_seconds = 0
    slots = (
        ProductionSlot.objects.filter(
            **{field_name: resource},
            start_datetime__lt=end,
            end_datetime__gt=start,
        )
        .exclude(start_datetime__isnull=True)
        .exclude(end_datetime__isnull=True)
    )

    for slot in slots:
        slot_start = max(slot.start_datetime, start)
        slot_end = min(slot.end_datetime, end)
        busy_seconds += max(0, (slot_end - slot_start).total_seconds())

    return busy_seconds


def _resource_load_row(resource, field_name, today_start, today_end, week_end):
    today_busy_seconds = _resource_busy_seconds(resource, field_name, today_start, today_end)
    week_busy_seconds = _resource_busy_seconds(resource, field_name, today_start, week_end)
    workday_hours = _resource_day_length_hours(resource)

    today_available_hours = workday_hours
    week_days = (week_end.date() - today_start.date()).days + 1
    week_available_hours = workday_hours * week_days

    today_busy_hours = round(today_busy_seconds / 3600, 1)
    week_busy_hours = round(week_busy_seconds / 3600, 1)
    today_load = round((today_busy_hours / today_available_hours * 100) if today_available_hours else 0)
    week_load = round((week_busy_hours / week_available_hours * 100) if week_available_hours else 0)

    return {
        "id": resource.id,
        "name": resource.name,
        "type_label": resource.get_type_display(),
        "today_busy_hours": today_busy_hours,
        "week_busy_hours": week_busy_hours,
        "today_available_hours": round(today_available_hours, 1),
        "week_available_hours": round(week_available_hours, 1),
        "today_load": today_load,
        "week_load": week_load,
        "status": "critical" if week_load >= 90 else "warning" if week_load >= 70 else "healthy",
    }


def get_production_load_context():
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    today_end = timezone.make_aware(datetime.combine(today, time.max), tz)
    week_end = timezone.make_aware(datetime.combine(today + timedelta(days=7), time.max), tz)

    machine_rows = []
    for machine in Machine.objects.all():
        row = _resource_load_row(machine, "machine", today_start, today_end, week_end)
        row["detail_url_name"] = "machine_detail_report"
        row["resource_kind"] = "machine"
        machine_rows.append(row)

    workunit_rows = []
    for work_unit in WorkUnit.objects.all():
        row = _resource_load_row(work_unit, "work_unit", today_start, today_end, week_end)
        row["detail_url_name"] = "workunit_detail_report"
        row["resource_kind"] = "work_unit"
        workunit_rows.append(row)

    all_rows = [*machine_rows, *workunit_rows]
    total_busy_today = sum(row["today_busy_hours"] for row in all_rows)
    total_available_today = sum(row["today_available_hours"] for row in all_rows)
    total_busy_week = sum(row["week_busy_hours"] for row in all_rows)
    total_available_week = sum(row["week_available_hours"] for row in all_rows)

    return {
        "machine_rows": machine_rows,
        "workunit_rows": workunit_rows,
        "top_resources": sorted(all_rows, key=lambda row: row["week_load"], reverse=True)[:6],
        "summary": {
            "resource_count": len(all_rows),
            "today_load": round((total_busy_today / total_available_today * 100) if total_available_today else 0),
            "week_load": round((total_busy_week / total_available_week * 100) if total_available_week else 0),
            "critical_resources": sum(1 for row in all_rows if row["status"] == "critical"),
            "scheduled_today": ProductionSlot.objects.filter(
                start_datetime__date__lte=today,
                end_datetime__date__gte=today,
            )
            .exclude(start_datetime__isnull=True)
            .exclude(end_datetime__isnull=True)
            .count(),
        },
    }


def _item_total_expression(quantity_field="quantity", unit_price_field="unit_price"):
    return ExpressionWrapper(F(quantity_field) * F(unit_price_field), output_field=MONEY_FIELD)


def get_total_revenue(order_filters=None):
    order_filters = order_filters or {}
    return OrderItem.objects.filter(**order_filters).aggregate(
        total=Coalesce(Sum(_item_total_expression()), ZERO_MONEY)
    )["total"]


def get_order_status_rows(queryset):
    counts = {
        row["status"]: row["count"]
        for row in queryset.values("status").annotate(count=Count("id"))
    }
    total_count = sum(counts.values())
    return [
        {
            "code": code,
            "label": label,
            "count": counts.get(code, 0),
            "bar_percent": int((counts.get(code, 0) / total_count) * 100) if total_count else 0,
        }
        for code, label in Order.Status.choices
    ]


def get_admin_dashboard_context():
    today = timezone.localdate()
    order_queryset = Order.objects.all()

    order_stats = order_queryset.aggregate(
        total_orders=Count("id"),
        new_orders=Count("id", filter=Q(status=Order.Status.NEW)),
        in_progress_orders=Count("id", filter=Q(status=Order.Status.IN_PROGRESS)),
        shipped_orders=Count("id", filter=Q(status=Order.Status.SHIPPED)),
        completed_orders=Count("id", filter=Q(status=Order.Status.COMPLETED)),
        canceled_orders=Count("id", filter=Q(status=Order.Status.CANCELED)),
        overdue_orders=Count(
            "id",
            filter=Q(deadline__lt=today) & ~Q(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED]),
        ),
    )

    return {
        "user_count": get_user_model().objects.count(),
        "active_user_count": get_user_model().objects.filter(is_active=True).count(),
        "role_count": Group.objects.filter(name__in=ROLE_GROUP_NAMES.values()).count(),
        "order_stats": order_stats,
        "order_status_rows": get_order_status_rows(order_queryset),
        "total_revenue": get_total_revenue(),
        "production": get_production_load_context(),
    }


def get_sales_dashboard_context(user):
    today = timezone.localdate()
    my_tasks = (
        Task.objects.filter(assigned_to=user)
        .select_related("contact", "contact__client", "assigned_by", "assigned_to")
        .order_by("date", "id")
    )
    my_orders = (
        Order.objects.filter(manager=user)
        .select_related("contact", "contact__client", "manager")
        .order_by("deadline", "-created_at")
    )

    task_board = [
        {
            "key": "today",
            "title": "На сьогодні",
            "items": list(my_tasks.filter(status=False, date=today)[:10]),
        },
        {
            "key": "planned",
            "title": "Заплановані",
            "items": list(my_tasks.filter(status=False, date__gt=today)[:10]),
        },
        {
            "key": "overdue",
            "title": "Прострочені",
            "items": list(my_tasks.filter(status=False, date__lt=today)[:10]),
        },
        {
            "key": "done",
            "title": "Виконано",
            "items": list(my_tasks.filter(status=True)[:10]),
        },
    ]

    return {
        "task_board": task_board,
        "task_stats": {
            "open_tasks": my_tasks.filter(status=False).count(),
            "overdue_tasks": my_tasks.filter(status=False, date__lt=today).count(),
            "done_tasks": my_tasks.filter(status=True).count(),
        },
        "order_status_rows": get_order_status_rows(my_orders),
        "orders_count": my_orders.count(),
        "nearest_deadlines": list(
            my_orders.filter(
                deadline__isnull=False,
                deadline__gte=today,
                deadline__lte=today + timedelta(days=14),
            )
            .exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])
            [:8]
        ),
    }


def get_production_dashboard_context():
    now = timezone.now()
    today = timezone.localdate()
    production = get_production_load_context()

    active_queue = list(
        ProductionStage.objects.select_related(
            "order_item",
            "order_item__order",
            "order_item__order__contact",
            "order_item__order__contact__client",
            "responsible",
        )
        .prefetch_related("slots__machine", "slots__work_unit")
        .filter(
            Q(status__in=[
                ProductionStage.Status.SCHEDULED,
                ProductionStage.Status.IN_PROGRESS,
                ProductionStage.Status.BLOCKED,
            ])
            | Q(status=ProductionStage.Status.NEW, planned_start__isnull=False)
        )
        .filter(Q(planned_end__isnull=True) | Q(planned_end__gte=now - timedelta(days=1)))
        .order_by("planned_start", "sequence", "id")[:10]
    )

    for stage in active_queue:
        stage.dashboard_order = stage.order_item.order
        stage.dashboard_state = stage.get_status_display()
        stage.dashboard_slot = next(iter(stage.slots.all()), None)
        if stage.dashboard_slot:
            stage.dashboard_resource = stage.dashboard_slot.machine or stage.dashboard_slot.work_unit
            stage.dashboard_start = stage.dashboard_slot.start_datetime or stage.planned_start
            stage.dashboard_end = stage.dashboard_slot.end_datetime or stage.planned_end
        else:
            stage.dashboard_resource = None
            stage.dashboard_start = stage.planned_start
            stage.dashboard_end = stage.planned_end

    overdue_orders = list(
        Order.objects.filter(deadline__lt=today)
        .exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])
        .select_related("contact", "contact__client", "manager")
        .order_by("deadline")[:10]
    )

    return {
        "production": production,
        "active_queue": active_queue,
        "overdue_orders": overdue_orders,
    }


def _shift_month(month_start, delta):
    month_index = month_start.month - 1 + delta
    year = month_start.year + month_index // 12
    month = month_index % 12 + 1
    return month_start.replace(year=year, month=month, day=1)


def get_monthly_revenue_rows(months=6):
    current_month = timezone.localdate().replace(day=1)
    month_starts = [_shift_month(current_month, offset) for offset in range(-(months - 1), 1)]
    raw_rows = (
        OrderItem.objects.filter(order__created_at__date__gte=month_starts[0])
        .annotate(month=TruncMonth("order__created_at"))
        .values("month")
        .annotate(revenue=Coalesce(Sum(_item_total_expression()), ZERO_MONEY))
        .order_by("month")
    )
    revenue_by_month = {
        row["month"].date().replace(day=1): row["revenue"]
        for row in raw_rows
        if row["month"] is not None
    }

    rows = []
    max_revenue = max((revenue_by_month.get(month_start, Decimal("0.00")) for month_start in month_starts), default=Decimal("0.00"))

    for month_start in month_starts:
        revenue = revenue_by_month.get(month_start, Decimal("0.00"))
        bar_percent = int((revenue / max_revenue) * 100) if max_revenue else 0
        rows.append(
            {
                "label": month_start.strftime("%m.%Y"),
                "revenue": revenue,
                "bar_percent": max(bar_percent, 4) if revenue else 0,
            }
        )

    return rows


def get_executive_dashboard_context():
    today = timezone.localdate()
    recent_since = today - timedelta(days=30)

    return {
        "new_clients_count": Client.objects.filter(created_at__date__gte=recent_since).count(),
        "new_orders_count": Order.objects.filter(created_at__date__gte=recent_since).count(),
        "recent_clients": list(Client.objects.order_by("-created_at")[:8]),
        "recent_orders": list(
            Order.objects.select_related("contact", "contact__client", "manager").order_by("-created_at")[:8]
        ),
        "monthly_revenue": get_monthly_revenue_rows(),
        "production": get_production_load_context(),
    }
