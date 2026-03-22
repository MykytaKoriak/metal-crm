from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import UserProfile


ROLE_ACCOUNT_SPECS = (
    {
        "role": UserProfile.Role.ADMIN,
        "email": "admin@mkcrm.local",
        "full_name": "Системний адміністратор",
        "phone": "+380000000001",
    },
    {
        "role": UserProfile.Role.SALES_MANAGER,
        "email": "sales.manager@mkcrm.local",
        "full_name": "Менеджер з продажу",
        "phone": "+380000000002",
    },
    {
        "role": UserProfile.Role.PRODUCTION,
        "email": "production@mkcrm.local",
        "full_name": "Технолог виробництва",
        "phone": "+380000000003",
    },
    {
        "role": UserProfile.Role.EXECUTIVE,
        "email": "executive@mkcrm.local",
        "full_name": "Керівник",
        "phone": "+380000000004",
    },
)


class Command(BaseCommand):
    help = "Create one active account for each internal role"

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="ChangeMe123!",
            help="Password for newly created role accounts. Default: ChangeMe123!",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Reset password for existing role accounts to the provided password.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]
        reset_password = options["reset_password"]
        user_model = get_user_model()

        self.stdout.write(self.style.MIGRATE_HEADING("Creating role accounts"))

        for spec in ROLE_ACCOUNT_SPECS:
            email = spec["email"].lower()
            user, created = user_model.objects.get_or_create(
                username=email,
                defaults={
                    "email": email,
                    "is_active": True,
                },
            )

            updated_fields = []
            if user.email != email:
                user.email = email
                updated_fields.append("email")
            if user.username != email:
                user.username = email
                updated_fields.append("username")
            if not user.is_active:
                user.is_active = True
                updated_fields.append("is_active")

            if created or reset_password:
                user.set_password(password)
                updated_fields.append("password")

            if updated_fields:
                user.save(update_fields=updated_fields)

            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.full_name = spec["full_name"]
            profile.phone = spec["phone"]
            profile.role = spec["role"]
            profile.save()

            state = "created" if created else "updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{state}: {email} -> {profile.get_role_display()}"
                )
            )

        self.stdout.write(self.style.WARNING(f"Password for new accounts: {password}"))
