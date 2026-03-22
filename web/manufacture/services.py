from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import local

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.visibility import filter_orders_queryset, filter_stages_queryset

from .models import (
    DEFAULT_WORKDAY_END,
    DEFAULT_WORKDAY_START,
    Machine,
    ProductionSlot,
    ProductionStage,
    ResourceDowntime,
    WorkUnit,
)


PLANNING_HORIZON_DAYS = 120
DEFAULT_PRIORITY_VALUE = "normal"
_planner_state = local()


def _ensure_aware(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


@contextmanager
def planner_execution():
    depth = getattr(_planner_state, "depth", 0)
    _planner_state.depth = depth + 1
    try:
        yield
    finally:
        _planner_state.depth -= 1


def planner_is_active():
    return getattr(_planner_state, "depth", 0) > 0


def request_replan_open_orders():
    if planner_is_active():
        return
    transaction.on_commit(replan_open_orders)


def serialize_slot(slot):
    return {
        "id": slot.pk,
        "order_id": slot.order_id,
        "stage_id": slot.stage_id,
        "slot_type": slot.slot_type,
        "operation_type": slot.operation_type,
        "machine_id": slot.machine_id,
        "work_unit_id": slot.work_unit_id,
        "start_datetime": slot.start_datetime.isoformat() if slot.start_datetime else None,
        "end_datetime": slot.end_datetime.isoformat() if slot.end_datetime else None,
        "planning_mode": slot.planning_mode,
        "planning_source": slot.planning_source,
        "is_locked": slot.is_locked,
        "purpose": slot.purpose,
        "comment": slot.comment,
        "dispatcher_comment": slot.dispatcher_comment,
    }


def get_resource_label(resource):
    return str(resource)


def get_resource_kind(resource):
    return "machine" if isinstance(resource, Machine) else "work_unit"


def get_resource_work_window(resource, current_date):
    if not resource.is_active:
        return None
    if current_date.weekday() not in resource.get_available_weekdays_set():
        return None

    start_time, end_time = resource.get_workday_bounds()
    if end_time <= start_time:
        start_time = DEFAULT_WORKDAY_START
        end_time = DEFAULT_WORKDAY_END

    start_dt = timezone.make_aware(datetime.combine(current_date, start_time), timezone.get_current_timezone())
    end_dt = timezone.make_aware(datetime.combine(current_date, end_time), timezone.get_current_timezone())
    return start_dt, end_dt


def _build_resource_filter(resource):
    return {get_resource_kind(resource): resource}


def merge_intervals(intervals):
    normalized = sorted((start, end) for start, end in intervals if start and end and start < end)
    merged = []
    for start, end in normalized:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
            continue
        if end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def subtract_intervals(base_interval, blocked_intervals):
    start, end = base_interval
    pointer = start
    free = []
    for blocked_start, blocked_end in merge_intervals(blocked_intervals):
        if blocked_start > pointer:
            free.append((pointer, blocked_start))
        if blocked_end > pointer:
            pointer = blocked_end
    if pointer < end:
        free.append((pointer, end))
    return free


def get_resource_slot_conflicts(resource, start, end, *, exclude_slot_id=None):
    queryset = ProductionSlot.objects.filter(
        **_build_resource_filter(resource),
        start_datetime__lt=end,
        end_datetime__gt=start,
    )
    if exclude_slot_id:
        queryset = queryset.exclude(pk=exclude_slot_id)
    return list(queryset.select_related("order", "stage", "machine", "work_unit").order_by("start_datetime", "id"))


def get_resource_downtime_conflicts(resource, start, end):
    return list(
        ResourceDowntime.objects.filter(
            **_build_resource_filter(resource),
            is_blocking=True,
            start_datetime__lt=end,
            end_datetime__gt=start,
        ).order_by("start_datetime", "id")
    )


def get_resource_block_intervals(resource, start, end):
    return merge_intervals(
        (block.start_datetime, block.end_datetime)
        for block in get_resource_downtime_conflicts(resource, start, end)
    )


def get_resource_unavailable_intervals(resource, start, end, *, exclude_slot_id=None):
    intervals = [
        (slot.start_datetime, slot.end_datetime)
        for slot in get_resource_slot_conflicts(resource, start, end, exclude_slot_id=exclude_slot_id)
    ]
    intervals.extend(get_resource_block_intervals(resource, start, end))
    return merge_intervals(intervals)


def get_resource_day_plan(resource, current_date, *, slot_queryset=None):
    window = get_resource_work_window(resource, current_date)
    if not window:
        return {
            "work_window": None,
            "busy": [],
            "blocks": [],
            "free": [],
        }

    day_start, day_end = window
    if slot_queryset is None:
        slot_iterable = get_resource_slot_conflicts(resource, day_start, day_end)
    else:
        slot_iterable = list(
            slot_queryset.filter(
                **_build_resource_filter(resource),
                start_datetime__lt=day_end,
                end_datetime__gt=day_start,
            )
            .select_related("order", "stage", "machine", "work_unit")
            .order_by("start_datetime", "id")
        )

    busy = [
        (max(slot.start_datetime, day_start), min(slot.end_datetime, day_end), slot)
        for slot in slot_iterable
        if slot.start_datetime and slot.end_datetime
    ]
    busy = [(start, end, slot) for start, end, slot in busy if start < end]
    busy.sort(key=lambda item: item[0])

    blocks = [
        (max(block.start_datetime, day_start), min(block.end_datetime, day_end), block)
        for block in get_resource_downtime_conflicts(resource, day_start, day_end)
    ]
    blocks = [(start, end, block) for start, end, block in blocks if start < end]
    blocks.sort(key=lambda item: item[0])

    free = subtract_intervals(
        (day_start, day_end),
        [(start, end) for start, end, _ in busy] + [(start, end) for start, end, _ in blocks],
    )
    return {
        "work_window": (day_start, day_end),
        "busy": busy,
        "blocks": blocks,
        "free": free,
    }


def get_resource_capacity_seconds(resource, start, end):
    start = _ensure_aware(start)
    end = _ensure_aware(end)
    if end <= start or not resource.is_active:
        return 0

    total = 0
    current_date = timezone.localtime(start).date()
    last_date = timezone.localtime(end).date()
    while current_date <= last_date:
        window = get_resource_work_window(resource, current_date)
        if window:
            window_start, window_end = window
            current_start = max(window_start, start)
            current_end = min(window_end, end)
            if current_start < current_end:
                blocks = get_resource_block_intervals(resource, current_start, current_end)
                for free_start, free_end in subtract_intervals((current_start, current_end), blocks):
                    total += (free_end - free_start).total_seconds()
        current_date += timedelta(days=1)
    return total


def get_resource_busy_seconds(resource, start, end, *, slot_queryset=None):
    start = _ensure_aware(start)
    end = _ensure_aware(end)
    if slot_queryset is None:
        slot_iterable = get_resource_slot_conflicts(resource, start, end)
    else:
        slot_iterable = list(
            slot_queryset.filter(
                **_build_resource_filter(resource),
                start_datetime__lt=end,
                end_datetime__gt=start,
            )
            .order_by("start_datetime", "id")
        )
    intervals = [
        (max(slot.start_datetime, start), min(slot.end_datetime, end))
        for slot in slot_iterable
        if slot.start_datetime and slot.end_datetime
    ]
    total = 0
    for interval_start, interval_end in merge_intervals(intervals):
        total += (interval_end - interval_start).total_seconds()
    return total


def get_stage_effective_start(stage):
    slots = stage.slots.exclude(start_datetime__isnull=True).order_by("start_datetime", "id")
    if slots.exists():
        return slots.first().start_datetime
    return stage.started_at or stage.planned_start


def get_stage_effective_end(stage):
    slots = stage.slots.exclude(end_datetime__isnull=True).order_by("-end_datetime", "-id")
    if slots.exists():
        return slots.first().end_datetime
    return stage.completed_at or stage.planned_end


def get_stage_primary_slot(stage):
    slot = (
        stage.slots.exclude(start_datetime__isnull=True)
        .exclude(end_datetime__isnull=True)
        .select_related("machine", "work_unit")
        .order_by("start_datetime", "id")
        .first()
    )
    if slot:
        return slot
    return stage.slots.select_related("machine", "work_unit").order_by("id").first()


def get_stage_resource(stage):
    slot = get_stage_primary_slot(stage)
    return slot.resource if slot else None


def get_stage_operation_type(stage):
    stage_type = getattr(stage, "stage_type", "")
    if stage_type in dict(ProductionSlot.OperationType.choices):
        return stage_type
    return ProductionSlot.OperationType.OTHER


def get_stage_purpose(stage):
    if not getattr(stage, "pk", None):
        return ""
    return f"{stage.order_item.product.name} / {stage.get_stage_type_display()}"


def build_stage_row(stage, *, now=None):
    now = now or timezone.now()
    slot = get_stage_primary_slot(stage)
    resource = slot.resource if slot else None
    planned_start = stage.planned_start or (slot.start_datetime if slot and slot.start_datetime else None)
    planned_end = stage.planned_end or (slot.end_datetime if slot and slot.end_datetime else None)
    is_terminal = stage.status in {ProductionStage.Status.DONE, ProductionStage.Status.CANCELLED}
    overdue_seconds = 0
    overdue_reason = ""
    if planned_end and not is_terminal and planned_end < now:
        overdue_seconds = max((now - planned_end).total_seconds(), 0)
        overdue_reason = "deadline"
    elif stage.status == ProductionStage.Status.IN_PROGRESS and stage.started_at and not planned_end:
        overdue_seconds = max((now - stage.started_at).total_seconds(), 0)
        overdue_reason = "stalled"

    return {
        "stage": stage,
        "slot": slot,
        "resource": resource,
        "resource_label": get_resource_label(resource) if resource else "",
        "resource_kind": get_resource_kind(resource) if resource else "",
        "planned_start": planned_start,
        "planned_end": planned_end,
        "operation_type": get_stage_operation_type(stage),
        "purpose": get_stage_purpose(stage),
        "overdue_seconds": int(overdue_seconds),
        "overdue_hours": round(overdue_seconds / 3600, 1) if overdue_seconds else 0,
        "overdue_reason": overdue_reason,
        "is_overdue": overdue_seconds > 0,
        "is_terminal": is_terminal,
    }


def get_resource_catalog(*, resource_kind="all", active_only=False):
    resources = []
    if resource_kind in {"all", "machine"}:
        queryset = Machine.objects.all().order_by("type", "name")
        if active_only:
            queryset = queryset.filter(is_active=True)
        resources.extend(
            {
                "key": f"machine:{resource.pk}",
                "kind": "machine",
                "id": resource.pk,
                "label": get_resource_label(resource),
                "resource": resource,
            }
            for resource in queryset
        )
    if resource_kind in {"all", "work_unit"}:
        queryset = WorkUnit.objects.all().order_by("type", "name")
        if active_only:
            queryset = queryset.filter(is_active=True)
        resources.extend(
            {
                "key": f"work_unit:{resource.pk}",
                "kind": "work_unit",
                "id": resource.pk,
                "label": get_resource_label(resource),
                "resource": resource,
            }
            for resource in queryset
        )
    return resources


def get_resource_from_filter(resource_kind, resource_id):
    if not resource_id:
        return None
    if resource_kind == "machine":
        return Machine.objects.filter(pk=resource_id).first()
    if resource_kind == "work_unit":
        return WorkUnit.objects.filter(pk=resource_id).first()
    return None


def build_free_slot_report(
    *,
    date_from=None,
    days=7,
    resource_kind="all",
    resource_id=None,
    min_duration_minutes=0,
    active_only=False,
    user=None,
):
    start_date = date_from or timezone.localdate()
    end_date = start_date + timedelta(days=max(days - 1, 0))
    min_seconds = max(min_duration_minutes, 0) * 60

    selected_resource = get_resource_from_filter(resource_kind, resource_id)
    catalog = []
    if selected_resource is not None:
        catalog.append(
            {
                "key": f"{resource_kind}:{selected_resource.pk}",
                "kind": resource_kind,
                "id": selected_resource.pk,
                "label": get_resource_label(selected_resource),
                "resource": selected_resource,
            }
        )
    else:
        catalog = get_resource_catalog(resource_kind=resource_kind, active_only=active_only)

    rows = []
    for entry in catalog:
        resource = entry["resource"]
        if active_only and not resource.is_active:
            continue
        for offset in range((end_date - start_date).days + 1):
            current_date = start_date + timedelta(days=offset)
            plan = get_resource_day_plan(resource, current_date)
            if not plan["work_window"]:
                continue
            for start, end in plan["free"]:
                duration_seconds = (end - start).total_seconds()
                if duration_seconds < min_seconds:
                    continue
                rows.append(
                    {
                        "resource": resource,
                        "resource_label": entry["label"],
                        "resource_kind": entry["kind"],
                        "resource_kind_label": "Верстат" if entry["kind"] == "machine" else "Дільниця",
                        "date": current_date,
                        "start": start,
                        "end": end,
                        "duration_seconds": int(duration_seconds),
                        "duration_hours": round(duration_seconds / 3600, 2),
                    }
                )

    rows.sort(key=lambda row: (row["start"], row["resource_kind"], row["resource_label"]))
    return {
        "rows": rows,
        "start_date": start_date,
        "end_date": end_date,
        "resource_choices": get_resource_catalog(resource_kind=resource_kind, active_only=False),
    }


def build_overdue_stage_report(*, now=None, resource_kind="all", resource_id=None, user=None):
    now = now or timezone.now()
    stages = ProductionStage.objects.select_related(
        "order_item",
        "order_item__product",
        "order_item__order",
        "order_item__order__contact",
        "order_item__order__contact__client",
        "responsible",
    )
    stages = (
        stages.prefetch_related("slots__machine", "slots__work_unit")
        .exclude(status__in=[ProductionStage.Status.DONE, ProductionStage.Status.CANCELLED])
        .order_by("planned_end", "sequence", "id")
    )
    if user is not None:
        stages = filter_stages_queryset(user, stages)

    rows = []
    for stage in stages:
        row = build_stage_row(stage, now=now)
        if not row["is_overdue"]:
            continue
        if resource_kind == "machine" and row["resource_kind"] != "machine":
            continue
        if resource_kind == "work_unit" and row["resource_kind"] != "work_unit":
            continue
        if resource_id and (not row["resource"] or row["resource"].pk != int(resource_id)):
            continue
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -row["overdue_seconds"],
            row["planned_end"] or now,
            row["stage"].pk,
        )
    )
    return {
        "rows": rows,
        "resource_choices": get_resource_catalog(resource_kind=resource_kind, active_only=False),
    }


