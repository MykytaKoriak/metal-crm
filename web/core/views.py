from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from crm.models import Task
from .models import UserProfile


@login_required
def my_account(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    today = timezone.localdate()

    base_tasks = (
        Task.objects.filter(assigned_to=request.user)
        .select_related("contact", "contact__client", "assigned_by", "assigned_to")
        .order_by("date", "id")
    )

    current_tasks = base_tasks.filter(status=False, date__gte=today)
    overdue_tasks = base_tasks.filter(status=False, date__lt=today)
    completed_tasks = base_tasks.filter(status=True)

    stats = base_tasks.aggregate(
        total_tasks=Count("id"),
        completed_tasks=Count("id", filter=Q(status=True)),
        open_tasks=Count("id", filter=Q(status=False)),
        overdue_tasks=Count("id", filter=Q(status=False, date__lt=today)),
        due_today_tasks=Count("id", filter=Q(status=False, date=today)),
    )
    stats["tasks_created_by_me"] = Task.objects.filter(assigned_by=request.user).count()
    stats["completion_rate"] = round(
        (stats["completed_tasks"] / stats["total_tasks"] * 100) if stats["total_tasks"] else 0,
        1,
    )

    context = {
        "profile": profile,
        "current_tasks": current_tasks,
        "overdue_tasks": overdue_tasks,
        "completed_tasks": completed_tasks,
        "stats": stats,
        "today": today,
    }
    return render(request, "core/my_account.html", context)
