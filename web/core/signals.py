from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .access import sync_user_role_membership
from .models import UserProfile


User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    profile, _ = UserProfile.objects.get_or_create(user=instance)
    sync_user_role_membership(profile.user)


@receiver(post_save, sender=UserProfile)
def sync_profile_role(sender, instance, **kwargs):
    sync_user_role_membership(instance.user)