def _get_order_stages(order):
    return list(
        ProductionStage.objects.filter(order_item__order=order)
        .select_related(
            "order_item",
            "order_item__product",
            "order_item__order",
            "order_item__order__contact",
            "order_item__order__contact__client",
            "responsible",
        )
        .prefetch_related("slots__machine", "slots__work_unit")
        .order_by("sequence", "planned_start", "id")
    )


def _select_current_stage(stage_rows):
    if not stage_rows:
        return None

    in_progress = [row for row in stage_rows if row["stage"].status == ProductionStage.Status.IN_PROGRESS]
    if in_progress:
        return min(
            in_progress,
            key=lambda row: (
                row["planned_start"] or timezone.now(),
                row["stage"].sequence,
                row["stage"].pk,
            ),
        )

    active = [
        row
        for row in stage_rows
        if row["stage"].status
        not in {ProductionStage.Status.DONE, ProductionStage.Status.CANCELLED}
    ]
    if active:
        return min(
            active,
            key=lambda row: (
                0 if row["stage"].status == ProductionStage.Status.BLOCKED else 1,
                row["planned_start"] or timezone.now() + timedelta(days=3650),
                row["stage"].sequence,
                row["stage"].pk,
            ),
        )

    return max(
        stage_rows,
        key=lambda row: (
            row["planned_end"] or timezone.now(),
            row["stage"].sequence,
            row["stage"].pk,
        ),
    )


