from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import (Contact, Tag, Order, Task)
from manufacture.models import  ProductionSlot


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ['name']
    list_display = ['name']


class OrderInline(admin.TabularInline):
    model = Order
    extra = 0
    fields = ["title", "status", "deadline", "created_at", "payment_amount"]
    readonly_fields = ["created_at"]


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ["title", "assigned_to", "assigned_by", "date", "status", "comment"]
    readonly_fields = ["assigned_by", "created_at"]


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email', 'source', 'created_at']
    list_filter = ['source', 'tags']  # фільтрація за джерелом + тегами
    search_fields = ['name', 'phone', 'email']
    filter_horizontal = ['tags']  # зручний вибір тегів (Jazzmin зробить красиво)

    inlines = [OrderInline, TaskInline]

    fieldsets = (
        ("Основна інформація", {
            'fields': ('name', 'phone', 'email', 'source', 'tags')
        }),
        ("Коментар", {
            'fields': ('comment',),
        }),
        ("Службова інформація", {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    readonly_fields = ['created_at', 'updated_at']


class ProductionSlotInline(admin.TabularInline):
    model = ProductionSlot
    extra = 0
    fields = ["stage", "machine", "work_unit", "start_datetime", "end_datetime", "comment"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "contact",
        "status",
        "deadline",
        "created_at",
        "payment_amount",
        "payment_type",
        "delivery_method",
    ]
    list_filter = ["status", "payment_type", "delivery_method"]
    search_fields = ["title", "contact__name", "contact__phone", "contact__email", "tracking_number"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    inlines = [ProductionSlotInline]

    fieldsets = (
        ("Основна інформація", {
            "fields": (
                "title",
                "contact",
                "status",
                ("deadline", "created_at"),
                "comment",
            )
        }),
        ("Доставка", {
            "fields": (
                "shipping_address",
                "recipient",
                "delivery_method",
                "tracking_number",
            ),
        }),
        ("Оплата", {
            "fields": (
                "payment_amount",
                "payment_type",
            )
        }),
    )

    readonly_fields = ["created_at"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        "title_link",     # клик по задаче ведёт на картку клієнта
        "contact",
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

    search_fields = ["title", "contact__name", "contact__phone", "contact__email"]
    ordering = ["date", "id"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("contact", "assigned_to", "assigned_by")

    @admin.display(description="Назва задачі")
    def title_link(self, obj):
        url = reverse("admin:crm_contact_change", args=[obj.contact_id])
        return format_html('<a href="{}">{}</a>', url, obj.title)
