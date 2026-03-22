from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.access import INTERNAL_ROLES, roles_required
from core.models import UserProfile
from core.visibility import (
    filter_clients_queryset,
    filter_contacts_queryset,
    filter_orders_queryset,
    filter_slots_queryset,
    filter_tasks_queryset,
    visible_manager_choices_queryset,
)

from .forms import ClientForm, ContactForm, OrderForm, OrderItemFormSet, ProductForm, TaskForm
from .models import Client, Contact, Order, Product, Task


MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)
ZERO_MONEY = Value(Decimal("0.00"), output_field=MONEY_FIELD)
ORDER_FULL_WIDTH_FIELDS = ["comment", "shipping_address", "payment_terms"]
ORDER_ITEM_FULL_WIDTH_FIELDS = ["comment"]
DEFAULT_FULL_WIDTH_FIELDS = [
    "notes",
    "comment",
    "description",
    "technical_description",
    "shipping_address",
    "payment_terms",
    "tags",
]
TASK_STATUS_TONES = {
    Task.Status.NEW: "warning",
    Task.Status.IN_PROGRESS: "critical",
    Task.Status.WAITING: "warning",
    Task.Status.DONE: "healthy",
}
TASK_DEADLINE_OPTIONS = (
    ("", "Усі дедлайни"),
    ("overdue", "Прострочені"),
    ("today", "На сьогодні"),
    ("next_7_days", "Наступні 7 днів"),
)
TASK_KANBAN_COLUMNS = (
    (Task.Status.NEW, "Нові"),
    (Task.Status.IN_PROGRESS, "В роботі"),
    (Task.Status.WAITING, "Очікують"),
    (Task.Status.DONE, "Виконано"),
)


def _crm_context(request, crm_nav):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    return {
        "profile": profile,
        "active_section": "crm",
        "crm_nav": crm_nav,
    }


def _search_param(request):
    return request.GET.get("q", "").strip()


def _annotated_orders_queryset():
    return Order.objects.select_related("contact", "contact__client", "manager").annotate(
        items_total=Coalesce(
            Sum(
                ExpressionWrapper(
                    F("items__quantity") * F("items__unit_price"),
                    output_field=MONEY_FIELD,
                )
            ),
            ZERO_MONEY,
        )
    )


def _build_url(view_name, *, args=None, params=None, next_url=None):
    url = reverse(view_name, args=args or [])
    query = {}
    if params:
        query.update({key: value for key, value in params.items() if value not in (None, "")})
    if next_url:
        query["next"] = next_url
    if query:
        return f"{url}?{urlencode(query)}"
    return url


def _safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


def _redirect_target(request, fallback_url):
    return redirect(_safe_next_url(request) or fallback_url)


def _require_permission(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _object_from_param(queryset, raw_value):
    if not raw_value:
        return None
    try:
        return queryset.get(pk=int(raw_value))
    except (TypeError, ValueError, queryset.model.DoesNotExist):
        return None


def _client_workspace_context(request, client, current_url=None):
    current_url = current_url or request.get_full_path()
    primary_contact = client.contacts.order_by("full_name", "id").first()
    can_change_client = request.user.has_perm("crm.change_client")
    can_delete_client = request.user.has_perm("crm.delete_client")
    can_add_contact = request.user.has_perm("crm.add_contact")
    can_add_order = request.user.has_perm("crm.add_order")
    can_add_task = request.user.has_perm("crm.add_task")

    context = {
        "client": client,
        "client_edit_url": _build_url("crm_client_update", args=[client.id], next_url=current_url)
        if can_change_client
        else None,
        "client_delete_url": _build_url("crm_client_delete", args=[client.id], next_url=current_url)
        if can_delete_client
        else None,
        "contact_add_url": _build_url(
            "crm_contact_create",
            params={"client": client.id},
            next_url=current_url,
        )
        if can_add_contact
        else None,
        "order_add_url": None,
        "task_add_url": None,
        "can_add_order": can_add_order,
        "can_add_task": can_add_task,
    }
    if primary_contact and can_add_order:
        context["order_add_url"] = _build_url(
            "crm_order_create",
            params={"contact": primary_contact.id, "manager": request.user.id},
            next_url=current_url,
        )
    if can_add_task:
        task_params = {
            "client": client.id,
            "assigned_by": request.user.id,
            "assigned_to": request.user.id,
            "date": timezone.localdate().isoformat(),
        }
        if primary_contact:
            task_params["contact"] = primary_contact.id
        context["task_add_url"] = _build_url(
            "crm_task_create",
            params=task_params,
            next_url=current_url,
        )
    return context


def _decorate_order(order, *, current_url, can_change_order, can_delete_order, today):
    order.client_url = reverse("client_details", args=[order.contact.client_id])
    order.edit_url = _build_url("crm_order_update", args=[order.id], next_url=current_url) if can_change_order else None
    order.delete_url = _build_url("crm_order_delete", args=[order.id], next_url=current_url) if can_delete_order else None
    order.is_overdue = bool(
        order.deadline and order.deadline < today and order.status not in [Order.Status.COMPLETED, Order.Status.CANCELED]
    )
    delivery_parts = []
    if order.delivery_method:
        delivery_parts.append(order.get_delivery_method_display())
    if order.recipient:
        delivery_parts.append(order.recipient)
    if order.recipient_phone:
        delivery_parts.append(order.recipient_phone)
    if order.tracking_number:
        delivery_parts.append(f"ТТН {order.tracking_number}")
    payment_parts = []
    if order.payment_type:
        payment_parts.append(order.get_payment_type_display())
    if order.payment_terms:
        payment_parts.append(order.payment_terms)
    if order.payment_amount is not None:
        payment_parts.append(f"{order.payment_amount:.2f}")
    order.delivery_summary = " · ".join(delivery_parts)
    order.payment_summary = " · ".join(payment_parts)


def _tasks_base_queryset():
    return Task.objects.select_related(
        "client",
        "contact",
        "order",
        "assigned_to",
        "assigned_by",
    ).order_by("date", "id")


def _task_filter_values(request):
    return {
        "search": _search_param(request),
        "assigned_to": request.GET.get("assigned_to", "").strip(),
        "client_id": request.GET.get("client", "").strip(),
        "order_id": request.GET.get("order", "").strip(),
        "status": request.GET.get("status", "").strip(),
        "deadline": request.GET.get("deadline", "").strip(),
    }


def _apply_task_filters(queryset, *, request, filter_values, today, include_status=True):
    search = filter_values["search"]
    selected_assigned_to = filter_values["assigned_to"]
    selected_client_id = filter_values["client_id"]
    selected_order_id = filter_values["order_id"]
    selected_status = filter_values["status"]
    selected_deadline = filter_values["deadline"]

    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(comment__icontains=search)
            | Q(client__name__icontains=search)
            | Q(contact__full_name__icontains=search)
            | Q(order__title__icontains=search)
        )

    if selected_assigned_to == "mine":
        queryset = queryset.filter(assigned_to=request.user)
    elif selected_assigned_to.isdigit():
        queryset = queryset.filter(assigned_to_id=int(selected_assigned_to))
    elif selected_assigned_to:
        queryset = queryset.none()

    if selected_client_id.isdigit():
        queryset = queryset.filter(client_id=int(selected_client_id))
    elif selected_client_id:
        queryset = queryset.none()

    if selected_order_id.isdigit():
        queryset = queryset.filter(order_id=int(selected_order_id))
    elif selected_order_id:
        queryset = queryset.none()

    if include_status and selected_status:
        queryset = queryset.filter(status=selected_status)

    if selected_deadline == "overdue":
        queryset = queryset.filter(date__lt=today).exclude(status=Task.Status.DONE)
    elif selected_deadline == "today":
        queryset = queryset.filter(date=today)
    elif selected_deadline == "next_7_days":
        queryset = queryset.filter(date__gt=today, date__lte=today + timedelta(days=7)).exclude(status=Task.Status.DONE)

    return queryset


