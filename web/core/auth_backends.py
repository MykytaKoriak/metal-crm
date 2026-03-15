from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        lookup_value = username or kwargs.get(user_model.USERNAME_FIELD)
        if lookup_value is None or password is None:
            return None

        user = (
            user_model._default_manager.filter(
                Q(email__iexact=lookup_value) | Q(username__iexact=lookup_value)
            )
            .order_by("id")
            .first()
        )
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