def build_order_row(order, *, now=None, today=None):
    now = now or timezone.now()
    today = today or timezone.localdate()
    stages = _get_order_stages(order)
    stage_rows = [build_stage_row(stage, now=now) for stage in stages]
    total_stages = len(stage_rows)
    terminal_stage_count = sum(1 for row in stage_rows if row["is_terminal"])
    done_stage_count = sum(1 for row in stage_rows if row["stage"].status == ProductionStage.Status.DONE)
    open_stage_count = max(total_stages - terminal_stage_count, 0)
    blocked_stage_count = sum(1 for row in stage_rows if row["stage"].status == ProductionStage.Status.BLOCKED)
    overdue_stage_count = sum(1 for row in stage_rows if row["is_overdue"])
    unplanned_stage_count = sum(
        1
        for row in stage_rows
        if not row["is_terminal"] and not row["planned_start"] and not row["planned_end"]
    )
    progress_percent = round((terminal_stage_count / total_stages * 100) if total_stages else 0)
    current_stage_row = _select_current_stage(stage_rows)

    deadline = order.deadline
    days_to_deadline = (deadline - today).days if deadline else None
    planned_completion = max(
        (row["planned_end"] for row in stage_rows if row["planned_end"]),
        default=None,
    )

    risk_reasons = []
    risk_level = "healthy"

    if deadline and deadline < today:
        risk_level = "critical"
        risk_reasons.append("Дедлайн уже просрочен")

    if overdue_stage_count:
        risk_level = "critical"
        risk_reasons.append(f"Просрочено этапов: {overdue_stage_count}")

    if planned_completion and deadline and planned_completion.date() > deadline and risk_level != "critical":
        risk_level = "warning"
        risk_reasons.append("Плановое завершение позже дедлайна")

    if blocked_stage_count and risk_level == "healthy":
        risk_level = "warning"
        risk_reasons.append("Есть заблокированный этап")

    if deadline and days_to_deadline is not None and days_to_deadline <= 2 and progress_percent < 100:
        if risk_level == "healthy":
            risk_level = "warning"
        risk_reasons.append("Близкий дедлайн")

    if deadline and days_to_deadline is not None and days_to_deadline <= 5 and unplanned_stage_count:
        if risk_level == "healthy":
            risk_level = "warning"
        risk_reasons.append("Есть незапланированные этапы перед дедлайном")

    if not risk_reasons:
        risk_reasons.append("Риск не выявлен")

    risk_label = {
        "healthy": "Норма",
        "warning": "Ризик",
        "critical": "Критично",
    }[risk_level]

    return {
        "order": order,
        "stage_rows": stage_rows,
        "current_stage_row": current_stage_row,
        "current_stage": current_stage_row["stage"] if current_stage_row else None,
        "current_stage_label": (
            current_stage_row["stage"].get_stage_type_display() if current_stage_row else "Без активного этапа"
        ),
        "current_resource_label": current_stage_row["resource_label"] if current_stage_row else "",
        "current_responsible": (
            current_stage_row["stage"].responsible if current_stage_row and current_stage_row["stage"].responsible else None
        ),
        "total_stages": total_stages,
        "terminal_stage_count": terminal_stage_count,
        "done_stage_count": done_stage_count,
        "open_stage_count": open_stage_count,
        "blocked_stage_count": blocked_stage_count,
        "overdue_stage_count": overdue_stage_count,
        "unplanned_stage_count": unplanned_stage_count,
        "progress_percent": progress_percent,
        "deadline": deadline,
        "days_to_deadline": days_to_deadline,
        "planned_completion": planned_completion,
        "risk_level": risk_level,
        "risk_label": risk_label,
        "risk_reasons": risk_reasons,
        "is_at_risk": risk_level in {"warning", "critical"},
    }


