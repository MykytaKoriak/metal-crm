from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .models import UserProfile


ROLE_GROUP_NAMES = {
    UserProfile.Role.ADMIN: "Role: Administrator",
    UserProfile.Role.SALES_MANAGER: "Role: Sales manager",
    UserProfile.Role.PRODUCTION: "Role: Production / Technologist",
    UserProfile.Role.EXECUTIVE: "Role: Executive",
}

INTERNAL_ROLES = tuple(ROLE_GROUP_NAMES.keys())
FULL_ACCESS_ACTIONS = ("add", "change", "delete", "view")
READ_ONLY_ACTIONS = ("view",)


def _permission_set(app_label, model_name, actions):
    return {f"{app_label}.{action}_{model_name}" for action in actions}


ADMIN_PERMISSIONS = (
    _permission_set("auth", "user", FULL_ACCESS_ACTIONS)
    | _permission_set("auth", "group", FULL_ACCESS_ACTIONS)
    | _permission_set("core", "userprofile", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "tag", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "client", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "contact", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "product", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "orderitem", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "order", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "task", FULL_ACCESS_ACTIONS)
    | _permission_set("manufacture", "machine", FULL_ACCESS_ACTIONS)
    | _permission_set("manufacture", "workunit", FULL_ACCESS_ACTIONS)
    | _permission_set("manufacture", "productionslot", FULL_ACCESS_ACTIONS)
)

SALES_MANAGER_PERMISSIONS = (
    _permission_set("crm", "tag", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "client", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "contact", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "orderitem", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "order", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "task", FULL_ACCESS_ACTIONS)
    | _permission_set("crm", "product", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "machine", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "workunit", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "productionslot", READ_ONLY_ACTIONS)
)

PRODUCTION_PERMISSIONS = (
    _permission_set("crm", "tag", READ_ONLY_ACTIONS)
    | _permission_set("crm", "client", READ_ONLY_ACTIONS)
    | _permission_set("crm", "contact", READ_ONLY_ACTIONS)
    | _permission_set("crm", "product", READ_ONLY_ACTIONS)
    | _permission_set("crm", "orderitem", READ_ONLY_ACTIONS)
    | _permission_set("crm", "order", READ_ONLY_ACTIONS)
    | _permission_set("crm", "task", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "machine", FULL_ACCESS_ACTIONS)
    | _permission_set("manufacture", "workunit", FULL_ACCESS_ACTIONS)
    | _permission_set("manufacture", "productionslot", FULL_ACCESS_ACTIONS)
)

EXECUTIVE_PERMISSIONS = (
    _permission_set("crm", "tag", READ_ONLY_ACTIONS)
    | _permission_set("crm", "client", READ_ONLY_ACTIONS)
    | _permission_set("crm", "contact", READ_ONLY_ACTIONS)
    | _permission_set("crm", "product", READ_ONLY_ACTIONS)
    | _permission_set("crm", "orderitem", READ_ONLY_ACTIONS)
    | _permission_set("crm", "order", READ_ONLY_ACTIONS)
    | _permission_set("crm", "task", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "machine", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "workunit", READ_ONLY_ACTIONS)
    | _permission_set("manufacture", "productionslot", READ_ONLY_ACTIONS)
)

ROLE_PERMISSION_MATRIX = {
    UserProfile.Role.ADMIN: ADMIN_PERMISSIONS,
    UserProfile.Role.SALES_MANAGER: SALES_MANAGER_PERMISSIONS,
    UserProfile.Role.PRODUCTION: PRODUCTION_PERMISSIONS,
    UserProfile.Role.EXECUTIVE: EXECUTIVE_PERMISSIONS,
}


def get_user_role(user):
    if not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return UserProfile.Role.ADMIN
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", None)


def user_has_role(user, *roles):
    return get_user_role(user) in roles


def roles_required(*roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not user_has_role(request.user, *roles):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator


def sync_role_groups(using=None):
    for role, group_name in ROLE_GROUP_NAMES.items():
        group, _ = Group.objects.using(using).get_or_create(name=group_name)
        permissions = []
        for permission_name in ROLE_PERMISSION_MATRIX[role]:
            app_label, codename = permission_name.split(".", 1)
            permission = Permission.objects.using(using).filter(
                content_type__app_label=app_label,
                codename=codename,
            ).first()
            if permission:
                permissions.append(permission)
        group.permissions.set(permissions)


def sync_user_role_membership(user, using=None):
    if not getattr(user, "is_authenticated", False):
        return

    role = get_user_role(user)
    if role not in ROLE_GROUP_NAMES:
        return

    sync_role_groups(using=using)
    role_group_names = tuple(ROLE_GROUP_NAMES.values())
    desired_group = Group.objects.using(using).get(name=ROLE_GROUP_NAMES[role])
    preserved_group_ids = list(
        user.groups.exclude(name__in=role_group_names).values_list("id", flat=True)
    )
    user.groups.set([*preserved_group_ids, desired_group.id])

    should_be_staff = user.is_active and role in INTERNAL_ROLES
    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        user.save(update_fields=["is_staff"])


@receiver(post_migrate)
def configure_role_access(sender, app_config, using, **kwargs):
    sync_role_groups(using=using)
    for profile in UserProfile.objects.select_related("user").all():
        sync_user_role_membership(profile.user, using=using)
