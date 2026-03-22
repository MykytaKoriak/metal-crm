from django.urls import path

from .views import (
    machine_detail_report,
    machine_load_report,
    production_free_slot_report,
    production_orders_in_work_report,
    production_overdue_stage_report,
    production_slot_events,
    production_stage_status_update,
    workunit_detail_report,
)

urlpatterns = [
    path("report/machine-load/", machine_load_report, name="machine_load_report"),
    path("report/machine/<int:machine_id>/", machine_detail_report, name="machine_detail_report"),
    path("report/workunit/<int:workunit_id>/", workunit_detail_report, name="workunit_detail_report"),
    path("report/free-slots/", production_free_slot_report, name="production_free_slot_report"),
    path("report/orders-in-work/", production_orders_in_work_report, name="production_orders_in_work_report"),
    path("report/overdue-stages/", production_overdue_stage_report, name="production_overdue_stage_report"),
    path("production/stage/<int:stage_id>/status/", production_stage_status_update, name="production_stage_status_update"),
    path("production-slots/events/", production_slot_events, name="production_slot_events"),
]