def build_orders_in_work_report(
    *,
    now=None,
    manager_id=None,
    responsible_id=None,
    status=None,
    risk_only=False,
    user=None,
):
    from crm.models import Order

    now = now or timezone.now()
    today = timezone.localdate()

    queryset = filter_orders_queryset(
        user,
        Order.objects.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])
        .select_related("contact", "contact__client", "manager")
        .order_by("deadline", "-created_at", "id"),
    )
    if user is None:
        queryset = (
            Order.objects.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED])
            .select_related("contact", "contact__client", "manager")
            .order_by("deadline", "-created_at", "id")
        )
    if manager_id:
        queryset = queryset.filter(manager_id=manager_id)
    if status:
        queryset = queryset.filter(status=status)

    rows = []
    for order in queryset:
        row = build_order_row(order, now=now, today=today)
        if responsible_id and (not row["current_responsible"] or str(row["current_responsible"].pk) != str(responsible_id)):
            continue
        if risk_only and not row["is_at_risk"]:
            continue
        rows.append(row)

    rows.sort(
        key=lambda row: (
            0 if row["risk_level"] == "critical" else 1 if row["risk_level"] == "warning" else 2,
            row["deadline"] or (today + timedelta(days=3650)),
            row["progress_percent"],
            row["order"].pk,
        )
    )
    return rows


