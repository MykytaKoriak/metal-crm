from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from crm.models import Task

from .access import INTERNAL_ROLES, get_user_role, roles_required
from .dashboard import (
    get_admin_dashboard_context,
    get_executive_dashboard_context,
    get_production_dashboard_context,
    get_sales_dashboard_context,
)
from .models import UserProfile


ROLE_DASHBOARD_NAMES = {
    UserProfile.Role.ADMIN: "admin_dashboard",
    UserProfile.Role.SALES_MANAGER: "sales_dashboard",
    UserProfile.Role.PRODUCTION: "production_dashboard",
    UserProfile.Role.EXECUTIVE: "executive_dashboard",
}


def _get_profile(user):
    return UserProfile.objects.get_or_create(user=user)[0]


def _dashboard_shell_context(request, active_section="dashboard"):
    profile = _get_profile(request.user)
    return {
        "profile": profile,
        "active_section": active_section,
    }


@roles_required(*INTERNAL_ROLES)
def dashboard(request):
    role = get_user_role(request.user)
    return redirect(ROLE_DASHBOARD_NAMES.get(role, "my_account"))


@roles_required(*INTERNAL_ROLES)
def my_account(request):
    profile = _get_profile(request.user)
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
        "active_section": "account",
    }
    return render(request, "core/my_account.html", context)


@roles_required(UserProfile.Role.ADMIN)
def admin_dashboard(request):
    context = _dashboard_shell_context(request)
    context.update(get_admin_dashboard_context())
    return render(request, "core/admin_dashboard.html", context)


@roles_required(UserProfile.Role.SALES_MANAGER)
def sales_dashboard(request):
    context = _dashboard_shell_context(request)
    context.update(get_sales_dashboard_context(request.user))
    return render(request, "core/sales_dashboard.html", context)


@roles_required(UserProfile.Role.PRODUCTION)
def production_dashboard(request):
    context = _dashboard_shell_context(request)
    context.update(get_production_dashboard_context())
    return render(request, "core/production_dashboard.html", context)


@roles_required(UserProfile.Role.EXECUTIVE)
def executive_dashboard(request):
    context = _dashboard_shell_context(request)
    context.update(get_executive_dashboard_context())
    return render(request, "core/executive_dashboard.html", context)
