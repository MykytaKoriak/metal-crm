from datetime import datetime, time, timedelta

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.timezone import get_current_timezone, localtime, make_aware

from core.access import INTERNAL_ROLES, roles_required

from .models import Machine, ProductionSlot, ResourceDowntime, WorkUnit
from .services import get_resource_busy_seconds, get_resource_capacity_seconds, get_resource_day_plan


def _build_ranges(today):
    return {
        "today": (
            make_aware(datetime.combine(today, time.min)),
            make_aware(datetime.combine(today, time.max)),
        ),
        "three_days": (
            make_aware(datetime.combine(today, time.min)),
            make_aware(datetime.combine(today + timedelta(days=3), time.max)),
        ),
        "week": (
            make_aware(datetime.combine(today, time.min)),
            make_aware(datetime.combine(today + timedelta(days=7), time.max)),
        ),
    }


def _calc_load(resource, start, end):
    capacity_seconds = get_resource_capacity_seconds(resource, start, end)
    if capacity_seconds <= 0:
        return 0
    busy_seconds = get_resource_busy_seconds(resource, start, end)
    return round(busy_seconds / capacity_seconds * 100)


def _resource_status(resource, load_percent):
    if not resource.is_active:
        return "red"
    if load_percent < 70:
        return "green"
    if load_percent < 90:
        return "yellow"
    return "red"


@roles_required(*INTERNAL_ROLES)
def machine_load_report(request):
    tz = get_current_timezone()
    today = datetime.now(tz).date()
    ranges = _build_ranges(today)

    machine_report = []
    for machine in Machine.objects.all():
        row = {
            "id": machine.id,
            "name": machine.name,
            "type": machine.get_type_display(),
            "today": _calc_load(machine, *ranges["today"]),
            "three_days": _calc_load(machine, *ranges["three_days"]),
            "week": _calc_load(machine, *ranges["week"]),
            "is_active": machine.is_active,
        }
        row["status"] = _resource_status(machine, row["week"])
        machine_report.append(row)

    workunit_report = []
    for unit in WorkUnit.objects.all():
        row = {
            "id": unit.id,
            "name": unit.name,
            "type": unit.get_type_display(),
            "today": _calc_load(unit, *ranges["today"]),
            "three_days": _calc_load(unit, *ranges["three_days"]),
            "week": _calc_load(unit, *ranges["week"]),
            "is_active": unit.is_active,
        }
        row["status"] = _resource_status(unit, row["week"])
        workunit_report.append(row)

    return render(
        request,
        "machine_load_report.html",
        {
            "machine_report": machine_report,
            "workunit_report": workunit_report,
        },
    )


@roles_required(*INTERNAL_ROLES)
def machine_detail_report(request, machine_id):
    tz = get_current_timezone()
    today = datetime.now(tz).date()
    machine = get_object_or_404(Machine, pk=machine_id)

    days = []
    for offset in range(8):
        day = today + timedelta(days=offset)
        plan = get_resource_day_plan(machine, day)
        days.append(
            {
                "date": day,
                "slots": plan["busy"],
                "blocks": plan["blocks"],
                "free": plan["free"],
                "work_window": plan["work_window"],
            }
        )

    return render(
        request,
        "machine_detail_report.html",
        {
            "machine": machine,
            "days": days,
        },
    )


@roles_required(*INTERNAL_ROLES)
def workunit_detail_report(request, workunit_id):
    tz = get_current_timezone()
    today = datetime.now(tz).date()
    work_unit = get_object_or_404(WorkUnit, pk=workunit_id)

    days = []
    for offset in range(8):
        day = today + timedelta(days=offset)
        plan = get_resource_day_plan(work_unit, day)
        days.append(
            {
                "date": day,
                "slots": plan["busy"],
                "blocks": plan["blocks"],
                "free": plan["free"],
                "work_window": plan["work_window"],
            }
        )

    return render(
        request,
        "workunit_detail_report.html",
        {
            "work_unit": work_unit,
            "days": days,
        },
    )


@roles_required(*INTERNAL_ROLES)
def production_slot_events(request):
    slot_qs = (
        ProductionSlot.objects.exclude(start_datetime__isnull=True)
        .exclude(end_datetime__isnull=True)
        .select_related("order", "stage", "stage__order_item", "stage__order_item__product", "machine", "work_unit")
    )
    downtime_qs = ResourceDowntime.objects.filter(is_blocking=True).select_related("machine", "work_unit")

    events = []
    for slot in slot_qs:
        location = slot.machine or slot.work_unit
        title = f"{slot.order}"
        if slot.stage_id:
            title = f"{slot.stage.order_item.product.name} / {slot.stage.get_stage_type_display()}"
        if location:
            title += f" – {location}"

        events.append(
            {
                "id": str(slot.id),
                "kind": "slot",
                "title": title,
                "start": localtime(slot.start_datetime).isoformat(),
                "end": localtime(slot.end_datetime).isoformat(),
            }
        )

    for downtime in downtime_qs:
        resource = downtime.resource
        events.append(
            {
                "id": f"downtime-{downtime.id}",
                "kind": "downtime",
                "title": f"{resource}: {downtime.get_downtime_type_display()}",
                "start": localtime(downtime.start_datetime).isoformat(),
                "end": localtime(downtime.end_datetime).isoformat(),
                "backgroundColor": "#f4c7c3",
                "borderColor": "#d92d20",
                "textColor": "#7a271a",
            }
        )

    return JsonResponse(events, safe=False)
