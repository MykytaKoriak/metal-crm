from datetime import datetime, time, timedelta
from django.shortcuts import get_object_or_404, render
from django.utils.timezone import make_aware, get_current_timezone
from .models import Machine, WorkUnit, ProductionSlot


def machine_load_report(request):
    tz = get_current_timezone()
    today = datetime.now(tz).date()

    # Періоди
    ranges = {
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

    # Загальна функція розрахунку завантаженості
    def calc_load(resource, start, end, field_name):
        """
        resource — Machine або WorkUnit
        field_name — 'machine' або 'work_unit'
        """
        slots = ProductionSlot.objects.filter(
            **{field_name: resource},
            start_datetime__lt=end,
            end_datetime__gt=start
        )

        # визначення тривалості робочого дня
        if hasattr(resource, "workday_start") and resource.workday_start:
            day_start = resource.workday_start
            day_end = resource.workday_end
        else:
            day_start = time(8, 0)
            day_end = time(17, 0)

        workday_hours = (
                                datetime.combine(today, day_end) -
                                datetime.combine(today, day_start)
                        ).seconds / 3600

        busy_seconds = 0
        for slot in slots:
            s = max(slot.start_datetime, start)
            e = min(slot.end_datetime, end)
            busy_seconds += max(0, (e - s).total_seconds())

        days = (end.date() - start.date()).days + 1
        total_available = workday_hours * days

        if total_available <= 0:
            return 0

        return round((busy_seconds / 3600) / total_available * 100)

    # Формуємо звіт
    machine_report = []
    for machine in Machine.objects.all():
        row = {
            "id": machine.id,
            "name": machine.name,
            "type": machine.get_type_display(),
            "today": calc_load(machine, *ranges["today"], "machine"),
            "three_days": calc_load(machine, *ranges["three_days"], "machine"),
            "week": calc_load(machine, *ranges["week"], "machine"),
        }
        row["status"] = (
            "green" if row["week"] < 70 else
            "yellow" if row["week"] < 90 else
            "red"
        )
        machine_report.append(row)

    workunit_report = []
    for unit in WorkUnit.objects.all():
        row = {
            "id": unit.id,
            "name": unit.name,
            "type": unit.get_type_display(),
            "today": calc_load(unit, *ranges["today"], "work_unit"),
            "three_days": calc_load(unit, *ranges["three_days"], "work_unit"),
            "week": calc_load(unit, *ranges["week"], "work_unit"),
        }
        row["status"] = (
            "green" if row["week"] < 70 else
            "yellow" if row["week"] < 90 else
            "red"
        )
        workunit_report.append(row)

    return render(request, "machine_load_report.html", {
        "machine_report": machine_report,
        "workunit_report": workunit_report,
    })


def machine_detail_report(request, machine_id):
    tz = get_current_timezone()
    today = datetime.now(tz).date()
    start_period = make_aware(datetime.combine(today, time.min))
    end_period = make_aware(datetime.combine(today + timedelta(days=7), time.max))

    machine = get_object_or_404(Machine, pk=machine_id)

    # робочий день
    if machine.workday_start and machine.workday_end:
        day_start_time = machine.workday_start
        day_end_time = machine.workday_end
    else:
        # дефолт 08:00–17:00, якщо не задано
        day_start_time = time(8, 0)
        day_end_time = time(17, 0)

    days = []

    for i in range(8):  # сьогодні + 7 днів
        day = today + timedelta(days=i)
        day_start = make_aware(datetime.combine(day, day_start_time))
        day_end = make_aware(datetime.combine(day, day_end_time))

        # слоти для цього верстата, які хоч якось перетинають день
        slots_qs = ProductionSlot.objects.filter(
            machine=machine,
            start_datetime__lt=day_end,
            end_datetime__gt=day_start,
        ).select_related("order")

        # приводимо до відрізків всередині робочого дня
        intervals = []
        for slot in slots_qs:
            s = max(slot.start_datetime, day_start)
            e = min(slot.end_datetime, day_end)
            if s < e:
                intervals.append((s, e, slot))

        # сортуємо по часу початку
        intervals.sort(key=lambda x: x[0])

        # шукаємо вільні проміжки
        free_intervals = []
        current = day_start
        for s, e, slot in intervals:
            if s > current:
                free_intervals.append((current, s))
            if e > current:
                current = e
        if current < day_end:
            free_intervals.append((current, day_end))

        days.append({
            "date": day,
            "slots": intervals,  # список (start, end, slot)
            "free": free_intervals,  # список (start, end)
        })

    context = {
        "machine": machine,
        "days": days,
    }
    return render(request, "machine_detail_report.html", context)


def workunit_detail_report(request, workunit_id):
    tz = get_current_timezone()
    today = datetime.now(tz).date()

    work_unit = get_object_or_404(WorkUnit, pk=workunit_id)

    # робочий день для дільниці
    # (якщо захочеш – можна додати workday_start/workday_end і сюди, зараз беремо дефолт 08–17)
    day_start_time = time(8, 0)
    day_end_time = time(17, 0)

    days = []

    for i in range(8):  # сьогодні + 7 днів
        day = today + timedelta(days=i)
        day_start = make_aware(datetime.combine(day, day_start_time))
        day_end = make_aware(datetime.combine(day, day_end_time))

        # слоти для цієї дільниці, які хоч якось перетинають день
        slots_qs = ProductionSlot.objects.filter(
            work_unit=work_unit,
            start_datetime__lt=day_end,
            end_datetime__gt=day_start,
        ).select_related("order")

        # приводимо до відрізків всередині робочого дня
        intervals = []
        for slot in slots_qs:
            s = max(slot.start_datetime, day_start)
            e = min(slot.end_datetime, day_end)
            if s < e:
                intervals.append((s, e, slot))

        # сортуємо по часу початку
        intervals.sort(key=lambda x: x[0])

        # шукаємо вільні проміжки
        free_intervals = []
        current = day_start
        for s, e, slot in intervals:
            if s > current:
                free_intervals.append((current, s))
            if e > current:
                current = e
        if current < day_end:
            free_intervals.append((current, day_end))

        days.append({
            "date": day,
            "slots": intervals,  # список (start, end, slot)
            "free": free_intervals,  # список (start, end)
        })

    context = {
        "work_unit": work_unit,
        "days": days,
    }

    # якщо machine_detail_report рендериш як "machine_detail_report.html" без префікса,
    # зроби тут аналогічно: "workunit_detail_report.html"
    return render(request, "workunit_detail_report.html", context)
