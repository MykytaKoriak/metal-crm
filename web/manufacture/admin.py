from django.contrib import admin
from .models import Machine, WorkUnit, ProductionSlot


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "workday_start", "workday_end"]
    list_filter = ["type"]
    search_fields = ["name", "comment"]
    fieldsets = (
        ("Основна інформація", {
            "fields": ("name", "type")
        }),
        ("Робочий день", {
            "fields": ("workday_start", "workday_end"),
        }),
        ("Коментар", {
            "fields": ("comment",),
        }),
    )

@admin.register(WorkUnit)
class WorkUnitAdmin(admin.ModelAdmin):
    list_display = ["name", "type"]
    list_filter = ["type"]
    search_fields = ["name", "comment"]


@admin.register(ProductionSlot)
class ProductionSlotAdmin(admin.ModelAdmin):
    list_display = ["order", "machine", "work_unit", "start_datetime", "end_datetime"]
    list_filter = ["machine", "work_unit"]
    search_fields = ["order__title", "order__contact__name", "comment"]
    autocomplete_fields = ["order", "machine", "work_unit"]