def _decorate_task(task, *, current_url, can_change_task, can_delete_task, can_change_order, today):
    task.client_url = reverse("client_details", args=[task.client_id])
    task.edit_url = _build_url("crm_task_update", args=[task.id], next_url=current_url) if can_change_task else None
    task.delete_url = _build_url("crm_task_delete", args=[task.id], next_url=current_url) if can_delete_task else None
    task.order_url = (
        _build_url("crm_order_update", args=[task.order_id], next_url=current_url)
        if task.order_id and can_change_order
        else None
    )
    task.is_overdue = task.status != Task.Status.DONE and task.date < today
    task.status_tone = TASK_STATUS_TONES.get(task.status, "warning")


def _task_stats(queryset, today):
    return {
        "total_tasks": queryset.count(),
        "new_tasks": queryset.filter(status=Task.Status.NEW).count(),
        "in_progress_tasks": queryset.filter(status=Task.Status.IN_PROGRESS).count(),
        "waiting_tasks": queryset.filter(status=Task.Status.WAITING).count(),
        "completed_tasks": queryset.filter(status=Task.Status.DONE).count(),
        "open_tasks": queryset.exclude(status=Task.Status.DONE).count(),
        "overdue_tasks": queryset.filter(date__lt=today).exclude(status=Task.Status.DONE).count(),
    }


def _task_filter_context(*, request, current_url, filter_values):
    selected_client_id = filter_values["client_id"]
    order_options = filter_orders_queryset(
        request.user,
        Order.objects.select_related("contact", "contact__client").order_by("-created_at", "-id"),
    )
    if selected_client_id.isdigit():
        order_options = order_options.filter(contact__client_id=int(selected_client_id))
    else:
        order_options = order_options[:100]

    manager_options = visible_manager_choices_queryset(
        request.user,
        get_user_model().objects.filter(is_active=True).order_by("email", "username"),
    )

    return {
        "selected_assigned_to": filter_values["assigned_to"],
        "selected_client_id": selected_client_id,
        "selected_order_id": filter_values["order_id"],
        "selected_status": filter_values["status"],
        "selected_deadline": filter_values["deadline"],
        "assigned_to_options": manager_options,
        "client_options": filter_clients_queryset(request.user, Client.objects.order_by("name")),
        "order_options": order_options,
        "task_status_choices": Task.Status.choices,
        "deadline_options": TASK_DEADLINE_OPTIONS,
        "task_add_url": _build_url(
            "crm_task_create",
            params={
                "assigned_by": request.user.id,
                "assigned_to": request.user.id,
                "date": timezone.localdate().isoformat(),
                "client": selected_client_id or None,
                "order": filter_values["order_id"] or None,
            },
            next_url=current_url,
        )
        if request.user.has_perm("crm.add_task")
        else None,
        "task_kanban_url": _build_url(
            "crm_tasks_kanban",
            params={
                "q": filter_values["search"] or None,
                "assigned_to": filter_values["assigned_to"] or None,
                "client": selected_client_id or None,
                "order": filter_values["order_id"] or None,
                "deadline": filter_values["deadline"] or None,
            },
        ),
        "task_list_url": _build_url(
            "crm_tasks",
            params={
                "q": filter_values["search"] or None,
                "assigned_to": filter_values["assigned_to"] or None,
                "client": selected_client_id or None,
                "order": filter_values["order_id"] or None,
                "status": filter_values["status"] or None,
                "deadline": filter_values["deadline"] or None,
            },
        ),
    }


