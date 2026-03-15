from django.conf import settings
from django.db import models
from django.utils.crypto import get_random_string


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        SALES_MANAGER = "sales_manager", "Sales manager"
        PRODUCTION = "production", "Production / Technologist"
        EXECUTIVE = "executive", "Executive"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="User",
    )
    full_name = models.CharField("Full name", max_length=255, blank=True)
    phone = models.CharField("Phone", max_length=50, blank=True)
    role = models.CharField(
        "Role",
        max_length=32,
        choices=Role.choices,
        default=Role.SALES_MANAGER,
    )
    telegram_chat_id = models.CharField("Telegram ID", max_length=64, blank=True)
    telegram_link_code = models.CharField(
        "Telegram link code",
        max_length=24,
        unique=True,
        blank=True,
    )

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"
        ordering = ("full_name", "user__email", "user__username")

    def __str__(self):
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.full_name or self.user.get_full_name() or self.user.email or self.user.username

    def save(self, *args, **kwargs):
        if not self.telegram_link_code:
            self.telegram_link_code = self._generate_link_code()
        super().save(*args, **kwargs)

    def _generate_link_code(self) -> str:
        while True:
            code = get_random_string(12).upper()
            if not UserProfile.objects.filter(telegram_link_code=code).exists():
                return code