def update_stage_status(stage, status, *, note=""):
    allowed_statuses = {
        ProductionStage.Status.IN_PROGRESS,
        ProductionStage.Status.DONE,
        ProductionStage.Status.CANCELLED,
        ProductionStage.Status.BLOCKED,
        ProductionStage.Status.SCHEDULED,
    }
    if status not in allowed_statuses:
        raise ValidationError("Непідтримуваний статус етапу.")

    with planner_execution(), transaction.atomic():
        stage = ProductionStage.objects.select_related("order_item", "order_item__order").get(pk=stage.pk)
        now = timezone.now()
        changed_fields = []

        if status == ProductionStage.Status.IN_PROGRESS:
            if stage.started_at is None:
                stage.started_at = now
                changed_fields.append("started_at")
            if stage.completed_at is not None:
                stage.completed_at = None
                changed_fields.append("completed_at")
        elif status == ProductionStage.Status.DONE:
            if stage.started_at is None:
                stage.started_at = get_stage_effective_start(stage) or now
                changed_fields.append("started_at")
            if stage.completed_at != now:
                stage.completed_at = now
                changed_fields.append("completed_at")
            if stage.planned_end is None:
                stage.planned_end = now
                changed_fields.append("planned_end")
        elif status == ProductionStage.Status.CANCELLED:
            for slot in list(stage.slots.all()):
                slot._history_source = "system"
                slot._history_note = note or "Слот видалено через скасування етапу."
                slot.delete()
            if stage.planned_start is not None:
                stage.planned_start = None
                changed_fields.append("planned_start")
            if stage.planned_end is not None:
                stage.planned_end = None
                changed_fields.append("planned_end")
            if stage.completed_at is None:
                stage.completed_at = now
                changed_fields.append("completed_at")
        elif status == ProductionStage.Status.BLOCKED:
            if stage.completed_at is not None:
                stage.completed_at = None
                changed_fields.append("completed_at")

        if stage.status != status:
            stage.status = status
            changed_fields.append("status")

        if changed_fields:
            stage.save(update_fields=changed_fields + ["updated_at"])

    request_replan_open_orders()
    return stage