def _render_form_page(
    request,
    *,
    crm_nav,
    form,
    page_title,
    hero_title,
    hero_text,
    submit_label,
    cancel_url,
    mode_label,
    object_label,
    formset=None,
    delete_url=None,
    full_width_fields=None,
    item_full_width_fields=None,
    client=None,
):
    context = _crm_context(request, crm_nav)
    if client is not None:
        context.update(_client_workspace_context(request, client))
    context.update(
        {
            "page_title": page_title,
            "hero_title": hero_title,
            "hero_text": hero_text,
            "submit_label": submit_label,
            "cancel_url": cancel_url,
            "next_url": _safe_next_url(request) or "",
            "mode_label": mode_label,
            "object_label": object_label,
            "form": form,
            "formset": formset,
            "delete_url": delete_url,
            "full_width_fields": full_width_fields or DEFAULT_FULL_WIDTH_FIELDS,
            "item_full_width_fields": item_full_width_fields or ORDER_ITEM_FULL_WIDTH_FIELDS,
        }
    )
    return render(request, "crm/form_page.html", context)


def _render_delete_page(
    request,
    *,
    crm_nav,
    object_label,
    object_title,
    hero_text,
    confirm_label,
    cancel_url,
    delete_error=None,
    client=None,
):
    context = _crm_context(request, crm_nav)
    if client is not None:
        context.update(_client_workspace_context(request, client))
    context.update(
        {
            "page_title": f"Delete {object_label}",
            "hero_title": f"Видалити {object_label.lower()}",
            "hero_text": hero_text,
            "confirm_label": confirm_label,
            "cancel_url": cancel_url,
            "next_url": _safe_next_url(request) or "",
            "object_label": object_label,
            "object_title": object_title,
            "delete_error": delete_error,
        }
    )
    return render(request, "crm/delete_confirm.html", context)


@roles_required(*INTERNAL_ROLES)
def clients_list(request):
    today = timezone.localdate()
    search = _search_param(request)
    queryset = filter_clients_queryset(
        request.user,
        Client.objects.prefetch_related("tags")
        .annotate(
            contacts_count=Count("contacts", distinct=True),
            orders_count=Count("contacts__orders", distinct=True),
            open_tasks_count=Count(
                "tasks",
                filter=Q(tasks__status__in=Task.OPEN_STATUSES),
                distinct=True,
            ),
        )
        .order_by("-created_at", "name"),
    )
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(tax_code__icontains=search)
            | Q(phones__icontains=search)
            | Q(email__icontains=search)
        )

    current_url = request.get_full_path()
    can_change_client = request.user.has_perm("crm.change_client")
    can_delete_client = request.user.has_perm("crm.delete_client")
    clients = list(queryset[:60])
    for client in clients:
        client.details_url = reverse("client_details", args=[client.id])
        client.edit_url = _build_url("crm_client_update", args=[client.id], next_url=current_url) if can_change_client else None
        client.delete_url = _build_url("crm_client_delete", args=[client.id], next_url=current_url) if can_delete_client else None

    stats = {
        "total_clients": queryset.count(),
        "new_clients": queryset.filter(created_at__date__gte=today - timedelta(days=30)).count(),
        "b2b_clients": queryset.filter(client_type__in=[Client.ClientType.FOP, Client.ClientType.TOV]).count(),
        "clients_with_open_tasks": queryset.filter(tasks__status__in=Task.OPEN_STATUSES).distinct().count(),
    }

    context = _crm_context(request, "clients")
    context.update(
        {
            "search": search,
            "stats": stats,
            "clients": clients,
            "client_add_url": _build_url("crm_client_create", next_url=current_url)
            if request.user.has_perm("crm.add_client")
            else None,
        }
    )
    return render(request, "crm/clients_list.html", context)


@roles_required(*INTERNAL_ROLES)
def client_details(request, client_id):
    today = timezone.localdate()
    current_url = request.get_full_path()
    client = get_object_or_404(
        filter_clients_queryset(
            request.user,
            Client.objects.prefetch_related("tags", "contacts__tags"),
        ),
        pk=client_id,
    )

    contacts = list(filter_contacts_queryset(request.user, client.contacts.all().order_by("full_name", "id")))
    orders = list(
        filter_orders_queryset(
            request.user,
            _annotated_orders_queryset(),
        )
        .filter(contact__client=client)
        .order_by("-created_at", "-id")
    )
    tasks = list(
        filter_tasks_queryset(
            request.user,
            Task.objects.filter(client=client),
        )
        .select_related("client", "contact", "order", "assigned_to", "assigned_by")
        .order_by("date", "id")
    )

    can_change_contact = request.user.has_perm("crm.change_contact")
    can_delete_contact = request.user.has_perm("crm.delete_contact")
    can_change_order = request.user.has_perm("crm.change_order")
    can_delete_order = request.user.has_perm("crm.delete_order")
    can_change_task = request.user.has_perm("crm.change_task")
    can_delete_task = request.user.has_perm("crm.delete_task")

    for contact in contacts:
        contact.edit_url = _build_url("crm_contact_update", args=[contact.id], next_url=current_url) if can_change_contact else None
        contact.delete_url = _build_url("crm_contact_delete", args=[contact.id], next_url=current_url) if can_delete_contact else None
        contact.quick_order_url = _build_url(
            "crm_order_create",
            params={"contact": contact.id, "manager": request.user.id},
            next_url=current_url,
        ) if request.user.has_perm("crm.add_order") else None
        contact.quick_task_url = _build_url(
            "crm_task_create",
            params={
                "client": client.id,
                "contact": contact.id,
                "assigned_by": request.user.id,
                "assigned_to": request.user.id,
                "date": today.isoformat(),
            },
            next_url=current_url,
        ) if request.user.has_perm("crm.add_task") else None

    for order in orders:
        _decorate_order(
            order,
            current_url=current_url,
            can_change_order=can_change_order,
            can_delete_order=can_delete_order,
            today=today,
        )

    for task in tasks:
        _decorate_task(
            task,
            current_url=current_url,
            can_change_task=can_change_task,
            can_delete_task=can_delete_task,
            can_change_order=can_change_order,
            today=today,
        )

    stats = {
        "contacts_count": len(contacts),
        "orders_count": len(orders),
        "tasks_count": len(tasks),
        "open_tasks_count": sum(1 for task in tasks if task.is_open),
        "overdue_tasks_count": sum(1 for task in tasks if task.is_overdue),
        "completed_tasks_count": sum(1 for task in tasks if task.is_done),
        "revenue_total": sum((order.items_total for order in orders), Decimal("0.00")),
    }

    context = _crm_context(request, "clients")
    context.update(_client_workspace_context(request, client, current_url=current_url))
    context.update(
        {
            "client": client,
            "contacts": contacts,
            "orders": orders,
            "tasks": tasks,
            "stats": stats,
            "today": today,
        }
    )
    return render(request, "crm/client_details.html", context)


