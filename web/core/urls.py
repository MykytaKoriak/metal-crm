from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from .forms import EmailAuthenticationForm
from .views import (
    admin_dashboard,
    dashboard,
    executive_dashboard,
    my_account,
    production_dashboard,
    sales_dashboard,
)


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False), name="home"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=EmailAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("dashboard/", dashboard, name="dashboard"),
    path("dashboard/admin/", admin_dashboard, name="admin_dashboard"),
    path("dashboard/sales/", sales_dashboard, name="sales_dashboard"),
    path("dashboard/production/", production_dashboard, name="production_dashboard"),
    path("dashboard/executive/", executive_dashboard, name="executive_dashboard"),
    path("account/me/", my_account, name="my_account"),
]
