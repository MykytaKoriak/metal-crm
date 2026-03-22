from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from core.visibility import filter_clients_queryset, filter_contacts_queryset, filter_orders_queryset, filter_slots_queryset, filter_tasks_queryset
from .models import (
    Client,
    ClientInteraction,
    Contact,
    Order,
    OrderItem,
    Product,
    ProductProductionNorm,
    Tag,
    Task,
)
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

    def get_queryset(self, request):
        return filter_orders_queryset(request.user, super().get_queryset(request))


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ["title", "priority", "assigned_to", "assigned_by", "date", "status", "comment"]
    readonly_fields = ["assigned_by", "created_at"]

    def get_queryset(self, request):
        return filter_tasks_queryset(request.user, super().get_queryset(request))


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("full_name", "position", "phone", "email", "source", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True

    def get_queryset(self, request):
        return filter_contacts_queryset(request.user, super().get_queryset(request))


class ClientInteractionInline(admin.TabularInline):
    model = ClientInteraction
    extra = 0
    fields = ("event_at", "event_type", "source", "title", "contact", "order", "task", "created_by")
    readonly_fields = ("event_at", "created_by")
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("contact", "order", "task", "created_by")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace_link", "client_type", "tax_code", "phones", "email", "source", "created_at")
    list_filter = ("client_type", "source", "tags")
    search_fields = ("name", "tax_code", "phones", "email")
    filter_horizontal = ("tags",)
    readonly_fields = ("created_at", "updated_at")

    inlines = [ContactInline, ClientInteractionInline]

    fieldsets = (
        ("Основна інформація", {
            "fields": ("name", "client_type", "tax_code", "phones", "email", "source", "tags")
        }),
        ("Примітки", {"fields": ("notes",)}),
        ("Службова інформація", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Workspace")
    def workspace_link(self, obj):
        url = reverse("client_details", args=[obj.id])
        return format_html('<a href="{}">Open</a>', url)

    def get_queryset(self, request):
        return filter_clients_queryset(request.user, super().get_queryset(request))


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

    def get_queryset(self, request):
        return filter_contacts_queryset(request.user, super().get_queryset(request))


class ProductionSlotInline(admin.TabularInline):
    model = ProductionSlot
    extra = 0
    fields = ["stage", "machine", "work_unit", "start_datetime", "end_datetime", "comment"]

    def get_queryset(self, request):
        return filter_slots_queryset(request.user, super().get_queryset(request))


class ProductProductionNormInline(admin.TabularInline):
    model = ProductProductionNorm
    extra = 0
    fields = (
        "stage_type",
        "time_value",
        "time_unit",
        "material_value",
        "material_unit",
        "version",
        "is_active",
        "comment",
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sku", "base_price", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "sku", "description", "technical_description"]
    inlines = [ProductProductionNormInline]

    fieldsets = (
        ("Основна інформація", {
            "fields": ("name", "sku", "is_active")
        }),
        ("Опис", {
            "fields": ("description", "technical_description")
        }),
        ("Ціна", {
            "fields": ("base_price",)
        }),
        ("Маркетплейси", {
            "fields": ("prom_url", "rozetka_url", "olx_url", "site_url")
        }),
        ("Файли та виробництво", {
            "fields": ("photos_url", "production_norms_url")
        }),
    )


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
        "manager",
        "priority",
        "status",
        "deadline",
        "created_at",
        "payment_amount",
        "payment_type",
        "delivery_method",
    ]
    list_filter = ["priority", "status", "manager", "payment_type", "delivery_method"]
    search_fields = [
        "title",
        "contact__full_name",
        "contact__phone",
        "contact__email",
        "tracking_number",
        "recipient",
        "recipient_phone",
        "payment_terms",
        "manager__email",
        "manager__username",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    inlines = [OrderItemInline, ProductionSlotInline]

    readonly_fields = ["created_at", "items_total", "title", "copy_delivery_request"]

    fieldsets = (
        ("Основна інформація", {
            "fields": (
                "contact",
                "manager",
                "priority",
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

    def save_model(self, request, obj, form, change):
        if not obj.manager and request.user.is_authenticated:
            obj.manager = request.user
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        return filter_orders_queryset(request.user, super().get_queryset(request))

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
        "title_link",
        "client_link",
        "contact_link",
        "order_link",
        "priority",
        "assigned_to",
        "assigned_by",
        "date",
        "status",
    ]

    list_display_links = None

    list_filter = [
        "status",
        "priority",
        "client",
        "assigned_by",
        "assigned_to",
    ]

    search_fields = [
        "title",
        "description",
        "comment",
        "client__name",
        "contact__full_name",
        "contact__phone",
        "contact__email",
        "order__title",
    ]
    ordering = ["date", "id"]
    autocomplete_fields = ["client", "contact", "order", "assigned_to", "assigned_by"]

    def get_queryset(self, request):
        qs = filter_tasks_queryset(request.user, super().get_queryset(request))
        return qs.select_related("client", "contact", "order", "assigned_to", "assigned_by")

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Назва задачі")
    def title_link(self, obj):
        url = reverse("admin:crm_task_change", args=[obj.id])
        return format_html('<a href="{}">{}</a>', url, obj.title)

    @admin.display(description="Клієнт")
    def client_link(self, obj):
        url = reverse("admin:crm_client_change", args=[obj.client_id])
        return format_html('<a href="{}">{}</a>', url, obj.client.name)

    @admin.display(description="Контакт")
    def contact_link(self, obj):
        if not obj.contact_id:
            return "—"
        url = reverse("admin:crm_contact_change", args=[obj.contact_id])
        return format_html('<a href="{}">{}</a>', url, obj.contact.full_name)

    @admin.display(description="Замовлення")
    def order_link(self, obj):
        if not obj.order_id:
            return "—"
        url = reverse("admin:crm_order_change", args=[obj.order_id])
        return format_html('<a href="{}">{}</a>', url, obj.order.title or f"Order #{obj.order_id}")


@admin.register(ClientInteraction)
class ClientInteractionAdmin(admin.ModelAdmin):
    list_display = ("event_at", "client", "event_type", "source", "title", "created_by")
    list_filter = ("event_type", "source", "event_at")
    search_fields = ("title", "description", "client__name", "contact__full_name", "order__title", "task__title")
    autocomplete_fields = ("client", "contact", "order", "task", "created_by")
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("client", "contact", "order", "task", "created_by")
            .filter(client__in=filter_clients_queryset(request.user, Client.objects.all()))
        )


@admin.register(ProductProductionNorm)
class ProductProductionNormAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "stage_type",
        "time_value",
        "time_unit",
        "material_value",
        "material_unit",
        "version",
        "is_active",
    )
    list_filter = ("stage_type", "time_unit", "material_unit", "is_active")
    search_fields = ("product__name", "product__sku", "version", "comment")
    autocomplete_fields = ("product",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product")
