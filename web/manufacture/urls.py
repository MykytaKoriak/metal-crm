from django.urls import path
from .views import machine_load_report, machine_detail_report, workunit_detail_report, production_slot_events

urlpatterns = [
    path("report/machine-load/", machine_load_report, name="machine_load_report"),
    path("report/machine/<int:machine_id>/", machine_detail_report, name="machine_detail_report"),
    path("report/workunit/<int:workunit_id>/", workunit_detail_report, name="workunit_detail_report"),
    path("production-slots/events/", production_slot_events, name="production_slot_events"),

]
