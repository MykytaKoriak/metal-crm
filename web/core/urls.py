from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from .forms import EmailAuthenticationForm
from .views import my_account


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="my_account", permanent=False), name="home"),
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
    path("account/me/", my_account, name="my_account"),
]