def validate_production_slot(slot):
    resource = slot.resource
    if resource is None:
        raise ValidationError("Для слота потрібно вказати ресурс.")
    if not resource.is_active:
        raise ValidationError(
            "Ресурс неактивний і недоступний для планування. Активуйте ресурс або виберіть інший."
        )

    start_local = timezone.localtime(_ensure_aware(slot.start_datetime))
    end_local = timezone.localtime(_ensure_aware(slot.end_datetime))
    if start_local.date() != end_local.date():
        raise ValidationError(
            "Слот має бути в межах одного робочого дня. Розбийте роботу на окремі слоти."
        )

    weekdays = resource.get_available_weekdays_set()
    if start_local.weekday() not in weekdays:
        raise ValidationError(
            "На вибраний день ресурс недоступний. Перенесіть слот на робочий день або змініть календар ресурсу."
        )

    workday_start, workday_end = resource.get_workday_bounds()
    if start_local.time() < workday_start or end_local.time() > workday_end:
        raise ValidationError(
            "Слот виходить за межі робочого часу ресурсу. Перенесіть його в робоче вікно."
        )

    conflicting_slot = next(
        iter(get_resource_slot_conflicts(resource, slot.start_datetime, slot.end_datetime, exclude_slot_id=slot.pk)),
        None,
    )
    if conflicting_slot:
        raise ValidationError(
            f"Ресурс уже зайнятий слотом #{conflicting_slot.pk}. Перенесіть слот у вільне вікно або виберіть інший ресурс."
        )

    downtime = next(iter(get_resource_downtime_conflicts(resource, slot.start_datetime, slot.end_datetime)), None)
    if downtime:
        raise ValidationError(
            f"На ресурсі заплановано '{downtime.get_downtime_type_display()}'. Перенесіть слот або зніміть блокування."
        )

    if slot.stage_id:
        previous_stage = (
            ProductionStage.objects.filter(order_item=slot.stage.order_item, sequence__lt=slot.stage.sequence)
            .order_by("-sequence", "-id")
            .first()
        )
        if previous_stage:
            previous_end = get_stage_effective_end(previous_stage)
            if previous_end and slot.start_datetime < previous_end:
                raise ValidationError(
                    f"Попередній етап '{previous_stage.get_stage_type_display()}' ще не завершений. "
                    "Перенесіть слот у наступне вільне вікно."
                )

        next_stage = (
            ProductionStage.objects.filter(order_item=slot.stage.order_item, sequence__gt=slot.stage.sequence)
            .order_by("sequence", "id")
            .first()
        )
        if next_stage:
            next_start = get_stage_effective_start(next_stage)
            next_stage_is_fixed = (
                next_stage.status
                in {
                    ProductionStage.Status.IN_PROGRESS,
                    ProductionStage.Status.DONE,
                    ProductionStage.Status.CANCELLED,
                }
                or next_stage.slots.filter(
                    Q(planning_mode=ProductionSlot.PlanningMode.MANUAL) | Q(is_locked=True)
                ).exists()
            )
            if next_start and next_stage_is_fixed and slot.end_datetime > next_start:
                raise ValidationError(
                    f"Наступний етап '{next_stage.get_stage_type_display()}' уже запланований раніше. "
                    "Зсуньте поточний слот або переплануйте наступний."
                )


def estimate_stage_duration(stage):
    quantity = max(stage.order_item.quantity or 1, 1)
    base_hours = {
        ProductionStage.StageType.INTAKE: 1.0,
        ProductionStage.StageType.PROCUREMENT: 1.5,
        ProductionStage.StageType.EXECUTION: 2.5,
        ProductionStage.StageType.PAINTING: 1.5,
        ProductionStage.StageType.READY_TO_SHIP: 1.0,
    }[stage.stage_type]
    extra_per_item = {
        ProductionStage.StageType.INTAKE: 0.0,
        ProductionStage.StageType.PROCUREMENT: 0.1,
        ProductionStage.StageType.EXECUTION: 0.4,
        ProductionStage.StageType.PAINTING: 0.25,
        ProductionStage.StageType.READY_TO_SHIP: 0.1,
    }[stage.stage_type]
    hours = base_hours + max(quantity - 1, 0) * extra_per_item
    hours = min(hours, 6.0)
    return timedelta(minutes=int(hours * 60))