@roles_required(*INTERNAL_ROLES)
def contacts_list(request):
    search = _search_param(request)
    queryset = filter_contacts_queryset(
        request.user,
        Contact.objects.select_related("client")
        .prefetch_related("tags")
        .annotate(
            orders_count=Count("orders", distinct=True),
            open_tasks_count=Count("tasks", filter=Q(tasks__status__in=Task.OPEN_STATUSES), distinct=True),
        )
        .order_by("-created_at", "full_name"),
    )
    if search:
        queryset = queryset.filter(
            Q(full_name__icontains=search)
            | Q(position__icontains=search)
            | Q(phone__icontains=search)
            | Q(email__icontains=search)
            | Q(client__name__icontains=search)
        )

    current_url = request.get_full_path()
    can_change_contact = request.user.has_perm("crm.change_contact")
    can_delete_contact = request.user.has_perm("crm.delete_contact")
    contacts = list(queryset[:80])
    for contact in contacts:
        contact.client_url = reverse("client_details", args=[contact.client_id])
        contact.edit_url = _build_url("crm_contact_update", args=[contact.id], next_url=current_url) if can_change_contact else None
        contact.delete_url = _build_url("crm_contact_delete", args=[contact.id], next_url=current_url) if can_delete_contact else None

    stats = {
        "total_contacts": queryset.count(),
        "with_email": queryset.exclude(email="").count(),
        "with_phone": queryset.exclude(phone="").count(),
        "with_open_tasks": queryset.filter(tasks__status__in=Task.OPEN_STATUSES).distinct().count(),
    }

    context = _crm_context(request, "contacts")
    context.update(
        {
            "search": search,
            "contacts": contacts,
            "stats": stats,
            "contact_add_url": _build_url("crm_contact_create", next_url=current_url)
            if request.user.has_perm("crm.add_contact")
            else None,
        }
    )
    return render(request, "crm/contacts_list.html", context)


@roles_required(*INTERNAL_ROLES)
def orders_list(request):
    today = timezone.localdate()
    search = _search_param(request)
    selected_manager = request.GET.get("manager", "").strip()
    selected_status = request.GET.get("status", "").strip()
    selected_delivery_method = request.GET.get("delivery_method", "").strip()
    selected_payment_type = request.GET.get("payment_type", "").strip()
    queryset = filter_orders_queryset(request.user, _annotated_orders_queryset().order_by("-created_at", "-id"))
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(contact__full_name__icontains=search)
            | Q(contact__client__name__icontains=search)
            | Q(tracking_number__icontains=search)
            | Q(recipient__icontains=search)
            | Q(recipient_phone__icontains=search)
            | Q(payment_terms__icontains=search)
            | Q(manager__email__icontains=search)
            | Q(manager__username__icontains=search)
        )
    if selected_manager == "mine":
        queryset = queryset.filter(manager=request.user)
    elif selected_manager.isdigit():
        queryset = queryset.filter(manager_id=int(selected_manager))
    elif selected_manager:
        queryset = queryset.none()
    if selected_status:
        queryset = queryset.filter(status=selected_status)
    if selected_delivery_method:
        queryset = queryset.filter(delivery_method=selected_delivery_method)
    if selected_payment_type:
        queryset = queryset.filter(payment_type=selected_payment_type)

    current_url = request.get_full_path()
    can_change_order = request.user.has_perm("crm.change_order")
    can_delete_order = request.user.has_perm("crm.delete_order")
    orders = list(queryset[:80])
    for order in orders:
        _decorate_order(
            order,
            current_url=current_url,
            can_change_order=can_change_order,
            can_delete_order=can_delete_order,
            today=today,
        )

    filtered_queryset = queryset
    stats = {
        "total_orders": filtered_queryset.count(),
        "new_orders": filtered_queryset.filter(status=Order.Status.NEW).count(),
        "in_progress_orders": filtered_queryset.filter(status=Order.Status.IN_PROGRESS).count(),
        "overdue_orders": filtered_queryset.filter(deadline__lt=today).exclude(
            status__in=[Order.Status.COMPLETED, Order.Status.CANCELED]
        ).count(),
    }

    context = _crm_context(request, "orders")
    context.update(
        {
            "search": search,
            "orders": orders,
            "stats": stats,
            "selected_manager": selected_manager,
            "selected_status": selected_status,
            "selected_delivery_method": selected_delivery_method,
            "selected_payment_type": selected_payment_type,
            "manager_options": visible_manager_choices_queryset(
                request.user,
                get_user_model().objects.filter(is_active=True).order_by("email", "username"),
            ),
            "status_choices": Order.Status.choices,
            "delivery_choices": Order.DeliveryMethod.choices,
            "payment_choices": Order.PaymentType.choices,
            "order_add_url": _build_url(
                "crm_order_create",
                params={"manager": request.user.id},
                next_url=current_url,
            )
            if request.user.has_perm("crm.add_order")
            else None,
        }
    )
    return render(request, "crm/orders_list.html", context)


