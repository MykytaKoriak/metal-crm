from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.dateparse import parse_datetime

from core.visibility import filter_orders_queryset, filter_slots_queryset, filter_stages_queryset

from .models import (
    Machine,
    ProductionSlot,
    ProductionSlotChangeLog,
    ProductionStage,
    ResourceDowntime,
    WorkUnit,
)
from .services import request_replan_open_orders


class MachineDowntimeInline(admin.TabularInline):
    model = ResourceDowntime
    fk_name = "machine"
    extra = 0
    fields = ("downtime_type", "start_datetime", "end_datetime", "is_blocking", "comment")


class WorkUnitDowntimeInline(admin.TabularInline):
    model = ResourceDowntime
    fk_name = "work_unit"
    extra = 0
    fields = ("downtime_type", "start_datetime", "end_datetime", "is_blocking", "comment")


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "is_active", "available_weekdays", "workday_start", "workday_end"]
    list_filter = ["type", "is_active"]
    search_fields = ["name", "comment"]
    inlines = [MachineDowntimeInline]
    fieldsets = (
        ("Основна інформація", {"fields": ("name", "type", "is_active")}),
        ("Доступність", {"fields": ("available_weekdays", "workday_start", "workday_end")}),
        ("Коментар", {"fields": ("comment",)}),
    )


@admin.register(WorkUnit)
class WorkUnitAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "is_active", "available_weekdays", "workday_start", "workday_end"]
    list_filter = ["type", "is_active"]
    search_fields = ["name", "comment"]
    inlines = [WorkUnitDowntimeInline]
    fieldsets = (
        ("Основна інформація", {"fields": ("name", "type", "is_active")}),
        ("Доступність", {"fields": ("available_weekdays", "workday_start", "workday_end")}),
        ("Коментар", {"fields": ("comment",)}),
    )


@admin.register(ResourceDowntime)
class ResourceDowntimeAdmin(admin.ModelAdmin):
    list_display = ("resource_display", "downtime_type", "start_datetime", "end_datetime", "is_blocking")
    list_filter = ("downtime_type", "is_blocking", "machine", "work_unit")
    search_fields = ("comment", "machine__name", "work_unit__name")

    @admin.display(description="Ресурс")
    def resource_display(self, obj):
        return obj.resource


class StageSlotInline(admin.TabularInline):
    model = ProductionSlot
    extra = 0
    fields = (
        "order",
        "slot_type",
        "operation_type",
        "machine",
        "work_unit",
        "start_datetime",
        "end_datetime",
        "planning_mode",
        "planning_source",
        "is_locked",
        "purpose",
        "dispatcher_comment",
        "comment",
    )

    def get_queryset(self, request):
        return filter_slots_queryset(request.user, super().get_queryset(request))


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

    def get_queryset(self, request):
        return filter_stages_queryset(request.user, super().get_queryset(request))

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)


class ProductionSlotChangeLogInline(admin.TabularInline):
    model = ProductionSlotChangeLog
    extra = 0
    can_delete = False
    fields = ("created_at", "action", "source", "note", "changed_by")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            slot__in=filter_slots_queryset(request.user, ProductionSlot.objects.all())
        )


@admin.register(ProductionSlot)
class ProductionSlotAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "stage",
        "slot_type",
        "operation_type",
        "machine",
        "work_unit",
        "planning_mode",
        "planning_source",
        "is_locked",
        "start_datetime",
        "end_datetime",
    )
    list_filter = (
        "slot_type",
        "operation_type",
        "planning_mode",
        "planning_source",
        "is_locked",
        "machine",
        "work_unit",
        "stage__stage_type",
    )
    search_fields = (
        "order__title",
        "stage__order_item__product__name",
        "purpose",
        "dispatcher_comment",
        "comment",
    )
    actions = ["return_to_auto_mode"]
    inlines = [ProductionSlotChangeLogInline]
    fieldsets = (
        ("Основне", {"fields": ("order", "stage", "slot_type", "operation_type", "purpose")}),
        ("Ресурс", {"fields": ("machine", "work_unit")}),
        (
            "Розклад",
            {"fields": ("start_datetime", "end_datetime", "planning_mode", "planning_source", "is_locked")},
        ),
        ("Коментарі", {"fields": ("dispatcher_comment", "comment")}),
    )

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

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        return filter_slots_queryset(request.user, super().get_queryset(request))

    @admin.action(description="Повернути вибрані слоти в автопланування")
    def return_to_auto_mode(self, request, queryset):
        for slot in queryset:
            slot.planning_mode = ProductionSlot.PlanningMode.AUTO
            slot.planning_source = ProductionSlot.PlanningSource.ADMIN
            slot.is_locked = False
            slot._changed_by = request.user
            slot._planner_operation = True
            slot._history_source = "system"
            slot._history_note = "Повернуто в автопланування через admin action."
            slot.save()
        request_replan_open_orders()

    def response_add(self, request, obj, post_url_continue=None):
        return HttpResponseRedirect(reverse("admin:manufacture_productionslot_calendar"))

    def response_change(self, request, obj):
        return HttpResponseRedirect(reverse("admin:manufacture_productionslot_calendar"))


@admin.register(ProductionSlotChangeLog)
class ProductionSlotChangeLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "slot_reference", "order", "stage", "action", "source", "changed_by")
    list_filter = ("action", "source")
    search_fields = ("note", "order__title", "stage__order_item__product__name")
    readonly_fields = (
        "slot",
        "slot_reference",
        "order",
        "stage",
        "action",
        "source",
        "snapshot_before",
        "snapshot_after",
        "note",
        "changed_by",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            order__in=filter_orders_queryset(request.user, self.model.order.field.related_model.objects.all())
        )
