from django.urls import path
from .views import machine_load_report, machine_detail_report

urlpatterns = [
    path("report/machine-load/", machine_load_report, name="machine_load_report"),
    path("report/machine/<int:machine_id>/", machine_detail_report, name="machine_detail_report"),
]