@roles_required(*INTERNAL_ROLES)
def tasks_list(request):
    today = timezone.localdate()
    filter_values = _task_filter_values(request)
    queryset = _apply_task_filters(
        filter_tasks_queryset(request.user, _tasks_base_queryset()),
        request=request,
        filter_values=filter_values,
        today=today,
        include_status=True,
    )

    current_url = request.get_full_path()
    can_change_task = request.user.has_perm("crm.change_task")
    can_delete_task = request.user.has_perm("crm.delete_task")
    can_change_order = request.user.has_perm("crm.change_order")
    tasks = list(queryset[:100])
    for task in tasks:
        _decorate_task(
            task,
            current_url=current_url,
            can_change_task=can_change_task,
            can_delete_task=can_delete_task,
            can_change_order=can_change_order,
            today=today,
        )

    stats = _task_stats(queryset, today)

    context = _crm_context(request, "tasks")
    context.update(
        {
            "search": filter_values["search"],
            "tasks": tasks,
            "stats": stats,
            "today": today,
            "current_url": current_url,
            **_task_filter_context(request=request, current_url=current_url, filter_values=filter_values),
        }
    )
    return render(request, "crm/tasks_list.html", context)


@roles_required(*INTERNAL_ROLES)
def tasks_kanban(request):
    today = timezone.localdate()
    filter_values = _task_filter_values(request)
    queryset = _apply_task_filters(
        filter_tasks_queryset(request.user, _tasks_base_queryset()),
        request=request,
        filter_values=filter_values,
        today=today,
        include_status=False,
    )

    current_url = request.get_full_path()
    can_change_task = request.user.has_perm("crm.change_task")
    can_delete_task = request.user.has_perm("crm.delete_task")
    can_change_order = request.user.has_perm("crm.change_order")
    columns = []

    for status_code, title in TASK_KANBAN_COLUMNS:
        items = list(queryset.filter(status=status_code)[:50])
        for task in items:
            _decorate_task(
                task,
                current_url=current_url,
                can_change_task=can_change_task,
                can_delete_task=can_delete_task,
                can_change_order=can_change_order,
                today=today,
            )
            task.status_update_url = reverse("crm_task_status_update", args=[task.id])
        columns.append({"code": status_code, "title": title, "items": items, "count": len(items)})

    context = _crm_context(request, "tasks")
    context.update(
        {
            "search": filter_values["search"],
            "stats": _task_stats(queryset, today),
            "columns": columns,
            "today": today,
            "can_change_task": can_change_task,
            "kanban_status_choices": Task.Status.choices,
            "current_url": current_url,
            **_task_filter_context(request=request, current_url=current_url, filter_values=filter_values),
        }
    )
    return render(request, "crm/tasks_kanban.html", context)


@roles_required(*INTERNAL_ROLES)
@require_POST
def task_status_update(request, task_id):
    _require_permission(request, "crm.change_task")
    task = get_object_or_404(
        filter_tasks_queryset(request.user, Task.objects.select_related("client")),
        pk=task_id,
    )
    allowed_statuses = {code for code, _ in Task.Status.choices}
    new_status = request.POST.get("status", "").strip()
    if new_status not in allowed_statuses:
        return HttpResponseBadRequest("Invalid task status.")

    task.status = new_status
    task._changed_by = request.user
    task.save(update_fields=["status"])

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "id": task.id,
                "status": task.status,
                "status_label": task.get_status_display(),
                "is_done": task.is_done,
                "tone": TASK_STATUS_TONES.get(task.status, "warning"),
            }
        )

    fallback_url = reverse("client_details", args=[task.client_id]) if task.client_id else reverse("crm_tasks_kanban")
    return _redirect_target(request, fallback_url)


@roles_required(*INTERNAL_ROLES)
def products_list(request):
    search = _search_param(request)
    queryset = (
        Product.objects.annotate(order_items_count=Count("order_items", distinct=True))
        .order_by("name", "id")
    )
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(sku__icontains=search)
            | Q(description__icontains=search)
            | Q(technical_description__icontains=search)
        )

    current_url = request.get_full_path()
    can_change_product = request.user.has_perm("crm.change_product")
    can_delete_product = request.user.has_perm("crm.delete_product")
    products = list(queryset[:100])
    for product in products:
        product.edit_url = _build_url("crm_product_update", args=[product.id], next_url=current_url) if can_change_product else None
        product.delete_url = _build_url("crm_product_delete", args=[product.id], next_url=current_url) if can_delete_product else None

    stats = {
        "total_products": Product.objects.count(),
        "active_products": Product.objects.filter(is_active=True).count(),
        "inactive_products": Product.objects.filter(is_active=False).count(),
        "used_in_orders": Product.objects.filter(order_items__isnull=False).distinct().count(),
    }

    context = _crm_context(request, "products")
    context.update(
        {
            "search": search,
            "products": products,
            "stats": stats,
            "product_add_url": _build_url("crm_product_create", next_url=current_url)
            if request.user.has_perm("crm.add_product")
            else None,
        }
    )
    return render(request, "crm/products_list.html", context)


@roles_required(*INTERNAL_ROLES)
def client_create(request):
    _require_permission(request, "crm.add_client")
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        return _redirect_target(request, reverse("client_details", args=[client.id]))
    return _render_form_page(
        request,
        crm_nav="clients",
        form=form,
        page_title="New client",
        hero_title="Створити клієнта",
        hero_text="Нова картка клієнта створюється прямо у CRM workspace без переходу в Django Admin.",
        submit_label="Створити клієнта",
        cancel_url=_safe_next_url(request) or reverse("crm_clients"),
        mode_label="Create",
        object_label="Client",
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
    )


