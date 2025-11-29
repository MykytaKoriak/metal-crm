from django.contrib import admin
from .models import Machine, WorkUnit, ProductionSlot
from django.urls import path
from django.template.response import TemplateResponse
from django.utils.dateparse import parse_datetime
from django.http import HttpResponseRedirect
from django.urls import reverse


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
    list_display = ("order", "machine", "work_unit", "start_datetime", "end_datetime")
    list_filter = ("machine", "work_unit")
    search_fields = ("order__id", "order__name")  # підстав свої поля в Order

    # 1) додаємо власний URL /calendar/ до маршрутизації цієї моделі
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

    # 2) view, який рендерить шаблон з календарем
    def calendar_view(self, request):
        context = dict(
            self.admin_site.each_context(request),
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/productionslot_calendar.html", context)

    # 3) підстановка start/end із параметрів URL у форму створення
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        start = request.GET.get("start")
        end = request.GET.get("end")

        if start:
            dt = parse_datetime(start)
            if dt:
                initial["start_datetime"] = dt

        if end:
            dt = parse_datetime(end)
            if dt:
                initial["end_datetime"] = dt

        return initial


    def response_add(self, request, obj, post_url_continue=None):
        """
        Після створення нового ProductionSlot -> перейти на календар.
        """
        url = reverse("admin:manufacture_productionslot_calendar")
        return HttpResponseRedirect(url)

    def response_change(self, request, obj):
        """
        Після редагування також перейти на календар (опційно).
        Якщо не хочеш — видали цей метод.
        """
        url = reverse("admin:manufacture_productionslot_calendar")
        return HttpResponseRedirect(url)


