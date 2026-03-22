from django.db.models import Q

from core.access import get_user_role
from core.models import UserProfile
from crm.models import Client, Contact, Order, Task
from manufacture.models import ProductionSlot, ProductionStage


UNRESTRICTED_ROLES = {UserProfile.Role.ADMIN, UserProfile.Role.EXECUTIVE}


def _is_authenticated(user):
    return bool(getattr(user, "is_authenticated", False))


def _is_unrestricted(user):
    return get_user_role(user) in UNRESTRICTED_ROLES


def filter_clients_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else Client.objects.all()
    if not _is_authenticated(user):
        return queryset.none()
    role = get_user_role(user)
    if role in UNRESTRICTED_ROLES | {UserProfile.Role.SALES_MANAGER}:
        return queryset
    if role == UserProfile.Role.PRODUCTION:
        return queryset.filter(
            Q(contacts__orders__items__production_stages__isnull=False)
            | Q(tasks__assigned_to=user)
        ).distinct()
    return queryset.none()


def filter_contacts_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else Contact.objects.all()
    if not _is_authenticated(user):
        return queryset.none()
    role = get_user_role(user)
    if role in UNRESTRICTED_ROLES | {UserProfile.Role.SALES_MANAGER}:
        return queryset
    if role == UserProfile.Role.PRODUCTION:
        return queryset.filter(
            Q(orders__items__production_stages__isnull=False)
            | Q(tasks__assigned_to=user)
        ).distinct()
    return queryset.none()


def filter_orders_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else Order.objects.all()
    if not _is_authenticated(user):
        return queryset.none()
    role = get_user_role(user)
    if role in UNRESTRICTED_ROLES:
        return queryset
    if role == UserProfile.Role.SALES_MANAGER:
        return queryset.filter(manager=user)
    if role == UserProfile.Role.PRODUCTION:
        return queryset.filter(items__production_stages__isnull=False).distinct()
    return queryset.none()


def filter_tasks_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else Task.objects.all()
    if not _is_authenticated(user):
        return queryset.none()
    role = get_user_role(user)
    if role in UNRESTRICTED_ROLES:
        return queryset
    if role == UserProfile.Role.SALES_MANAGER:
        return queryset.filter(
            Q(assigned_to=user)
            | Q(assigned_by=user)
            | Q(order__manager=user)
        ).distinct()
    if role == UserProfile.Role.PRODUCTION:
        return queryset.filter(assigned_to=user).distinct()
    return queryset.none()


def filter_stages_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else ProductionStage.objects.all()
    if not _is_authenticated(user):
        return queryset.none()
    role = get_user_role(user)
    if role in UNRESTRICTED_ROLES | {UserProfile.Role.PRODUCTION}:
        return queryset
    if role == UserProfile.Role.SALES_MANAGER:
        return queryset.filter(order_item__order__manager=user).distinct()
    return queryset.none()


def filter_slots_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else ProductionSlot.objects.all()
    if not _is_authenticated(user):
        return queryset.none()
    role = get_user_role(user)
    if role in UNRESTRICTED_ROLES | {UserProfile.Role.PRODUCTION}:
        return queryset
    if role == UserProfile.Role.SALES_MANAGER:
        return queryset.filter(order__manager=user).distinct()
    return queryset.none()


def visible_manager_choices_queryset(user, queryset):
    if _is_unrestricted(user) or get_user_role(user) == UserProfile.Role.PRODUCTION:
        return queryset
    if get_user_role(user) == UserProfile.Role.SALES_MANAGER:
        return queryset.filter(pk=user.pk)
    return queryset.none()


def visible_responsible_choices_queryset(user, queryset):
    if _is_unrestricted(user) or get_user_role(user) == UserProfile.Role.PRODUCTION:
        return queryset
    if get_user_role(user) == UserProfile.Role.SALES_MANAGER:
        return queryset.filter(pk=user.pk)
    return queryset.none()