@roles_required(*INTERNAL_ROLES)
def client_update(request, client_id):
    _require_permission(request, "crm.change_client")
    client = get_object_or_404(
        filter_clients_queryset(request.user, Client.objects.prefetch_related("tags")),
        pk=client_id,
    )
    fallback_url = reverse("client_details", args=[client.id])
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        return _redirect_target(request, reverse("client_details", args=[client.id]))
    return _render_form_page(
        request,
        crm_nav="clients",
        form=form,
        page_title=client.name,
        hero_title="Редагувати клієнта",
        hero_text="Оновлення реквізитів, контактних каналів і тегації клієнта в робочому CRM-інтерфейсі.",
        submit_label="Зберегти клієнта",
        cancel_url=_safe_next_url(request) or fallback_url,
        mode_label="Edit",
        object_label="Client",
        delete_url=_build_url("crm_client_delete", args=[client.id], next_url=_safe_next_url(request) or fallback_url)
        if request.user.has_perm("crm.delete_client")
        else None,
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
        client=client,
    )


@roles_required(*INTERNAL_ROLES)
def client_delete(request, client_id):
    _require_permission(request, "crm.delete_client")
    client = get_object_or_404(
        filter_clients_queryset(request.user, Client.objects.prefetch_related("contacts")),
        pk=client_id,
    )
    fallback_url = reverse("crm_clients")
    cancel_url = _safe_next_url(request) or reverse("client_details", args=[client.id])
    delete_error = None
    if request.method == "POST":
        try:
            client.delete()
        except ProtectedError:
            delete_error = "Клієнта не можна видалити, поки до нього прив’язані контакти або інші залежні записи."
        else:
            return _redirect_target(request, fallback_url)
    return _render_delete_page(
        request,
        crm_nav="clients",
        object_label="Client",
        object_title=client.name,
        hero_text="Видалення клієнта прибере доступ до його картки. Якщо є залежні записи, система не дозволить операцію.",
        confirm_label="Видалити клієнта",
        cancel_url=cancel_url,
        delete_error=delete_error,
        client=client,
    )


@roles_required(*INTERNAL_ROLES)
def contact_create(request):
    _require_permission(request, "crm.add_contact")
    selected_client = _object_from_param(
        filter_clients_queryset(request.user, Client.objects.all()),
        request.GET.get("client"),
    )
    initial = {}
    if selected_client:
        initial["client"] = selected_client.id
    if request.method == "POST":
        form = ContactForm(request.POST, user=request.user)
        sidebar_client = _object_from_param(
            filter_clients_queryset(request.user, Client.objects.all()),
            request.POST.get("client"),
        ) or selected_client
    else:
        form = ContactForm(initial=initial, user=request.user)
        sidebar_client = selected_client

    if request.method == "POST" and form.is_valid():
        contact = form.save()
        return _redirect_target(request, reverse("client_details", args=[contact.client_id]))

    return _render_form_page(
        request,
        crm_nav="contacts",
        form=form,
        page_title="New contact",
        hero_title="Створити контакт",
        hero_text="Новий контакт одразу прив’язується до клієнта і з’являється у CRM workspace.",
        submit_label="Створити контакт",
        cancel_url=_safe_next_url(request)
        or (reverse("client_details", args=[selected_client.id]) if selected_client else reverse("crm_contacts")),
        mode_label="Create",
        object_label="Contact",
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
        client=sidebar_client,
    )


@roles_required(*INTERNAL_ROLES)
def contact_update(request, contact_id):
    _require_permission(request, "crm.change_contact")
    contact = get_object_or_404(
        filter_contacts_queryset(request.user, Contact.objects.select_related("client").prefetch_related("tags")),
        pk=contact_id,
    )
    fallback_url = reverse("client_details", args=[contact.client_id])
    form = ContactForm(request.POST or None, instance=contact, user=request.user)
    if request.method == "POST" and form.is_valid():
        contact = form.save()
        return _redirect_target(request, reverse("client_details", args=[contact.client_id]))
    return _render_form_page(
        request,
        crm_nav="contacts",
        form=form,
        page_title=contact.full_name,
        hero_title="Редагувати контакт",
        hero_text="Оновлення контактної особи, посадових даних і каналів зв’язку без переходу в admin.",
        submit_label="Зберегти контакт",
        cancel_url=_safe_next_url(request) or fallback_url,
        mode_label="Edit",
        object_label="Contact",
        delete_url=_build_url("crm_contact_delete", args=[contact.id], next_url=_safe_next_url(request) or fallback_url)
        if request.user.has_perm("crm.delete_contact")
        else None,
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
        client=contact.client,
    )


@roles_required(*INTERNAL_ROLES)
def contact_delete(request, contact_id):
    _require_permission(request, "crm.delete_contact")
    contact = get_object_or_404(
        filter_contacts_queryset(request.user, Contact.objects.select_related("client")),
        pk=contact_id,
    )
    fallback_url = reverse("client_details", args=[contact.client_id])
    delete_error = None
    if request.method == "POST":
        try:
            contact.delete()
        except ProtectedError:
            delete_error = "Контакт не можна видалити, поки на нього посилаються захищені записи."
        else:
            return _redirect_target(request, fallback_url)
    return _render_delete_page(
        request,
        crm_nav="contacts",
        object_label="Contact",
        object_title=contact.full_name,
        hero_text="Видалення контакту також зачепить пов’язані задачі та замовлення, якщо для них немає захисту на рівні моделі.",
        confirm_label="Видалити контакт",
        cancel_url=_safe_next_url(request) or fallback_url,
        delete_error=delete_error,
        client=contact.client,
    )


