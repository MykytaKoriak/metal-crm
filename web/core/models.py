from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Адміністратор"
        SALES_MANAGER = "sales_manager", "Менеджер з продажу"
        PRODUCTION = "production", "Виробництво / технолог"
        EXECUTIVE = "executive", "Керівник"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Користувач",
    )
    full_name = models.CharField("ПІБ", max_length=255, blank=True)
    phone = models.CharField("Телефон", max_length=50, blank=True)
    role = models.CharField(
        "Роль",
        max_length=32,
        choices=Role.choices,
        default=Role.SALES_MANAGER,
    )
    telegram_chat_id = models.CharField("ID Telegram-чату", max_length=64, blank=True)
    telegram_username = models.CharField("Ім’я користувача Telegram", max_length=255, blank=True)
    telegram_link_code = models.CharField(
        "Код прив’язки Telegram",
        max_length=24,
        unique=True,
        blank=True,
    )
    telegram_linked_at = models.DateTimeField("Прив’язано до Telegram", null=True, blank=True)
    telegram_notifications_enabled = models.BooleanField("Telegram-сповіщення увімкнено", default=True)
    telegram_notify_new_tasks = models.BooleanField("Сповіщати про нові задачі", default=True)
    telegram_notify_new_orders = models.BooleanField("Сповіщати про нові замовлення", default=True)
    telegram_notify_deadlines = models.BooleanField("Сповіщати про наближення дедлайнів", default=True)
    telegram_notify_overdue = models.BooleanField("Сповіщати про прострочення", default=True)
    telegram_notify_order_updates = models.BooleanField("Сповіщати про оновлення замовлень", default=True)
    telegram_notify_comments = models.BooleanField("Сповіщати про коментарі", default=True)
    telegram_notify_production_events = models.BooleanField("Сповіщати про події виробництва", default=True)

    class Meta:
        verbose_name = "Профіль користувача"
        verbose_name_plural = "Профілі користувачів"
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
        PROCESSED = "processed", "Оброблено"
        IGNORED = "ignored", "Проігноровано"
        FAILED = "failed", "Помилка"

    update_id = models.BigIntegerField("ID оновлення Telegram", unique=True, db_index=True)
    chat_id = models.CharField("ID Telegram-чату", max_length=64, blank=True)
    username = models.CharField("Ім’я користувача Telegram", max_length=255, blank=True)
    update_type = models.CharField("Тип оновлення", max_length=32, blank=True)
    payload = models.JSONField("Вміст", default=dict, blank=True)
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSED,
        db_index=True,
    )
    error_message = models.TextField("Текст помилки", blank=True)
    processed_at = models.DateTimeField("Оброблено", default=timezone.now, db_index=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Журнал оновлень Telegram"
        verbose_name_plural = "Журнал оновлень Telegram"
        ordering = ("-processed_at", "-id")

    def __str__(self):
        return f"Оновлення {self.update_id} ({self.get_status_display()})"


class TelegramNotification(models.Model):
    class Type(models.TextChoices):
        TASK_CREATED = "task_created", "Створено задачу"
        TASK_COMMENT = "task_comment", "Коментар до задачі"
        ORDER_CREATED = "order_created", "Створено замовлення"
        ORDER_COMMENT = "order_comment", "Коментар до замовлення"
        TASK_DEADLINE = "task_deadline", "Дедлайн задачі"
        TASK_OVERDUE = "task_overdue", "Прострочена задача"
        ORDER_STATUS = "order_status", "Змінено статус замовлення"
        ORDER_DEADLINE = "order_deadline", "Дедлайн замовлення"
        ORDER_OVERDUE = "order_overdue", "Прострочене замовлення"
        PRODUCTION_EVENT = "production_event", "Подія виробництва"

    class Status(models.TextChoices):
        PENDING = "pending", "У черзі"
        SENT = "sent", "Надіслано"
        SKIPPED = "skipped", "Пропущено"
        FAILED = "failed", "Помилка"

    profile = models.ForeignKey(
        "core.UserProfile",
        related_name="telegram_notifications",
        on_delete=models.CASCADE,
        verbose_name="Профіль",
    )
    notification_type = models.CharField(
        "Тип сповіщення",
        max_length=32,
        choices=Type.choices,
        db_index=True,
    )
    dedupe_key = models.CharField("Ключ дедуплікації", max_length=255, unique=True)
    message_text = models.TextField("Текст повідомлення")
    payload = models.JSONField("Вміст", default=dict, blank=True)
    task = models.ForeignKey(
        "crm.Task",
        related_name="telegram_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Задача",
    )
    order = models.ForeignKey(
        "crm.Order",
        related_name="telegram_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Замовлення",
    )
    stage = models.ForeignKey(
        "manufacture.ProductionStage",
        related_name="telegram_notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Виробничий етап",
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    scheduled_for = models.DateTimeField("Заплановано на", default=timezone.now, db_index=True)
    sent_at = models.DateTimeField("Надіслано", null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField("Спроб доставки", default=0)
    error_message = models.TextField("Текст помилки", blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Telegram-сповіщення"
        verbose_name_plural = "Telegram-сповіщення"
        ordering = ("scheduled_for", "id")

    def __str__(self):
        return f"{self.get_notification_type_display()} -> {self.profile.display_name}"


class ChangeAuditLog(models.Model):
    class EntityType(models.TextChoices):
        ORDER = "order", "Замовлення"
        TASK = "task", "Задача"
        PRODUCTION_STAGE = "production_stage", "Виробничий етап"
        PRODUCTION_SLOT = "production_slot", "Виробничий слот"

    class Action(models.TextChoices):
        CREATED = "created", "Створено"
        UPDATED = "updated", "Оновлено"
        DELETED = "deleted", "Видалено"

    entity_type = models.CharField(
        "Тип сутності",
        max_length=32,
        choices=EntityType.choices,
        db_index=True,
    )
    action = models.CharField(
        "Дія",
        max_length=16,
        choices=Action.choices,
        db_index=True,
    )
    object_id = models.PositiveBigIntegerField("ID об’єкта", db_index=True)
    object_label = models.CharField("Назва об’єкта", max_length=255, blank=True)
    changed_fields = models.JSONField("Змінені поля", default=list, blank=True)
    snapshot_before = models.JSONField("Було", default=dict, blank=True)
    snapshot_after = models.JSONField("Стало", default=dict, blank=True)
    note = models.CharField("Примітка", max_length=255, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Хто змінив",
    )
    order = models.ForeignKey(
        "crm.Order",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Замовлення",
    )
    task = models.ForeignKey(
        "crm.Task",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Задача",
    )
    stage = models.ForeignKey(
        "manufacture.ProductionStage",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Виробничий етап",
    )
    slot = models.ForeignKey(
        "manufacture.ProductionSlot",
        related_name="change_audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        verbose_name="Виробничий слот",
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Журнал змін"
        verbose_name_plural = "Журнал змін"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_entity_type_display()} #{self.object_id} / {self.get_action_display()}"