def _resource_candidates_for_stage(stage):
    machines = Machine.objects.filter(is_active=True).order_by("type", "name")
    work_units = WorkUnit.objects.filter(is_active=True).order_by("type", "name")

    if stage.stage_type == ProductionStage.StageType.PAINTING:
        preferred = [("machine", resource) for resource in machines.filter(type=Machine.MachineType.PAINTING)]
        preferred += [("work_unit", resource) for resource in work_units.filter(type=WorkUnit.UnitType.PAINTING)]
        return preferred or [("machine", resource) for resource in machines] or [
            ("work_unit", resource) for resource in work_units
        ]

    if stage.stage_type == ProductionStage.StageType.EXECUTION:
        preferred = [
            ("machine", resource)
            for resource in machines.exclude(type=Machine.MachineType.PAINTING)
        ]
        preferred += [
            ("work_unit", resource)
            for resource in work_units.filter(type__in=[WorkUnit.UnitType.WELDING, WorkUnit.UnitType.ASSEMBLY, WorkUnit.UnitType.OTHER])
        ]
        return preferred or [("machine", resource) for resource in machines] or [
            ("work_unit", resource) for resource in work_units
        ]

    if stage.stage_type == ProductionStage.StageType.PROCUREMENT:
        preferred = [
            ("work_unit", resource)
            for resource in work_units.filter(type__in=[WorkUnit.UnitType.STORAGE, WorkUnit.UnitType.OTHER])
        ]
        return preferred or [("work_unit", resource) for resource in work_units] or [
            ("machine", resource) for resource in machines
        ]

    if stage.stage_type == ProductionStage.StageType.READY_TO_SHIP:
        preferred = [
            ("work_unit", resource)
            for resource in work_units.filter(type__in=[WorkUnit.UnitType.STORAGE, WorkUnit.UnitType.ASSEMBLY, WorkUnit.UnitType.OTHER])
        ]
        return preferred or [("work_unit", resource) for resource in work_units] or [
            ("machine", resource) for resource in machines
        ]

    preferred = [
        ("work_unit", resource)
        for resource in work_units.filter(type__in=[WorkUnit.UnitType.ASSEMBLY, WorkUnit.UnitType.STORAGE, WorkUnit.UnitType.OTHER])
    ]
    return preferred or [("work_unit", resource) for resource in work_units] or [
        ("machine", resource) for resource in machines
    ]


def find_next_available_window(resource, start_from, duration, *, exclude_slot_id=None, horizon_days=PLANNING_HORIZON_DAYS):
    start_from = _ensure_aware(start_from)
    current_date = timezone.localtime(start_from).date()
    for offset in range(horizon_days + 1):
        day = current_date + timedelta(days=offset)
        work_window = get_resource_work_window(resource, day)
        if not work_window:
            continue

        window_start, window_end = work_window
        candidate_start = max(window_start, start_from)
        if candidate_start >= window_end:
            continue

        unavailable = get_resource_unavailable_intervals(
            resource,
            candidate_start,
            window_end,
            exclude_slot_id=exclude_slot_id,
        )
        pointer = candidate_start
        for blocked_start, blocked_end in unavailable:
            if pointer + duration <= blocked_start:
                return pointer, pointer + duration
            if blocked_end > pointer:
                pointer = blocked_end
        if pointer + duration <= window_end:
            return pointer, pointer + duration
    return None


def sync_stage_schedule_from_slots(stage, *, save=True):
    slots = stage.slots.exclude(start_datetime__isnull=True).exclude(end_datetime__isnull=True).order_by("start_datetime", "id")
    planned_start = slots.first().start_datetime if slots.exists() else None
    planned_end = slots.order_by("-end_datetime", "-id").first().end_datetime if slots.exists() else None

    next_status = stage.status
    if planned_start and planned_end and stage.status in {ProductionStage.Status.NEW, ProductionStage.Status.BLOCKED}:
        next_status = ProductionStage.Status.SCHEDULED
    elif not planned_start and not planned_end and stage.status == ProductionStage.Status.SCHEDULED:
        next_status = ProductionStage.Status.NEW

    changed_fields = []
    if planned_start != stage.planned_start:
        stage.planned_start = planned_start
        changed_fields.append("planned_start")
    if planned_end != stage.planned_end:
        stage.planned_end = planned_end
        changed_fields.append("planned_end")
    if next_status != stage.status:
        stage.status = next_status
        changed_fields.append("status")
    if changed_fields and save:
        stage.save(update_fields=changed_fields + ["updated_at"])
    return stage


def _stage_has_fixed_schedule(stage):
    if stage.status in {
        ProductionStage.Status.IN_PROGRESS,
        ProductionStage.Status.DONE,
        ProductionStage.Status.CANCELLED,
    }:
        return True
    return stage.slots.filter(Q(planning_mode=ProductionSlot.PlanningMode.MANUAL) | Q(is_locked=True)).exists()


