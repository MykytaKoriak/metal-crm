from django.db import models

class Machine(models.Model):
    class MachineType(models.TextChoices):
        LASER = "laser", "Лазер"
        BENDING = "bending", "Гибка"
        WELDING = "welding", "Сварка"
        PAINTING = "painting", "Покраска"
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
        help_text="Якщо порожньо — використовується загальний графік",
    )

    workday_end = models.TimeField(
        "Кінець робочого дня",
        null=True,
        blank=True,
        help_text="Якщо порожньо — використовується загальний графік",
    )

    comment = models.TextField("Коментар", blank=True)

    class Meta:
        verbose_name = "Верстат"
        verbose_name_plural = "Верстати"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class WorkUnit(models.Model):
    """
    Робоча виробнича одиниця: зварювальна дільниця, фарбувальна камера тощо.
    """
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


class ProductionSlot(models.Model):
    order = models.ForeignKey(
        "crm.Order",
        related_name="slots",
        on_delete=models.CASCADE,
        verbose_name="Замовлення",
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
        return f"{self.order} – {location}"
