from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Client, Contact, Task
from .models import UserProfile


class TestUserProfile(TestCase):
    def test_profile_created_automatically(self):
        user = get_user_model().objects.create_user(
            username="profile@example.com",
            email="profile@example.com",
            password="secret123",
        )

        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertTrue(user.profile.telegram_link_code)


class TestEmailLogin(TestCase):
    def test_user_can_authenticate_with_email(self):
        user = get_user_model().objects.create_user(
            username="email-login@example.com",
            email="email-login@example.com",
            password="secret123",
        )

        authenticated = authenticate(username="email-login@example.com", password="secret123")

        self.assertEqual(authenticated, user)


class TestMyAccountView(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="account@example.com",
            email="account@example.com",
            password="secret123",
            is_active=True,
        )
        self.user.profile.full_name = "Account User"
        self.user.profile.role = UserProfile.Role.SALES_MANAGER
        self.user.profile.save()

        client = Client.objects.create(name="Client", email="client@example.com")
        self.contact = Contact.objects.create(client=client, full_name="Primary Contact")

    def test_login_required(self):
        response = self.client.get(reverse("my_account"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_my_account_groups_tasks_and_stats(self):
        today = timezone.localdate()
        Task.objects.create(
            contact=self.contact,
            title="Current task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today + timedelta(days=1),
            status=False,
        )
        Task.objects.create(
            contact=self.contact,
            title="Overdue task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today - timedelta(days=1),
            status=False,
        )
        Task.objects.create(
            contact=self.contact,
            title="Completed task",
            assigned_by=self.user,
            assigned_to=self.user,
            date=today,
            status=True,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("my_account"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account User")
        self.assertEqual(response.context["current_tasks"].count(), 1)
        self.assertEqual(response.context["overdue_tasks"].count(), 1)
        self.assertEqual(response.context["completed_tasks"].count(), 1)
        self.assertEqual(response.context["stats"]["total_tasks"], 3)
        self.assertEqual(response.context["stats"]["completed_tasks"], 1)
        self.assertEqual(response.context["stats"]["overdue_tasks"], 1)
