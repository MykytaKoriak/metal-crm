from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import get_current_timezone, localtime, make_aware

from core.access import INTERNAL_ROLES, roles_required
from core.models import UserProfile
from core.visibility import (
    filter_orders_queryset,
    filter_slots_queryset,
    filter_stages_queryset,
    visible_manager_choices_queryset,
    visible_responsible_choices_queryset,
)
from crm.models import Order

from .models import Machine, ProductionSlot, ProductionStage, ResourceDowntime, WorkUnit
from .services import (
    build_free_slot_report,
    build_orders_in_work_report,
    build_overdue_stage_report,
    get_resource_busy_seconds,
    get_resource_capacity_seconds,
    get_resource_day_plan,
    update_stage_status,
)


def _dashboard_shell_context(request, active_section="dashboard"):
    profile = UserProfile.objects.get_or_create(user=request.user)[0]
    return {
        "profile": profile,
        "active_section": active_section,
    }


def _parse_date(value, default):
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return default


def _parse_int(value, default, *, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _normalize_resource_filters(request):
    resource_kind = request.GET.get("resource_kind", "all")
    if resource_kind not in {"all", "machine", "work_unit"}:
        resource_kind = "all"
    resource_id = request.GET.get("resource_id", "").strip()
    if resource_kind == "all":
        resource_id = ""
    return resource_kind, resource_id


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
    visible_slots = filter_slots_queryset(
        request.user,
        ProductionSlot.objects.exclude(start_datetime__isnull=True)
        .exclude(end_datetime__isnull=True),
    )

    days = []
    for offset in range(8):
        day = today + timedelta(days=offset)
        plan = get_resource_day_plan(machine, day, slot_queryset=visible_slots)
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
    visible_slots = filter_slots_queryset(
        request.user,
        ProductionSlot.objects.exclude(start_datetime__isnull=True)
        .exclude(end_datetime__isnull=True),
    )

    days = []
    for offset in range(8):
        day = today + timedelta(days=offset)
        plan = get_resource_day_plan(work_unit, day, slot_queryset=visible_slots)
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
def production_free_slot_report(request):
    resource_kind, resource_id = _normalize_resource_filters(request)
    start_date = _parse_date(request.GET.get("date_from"), timezone.localdate())
    days = _parse_int(request.GET.get("days"), 7, minimum=1, maximum=31)
    min_hours = _parse_int(request.GET.get("min_hours"), 1, minimum=0, maximum=12)
    active_only = request.GET.get("active_only", "1") == "1"

    report = build_free_slot_report(
        date_from=start_date,
        days=days,
        resource_kind=resource_kind,
        resource_id=resource_id or None,
        min_duration_minutes=min_hours * 60,
        active_only=active_only,
    )

    context = _dashboard_shell_context(request)
    context.update(
        {
            "filters": {
                "resource_kind": resource_kind,
                "resource_id": resource_id,
                "date_from": start_date,
                "days": days,
                "min_hours": min_hours,
                "active_only": active_only,
            },
            "report": report,
            "rows": report["rows"],
            "resource_choices": report["resource_choices"],
            "summary": {
                "window_count": len(report["rows"]),
                "total_hours": round(sum(row["duration_hours"] for row in report["rows"]), 1),
            },
        }
    )
    return render(request, "manufacture/free_slot_report.html", context)


@roles_required(*INTERNAL_ROLES)
def production_overdue_stage_report(request):
    resource_kind, resource_id = _normalize_resource_filters(request)
    report = build_overdue_stage_report(
        now=timezone.now(),
        resource_kind=resource_kind,
        resource_id=resource_id or None,
        user=request.user,
    )

    context = _dashboard_shell_context(request)
    context.update(
        {
            "filters": {
                "resource_kind": resource_kind,
                "resource_id": resource_id,
            },
            "rows": report["rows"],
            "resource_choices": report["resource_choices"],
            "summary": {
                "stage_count": len(report["rows"]),
                "max_overdue_hours": max((row["overdue_hours"] for row in report["rows"]), default=0),
            },
        }
    )
    return render(request, "manufacture/overdue_stage_report.html", context)


@roles_required(*INTERNAL_ROLES)
def production_orders_in_work_report(request):
    manager_id = request.GET.get("manager_id", "").strip()
    responsible_id = request.GET.get("responsible_id", "").strip()
    order_status = request.GET.get("status", "").strip()
    risk_only = request.GET.get("risk_only") == "1"

    rows = build_orders_in_work_report(
        now=timezone.now(),
        manager_id=manager_id or None,
        responsible_id=responsible_id or None,
        status=order_status or None,
        risk_only=risk_only,
        user=request.user,
    )

    users = get_user_model().objects.filter(is_active=True).order_by("email", "username")
    manager_choices = visible_manager_choices_queryset(
        request.user,
        users.filter(managed_orders__isnull=False).distinct(),
    )
    responsible_choices = visible_responsible_choices_queryset(
        request.user,
        users.filter(production_stages__isnull=False).distinct(),
    )

    context = _dashboard_shell_context(request)
    context.update(
        {
            "filters": {
                "manager_id": manager_id,
                "responsible_id": responsible_id,
                "status": order_status,
                "risk_only": risk_only,
            },
            "rows": rows,
            "manager_choices": manager_choices,
            "responsible_choices": responsible_choices,
            "status_choices": Order.Status.choices,
            "summary": {
                "order_count": len(rows),
                "risk_count": sum(1 for row in rows if row["is_at_risk"]),
                "production_count": sum(1 for row in rows if row["order"].status == Order.Status.IN_PRODUCTION),
                "avg_progress": round(
                    (sum(row["progress_percent"] for row in rows) / len(rows)) if rows else 0,
                    1,
                ),
            },
        }
    )
    return render(request, "manufacture/orders_in_work_report.html", context)


@roles_required(UserProfile.Role.ADMIN, UserProfile.Role.PRODUCTION)
def production_stage_status_update(request, stage_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    stage = get_object_or_404(filter_stages_queryset(request.user, ProductionStage.objects.all()), pk=stage_id)
    next_url = request.POST.get("next") or "production_dashboard"
    status = request.POST.get("status", "").strip()
    note = request.POST.get("note", "").strip()
    stage._changed_by = request.user
    update_stage_status(stage, status, note=note)
    return redirect(next_url)


@roles_required(*INTERNAL_ROLES)
def production_slot_events(request):
    resource_kind, resource_id = _normalize_resource_filters(request)
    slot_qs = filter_slots_queryset(
        request.user,
        ProductionSlot.objects.exclude(start_datetime__isnull=True)
        .exclude(end_datetime__isnull=True)
        .select_related("order", "stage", "stage__order_item", "stage__order_item__product", "machine", "work_unit"),
    )
    downtime_qs = ResourceDowntime.objects.filter(is_blocking=True).select_related("machine", "work_unit")

    if resource_kind == "machine":
        slot_qs = slot_qs.filter(machine_id=resource_id) if resource_id else slot_qs.exclude(machine__isnull=True)
        downtime_qs = (
            downtime_qs.filter(machine_id=resource_id) if resource_id else downtime_qs.exclude(machine__isnull=True)
        )
    elif resource_kind == "work_unit":
        slot_qs = (
            slot_qs.filter(work_unit_id=resource_id) if resource_id else slot_qs.exclude(work_unit__isnull=True)
        )
        downtime_qs = (
            downtime_qs.filter(work_unit_id=resource_id)
            if resource_id
            else downtime_qs.exclude(work_unit__isnull=True)
        )

    events = []
    for slot in slot_qs:
        resource = slot.resource
        title = slot.purpose or f"{slot.order}"
        if resource:
            title += f" - {resource}"

        events.append(
            {
                "id": str(slot.id),
                "kind": "slot",
                "title": title,
                "start": localtime(slot.start_datetime).isoformat(),
                "end": localtime(slot.end_datetime).isoformat(),
                "backgroundColor": "#dbe7f6" if slot.is_automatic else "#e7f1ef",
                "borderColor": "#7a8aa0" if slot.is_automatic else "#1f5f57",
                "extendedProps": {
                    "slotType": slot.get_slot_type_display(),
                    "operationType": slot.get_operation_type_display(),
                    "planningMode": slot.get_planning_mode_display(),
                    "planningSource": slot.get_planning_source_display(),
                    "dispatcherComment": slot.dispatcher_comment,
                },
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
