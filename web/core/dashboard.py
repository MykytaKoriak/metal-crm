from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from crm.models import Client, Order, OrderItem, Task
from manufacture.models import Machine, ProductionSlot, ProductionStage, WorkUnit
from manufacture.services import (
    build_free_slot_report,
    build_orders_in_work_report,
    build_overdue_stage_report,
    build_stage_row,
    get_resource_catalog,
)

from .access import ROLE_GROUP_NAMES
from .visibility import filter_orders_queryset, filter_stages_queryset


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
        in_production_orders=Count("id", filter=Q(status=Order.Status.IN_PRODUCTION)),
        ready_orders=Count("id", filter=Q(status=Order.Status.READY)),
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
        .select_related("client", "contact", "order", "assigned_by", "assigned_to")
        .order_by("date", "id")
    )
    my_orders = (
        Order.objects.filter(manager=user)
        .select_related("contact", "contact__client", "manager")
        .order_by("deadline", "-created_at")
    )

    task_board = [
        {
            "key": Task.Status.NEW,
            "title": "Нові",
            "items": list(my_tasks.filter(status=Task.Status.NEW)[:5]),
        },
        {
            "key": Task.Status.IN_PROGRESS,
            "title": "В роботі",
            "items": list(my_tasks.filter(status=Task.Status.IN_PROGRESS)[:5]),
        },
        {
            "key": Task.Status.WAITING,
            "title": "Очікують",
            "items": list(my_tasks.filter(status=Task.Status.WAITING)[:5]),
        },
        {
            "key": Task.Status.DONE,
            "title": "Виконано",
            "items": list(my_tasks.filter(status=Task.Status.DONE)[:5]),
        },
    ]

    return {
        "task_board": task_board,
        "task_stats": {
            "open_tasks": my_tasks.exclude(status=Task.Status.DONE).count(),
            "overdue_tasks": my_tasks.exclude(status=Task.Status.DONE).filter(date__lt=today).count(),
            "done_tasks": my_tasks.filter(status=Task.Status.DONE).count(),
            "waiting_tasks": my_tasks.filter(status=Task.Status.WAITING).count(),
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


def get_production_dashboard_context(request=None):
    now = timezone.now()
    today = timezone.localdate()
    production = get_production_load_context()
    user = getattr(request, "user", None)

    query = request.GET if request is not None else {}
    resource_kind = query.get("resource_kind", "all")
    if resource_kind not in {"all", "machine", "work_unit"}:
        resource_kind = "all"
    resource_id = query.get("resource_id", "").strip()
    stage_status = query.get("stage_status", "").strip()
    only_overdue = query.get("only_overdue") == "1"

    queue_queryset = filter_stages_queryset(
        user,
        ProductionStage.objects.select_related(
            "order_item",
            "order_item__product",
            "order_item__order",
            "order_item__order__contact",
            "order_item__order__contact__client",
            "responsible",
        )
        .prefetch_related("slots__machine", "slots__work_unit")
        .order_by("planned_start", "sequence", "id"),
    )

    if stage_status:
        queue_queryset = queue_queryset.filter(status=stage_status)
    else:
        queue_queryset = queue_queryset.filter(
            Q(status__in=[
                ProductionStage.Status.SCHEDULED,
                ProductionStage.Status.IN_PROGRESS,
                ProductionStage.Status.BLOCKED,
            ])
            | Q(status=ProductionStage.Status.NEW, planned_start__isnull=False)
        )

    queue_rows = []
    for stage in queue_queryset[:80]:
        row = build_stage_row(stage, now=now)
        if resource_kind == "machine" and row["resource_kind"] != "machine":
            continue
        if resource_kind == "work_unit" and row["resource_kind"] != "work_unit":
            continue
        if resource_id and (not row["resource"] or str(row["resource"].pk) != resource_id):
            continue
        if only_overdue and not row["is_overdue"]:
            continue
        queue_rows.append(row)

    queue_rows.sort(
        key=lambda row: (
            0 if row["stage"].status == ProductionStage.Status.IN_PROGRESS else 1,
            0 if row["is_overdue"] else 1,
            row["planned_start"] or now + timedelta(days=3650),
            row["stage"].sequence,
            row["stage"].pk,
        )
    )

    overdue_stage_report = build_overdue_stage_report(
        now=now,
        resource_kind=resource_kind,
        resource_id=resource_id or None,
        user=user,
    )
    free_slot_report = build_free_slot_report(
        date_from=today,
        days=5,
        resource_kind=resource_kind,
        resource_id=resource_id or None,
        min_duration_minutes=60,
        active_only=True,
    )

    overdue_orders = list(
        filter_orders_queryset(
            user,
            Order.objects.filter(deadline__lt=today),
        )
        .exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])
        .select_related("contact", "contact__client", "manager")
        .order_by("deadline")[:10]
    )

    return {
        "production": production,
        "active_queue": queue_rows[:20],
        "overdue_orders": overdue_orders,
        "overdue_stage_rows": overdue_stage_report["rows"][:10],
        "free_slot_rows": free_slot_report["rows"][:12],
        "resource_choices": get_resource_catalog(
            resource_kind=resource_kind if resource_kind != "all" else "all",
            active_only=False,
        ),
        "stage_status_choices": ProductionStage.Status.choices,
        "filters": {
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "stage_status": stage_status,
            "only_overdue": only_overdue,
        },
        "production_summary": {
            "queue_count": len(queue_rows),
            "in_progress_count": sum(
                1 for row in queue_rows if row["stage"].status == ProductionStage.Status.IN_PROGRESS
            ),
            "overdue_stage_count": len(overdue_stage_report["rows"]),
            "free_window_count": len(free_slot_report["rows"]),
            "critical_resources": production["summary"]["critical_resources"],
        },
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
    now = timezone.now()
    production = get_production_load_context()
    overdue_stage_report = build_overdue_stage_report(now=now)
    orders_in_work = build_orders_in_work_report(now=now)
    at_risk_orders = [row for row in orders_in_work if row["is_at_risk"]]
    critical_resources = [
        row
        for row in [*production["machine_rows"], *production["workunit_rows"]]
        if row["status"] == "critical"
    ]
    stalled_tasks_qs = (
        Task.objects.exclude(status=Task.Status.DONE)
        .filter(Q(date__lt=today) | Q(status=Task.Status.WAITING))
        .select_related("client", "contact", "order", "assigned_to")
        .order_by("date", "id")
    )
    stalled_tasks = list(stalled_tasks_qs[:10])
    open_orders_count = Order.objects.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED]).count()
    in_production_count = Order.objects.filter(status=Order.Status.IN_PRODUCTION).count()
    in_production_share = round((in_production_count / open_orders_count * 100) if open_orders_count else 0, 1)

    return {
        "new_clients_count": Client.objects.filter(created_at__date__gte=recent_since).count(),
        "new_orders_count": Order.objects.filter(created_at__date__gte=recent_since).count(),
        "recent_clients": list(Client.objects.order_by("-created_at")[:8]),
        "recent_orders": list(
            Order.objects.select_related("contact", "contact__client", "manager").order_by("-created_at")[:8]
        ),
        "monthly_revenue": get_monthly_revenue_rows(),
        "production": production,
        "critical_resources": critical_resources[:8],
        "overdue_stage_rows": overdue_stage_report["rows"][:8],
        "at_risk_orders": at_risk_orders[:8],
        "stalled_tasks": stalled_tasks,
        "order_status_rows": get_order_status_rows(Order.objects.all()),
        "executive_summary": {
            "open_orders_count": open_orders_count,
            "in_production_count": in_production_count,
            "in_production_share": in_production_share,
            "overdue_stage_count": len(overdue_stage_report["rows"]),
            "critical_resource_count": len(critical_resources),
            "at_risk_order_count": len(at_risk_orders),
            "stalled_task_count": stalled_tasks_qs.count(),
        },
    }
