from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import UserProfile


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="Email or login",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )


class TelegramPreferencesForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = (
            "telegram_notifications_enabled",
            "telegram_notify_new_tasks",
            "telegram_notify_deadlines",
            "telegram_notify_overdue",
            "telegram_notify_order_updates",
            "telegram_notify_production_events",
        )
        widgets = {
            "telegram_notifications_enabled": forms.CheckboxInput(),
            "telegram_notify_new_tasks": forms.CheckboxInput(),
            "telegram_notify_deadlines": forms.CheckboxInput(),
            "telegram_notify_overdue": forms.CheckboxInput(),
            "telegram_notify_order_updates": forms.CheckboxInput(),
            "telegram_notify_production_events": forms.CheckboxInput(),
        }
