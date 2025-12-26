from django.db import models
from django.conf import settings  # ← ДОЛЖЕН быть только этот импорт
from manufacture.models import ProductionSlot
from django.core.exceptions import ValidationError


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
    email = models.EmailField("Email", blank=True)

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
    email = models.EmailField("Email", blank=True)

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

    def __str__(self):
        return f"{self.name} ({self.sku})" if self.sku else self.name



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
        SHIPPED = "shipped", "Відправлений"
        COMPLETED = "completed", "Завершений"
        CANCELED = "canceled", "Відмінений"

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
    contact = models.ForeignKey(
        "crm.Contact",
        related_name="tasks",
        on_delete=models.CASCADE,
        verbose_name="Клієнт",
    )

    title = models.CharField("Назва задачі", max_length=255)

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

    date = models.DateField("Дата задачі", db_index=True)

    status = models.BooleanField(
        "Виконано",
        default=False,
        help_text="Позначає, чи задача виконана",
    )

    comment = models.TextField("Коментар", blank=True)

    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачі"
        ordering = ["date", "id"]

    def __str__(self):
        return f"{self.title} ({'виконано' if self.status else 'не виконано'})"
