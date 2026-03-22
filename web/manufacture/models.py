from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


DEFAULT_AVAILABLE_WEEKDAYS = "0,1,2,3,4"
DEFAULT_WORKDAY_START = time(8, 0)
DEFAULT_WORKDAY_END = time(17, 0)


def parse_available_weekdays(raw_value):
    value = (raw_value or DEFAULT_AVAILABLE_WEEKDAYS).strip()
    days = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValidationError("Дні доступності мають бути числами від 0 до 6 через кому.")
        number = int(part)
        if number < 0 or number > 6:
            raise ValidationError("Дні доступності мають бути в діапазоні від 0 до 6.")
        days.add(number)
    if not days:
        raise ValidationError("Потрібно вказати хоча б один день доступності ресурсу.")
    return days


class ResourceAvailabilityMixin(models.Model):
    is_active = models.BooleanField("Активний", default=True)
    available_weekdays = models.CharField(
        "Дні доступності",
        max_length=32,
        default=DEFAULT_AVAILABLE_WEEKDAYS,
        help_text="Номери днів тижня через кому: 0=пн, 1=вт ... 6=нд.",
    )
    workday_start = models.TimeField(
        "Початок робочого дня",
        null=True,
        blank=True,
        help_text="Якщо порожньо, використовується 08:00.",
    )
    workday_end = models.TimeField(
        "Кінець робочого дня",
        null=True,
        blank=True,
        help_text="Якщо порожньо, використовується 17:00.",
    )
    comment = models.TextField("Коментар", blank=True)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        parse_available_weekdays(self.available_weekdays)
        start = self.workday_start or DEFAULT_WORKDAY_START
        end = self.workday_end or DEFAULT_WORKDAY_END
        if end <= start:
            raise ValidationError({"workday_end": "Кінець робочого дня має бути пізніше за початок."})

    def get_available_weekdays_set(self):
        return parse_available_weekdays(self.available_weekdays)

    def get_workday_bounds(self):
        return self.workday_start or DEFAULT_WORKDAY_START, self.workday_end or DEFAULT_WORKDAY_END


class Machine(ResourceAvailabilityMixin, models.Model):
    class MachineType(models.TextChoices):
        LASER = "laser", "Лазер"
        BENDING = "bending", "Гибка"
        WELDING = "welding", "Зварка"
        PAINTING = "painting", "Фарбування"
        OTHER = "other", "Інше"

    name = models.CharField("Назва верстата", max_length=255)
    type = models.CharField(
        "Тип верстата",
        max_length=20,
        choices=MachineType.choices,
        default=MachineType.OTHER,
        db_index=True,
    )

    class Meta:
        verbose_name = "Верстат"
        verbose_name_plural = "Верстати"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def has_delete_permission(self, request, obj=None):
        return False


class WorkUnit(ResourceAvailabilityMixin, models.Model):
    class UnitType(models.TextChoices):
        WELDING = "welding_section", "Зварювальна дільниця"
        PAINTING = "painting_section", "Фарбувальна дільниця"
        ASSEMBLY = "assembly_section", "Збірочна дільниця"
        STORAGE = "storage", "Склад / зберігання"
        OTHER = "other", "Інше"

    name = models.CharField("Назва дільниці", max_length=255)
    type = models.CharField(
        "Тип дільниці",
        max_length=32,
        choices=UnitType.choices,
        default=UnitType.OTHER,
        db_index=True,
    )

    class Meta:
        verbose_name = "Виробнича дільниця"
        verbose_name_plural = "Виробничі дільниці"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def has_delete_permission(self, request, obj=None):
        return False