def _ensure_stage_blocked(stage):
    changed_fields = []
    if stage.planned_start is not None:
        stage.planned_start = None
        changed_fields.append("planned_start")
    if stage.planned_end is not None:
        stage.planned_end = None
        changed_fields.append("planned_end")
    if stage.status != ProductionStage.Status.BLOCKED:
        stage.status = ProductionStage.Status.BLOCKED
        changed_fields.append("status")
    if changed_fields:
        stage.save(update_fields=changed_fields + ["updated_at"])


def _schedule_stage(stage, start_from):
    if _stage_has_fixed_schedule(stage):
        sync_stage_schedule_from_slots(stage, save=True)
        return get_stage_effective_end(stage) or start_from

    duration = estimate_stage_duration(stage)
    best_choice = None
    for field_name, resource in _resource_candidates_for_stage(stage):
        candidate = find_next_available_window(resource, start_from, duration)
        if not candidate:
            continue
        candidate_start, candidate_end = candidate
        if best_choice is None or candidate_start < best_choice["start"]:
            best_choice = {
                "field_name": field_name,
                "resource": resource,
                "start": candidate_start,
                "end": candidate_end,
            }

    if best_choice is None:
        ProductionSlot.objects.filter(
            stage=stage,
            planning_mode=ProductionSlot.PlanningMode.AUTO,
            is_locked=False,
        ).delete()
        _ensure_stage_blocked(stage)
        return start_from

    auto_slots = list(
        stage.slots.filter(
            planning_mode=ProductionSlot.PlanningMode.AUTO,
            is_locked=False,
        ).order_by("id")
    )
    current_slot = auto_slots[0] if auto_slots else ProductionSlot(order=stage.order, stage=stage)
    for redundant_slot in auto_slots[1:]:
        redundant_slot.delete()

    current_slot.order = stage.order
    current_slot.stage = stage
    current_slot.start_datetime = best_choice["start"]
    current_slot.end_datetime = best_choice["end"]
    current_slot.slot_type = ProductionSlot.SlotType.WORK
    current_slot.operation_type = get_stage_operation_type(stage)
    current_slot.planning_mode = ProductionSlot.PlanningMode.AUTO
    current_slot.planning_source = ProductionSlot.PlanningSource.PLANNER
    current_slot.is_locked = False
    current_slot.purpose = get_stage_purpose(stage)
    current_slot.comment = f"Автопланування: {stage.get_stage_type_display()}"
    current_slot.dispatcher_comment = ""
    if best_choice["field_name"] == "machine":
        current_slot.machine = best_choice["resource"]
        current_slot.work_unit = None
    else:
        current_slot.work_unit = best_choice["resource"]
        current_slot.machine = None
    current_slot._history_source = "auto"
    current_slot._planner_operation = True
    current_slot.save()
    sync_stage_schedule_from_slots(stage, save=True)
    return current_slot.end_datetime


def _priority_rank(order):
    priority_value = getattr(order, "priority", DEFAULT_PRIORITY_VALUE)
    return {
        "urgent": 0,
        "high": 1,
        "normal": 2,
        "low": 3,
    }.get(priority_value, 2)


def get_plannable_orders():
    from crm.models import Order

    orders = list(
        Order.objects.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELED]).select_related("contact")
    )
    orders.sort(
        key=lambda order: (
            _priority_rank(order),
            order.deadline is None,
            order.deadline or timezone.localdate() + timedelta(days=3650),
            order.created_at,
            order.pk,
        )
    )
    return orders


def replan_open_orders():
    from crm.services import sync_order_status_from_production

    if planner_is_active():
        return

    orders = get_plannable_orders()
    if not orders:
        return

    with planner_execution(), transaction.atomic():
        auto_slots = ProductionSlot.objects.filter(
            order__in=orders,
            planning_mode=ProductionSlot.PlanningMode.AUTO,
            is_locked=False,
        ).exclude(
            stage__status__in=[
                ProductionStage.Status.IN_PROGRESS,
                ProductionStage.Status.DONE,
                ProductionStage.Status.CANCELLED,
            ]
        )
        for slot in auto_slots:
            slot.delete()

        now = timezone.now()
        for order in orders:
            order_stages = list(
                ProductionStage.objects.filter(order_item__order=order)
                .select_related("order_item", "order_item__product")
                .order_by("order_item_id", "sequence", "id")
            )
            cursor = now
            for stage in order_stages:
                cursor = _schedule_stage(stage, cursor)
            sync_order_status_from_production(order, save=True)
