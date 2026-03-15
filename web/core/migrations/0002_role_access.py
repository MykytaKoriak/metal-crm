from django.db import migrations


ROLE_GROUP_NAMES = {
    "admin": "Role: Administrator",
    "sales_manager": "Role: Sales manager",
    "production": "Role: Production / Technologist",
    "executive": "Role: Executive",
}

FULL_ACCESS_ACTIONS = ("add", "change", "delete", "view")
READ_ONLY_ACTIONS = ("view",)


def permission_set(app_label, model_name, actions):
    return {f"{app_label}.{action}_{model_name}" for action in actions}


ROLE_PERMISSION_MATRIX = {
    "admin": (
        permission_set("auth", "user", FULL_ACCESS_ACTIONS)
        | permission_set("auth", "group", FULL_ACCESS_ACTIONS)
        | permission_set("core", "userprofile", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "tag", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "client", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "contact", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "product", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "orderitem", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "order", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "task", FULL_ACCESS_ACTIONS)
        | permission_set("manufacture", "machine", FULL_ACCESS_ACTIONS)
        | permission_set("manufacture", "workunit", FULL_ACCESS_ACTIONS)
        | permission_set("manufacture", "productionslot", FULL_ACCESS_ACTIONS)
    ),
    "sales_manager": (
        permission_set("crm", "tag", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "client", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "contact", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "orderitem", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "order", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "task", FULL_ACCESS_ACTIONS)
        | permission_set("crm", "product", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "machine", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "workunit", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "productionslot", READ_ONLY_ACTIONS)
    ),
    "production": (
        permission_set("crm", "tag", READ_ONLY_ACTIONS)
        | permission_set("crm", "client", READ_ONLY_ACTIONS)
        | permission_set("crm", "contact", READ_ONLY_ACTIONS)
        | permission_set("crm", "product", READ_ONLY_ACTIONS)
        | permission_set("crm", "orderitem", READ_ONLY_ACTIONS)
        | permission_set("crm", "order", READ_ONLY_ACTIONS)
        | permission_set("crm", "task", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "machine", FULL_ACCESS_ACTIONS)
        | permission_set("manufacture", "workunit", FULL_ACCESS_ACTIONS)
        | permission_set("manufacture", "productionslot", FULL_ACCESS_ACTIONS)
    ),
    "executive": (
        permission_set("crm", "tag", READ_ONLY_ACTIONS)
        | permission_set("crm", "client", READ_ONLY_ACTIONS)
        | permission_set("crm", "contact", READ_ONLY_ACTIONS)
        | permission_set("crm", "product", READ_ONLY_ACTIONS)
        | permission_set("crm", "orderitem", READ_ONLY_ACTIONS)
        | permission_set("crm", "order", READ_ONLY_ACTIONS)
        | permission_set("crm", "task", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "machine", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "workunit", READ_ONLY_ACTIONS)
        | permission_set("manufacture", "productionslot", READ_ONLY_ACTIONS)
    ),
}


def sync_role_access(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    UserProfile = apps.get_model("core", "UserProfile")

    for role, group_name in ROLE_GROUP_NAMES.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        permissions = []
        for permission_name in ROLE_PERMISSION_MATRIX[role]:
            app_label, codename = permission_name.split(".", 1)
            permission = Permission.objects.filter(
                content_type__app_label=app_label,
                codename=codename,
            ).first()
            if permission:
                permissions.append(permission)
        group.permissions.set(permissions)

    role_group_names = tuple(ROLE_GROUP_NAMES.values())
    for profile in UserProfile.objects.select_related("user").all():
        user = profile.user
        desired_group = Group.objects.get(name=ROLE_GROUP_NAMES[profile.role])
        preserved_group_ids = list(
            user.groups.exclude(name__in=role_group_names).values_list("id", flat=True)
        )
        user.groups.set([*preserved_group_ids, desired_group.id])
        should_be_staff = user.is_active
        if user.is_staff != should_be_staff:
            user.is_staff = should_be_staff
            user.save(update_fields=["is_staff"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("crm", "0003_product_olx_url_product_photos_url_and_more"),
        ("manufacture", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(sync_role_access, migrations.RunPython.noop),
    ]