@roles_required(*INTERNAL_ROLES)
def order_create(request):
    _require_permission(request, "crm.add_order")
    user_model = get_user_model()
    selected_contact = _object_from_param(
        filter_contacts_queryset(request.user, Contact.objects.select_related("client")),
        request.GET.get("contact"),
    )
    selected_manager = _object_from_param(user_model.objects.all(), request.GET.get("manager"))
    initial = {"manager": (selected_manager or request.user).id}
    if selected_contact:
        initial["contact"] = selected_contact.id

    if request.method == "POST":
        order = Order()
        form = OrderForm(request.POST, instance=order, user=request.user)
        formset = OrderItemFormSet(request.POST, instance=order)
        sidebar_contact = _object_from_param(
            filter_contacts_queryset(request.user, Contact.objects.select_related("client")),
            request.POST.get("contact"),
        ) or selected_contact
    else:
        order = Order()
        form = OrderForm(initial=initial, instance=order, user=request.user)
        formset = OrderItemFormSet(instance=order)
        sidebar_contact = selected_contact

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            order._changed_by = request.user
            order.save()
            formset.instance = order
            formset.save()
            order.refresh_title()
        return _redirect_target(request, reverse("client_details", args=[order.contact.client_id]))

    sidebar_client = sidebar_contact.client if sidebar_contact else None
    cancel_url = _safe_next_url(request)
    if not cancel_url:
        cancel_url = reverse("client_details", args=[sidebar_client.id]) if sidebar_client else reverse("crm_orders")

    return _render_form_page(
        request,
        crm_nav="orders",
        form=form,
        page_title="New order",
        hero_title="Створити замовлення",
        hero_text="Замовлення створюється в CRM workspace разом із позиціями, доставкою та оплатою без переходу в admin.",
        submit_label="Створити замовлення",
        cancel_url=cancel_url,
        mode_label="Create",
        object_label="Order",
        formset=formset,
        full_width_fields=ORDER_FULL_WIDTH_FIELDS,
        item_full_width_fields=ORDER_ITEM_FULL_WIDTH_FIELDS,
        client=sidebar_client,
    )


@roles_required(*INTERNAL_ROLES)
def order_update(request, order_id):
    _require_permission(request, "crm.change_order")
    order = get_object_or_404(
        filter_orders_queryset(
            request.user,
            Order.objects.select_related("contact", "contact__client", "manager"),
        ),
        pk=order_id,
    )
    fallback_url = reverse("client_details", args=[order.contact.client_id])
    if request.method == "POST":
        form = OrderForm(request.POST, instance=order, user=request.user)
        formset = OrderItemFormSet(request.POST, instance=order)
    else:
        form = OrderForm(instance=order, user=request.user)
        formset = OrderItemFormSet(instance=order)

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            order._changed_by = request.user
            order.save()
            formset.save()
            order.refresh_title()
        return _redirect_target(request, reverse("client_details", args=[order.contact.client_id]))

    return _render_form_page(
        request,
        crm_nav="orders",
        form=form,
        page_title=order.title or f"Order #{order.id}",
        hero_title="Редагувати замовлення",
        hero_text="Оновлення статусу, дедлайну, доставки, оплати і позицій замовлення в єдиній формі.",
        submit_label="Зберегти замовлення",
        cancel_url=_safe_next_url(request) or fallback_url,
        mode_label="Edit",
        object_label="Order",
        formset=formset,
        delete_url=_build_url("crm_order_delete", args=[order.id], next_url=_safe_next_url(request) or fallback_url)
        if request.user.has_perm("crm.delete_order")
        else None,
        full_width_fields=ORDER_FULL_WIDTH_FIELDS,
        item_full_width_fields=ORDER_ITEM_FULL_WIDTH_FIELDS,
        client=order.contact.client,
    )


@roles_required(*INTERNAL_ROLES)
def order_delete(request, order_id):
    _require_permission(request, "crm.delete_order")
    order = get_object_or_404(
        filter_orders_queryset(request.user, Order.objects.select_related("contact", "contact__client")),
        pk=order_id,
    )
    fallback_url = reverse("client_details", args=[order.contact.client_id])
    delete_error = None
    if request.method == "POST":
        try:
            order.delete()
        except ProtectedError:
            delete_error = "Замовлення не можна видалити, поки на нього посилаються захищені виробничі записи."
        else:
            return _redirect_target(request, fallback_url)
    return _render_delete_page(
        request,
        crm_nav="orders",
        object_label="Order",
        object_title=order.title or f"Order #{order.id}",
        hero_text="Видалення замовлення також прибере його позиції та пов’язані каскадні записи.",
        confirm_label="Видалити замовлення",
        cancel_url=_safe_next_url(request) or fallback_url,
        delete_error=delete_error,
        client=order.contact.client,
    )


@roles_required(*INTERNAL_ROLES)
def task_create(request):
    _require_permission(request, "crm.add_task")
    user_model = get_user_model()
    selected_client = _object_from_param(
        filter_clients_queryset(request.user, Client.objects.all()),
        request.GET.get("client"),
    )
    selected_contact = _object_from_param(
        filter_contacts_queryset(request.user, Contact.objects.select_related("client")),
        request.GET.get("contact"),
    )
    selected_order = _object_from_param(
        filter_orders_queryset(request.user, Order.objects.select_related("contact", "contact__client")),
        request.GET.get("order"),
    )
    selected_assigned_by = _object_from_param(user_model.objects.all(), request.GET.get("assigned_by")) or request.user
    selected_assigned_to = _object_from_param(user_model.objects.all(), request.GET.get("assigned_to")) or request.user
    resolved_client = (
        selected_client
        or (selected_contact.client if selected_contact else None)
        or (selected_order.contact.client if selected_order else None)
    )
    initial = {
        "client": resolved_client.id if resolved_client else None,
        "contact": selected_contact.id if selected_contact else None,
        "order": selected_order.id if selected_order else None,
        "status": Task.Status.NEW,
        "assigned_by": selected_assigned_by.id if selected_assigned_by else None,
        "assigned_to": selected_assigned_to.id if selected_assigned_to else None,
        "date": request.GET.get("date") or timezone.localdate(),
    }

    if request.method == "POST":
        form = TaskForm(request.POST, user=request.user)
        posted_client = _object_from_param(
            filter_clients_queryset(request.user, Client.objects.all()),
            request.POST.get("client"),
        )
        posted_contact = _object_from_param(
            filter_contacts_queryset(request.user, Contact.objects.select_related("client")),
            request.POST.get("contact"),
        )
        posted_order = _object_from_param(
            filter_orders_queryset(request.user, Order.objects.select_related("contact", "contact__client")),
            request.POST.get("order"),
        )
        sidebar_client = (
            posted_client
            or (posted_contact.client if posted_contact else None)
            or (posted_order.contact.client if posted_order else None)
            or resolved_client
        )
    else:
        form = TaskForm(initial=initial, user=request.user)
        sidebar_client = resolved_client

    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        task._changed_by = request.user
        task.save()
        return _redirect_target(request, reverse("client_details", args=[task.client_id]))

    cancel_url = _safe_next_url(request)
    if not cancel_url:
        cancel_url = reverse("client_details", args=[sidebar_client.id]) if sidebar_client else reverse("crm_tasks")

    return _render_form_page(
        request,
        crm_nav="tasks",
        form=form,
        page_title="New task",
        hero_title="Створити задачу",
        hero_text="Нова CRM-задача створюється з прив’язкою до клієнта, контакту, замовлення та відповідальних.",
        submit_label="Створити задачу",
        cancel_url=cancel_url,
        mode_label="Create",
        object_label="Task",
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
        client=sidebar_client,
    )


