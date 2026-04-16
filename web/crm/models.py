from django.db import models
from django.conf import settings  # ← ДОЛЖЕН быть только этот импорт
from manufacture.models import ProductionStage
from django.core.exceptions import ValidationError
from django.utils import timezone


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"

    def __str__(self):
        return self.name


class Client(models.Model):
    class ClientType(models.TextChoices):
        FOP = "fop", "ФОП"
        TOV = "tov", "ТОВ"
        INDIVIDUAL = "individual", "Фізособа"

    class Source(models.TextChoices):
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"
        PROM = "prom", "Prom.ua"
        OLX = "olx", "OLX"
        PHONE = "phone", "Телефон"
        RECOMMENDATION = "recommendation", "Рекомендація"
        WORD_OF_MOUTH = "word_of_mouth", "Сарафанне радіо"
        OTHER = "other", "Інше"

    name = models.CharField("Ім’я / назва клієнта", max_length=255)

    client_type = models.CharField(
        "Тип клієнта",
        max_length=16,
        choices=ClientType.choices,
        default=ClientType.INDIVIDUAL,
    )

    tax_code = models.CharField(
        "Код ЄДРПОУ / РНОКПП",
        max_length=16,
        blank=True,
        help_text="Обов’язково для ФОП/ТОВ.",
    )

    phones = models.CharField("Телефони", max_length=255, blank=True, help_text="Кілька через кому")
    email = models.EmailField("Електронна пошта", blank=True)

    # ✅ додаємо джерело і теги на рівні клієнта (дуже корисно)
    source = models.CharField(
        "Джерело клієнта",
        max_length=32,
        choices=Source.choices,
        default=Source.OTHER,
    )
    tags = models.ManyToManyField("Tag", related_name="clients", blank=True)

    notes = models.TextField("Примітки", blank=True)

    created_at = models.DateTimeField("Створено", auto_now_add=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Клієнт"
        verbose_name_plural = "Клієнти"
        ordering = ("-created_at",)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.client_type in (self.ClientType.FOP, self.ClientType.TOV) and not self.tax_code.strip():
            raise ValidationError({"tax_code": "Для ФОП/ТОВ код ЄДРПОУ/РНОКПП обов’язковий."})
        if self.tax_code:
            t = self.tax_code.strip()
            if not t.isdigit():
                raise ValidationError({"tax_code": "Код має містити лише цифри."})
            if self.client_type == self.ClientType.TOV and len(t) != 8:
                raise ValidationError({"tax_code": "Для ТОВ очікується ЄДРПОУ з 8 цифр."})
            if self.client_type == self.ClientType.FOP and len(t) not in (8, 10):
                raise ValidationError({"tax_code": "Для ФОП очікується 8 або 10 цифр."})


class Contact(models.Model):
    class Source(models.TextChoices):
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"
        PROM = "prom", "Prom.ua"
        OLX = "olx", "OLX"
        PHONE = "phone", "Телефон"
        RECOMMENDATION = "recommendation", "Рекомендація"
        WORD_OF_MOUTH = "word_of_mouth", "Сарафанне радіо"
        OTHER = "other", "Інше"

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="contacts",
        verbose_name="Клієнт",
    )

    full_name = models.CharField("ПІБ", max_length=255)
    position = models.CharField("Посада", max_length=255, blank=True)
    phone = models.CharField("Телефон", max_length=50, blank=True)
    email = models.EmailField("Електронна пошта", blank=True)

    # ✅ повертаємо теги і джерело саме на контакті (як ти і хочеш)
    tags = models.ManyToManyField("Tag", related_name="contacts", blank=True)
    source = models.CharField(
        "Джерело контакту",
        max_length=32,
        choices=Source.choices,
        default=Source.OTHER,
    )

    notes = models.TextField("Примітки", blank=True)

    created_at = models.DateTimeField("Створено", auto_now_add=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Контакт"
        verbose_name_plural = "Контакти"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["client", "full_name"])]

    def __str__(self):
        return f"{self.full_name} ({self.client})"

    def has_delete_permission(self, request, obj=None):
        return False


