from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.dateparse import parse_datetime

from .models import Machine, ProductionSlot, ProductionStage, WorkUnit


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "workday_start", "workday_end"]
    list_filter = ["type"]
    search_fields = ["name", "comment"]
    fieldsets = (
        ("Основна інформація", {"fields": ("name", "type")}),
        ("Робочий день", {"fields": ("workday_start", "workday_end")}),
        ("Коментар", {"fields": ("comment",)}),
    )


@admin.register(WorkUnit)
class WorkUnitAdmin(admin.ModelAdmin):
    list_display = ["name", "type"]
    list_filter = ["type"]
    search_fields = ["name", "comment"]


class StageSlotInline(admin.TabularInline):
    model = ProductionSlot
    extra = 0
    fields = ("order", "machine", "work_unit", "start_datetime", "end_datetime", "comment")


@admin.register(ProductionStage)
class ProductionStageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order_display",
        "order_item",
        "stage_type",
        "status",
        "sequence",
        "responsible",
        "planned_start",
        "planned_end",
    )
    list_filter = ("stage_type", "status", "responsible")
    search_fields = ("order_item__order__title", "order_item__product__name", "comment")
    inlines = [StageSlotInline]

    @admin.display(description="Замовлення")
    def order_display(self, obj):
        return obj.order


@admin.register(ProductionSlot)
class ProductionSlotAdmin(admin.ModelAdmin):
    list_display = ("order", "stage", "machine", "work_unit", "start_datetime", "end_datetime")
    list_filter = ("machine", "work_unit", "stage__stage_type")
    search_fields = ("order__title", "stage__order_item__product__name", "comment")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "calendar/",
                self.admin_site.admin_view(self.calendar_view),
                name="manufacture_productionslot_calendar",
            ),
        ]
        return custom_urls + urls

    def calendar_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        context = dict(
            self.admin_site.each_context(request),
            opts=self.model._meta,
            can_add_slots=self.has_add_permission(request),
            can_view_slots=self.has_view_permission(request),
        )
        return TemplateResponse(request, "admin/productionslot_calendar.html", context)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        start = request.GET.get("start")
        end = request.GET.get("end")

        if start:
            start_value = parse_datetime(start)
            if start_value:
                initial["start_datetime"] = start_value

        if end:
            end_value = parse_datetime(end)
            if end_value:
                initial["end_datetime"] = end_value

        return initial

    def response_add(self, request, obj, post_url_continue=None):
        return HttpResponseRedirect(reverse("admin:manufacture_productionslot_calendar"))

    def response_change(self, request, obj):
        return HttpResponseRedirect(reverse("admin:manufacture_productionslot_calendar"))