@roles_required(*INTERNAL_ROLES)
def task_update(request, task_id):
    _require_permission(request, "crm.change_task")
    task = get_object_or_404(
        filter_tasks_queryset(
            request.user,
            Task.objects.select_related("client", "contact", "order", "assigned_by", "assigned_to"),
        ),
        pk=task_id,
    )
    fallback_url = reverse("client_details", args=[task.client_id])
    form = TaskForm(request.POST or None, instance=task, user=request.user)
    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        task._changed_by = request.user
        task.save()
        return _redirect_target(request, reverse("client_details", args=[task.client_id]))
    return _render_form_page(
        request,
        crm_nav="tasks",
        form=form,
        page_title=task.title,
        hero_title="Редагувати задачу",
        hero_text="Оновлення статусу, дедлайну, зв’язків із клієнтом, контактом, замовленням і відповідальних у CRM-формі.",
        submit_label="Зберегти задачу",
        cancel_url=_safe_next_url(request) or fallback_url,
        mode_label="Edit",
        object_label="Task",
        delete_url=_build_url("crm_task_delete", args=[task.id], next_url=_safe_next_url(request) or fallback_url)
        if request.user.has_perm("crm.delete_task")
        else None,
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
        client=task.client,
    )


@roles_required(*INTERNAL_ROLES)
def task_delete(request, task_id):
    _require_permission(request, "crm.delete_task")
    task = get_object_or_404(
        filter_tasks_queryset(request.user, Task.objects.select_related("client", "contact", "order")),
        pk=task_id,
    )
    fallback_url = reverse("client_details", args=[task.client_id])
    delete_error = None
    if request.method == "POST":
        try:
            task.delete()
        except ProtectedError:
            delete_error = "Задачу не можна видалити через наявність захищених залежностей."
        else:
            return _redirect_target(request, fallback_url)
    return _render_delete_page(
        request,
        crm_nav="tasks",
        object_label="Task",
        object_title=task.title,
        hero_text="Видалення задачі прибере її з client workspace, списків і персонального акаунта.",
        confirm_label="Видалити задачу",
        cancel_url=_safe_next_url(request) or fallback_url,
        delete_error=delete_error,
        client=task.client,
    )


@roles_required(*INTERNAL_ROLES)
def product_create(request):
    _require_permission(request, "crm.add_product")
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_target(request, reverse("crm_products"))
    return _render_form_page(
        request,
        crm_nav="products",
        form=form,
        page_title="New product",
        hero_title="Створити продукт",
        hero_text="Новий продукт додається у власний CRM-каталог з технічними даними та посиланнями.",
        submit_label="Створити продукт",
        cancel_url=_safe_next_url(request) or reverse("crm_products"),
        mode_label="Create",
        object_label="Product",
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
    )


@roles_required(*INTERNAL_ROLES)
def product_update(request, product_id):
    _require_permission(request, "crm.change_product")
    product = get_object_or_404(Product, pk=product_id)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_target(request, reverse("crm_products"))
    fallback_url = reverse("crm_products")
    return _render_form_page(
        request,
        crm_nav="products",
        form=form,
        page_title=product.name,
        hero_title="Редагувати продукт",
        hero_text="Оновлення каталожних, технічних і цінових даних продукту у CRM workspace.",
        submit_label="Зберегти продукт",
        cancel_url=_safe_next_url(request) or fallback_url,
        mode_label="Edit",
        object_label="Product",
        delete_url=_build_url("crm_product_delete", args=[product.id], next_url=_safe_next_url(request) or fallback_url)
        if request.user.has_perm("crm.delete_product")
        else None,
        full_width_fields=DEFAULT_FULL_WIDTH_FIELDS,
    )


@roles_required(*INTERNAL_ROLES)
def product_delete(request, product_id):
    _require_permission(request, "crm.delete_product")
    product = get_object_or_404(Product, pk=product_id)
    fallback_url = reverse("crm_products")
    delete_error = None
    if request.method == "POST":
        try:
            product.delete()
        except ProtectedError:
            delete_error = "Продукт не можна видалити, поки він використовується у позиціях замовлень."
        else:
            return _redirect_target(request, fallback_url)
    return _render_delete_page(
        request,
        crm_nav="products",
        object_label="Product",
        object_title=product.name,
        hero_text="Видалення продукту можливе лише тоді, коли він не використовується в існуючих замовленнях.",
        confirm_label="Видалити продукт",
        cancel_url=_safe_next_url(request) or fallback_url,
        delete_error=delete_error,
    )