class Product(models.Model):
    name = models.CharField("Назва продукту", max_length=255)
    sku = models.CharField(
        "Артикул / код",
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text="Необов’язково, але бажано для уніфікації"
    )

    description = models.TextField("Опис", blank=True)
    technical_description = models.TextField("Технічний опис", blank=True)

    base_price = models.DecimalField(
        "Базова ціна",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # Маркетплейси
    prom_url = models.URLField("Prom.ua", blank=True)
    rozetka_url = models.URLField("Rozetka", blank=True)
    olx_url = models.URLField("OLX", blank=True)
    site_url = models.URLField("Сайт", blank=True)

    # Медіа / виробництво
    photos_url = models.URLField(
        "Посилання на фото",
        blank=True,
        help_text="Google Drive / Dropbox / CDN"
    )
    production_norms_url = models.URLField(
        "Норми виробництва (Google Drive)",
        blank=True
    )

    is_active = models.BooleanField("Активний", default=True)

    class Meta:
        verbose_name = "Продукт"
        verbose_name_plural = "Продукти"
        ordering = ["name"]

    track_inventory = models.BooleanField("Вести складський облік", default=True)
    is_material = models.BooleanField("Є матеріалом", default=False)
    min_stock_level = models.DecimalField("Мінімальний залишок", max_digits=14, decimal_places=3, default=0)
    unit = models.CharField("Одиниця виміру", max_length=32, default="шт", blank=True)

    def __str__(self):
        return f"{self.name} ({self.sku})" if self.sku else self.name



class ProductProductionNorm(models.Model):
    class TimeUnit(models.TextChoices):
        MINUTES = "minutes", "Хвилини"
        HOURS = "hours", "Години"

    class MaterialUnit(models.TextChoices):
        PIECE = "piece", "шт"
        SQUARE_METER = "square_meter", "м²"
        KILOGRAM = "kilogram", "кг"
        METER = "meter", "м"
        LITER = "liter", "л"
        OTHER = "other", "Інше"

    product = models.ForeignKey(
        Product,
        related_name="production_norms",
        on_delete=models.CASCADE,
        verbose_name="Продукт",
    )
    stage_type = models.CharField(
        "Тип етапу",
        max_length=32,
        choices=ProductionStage.StageType.choices,
        db_index=True,
    )
    time_value = models.DecimalField(
        "Норма часу",
        max_digits=8,
        decimal_places=2,
        help_text="Тривалість етапу для однієї одиниці продукції.",
    )
    time_unit = models.CharField(
        "Одиниця норми часу",
        max_length=16,
        choices=TimeUnit.choices,
        default=TimeUnit.HOURS,
    )
    material_value = models.DecimalField(
        "Норма матеріалу",
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
    )
    material_unit = models.CharField(
        "Одиниця норми матеріалу",
        max_length=24,
        choices=MaterialUnit.choices,
        default=MaterialUnit.PIECE,
    )
    version = models.CharField("Версія", max_length=50, default="v1", blank=True)
    comment = models.CharField("Коментар", max_length=255, blank=True)
    is_active = models.BooleanField("Активний норматив", default=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Норматив виробництва"
        verbose_name_plural = "Нормативи виробництва"
        ordering = ["product__name", "stage_type", "-is_active", "-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "stage_type", "version"],
                name="crm_unique_product_stage_norm_version",
            )
        ]

    def __str__(self):
        return f"{self.product} / {self.get_stage_type_display()} / {self.time_value} {self.get_time_unit_display()}"

    def clean(self):
        super().clean()
        if self.time_value is None or self.time_value <= 0:
            raise ValidationError({"time_value": "Норма часу має бути більшою за нуль."})
        if self.material_value is not None and self.material_value <= 0:
            raise ValidationError({"material_value": "Норма матеріалу має бути більшою за нуль."})
        if self.version is not None:
            self.version = self.version.strip() or "v1"

    def get_time_minutes(self):
        if self.time_unit == self.TimeUnit.MINUTES:
            return float(self.time_value)
        return float(self.time_value) * 60.0


class Warehouse(models.Model):
    class WarehouseType(models.TextChoices):
        RAW = "raw", "Сировина"
        WIP = "wip", "Незавершене виробництво"
        FINISHED = "finished", "Готова продукція"

    name = models.CharField("Назва складу", max_length=255, unique=True)
    type = models.CharField(
        "Тип складу",
        max_length=16,
        choices=WarehouseType.choices,
        default=WarehouseType.RAW,
        db_index=True,
    )
    is_active = models.BooleanField("Активний", default=True)

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склади"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class InventoryBalance(models.Model):
    product = models.ForeignKey(
        Product,
        related_name="inventory_balances",
        on_delete=models.CASCADE,
        verbose_name="Продукт",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        related_name="inventory_balances",
        on_delete=models.CASCADE,
        verbose_name="Склад",
    )
    quantity = models.DecimalField("Кількість", max_digits=14, decimal_places=3, default=0)
    reserved_quantity = models.DecimalField("Зарезервовано", max_digits=14, decimal_places=3, default=0)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Залишок на складі"
        verbose_name_plural = "Залишки на складах"
        ordering = ["product__name", "warehouse__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "warehouse"],
                name="crm_unique_inventory_balance_product_warehouse",
            )
        ]

    def __str__(self):
        return f"{self.product} / {self.warehouse}"

    @property
    def available(self):
        return self.quantity - self.reserved_quantity

    def clean(self):
        super().clean()
        if self.quantity < 0:
            raise ValidationError({"quantity": "Кількість не може бути від'ємною."})
        if self.reserved_quantity < 0:
            raise ValidationError({"reserved_quantity": "Резерв не може бути від'ємним."})
        if self.reserved_quantity > self.quantity:
            raise ValidationError({"reserved_quantity": "Резерв не може перевищувати залишок."})

    def save(self, *args, **kwargs):
        service_operation = getattr(self, "_inventory_service_operation", False)
        if not service_operation:
            if self.pk:
                previous = type(self).objects.filter(pk=self.pk).values("quantity", "reserved_quantity").first()
                if previous and (
                    previous["quantity"] != self.quantity
                    or previous["reserved_quantity"] != self.reserved_quantity
                ):
                    raise ValidationError("Пряме редагування залишків заборонено. Використовуйте складські операції.")
            elif self.quantity or self.reserved_quantity:
                raise ValidationError("Початковий залишок можна створювати лише через складську операцію.")
        self.full_clean()
        super().save(*args, **kwargs)


