from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        SALES_MANAGER = "sales_manager", "Sales manager"
        PRODUCTION = "production", "Production / Technologist"
        EXECUTIVE = "executive", "Executive"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="User",
    )
    full_name = models.CharField("Full name", max_length=255, blank=True)
    phone = models.CharField("Phone", max_length=50, blank=True)
    role = models.CharField(
        "Role",
        max_length=32,
        choices=Role.choices,
        default=Role.SALES_MANAGER,
    )
    telegram_chat_id = models.CharField("Telegram ID", max_length=64, blank=True)
    telegram_username = models.CharField("Telegram username", max_length=255, blank=True)
    telegram_link_code = models.CharField(
        "Telegram link code",
        max_length=24,
        unique=True,
        blank=True,
    )
    telegram_linked_at = models.DateTimeField("Telegram linked at", null=True, blank=True)
    telegram_notifications_enabled = models.BooleanField("Telegram notifications enabled", default=True)
    telegram_notify_new_tasks = models.BooleanField("Notify about new tasks", default=True)
    telegram_notify_deadlines = models.BooleanField("Notify about approaching deadlines", default=True)
    telegram_notify_overdue = models.BooleanField("Notify about overdue items", default=True)
    telegram_notify_order_updates = models.BooleanField("Notify about order updates", default=True)
    telegram_notify_production_events = models.BooleanField("Notify about production events", default=True)

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"
        ordering = ("full_name", "user__email", "user__username")

    def __str__(self):
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.full_name or self.user.get_full_name() or self.user.email or self.user.username

    def save(self, *args, **kwargs):
        if not self.telegram_link_code:
            self.telegram_link_code = self._generate_link_code()
        super().save(*args, **kwargs)

    @property
    def telegram_is_linked(self) -> bool:
        return bool(self.telegram_chat_id)

    def rotate_telegram_link_code(self, *, save=True) -> str:
        self.telegram_link_code = self._generate_link_code()
        if save:
            self.save(update_fields=["telegram_link_code"])
        return self.telegram_link_code

    def _generate_link_code(self) -> str:
        while True:
            code = get_random_string(12).upper()
            if not UserProfile.objects.filter(telegram_link_code=code).exists():
                return code


class TelegramUpdateLog(models.Model):
    class Status(models.TextChoices):
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    update_id = models.BigIntegerField("Telegram update ID", unique=True, db_index=True)
    chat_id = models.CharField("Telegram chat ID", max_length=64, blank=True)
    username = models.CharField("Telegram username", max_length=255, blank=True)
    update_type = models.CharField("Update type", max_length=32, blank=True)
    payload = models.JSONField("Payload", default=dict, blank=True)
    status = models.CharField(
        "Status",
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSED,
        db_index=True,
    )
    error_message = models.TextField("Error message", blank=True)
    processed_at = models.DateTimeField("Processed at", default=timezone.now, db_index=True)
    created_at = models.DateTimeField("Created at", auto_now_add=True)

    class Meta:
        verbose_name = "Telegram update log"
        verbose_name_plural = "Telegram update logs"
        ordering = ("-processed_at", "-id")

    def __str__(self):
        return f"Update {self.update_id} ({self.get_status_display()})"


class TelegramNotification(models.Model):
    class Type(models.TextChoices):
        TASK_CREATED = "task_created", "Task created"
        TASK_DEADLINE = "task_deadline", "Task deadline"
        TASK_OVERDUE = "task_overdue", "Task overdue"
        ORDER_STATUS = "order_status", "Order status changed"
        ORDER_DEADLINE = "order_deadline", "Order deadline"
        ORDER_OVERDUE = "order_overdue", "Order overdue"
        PRODUCTION_EVENT = "production_event", "Production event"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(
        "core.UserProfile",
        related_name="telegram_notifications",
        on_delete=models.CASCADE,
        verbose_name="Profile",
    )
    notification_type = models.CharField(
        "Notification type",
        max_length=32,
        choices=Type.choices,
        db_index=True,
    )
    dedupe_key = models.CharField("Dedupe key", max_length=255, unique=True)
    message_text = models.TextField("Message text")
    payload = models.JSONField("Payload", default=dict, blank=True)
    task = models.ForeignKey(
        "crm.Task",
        related_name="telegram_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Task",
    )
    order = models.ForeignKey(
        "crm.Order",
        related_name="telegram_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Order",
    )
    stage = models.ForeignKey(
        "manufacture.ProductionStage",
        related_name="telegram_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Production stage",
    )
    status = models.CharField(
        "Status",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    scheduled_for = models.DateTimeField("Scheduled for", default=timezone.now, db_index=True)
    sent_at = models.DateTimeField("Sent at", null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField("Delivery attempts", default=0)
    error_message = models.TextField("Error message", blank=True)
    created_at = models.DateTimeField("Created at", auto_now_add=True)

    class Meta:
        verbose_name = "Telegram notification"
        verbose_name_plural = "Telegram notifications"
        ordering = ("scheduled_for", "id")

    def __str__(self):
        return f"{self.get_notification_type_display()} -> {self.profile.display_name}"


class ChangeAuditLog(models.Model):
    class EntityType(models.TextChoices):
        ORDER = "order", "Order"
        TASK = "task", "Task"
        PRODUCTION_STAGE = "production_stage", "Production stage"
        PRODUCTION_SLOT = "production_slot", "Production slot"

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"

    entity_type = models.CharField(
        "Entity type",
        max_length=32,
        choices=EntityType.choices,
        db_index=True,
    )
    action = models.CharField(
        "Action",
        max_length=16,
        choices=Action.choices,
        db_index=True,
    )
    object_id = models.PositiveBigIntegerField("Object ID", db_index=True)
    object_label = models.CharField("Object label", max_length=255, blank=True)
    changed_fields = models.JSONField("Changed fields", default=list, blank=True)
    snapshot_before = models.JSONField("Before", default=dict, blank=True)
    snapshot_after = models.JSONField("After", default=dict, blank=True)
    note = models.CharField("Note", max_length=255, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Changed by",
    )
    order = models.ForeignKey(
        "crm.Order",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Order",
    )
    task = models.ForeignKey(
        "crm.Task",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Task",
    )
    stage = models.ForeignKey(
        "manufacture.ProductionStage",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Production stage",
    )
    slot = models.ForeignKey(
        "manufacture.ProductionSlot",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Production slot",
    )
    created_at = models.DateTimeField("Created at", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Change audit log"
        verbose_name_plural = "Change audit logs"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_entity_type_display()} #{self.object_id} / {self.get_action_display()}"
