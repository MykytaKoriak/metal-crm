from multiprocessing.connection import Client

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import Contact, Tag, Order, Task, Product, OrderItem, Client
from manufacture.models import ProductionSlot


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ['name']
    list_display = ['name']


class OrderInline(admin.TabularInline):
    model = Order
    extra = 0
    fields = ["status", "deadline", "created_at", "payment_amount"]
    readonly_fields = ["created_at"]


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ["title", "assigned_to", "assigned_by", "date", "status", "comment"]
    readonly_fields = ["assigned_by", "created_at"]


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("full_name", "position", "phone", "email", "source", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "client_type", "tax_code", "phones", "email", "source", "created_at")
    list_filter = ("client_type", "source", "tags")
    search_fields = ("name", "tax_code", "phones", "email")
    filter_horizontal = ("tags",)
    readonly_fields = ("created_at", "updated_at")

    inlines = [ContactInline]

    fieldsets = (
        ("Основна інформація", {
            "fields": ("name", "client_type", "tax_code", "phones", "email", "source", "tags")
        }),
        ("Примітки", {"fields": ("notes",)}),
        ("Службова інформація", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("full_name", "client", "position", "phone", "email", "source", "created_at")
    list_filter = ("source", "tags", "created_at")
    search_fields = ("full_name", "position", "phone", "email", "client__name", "client__tax_code")
    autocomplete_fields = ("client",)
    filter_horizontal = ("tags",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Прив’язка", {"fields": ("client",)}),
        ("Дані контакту", {"fields": ("full_name", "position", "phone", "email", "source", "tags")}),
        ("Примітки", {"fields": ("notes",)}),
        ("Службова інформація", {"fields": ("created_at", "updated_at")}),
    )


class ProductionSlotInline(admin.TabularInline):
    model = ProductionSlot
    extra = 0
    fields = ["machine", "work_unit", "start_datetime", "end_datetime", "comment"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sku", "base_price", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "sku", "description"]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    autocomplete_fields = ["product"]
    fields = ["product", "quantity", "unit_price", "comment"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "title_display",
        "contact",
        "status",
        "deadline",
        "created_at",
        "payment_amount",
        "payment_type",
        "delivery_method",
    ]
    list_filter = ["status", "payment_type", "delivery_method"]
    search_fields = ["title", "contact__full_name", "contact__phone", "contact__email", "tracking_number"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    inlines = [OrderItemInline, ProductionSlotInline]

    readonly_fields = ["created_at", "items_total", "title", "copy_delivery_request"]

    fieldsets = (
        ("Основна інформація", {
            "fields": (
                "contact",
                "status",
                "title",
                ("deadline", "created_at"),
                "comment",
            )
        }),
        ("Доставка", {
            "fields": (
                "delivery_method",      # спосіб доставки
                "shipping_address",     # адреса/відділення
                "recipient",            # ім’я отримувача
                "recipient_phone",      # ✅ нове поле
                "tracking_number",
                "copy_delivery_request" # ✅ копіювання в 1 клік
            ),
        }),
        ("Оплата", {
            "fields": (
                "payment_type",
                "payment_terms",        # ✅ нове поле
                "payment_amount",
                "items_total",
            )
        }),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Після того як інлайни (OrderItem) збережені — перераховуємо title
        obj = form.instance
        obj.refresh_title(save=True)

    @admin.display(description="Назва замовлення")
    def title_display(self, obj):
        return obj.title or "—"

    def items_total(self, obj):
        return obj.calculate_items_total()
    items_total.short_description = "Сума по позиціях"

    @admin.display(description="Запит даних для доставки (копіювання)")
    def copy_delivery_request(self, obj):
        text = (
            "Будь ласка, надайте інформацію для доставки:\n"
            "• ПІБ отримувача\n"
            "• Номер телефону\n"
            "• Місто\n"
            "• Відділення/адреса доставки\n"
            "• Перевізник\n"
        )
        # Кнопка копіювання прямо в адмінці (без окремих файлів JS)
        return format_html(
            """
            <div style="max-width: 700px;">
              <textarea id="delivery_req" rows="6" style="width:100%; font-family: monospace;">{}</textarea>
              <button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById('delivery_req').value)">
                Скопіювати в буфер
              </button>
            </div>
            """,
            text
        )



@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        "title_link",     # клик по задаче ведёт на картку клієнта
        "contact_link",
        "assigned_to",
        "assigned_by",
        "date",           # ← ВАЖНО: date, не due_date
        "status",
    ]

    list_display_links = None

    list_filter = [
        # если делал кастомные фильтры:
        # AssignedToMeFilter,
        # AssignedByMeFilter,
        "status",
    ]

    search_fields = ["title", "contact__full_name", "contact__phone", "contact__email"]
    ordering = ["date", "id"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("contact", "assigned_to", "assigned_by")

    @admin.display(description="Назва задачі")
    def title_link(self, obj):
        url = reverse("admin:crm_task_change", args=[obj.id])
        return format_html('<a href="{}">{}</a>', url, obj.title)

    @admin.display(description="Клієнт")
    def contact_link(self, obj):
        client = Contact.objects.get(id=obj.contact.id)
        url = reverse("admin:crm_contact_change", args=[obj.contact_id])
        return format_html('<a href="{}">{}</a>', url, client.full_name)