class InventoryReceipt(models.Model):
    warehouse = models.ForeignKey(
        Warehouse,
        related_name="inventory_receipts",
        on_delete=models.PROTECT,
        verbose_name="Склад",
    )
    supplier_name = models.CharField("Постачальник", max_length=255, blank=True)
    invoice_number = models.CharField("Номер накладної", max_length=100, blank=True)
    document_date = models.DateField("Дата накладної", default=timezone.localdate, db_index=True)
    comment = models.CharField("Коментар", max_length=255, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="inventory_receipts_created",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Ким створено",
    )
    posted_at = models.DateTimeField("Проведено", null=True, blank=True, db_index=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="inventory_receipts_posted",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Ким проведено",
    )

    class Meta:
        verbose_name = "Прибуткова накладна"
        verbose_name_plural = "Прибуткові накладні"
        ordering = ["-document_date", "-id"]

    def __str__(self):
        number = self.invoice_number or f"Накладна #{self.pk or 'new'}"
        return f"{number} · {self.document_date:%d.%m.%Y}"


class InventoryReceiptItem(models.Model):
    receipt = models.ForeignKey(
        InventoryReceipt,
        related_name="items",
        on_delete=models.CASCADE,
        verbose_name="Накладна",
    )
    product = models.ForeignKey(
        Product,
        related_name="inventory_receipt_items",
        on_delete=models.PROTECT,
        verbose_name="Матеріал",
    )
    quantity = models.DecimalField("Кількість", max_digits=14, decimal_places=3)
    comment = models.CharField("Коментар", max_length=255, blank=True)

    class Meta:
        verbose_name = "Позиція накладної"
        verbose_name_plural = "Позиції накладної"
        ordering = ["receipt_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["receipt", "product"],
                name="crm_unique_inventory_receipt_item_product",
            )
        ]

    def __str__(self):
        return f"{self.receipt} / {self.product} / {self.quantity}"

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({"quantity": "Кількість у накладній має бути більшою за нуль."})
        if self.product_id and not self.product.is_material:
            raise ValidationError({"product": "У прибутковій накладній для матеріалів можна вказувати лише матеріали."})


class ProductBOM(models.Model):
    product = models.ForeignKey(
        Product,
        related_name="bom_items",
        on_delete=models.CASCADE,
        verbose_name="Продукт",
    )
    material = models.ForeignKey(
        Product,
        related_name="used_in_bom",
        on_delete=models.PROTECT,
        verbose_name="Матеріал",
    )
    quantity = models.DecimalField("Кількість", max_digits=14, decimal_places=3)

    class Meta:
        verbose_name = "Склад продукту"
        verbose_name_plural = "Склад продуктів"
        ordering = ["product__name", "material__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "material"],
                name="crm_unique_product_bom_material",
            )
        ]

    def __str__(self):
        return f"{self.product} / {self.material} / {self.quantity}"

    def clean(self):
        super().clean()
        if self.product_id and self.material_id and self.product_id == self.material_id:
            raise ValidationError({"material": "Матеріал не може збігатися з самим продуктом."})
        if self.quantity <= 0:
            raise ValidationError({"quantity": "Кількість у складі продукту має бути більшою за нуль."})
        if self.material_id and not self.material.is_material:
            raise ValidationError({"material": "У склад продукту можна додавати лише продукти, позначені як матеріали."})


