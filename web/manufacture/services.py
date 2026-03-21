from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import local

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

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
        "machine_id": slot.machine_id,
        "work_unit_id": slot.work_unit_id,
        "start_datetime": slot.start_datetime.isoformat() if slot.start_datetime else None,
        "end_datetime": slot.end_datetime.isoformat() if slot.end_datetime else None,
        "planning_mode": slot.planning_mode,
        "is_locked": slot.is_locked,
        "comment": slot.comment,
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


def get_resource_day_plan(resource, current_date):
    window = get_resource_work_window(resource, current_date)
    if not window:
        return {
            "work_window": None,
            "busy": [],
            "blocks": [],
            "free": [],
        }

    day_start, day_end = window
    busy = [
        (max(slot.start_datetime, day_start), min(slot.end_datetime, day_end), slot)
        for slot in get_resource_slot_conflicts(resource, day_start, day_end)
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


def get_resource_busy_seconds(resource, start, end):
    start = _ensure_aware(start)
    end = _ensure_aware(end)
    intervals = [
        (max(slot.start_datetime, start), min(slot.end_datetime, end))
        for slot in get_resource_slot_conflicts(resource, start, end)
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
                next_stage.status in {ProductionStage.Status.IN_PROGRESS, ProductionStage.Status.DONE}
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
    if stage.status in {ProductionStage.Status.IN_PROGRESS, ProductionStage.Status.DONE}:
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
    current_slot.planning_mode = ProductionSlot.PlanningMode.AUTO
    current_slot.is_locked = False
    current_slot.comment = f"Автопланування: {stage.get_stage_type_display()}"
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
        ).exclude(stage__status__in=[ProductionStage.Status.IN_PROGRESS, ProductionStage.Status.DONE])
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
