from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Machine(models.Model):
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
    workday_start = models.TimeField(
        "Початок робочого дня",
        null=True,
        blank=True,
        help_text="Якщо порожньо, використовується загальний графік.",
    )
    workday_end = models.TimeField(
        "Кінець робочого дня",
        null=True,
        blank=True,
        help_text="Якщо порожньо, використовується загальний графік.",
    )
    comment = models.TextField("Коментар", blank=True)

    class Meta:
        verbose_name = "Верстат"
        verbose_name_plural = "Верстати"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def has_delete_permission(self, request, obj=None):
        return False


class WorkUnit(models.Model):
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
    comment = models.TextField("Коментар", blank=True)

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
    comment = models.CharField("Коментар", max_length=500, blank=True)

    class Meta:
        verbose_name = "Слот виробництва"
        verbose_name_plural = "Слоти виробництва"
        ordering = ["start_datetime", "id"]

    def __str__(self):
        location = self.machine or self.work_unit
        if self.stage_id:
            return f"{self.stage} – {location}"
        return f"{self.order} – {location}"

    def clean(self):
        super().clean()
        if self.start_datetime and self.end_datetime and self.end_datetime <= self.start_datetime:
            raise ValidationError({"end_datetime": "Кінець слота має бути пізніше за початок."})
        if self.stage_id and self.stage.order_item.order_id != self.order_id:
            raise ValidationError({"stage": "Етап має належати тому ж замовленню, що і слот."})

    def save(self, *args, **kwargs):
        if self.stage_id and not self.order_id:
            self.order = self.stage.order_item.order
        super().save(*args, **kwargs)
