from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.models import User

from .models import ChangeAuditLog, TelegramNotification, TelegramUpdateLog, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fields = (
        "full_name",
        "phone",
        "role",
        "telegram_chat_id",
        "telegram_username",
        "telegram_link_code",
        "telegram_linked_at",
        "telegram_notifications_enabled",
        "telegram_notify_new_tasks",
        "telegram_notify_deadlines",
        "telegram_notify_overdue",
        "telegram_notify_order_updates",
        "telegram_notify_production_events",
    )
    readonly_fields = ("telegram_link_code", "telegram_linked_at")


class AppUserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Password confirmation", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("email", "is_staff", "is_superuser", "is_active", "groups")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("Email is required.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.username = self.cleaned_data["email"]
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class AppUserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(label="Password")

    class Meta:
        model = User
        fields = (
            "email",
            "password",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("Email is required.")
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.username = self.cleaned_data["email"]
        if commit:
            user.save()
            self.save_m2m()
        return user


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "user_email",
        "role",
        "phone",
        "telegram_chat_id",
        "telegram_notifications_enabled",
    )
    list_filter = ("role", "telegram_notifications_enabled")
    search_fields = ("full_name", "phone", "telegram_chat_id", "telegram_username", "user__email", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("telegram_link_code", "telegram_linked_at")

    @admin.display(description="Email")
    def user_email(self, obj):
        return obj.user.email


@admin.register(TelegramUpdateLog)
class TelegramUpdateLogAdmin(admin.ModelAdmin):
    list_display = ("update_id", "update_type", "chat_id", "username", "status", "processed_at")
    list_filter = ("status", "update_type")
    search_fields = ("chat_id", "username", "error_message")
    readonly_fields = ("update_id", "chat_id", "username", "update_type", "payload", "status", "error_message", "processed_at", "created_at")


@admin.register(TelegramNotification)
class TelegramNotificationAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "profile", "status", "scheduled_for", "sent_at", "delivery_attempts")
    list_filter = ("notification_type", "status")
    search_fields = ("dedupe_key", "message_text", "profile__full_name", "profile__user__email")
    autocomplete_fields = ("profile", "task", "order", "stage")
    readonly_fields = ("dedupe_key", "message_text", "payload", "delivery_attempts", "sent_at", "created_at", "error_message")


@admin.register(ChangeAuditLog)
class ChangeAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "entity_type", "object_id", "action", "changed_by", "object_label")
    list_filter = ("entity_type", "action")
    search_fields = ("object_label", "note", "changed_by__email", "changed_by__username")
    autocomplete_fields = ("changed_by", "order", "task", "stage", "slot")
    readonly_fields = (
        "entity_type",
        "action",
        "object_id",
        "object_label",
        "changed_fields",
        "snapshot_before",
        "snapshot_after",
        "note",
        "changed_by",
        "order",
        "task",
        "stage",
        "slot",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class AppUserAdmin(DjangoUserAdmin):
    form = AppUserChangeForm
    add_form = AppUserCreationForm
    inlines = (UserProfileInline,)
    list_display = ("email", "profile_name", "profile_role", "is_staff", "is_active", "last_login")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups", "profile__role")
    ordering = ("email",)
    search_fields = ("email", "username", "profile__full_name", "profile__phone")
    readonly_fields = ("last_login", "date_joined", "username")

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_active", "is_staff", "is_superuser", "groups"),
            },
        ),
    )

    @admin.display(description="Full name")
    def profile_name(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.display_name if profile else "-"

    @admin.display(description="Role")
    def profile_role(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.get_role_display() if profile else "-"


admin.site.unregister(User)
admin.site.register(User, AppUserAdmin)
