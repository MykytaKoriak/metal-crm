import json

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from crm.models import Task

from .access import INTERNAL_ROLES, get_user_role, roles_required
from .dashboard import (
    get_admin_dashboard_context,
    get_executive_dashboard_context,
    get_production_dashboard_context,
    get_sales_dashboard_context,
)
from .forms import TelegramPreferencesForm
from .models import UserProfile
from .telegram.handlers import process_update


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
        .select_related("client", "contact", "order", "assigned_by", "assigned_to")
        .order_by("date", "id")
    )

    current_tasks = base_tasks.exclude(status=Task.Status.DONE).filter(date__gte=today)
    overdue_tasks = base_tasks.exclude(status=Task.Status.DONE).filter(date__lt=today)
    completed_tasks = base_tasks.filter(status=Task.Status.DONE)

    stats = base_tasks.aggregate(
        total_tasks=Count("id"),
        completed_tasks=Count("id", filter=Q(status=Task.Status.DONE)),
        open_tasks=Count("id", filter=~Q(status=Task.Status.DONE)),
        overdue_tasks=Count("id", filter=~Q(status=Task.Status.DONE) & Q(date__lt=today)),
        due_today_tasks=Count("id", filter=~Q(status=Task.Status.DONE) & Q(date=today)),
        new_tasks=Count("id", filter=Q(status=Task.Status.NEW)),
        in_progress_tasks=Count("id", filter=Q(status=Task.Status.IN_PROGRESS)),
        waiting_tasks=Count("id", filter=Q(status=Task.Status.WAITING)),
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
        "telegram_preferences_form": TelegramPreferencesForm(instance=profile),
    }
    return render(request, "core/my_account.html", context)


@roles_required(*INTERNAL_ROLES)
@require_POST
def update_telegram_preferences(request):
    profile = _get_profile(request.user)
    form = TelegramPreferencesForm(request.POST, instance=profile)
    if form.is_valid():
        form.save()
        messages.success(request, "Налаштування Telegram оновлено.")
    else:
        messages.error(request, "Не вдалося зберегти налаштування Telegram.")
    return redirect("my_account")


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
    context.update(get_production_dashboard_context(request))
    return render(request, "core/production_dashboard.html", context)


@roles_required(UserProfile.Role.EXECUTIVE)
def executive_dashboard(request):
    context = _dashboard_shell_context(request)
    context.update(get_executive_dashboard_context())
    return render(request, "core/executive_dashboard.html", context)


@csrf_exempt
@require_POST
def telegram_webhook(request):
    secret = (getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
    if secret:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if received_secret != secret:
            return JsonResponse({"ok": False, "error": "некоректний секретний ключ"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Некоректне JSON-повідомлення.")

    process_update(payload)
    return JsonResponse({"ok": True})