class InventoryTransaction(models.Model):
    class TransactionType(models.TextChoices):
        IN = "in", "Прихід"
        OUT = "out", "Списання"
        MOVE = "move", "Переміщення"
        RESERVE = "reserve", "Резерв"
        RELEASE = "release", "Зняття резерву"

    type = models.CharField("Тип операції", max_length=16, choices=TransactionType.choices, db_index=True)
    product = models.ForeignKey(
        Product,
        related_name="inventory_transactions",
        on_delete=models.PROTECT,
        verbose_name="Продукт",
    )
    quantity = models.DecimalField("Кількість", max_digits=14, decimal_places=3)
    warehouse_from = models.ForeignKey(
        Warehouse,
        related_name="outgoing_transactions",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Склад джерело",
    )
    warehouse_to = models.ForeignKey(
        Warehouse,
        related_name="incoming_transactions",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Склад призначення",
    )
    order = models.ForeignKey(
        "crm.Order",
        related_name="inventory_transactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Замовлення",
    )
    order_item = models.ForeignKey(
        "crm.OrderItem",
        related_name="inventory_transactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Позиція замовлення",
    )
    production_stage = models.ForeignKey(
        "manufacture.ProductionStage",
        related_name="inventory_transactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Етап виробництва",
    )
    receipt = models.ForeignKey(
        "crm.InventoryReceipt",
        related_name="transactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Прибуткова накладна",
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="inventory_transactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Ким створено",
    )

    class Meta:
        verbose_name = "Складська операція"
        verbose_name_plural = "Складські операції"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_type_display()} / {self.product} / {self.quantity}"

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({"quantity": "Кількість операції має бути більшою за нуль."})
        if self.type == self.TransactionType.IN and not self.warehouse_to_id:
            raise ValidationError({"warehouse_to": "Для приходу потрібно вказати склад призначення."})
        if self.type in {self.TransactionType.OUT, self.TransactionType.RESERVE, self.TransactionType.RELEASE} and not self.warehouse_from_id:
            raise ValidationError({"warehouse_from": "Для цієї операції потрібно вказати склад джерело."})
        if self.type == self.TransactionType.MOVE and (not self.warehouse_from_id or not self.warehouse_to_id):
            raise ValidationError("Для переміщення потрібно вказати склад джерело та склад призначення.")
        if self.warehouse_from_id and self.warehouse_to_id and self.warehouse_from_id == self.warehouse_to_id:
            raise ValidationError("Склад джерело та склад призначення мають відрізнятися.")


class OrderItem(models.Model):
    order = models.ForeignKey(
        "crm.Order",
        related_name="items",
        on_delete=models.CASCADE,
        verbose_name="Замовлення",
    )
    product = models.ForeignKey(
        Product,
        related_name="order_items",
        on_delete=models.PROTECT,
        verbose_name="Продукт",
    )
    quantity = models.PositiveIntegerField("Кількість", default=1)
    unit_price = models.DecimalField(
        "Ціна за одиницю",
        max_digits=10,
        decimal_places=2,
    )
    comment = models.CharField("Коментар", max_length=255, blank=True)

    class Meta:
        verbose_name = "Позиція замовлення"
        verbose_name_plural = "Позиції замовлення"

    required_materials = models.JSONField("Потрібні матеріали", default=list, blank=True)
    reserved_materials = models.JSONField("Зарезервовані матеріали", default=list, blank=True)
    consumed_materials = models.JSONField("Списані матеріали", default=list, blank=True)

    def __str__(self):
        return f"{self.product} x {self.quantity}"

    @property
    def total_price(self):
        if self.unit_price is not None and self.quantity is not None:
            return self.unit_price * self.quantity
        return None