class ProductionStage(models.Model):
    class StageType(models.TextChoices):
        INTAKE = "intake", "Приймання"
        PROCUREMENT = "procurement", "Закупка матеріалів"
        EXECUTION = "execution", "Виконання"
        PAINTING = "painting", "Фарбування"
        READY_TO_SHIP = "ready_to_ship", "Готово до відправки"

    class Status(models.TextChoices):
        NEW = "new", "Новий"
        SCHEDULED = "scheduled", "Заплановано"
        IN_PROGRESS = "in_progress", "В роботі"
        BLOCKED = "blocked", "Заблоковано"
        CANCELLED = "cancelled", "Скасовано"
        DONE = "done", "Завершено"

    order_item = models.ForeignKey(
        "crm.OrderItem",
        related_name="production_stages",
        on_delete=models.CASCADE,
        verbose_name="Позиція замовлення",
    )
    stage_type = models.CharField(
        "Тип етапу",
        max_length=32,
        choices=StageType.choices,
        default=StageType.EXECUTION,
        db_index=True,
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    sequence = models.PositiveIntegerField("Порядок", default=1, db_index=True)
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="production_stages",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Відповідальний",
    )
    planned_start = models.DateTimeField("Плановий початок", null=True, blank=True)
    planned_end = models.DateTimeField("Планове завершення", null=True, blank=True)
    started_at = models.DateTimeField("Фактичний старт", null=True, blank=True)
    completed_at = models.DateTimeField("Фактичне завершення", null=True, blank=True)
    comment = models.TextField("Коментар", blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Виробничий етап"
        verbose_name_plural = "Виробничі етапи"
        ordering = ["order_item__order_id", "sequence", "id"]

    def __str__(self):
        return f"{self.order_item.order_id} / {self.order_item.product} / {self.get_stage_type_display()}"

    @property
    def order(self):
        return self.order_item.order

    def clean(self):
        super().clean()
        if self.planned_start and self.planned_end and self.planned_end <= self.planned_start:
            raise ValidationError({"planned_end": "Планове завершення має бути пізніше за старт."})
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValidationError({"completed_at": "Фактичне завершення має бути пізніше за старт."})


class ProductionSlot(models.Model):
    class SlotType(models.TextChoices):
        WORK = "work", "Робочий слот"
        SETUP = "setup", "Налаштування"
        TRANSFER = "transfer", "Переміщення"
        BUFFER = "buffer", "Буфер / очікування"
        RESERVATION = "reservation", "Резерв"
        OTHER = "other", "Інше"

    class OperationType(models.TextChoices):
        INTAKE = ProductionStage.StageType.INTAKE, ProductionStage.StageType.INTAKE.label
        PROCUREMENT = ProductionStage.StageType.PROCUREMENT, ProductionStage.StageType.PROCUREMENT.label
        EXECUTION = ProductionStage.StageType.EXECUTION, ProductionStage.StageType.EXECUTION.label
        PAINTING = ProductionStage.StageType.PAINTING, ProductionStage.StageType.PAINTING.label
        READY_TO_SHIP = ProductionStage.StageType.READY_TO_SHIP, ProductionStage.StageType.READY_TO_SHIP.label
        OTHER = "other", "Інша операція"

    class PlanningMode(models.TextChoices):
        AUTO = "auto", "Авто"
        MANUAL = "manual", "Ручний"

    class PlanningSource(models.TextChoices):
        PLANNER = "planner", "Автопланувальник"
        DISPATCHER = "dispatcher", "Диспетчер"
        ADMIN = "admin", "Адміністратор"
        SYSTEM = "system", "Система"
        SEED = "seed", "Демо-наповнення"

    order = models.ForeignKey(
        "crm.Order",
        related_name="slots",
        on_delete=models.CASCADE,
        verbose_name="Замовлення",
    )
    stage = models.ForeignKey(
        ProductionStage,
        related_name="slots",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Етап",
    )
    slot_type = models.CharField(
        "Тип слота",
        max_length=20,
        choices=SlotType.choices,
        default=SlotType.WORK,
        db_index=True,
    )
    operation_type = models.CharField(
        "Тип операції",
        max_length=32,
        choices=OperationType.choices,
        default=OperationType.OTHER,
        db_index=True,
    )
    machine = models.ForeignKey(
        Machine,
        related_name="slots",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Верстат",
    )
    work_unit = models.ForeignKey(
        WorkUnit,
        related_name="slots",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Виробнича дільниця",
    )
    start_datetime = models.DateTimeField("Початок", null=True, blank=True)
    end_datetime = models.DateTimeField("Кінець", null=True, blank=True)
    planning_mode = models.CharField(
        "Режим планування",
        max_length=12,
        choices=PlanningMode.choices,
        default=PlanningMode.AUTO,
        db_index=True,
    )
    planning_source = models.CharField(
        "Джерело планування",
        max_length=16,
        choices=PlanningSource.choices,
        default=PlanningSource.SYSTEM,
        db_index=True,
    )
    is_locked = models.BooleanField(
        "Зафіксовано вручну",
        default=False,
        help_text="Зафіксований слот не буде пересунуто автопланувальником.",
    )
    purpose = models.CharField("Призначення", max_length=255, blank=True)
    comment = models.CharField("Службовий коментар", max_length=500, blank=True)
    dispatcher_comment = models.CharField("Коментар диспетчера", max_length=500, blank=True)

    class Meta:
        verbose_name = "Слот виробництва"
        verbose_name_plural = "Слоти виробництва"
        ordering = ["start_datetime", "id"]

    def __str__(self):
        location = self.machine or self.work_unit
        if self.stage_id:
            return f"{self.stage} - {location}"
        return f"{self.order} - {location}"

    @property
    def resource(self):
        return self.machine or self.work_unit

    @property
    def is_manual(self):
        return self.planning_mode == self.PlanningMode.MANUAL or self.is_locked

    @property
    def is_automatic(self):
        return self.planning_mode == self.PlanningMode.AUTO and not self.is_locked

    def clean(self):
        super().clean()
        if self.stage_id and self.stage.order_item.order_id != self.order_id:
            raise ValidationError({"stage": "Етап має належати тому ж замовленню, що і слот."})
        if bool(self.machine_id) == bool(self.work_unit_id):
            raise ValidationError("Для слота потрібно вказати рівно один ресурс: верстат або дільницю.")
        if self.start_datetime and self.end_datetime and self.end_datetime <= self.start_datetime:
            raise ValidationError({"end_datetime": "Кінець слота має бути пізніше за початок."})
        if self.start_datetime and self.end_datetime:
            from .services import validate_production_slot

            validate_production_slot(self)

    def save(self, *args, **kwargs):
        if self.stage_id and not self.order_id:
            self.order = self.stage.order_item.order
        if self.stage_id and self.operation_type == self.OperationType.OTHER:
            if self.stage.stage_type in dict(self.OperationType.choices):
                self.operation_type = self.stage.stage_type
        if not self.purpose and self.stage_id:
            self.purpose = f"{self.stage.order_item.product.name} / {self.stage.get_stage_type_display()}"
        self.full_clean()
        super().save(*args, **kwargs)


class ResourceDowntime(models.Model):
    class DowntimeType(models.TextChoices):
        MAINTENANCE = "maintenance", "Технічне обслуговування"
        MANUAL_BLOCK = "manual_block", "Ручне блокування"
        DOWNTIME = "downtime", "Простій"
        HOLIDAY = "holiday", "Неробочий інтервал"

    machine = models.ForeignKey(
        Machine,
        related_name="downtimes",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="Верстат",
    )
    work_unit = models.ForeignKey(
        WorkUnit,
        related_name="downtimes",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="Виробнича дільниця",
    )
    start_datetime = models.DateTimeField("Початок")
    end_datetime = models.DateTimeField("Кінець")
    downtime_type = models.CharField(
        "Тип простою",
        max_length=20,
        choices=DowntimeType.choices,
        default=DowntimeType.DOWNTIME,
        db_index=True,
    )
    is_blocking = models.BooleanField("Блокує планування", default=True)
    comment = models.CharField("Коментар", max_length=255, blank=True)

    class Meta:
        verbose_name = "Простій ресурсу"
        verbose_name_plural = "Простої ресурсів"
        ordering = ["start_datetime", "id"]

    def __str__(self):
        resource = self.machine or self.work_unit
        return f"{resource} / {self.get_downtime_type_display()} / {self.start_datetime:%d.%m.%Y %H:%M}"

    @property
    def resource(self):
        return self.machine or self.work_unit

    def clean(self):
        super().clean()
        if bool(self.machine_id) == bool(self.work_unit_id):
            raise ValidationError("Для простою потрібно вказати рівно один ресурс: верстат або дільницю.")
        if self.end_datetime <= self.start_datetime:
            raise ValidationError({"end_datetime": "Кінець простою має бути пізніше за початок."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProductionSlotChangeLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Створено"
        UPDATED = "updated", "Оновлено"
        DELETED = "deleted", "Видалено"

    class Source(models.TextChoices):
        AUTO = "auto", "Автопланувальник"
        MANUAL = "manual", "Ручне редагування"
        SYSTEM = "system", "Система"

    slot = models.ForeignKey(
        ProductionSlot,
        related_name="change_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Слот",
    )
    slot_reference = models.PositiveBigIntegerField("ID слота", null=True, blank=True)
    order = models.ForeignKey(
        "crm.Order",
        related_name="slot_change_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Замовлення",
    )
    stage = models.ForeignKey(
        ProductionStage,
        related_name="slot_change_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Етап",
    )
    action = models.CharField("Дія", max_length=16, choices=Action.choices, db_index=True)
    source = models.CharField("Джерело", max_length=16, choices=Source.choices, default=Source.SYSTEM)
    snapshot_before = models.JSONField("Було", default=dict, blank=True)
    snapshot_after = models.JSONField("Стало", default=dict, blank=True)
    note = models.CharField("Примітка", max_length=255, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="production_slot_change_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Ким змінено",
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Історія зміни слота"
        verbose_name_plural = "Історія змін слотів"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()} / слот #{self.slot_reference or '-'}"
