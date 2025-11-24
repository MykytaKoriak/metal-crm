from datetime import datetime, time, timedelta
from django.shortcuts import get_object_or_404, render
from django.utils.timezone import make_aware, get_current_timezone
from .models import Machine, ProductionSlot


def machine_load_report(request):
    today = datetime.now().date()
    now = datetime.now()

    ranges = {
        "today": (make_aware(datetime.combine(today, time.min)),
                  make_aware(datetime.combine(today, time.max))),

        "three_days": (make_aware(datetime.combine(today, time.min)),
                       make_aware(datetime.combine(today + timedelta(days=3), time.max))),

        "week": (make_aware(datetime.combine(today, time.min)),
                 make_aware(datetime.combine(today + timedelta(days=7), time.max))),
    }

    report = []

    machines = Machine.objects.all()

    for machine in machines:
        row = {
            "machine": machine,
            "today": None,
            "three_days": None,
            "week": None,
            "status": None,
        }

        # тривалість робочого дня
        if machine.workday_start and machine.workday_end:
            workday_hours = (
                datetime.combine(today, machine.workday_end) -
                datetime.combine(today, machine.workday_start)
            ).seconds / 3600
        else:
            workday_hours = 8  # якщо не задано – дефолт

        def calc_load(start, end):
            slots = ProductionSlot.objects.filter(
                machine=machine,
                start_datetime__lt=end,
                end_datetime__gt=start
            )

            busy_seconds = 0
            for slot in slots:
                s = max(slot.start_datetime, start)
                e = min(slot.end_datetime, end)
                busy_seconds += max(0, (e - s).total_seconds())

            # доступний робочий час за період
            days = (end.date() - start.date()).days + 1
            available_hours = workday_hours * days

            if available_hours == 0:
                return 0

            return round((busy_seconds / 3600) / available_hours * 100)

        # розрахунок
        row["today"] = calc_load(*ranges["today"])
        row["three_days"] = calc_load(*ranges["three_days"])
        row["week"] = calc_load(*ranges["week"])

        # статус
        week_load = row["week"]
        if week_load < 70:
            row["status"] = "green"
        elif week_load < 90:
            row["status"] = "yellow"
        else:
            row["status"] = "red"

        report.append(row)

    return render(request, "machine_load_report.html", {
        "report": report
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
            "slots": intervals,       # список (start, end, slot)
            "free": free_intervals,   # список (start, end)
        })

    context = {
        "machine": machine,
        "days": days,
    }
    return render(request, "machine_detail_report.html", context)