class Order(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новий"
        IN_PROGRESS = "in_progress", "В роботі"
        IN_PRODUCTION = "in_production", "В виробництві"
        READY = "ready", "Готовий"
        COMPLETED = "completed", "Завершений"
        CANCELED = "canceled", "Скасований"

    class Priority(models.TextChoices):
        LOW = "low", "Низький"
        NORMAL = "normal", "Нормальний"
        HIGH = "high", "Високий"
        URGENT = "urgent", "Терміновий"

    class PaymentType(models.TextChoices):
        COD = "cod", "Післяплата"
        PREPAY = "prepay", "Передоплата"
        PARTIAL_PREPAY = "partial_prepay", "Часткова передоплата"
        CASHLESS = "cashless", "Безготівка"
        FREE = "free", "Гарантія / безкоштовно"

    class DeliveryMethod(models.TextChoices):
        NOVA_POSHTA = "nova_poshta", "Нова Пошта"
        UKRPOSHTA = "ukrposhta", "Укрпошта"
        COURIER = "courier", "Кур’єр"
        PICKUP = "pickup", "Самовивіз"
        OTHER = "other", "Інше"

    contact = models.ForeignKey(
        "crm.Contact",
        related_name="orders",
        on_delete=models.CASCADE,
        verbose_name="Клієнт",
    )

    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="managed_orders",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Manager",
    )

    priority = models.CharField(
        "Пріоритет",
        max_length=16,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )

    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )

    deadline = models.DateField("Дедлайн", null=True, blank=True)
    created_at = models.DateTimeField("Дата створення", auto_now_add=True)

    comment = models.TextField("Додаткові нотатки", blank=True)

    # Додаткові поля
    shipping_address = models.CharField(
        "Адреса відправки",
        max_length=500,
        blank=True,
    )
    tracking_number = models.CharField(
        "№ ТТН",
        max_length=100,
        blank=True,
    )
    recipient = models.CharField(
        "Отримувач",
        max_length=255,
        blank=True,
    )

    payment_amount = models.DecimalField(
        "Сума оплати",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    payment_type = models.CharField(
        "Тип оплати",
        max_length=20,
        choices=PaymentType.choices,
        null=True,
        blank=True,
    )

    delivery_method = models.CharField(
        "Метод доставки",
        max_length=20,
        choices=DeliveryMethod.choices,
        null=True,
        blank=True,
    )

    title = models.CharField(
        "Назва замовлення (авто)",
        max_length=500,
        blank=True,
        editable=False,
        help_text="Формується автоматично з товарів у позиціях замовлення."
    )

    recipient_phone = models.CharField(
        "Телефон отримувача",
        max_length=50,
        blank=True,
    )

    payment_terms = models.CharField(
        "Умови оплати",
        max_length=255,
        blank=True,
        help_text="Напр.: 50% передоплата / оплата при отриманні / оплата 3 дні після відвантаження"
    )

    @staticmethod
    def delivery_request_template():
        return (
            "Будь ласка, надайте інформацію для доставки:\n"
            "- ПІБ отримувача\n"
            "- Номер телефону\n"
            "- Місто\n"
            "- Відділення або адреса доставки\n"
            "- Перевізник\n"
        )

    def get_delivery_request_text(self):
        return self.delivery_request_template()

    def build_title_from_items(self) -> str:
        # товари через кому, унікальні, у стабільному порядку
        names = list(
            self.items.select_related("product")
            .values_list("product__name", flat=True)
        )
        # унікалізація зі збереженням порядку
        seen = set()
        uniq = []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                uniq.append(n)
        return ", ".join(uniq)

    def refresh_title(self, save: bool = True):
        new_title = self.build_title_from_items()
        if new_title != (self.title or ""):
            self.title = new_title
            if save:
                self.save(update_fields=["title"])


    class Meta:
        verbose_name = "Замовлення"
        verbose_name_plural = "Замовлення"
        ordering = ["-created_at"]

    def __str__(self):
        date_str = self.created_at.strftime("%d.%m.%Y %H:%M")
        title = self.title or "Без товарів"
        return f"{date_str} – {self.contact.full_name} – {title} – {self.calculate_items_total()} ({self.get_status_display()})"

    def calculate_items_total(self):
        from django.db.models import F, Sum
        agg = self.items.aggregate(
            total=Sum(F("unit_price") * F("quantity"))
        )
        return agg["total"] or 0

    def has_delete_permission(self, request, obj=None):
        return False  # нельзя удалить нигде в админке




class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Низький"
        NORMAL = "normal", "Нормальний"
        HIGH = "high", "Високий"
        URGENT = "urgent", "Терміновий"

    class Status(models.TextChoices):
        NEW = "new", "Нова"
        IN_PROGRESS = "in_progress", "В роботі"
        WAITING = "waiting", "Очікує"
        DONE = "done", "Виконано"

    OPEN_STATUSES = (Status.NEW, Status.IN_PROGRESS, Status.WAITING)

    client = models.ForeignKey(
        Client,
        related_name="tasks",
        on_delete=models.CASCADE,
        verbose_name="Клієнт",
    )
    contact = models.ForeignKey(
        "crm.Contact",
        related_name="tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Контакт",
    )
    order = models.ForeignKey(
        "crm.Order",
        related_name="tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Замовлення",
    )

    title = models.CharField("Назва задачі", max_length=255)

    description = models.TextField("Опис", blank=True)

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="tasks_assigned_by",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Ким створена",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="tasks_assigned_to",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Кому призначена",
    )

    priority = models.CharField(
        "Пріоритет",
        max_length=16,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )

    date = models.DateField("Дедлайн", db_index=True)

    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )

    comment = models.TextField("Коментар", blank=True)

    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачі"
        ordering = ["date", "id"]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def is_done(self):
        return self.status == self.Status.DONE

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    def clean(self):
        super().clean()

        if self.order_id and not self.contact_id:
            self.contact = self.order.contact

        if not self.client_id and self.contact_id:
            self.client = self.contact.client
        if not self.client_id and self.order_id:
            self.client = self.order.contact.client

        if not self.client_id:
            raise ValidationError({"client": "Задача має бути прив’язана до клієнта."})

        if self.contact_id and self.contact.client_id != self.client_id:
            raise ValidationError({"contact": "Контакт має належати вибраному клієнту."})

        if self.order_id and self.order.contact.client_id != self.client_id:
            raise ValidationError({"order": "Замовлення має належати вибраному клієнту."})

    def save(self, *args, **kwargs):
        if self.order_id and not self.contact_id:
            self.contact = self.order.contact
        if not self.client_id and self.contact_id:
            self.client = self.contact.client
        if not self.client_id and self.order_id:
            self.client = self.order.contact.client
        super().save(*args, **kwargs)


class ClientInteraction(models.Model):
    class EventType(models.TextChoices):
        NOTE = "note", "Нотатка"
        CALL = "call", "Дзвінок"
        MESSAGE = "message", "Повідомлення"
        COMMENT = "comment", "Коментар"
        CONTACT = "contact", "Контакт"
        ORDER = "order", "Замовлення"
        TASK = "task", "Задача"
        PRODUCTION = "production", "Виробництво"
        SYSTEM = "system", "Службова подія"

    class Source(models.TextChoices):
        MANUAL = "manual", "Ручне внесення"
        AUTO = "auto", "Автоматично"
        SYSTEM = "system", "Система"

    client = models.ForeignKey(
        Client,
        related_name="interactions",
        on_delete=models.CASCADE,
        verbose_name="Клієнт",
    )
    contact = models.ForeignKey(
        Contact,
        related_name="interactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Контакт",
    )
    order = models.ForeignKey(
        Order,
        related_name="interactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Замовлення",
    )
    task = models.ForeignKey(
        Task,
        related_name="interactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Задача",
    )
    event_type = models.CharField(
        "Тип події",
        max_length=20,
        choices=EventType.choices,
        default=EventType.SYSTEM,
        db_index=True,
    )
    source = models.CharField(
        "Джерело",
        max_length=16,
        choices=Source.choices,
        default=Source.SYSTEM,
        db_index=True,
    )
    title = models.CharField("Заголовок", max_length=255)
    description = models.TextField("Опис", blank=True)
    event_at = models.DateTimeField("Дата події", default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="client_interactions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Хто зафіксував",
    )
    payload = models.JSONField("Додаткові дані", default=dict, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Історія взаємодії"
        verbose_name_plural = "Історія взаємодій"
        ordering = ["-event_at", "-id"]
        indexes = [
            models.Index(fields=["client", "event_at"]),
            models.Index(fields=["event_type", "event_at"]),
        ]

    def __str__(self):
        return f"{self.client} / {self.title}"

    def clean(self):
        super().clean()
        if self.contact_id and self.contact.client_id != self.client_id:
            raise ValidationError({"contact": "Контакт має належати вибраному клієнту."})
        if self.order_id and self.order.contact.client_id != self.client_id:
            raise ValidationError({"order": "Замовлення має належати вибраному клієнту."})
        if self.task_id and self.task.client_id != self.client_id:
            raise ValidationError({"task": "Задача має належати вибраному клієнту."})

    @property
    def is_manual(self):
        return self.source == self.Source.MANUAL
