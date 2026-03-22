from django import forms
from django.contrib.auth import get_user_model
from django.forms import BaseInlineFormSet, inlineformset_factory

from core.access import get_user_role
from core.models import UserProfile
from core.visibility import filter_clients_queryset, filter_contacts_queryset, filter_orders_queryset, filter_tasks_queryset

from .models import (
    Client,
    ClientInteraction,
    Contact,
    Order,
    OrderItem,
    Product,
    ProductProductionNorm,
    Task,
)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            if isinstance(widget, forms.SelectMultiple):
                widget.attrs.setdefault("size", 6)
            widget.attrs.setdefault("class", "crm-input")


class ClientForm(StyledModelForm):
    class Meta:
        model = Client
        fields = ["name", "client_type", "tax_code", "phones", "email", "source", "tags", "notes"]


class ContactForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = filter_clients_queryset(
            self.user,
            Client.objects.order_by("name"),
        )

    class Meta:
        model = Contact
        fields = ["client", "full_name", "position", "phone", "email", "source", "tags", "notes"]


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "sku",
            "description",
            "technical_description",
            "base_price",
            "prom_url",
            "rozetka_url",
            "olx_url",
            "site_url",
            "photos_url",
            "production_norms_url",
            "is_active",
        ]
        widgets = {
            "base_price": forms.NumberInput(attrs={"step": "0.01"}),
        }


class ProductProductionNormForm(StyledModelForm):
    class Meta:
        model = ProductProductionNorm
        fields = [
            "stage_type",
            "time_value",
            "time_unit",
            "material_value",
            "material_unit",
            "version",
            "comment",
            "is_active",
        ]
        widgets = {
            "time_value": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "material_value": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
        }


class TaskForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        active_users = user_model.objects.filter(is_active=True).order_by("email", "username")
        self.fields["client"].queryset = filter_clients_queryset(self.user, Client.objects.order_by("name"))
        self.fields["assigned_by"].queryset = active_users
        self.fields["assigned_to"].queryset = active_users
        self.fields["priority"].required = False
        self.initial.setdefault("priority", Task.Priority.NORMAL)

        selected_client_id = None
        if self.is_bound:
            selected_client_id = self.data.get("client") or None
        elif self.instance.pk and self.instance.client_id:
            selected_client_id = str(self.instance.client_id)
        else:
            initial_client = self.initial.get("client")
            if isinstance(initial_client, Client):
                selected_client_id = str(initial_client.pk)
            elif initial_client:
                selected_client_id = str(initial_client)

        contacts = filter_contacts_queryset(self.user, Contact.objects.select_related("client"))
        orders = filter_orders_queryset(self.user, Order.objects.select_related("contact", "contact__client"))
        if selected_client_id and str(selected_client_id).isdigit():
            contacts = contacts.filter(client_id=int(selected_client_id))
            orders = orders.filter(contact__client_id=int(selected_client_id))

        self.fields["contact"].queryset = contacts.order_by("client__name", "full_name")
        self.fields["order"].queryset = orders.order_by("-created_at", "-id")

    class Meta:
        model = Task
        fields = [
            "client",
            "contact",
            "order",
            "title",
            "description",
            "priority",
            "status",
            "assigned_by",
            "assigned_to",
            "date",
            "comment",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_priority(self):
        return self.cleaned_data.get("priority") or Task.Priority.NORMAL


class ClientInteractionForm(StyledModelForm):
    MANUAL_EVENT_TYPES = (
        ClientInteraction.EventType.NOTE,
        ClientInteraction.EventType.CALL,
        ClientInteraction.EventType.MESSAGE,
        ClientInteraction.EventType.COMMENT,
        ClientInteraction.EventType.SYSTEM,
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.client_instance = kwargs.pop("client", None)
        super().__init__(*args, **kwargs)
        if self.client_instance is not None:
            self.instance.client = self.client_instance
        self.fields["event_type"].choices = [
            choice
            for choice in ClientInteraction.EventType.choices
            if choice[0] in self.MANUAL_EVENT_TYPES
        ]
        self.fields["event_at"].widget = forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        )
        self.fields["event_at"].input_formats = ("%Y-%m-%dT%H:%M",)

        contacts = filter_contacts_queryset(self.user, Contact.objects.select_related("client"))
        orders = filter_orders_queryset(self.user, Order.objects.select_related("contact", "contact__client"))
        if self.client_instance is not None:
            contacts = contacts.filter(client=self.client_instance)
            orders = orders.filter(contact__client=self.client_instance)
            self.initial.setdefault("contact", self.client_instance.contacts.order_by("full_name").first())

        self.fields["contact"].queryset = contacts.order_by("full_name")
        self.fields["order"].queryset = orders.order_by("-created_at", "-id")
        self.fields["task"].queryset = Task.objects.none()

        task_queryset = filter_tasks_queryset(
            self.user,
            Task.objects.select_related("client", "contact", "order"),
        )
        if self.client_instance is not None:
            task_queryset = task_queryset.filter(client=self.client_instance)
        self.fields["task"].queryset = task_queryset.order_by("date", "id")

    class Meta:
        model = ClientInteraction
        fields = ["event_type", "title", "description", "event_at", "contact", "order", "task"]

    def clean(self):
        cleaned_data = super().clean()
        contact = cleaned_data.get("contact")
        order = cleaned_data.get("order")
        task = cleaned_data.get("task")
        client = self.client_instance
        if client is None:
            return cleaned_data
        if contact and contact.client_id != client.id:
            self.add_error("contact", "Контакт має належати поточному клієнту.")
        if order and order.contact.client_id != client.id:
            self.add_error("order", "Замовлення має належати поточному клієнту.")
        if task and task.client_id != client.id:
            self.add_error("task", "Задача має належати поточному клієнту.")
        return cleaned_data


class OrderForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        self.fields["contact"].queryset = filter_contacts_queryset(
            self.user,
            Contact.objects.select_related("client").order_by("client__name", "full_name"),
        )
        manager_queryset = user_model.objects.filter(is_active=True).order_by("email", "username")
        if get_user_role(self.user) == UserProfile.Role.SALES_MANAGER:
            manager_queryset = manager_queryset.filter(pk=self.user.pk)
            self.initial["manager"] = self.user.pk
        self.fields["manager"].queryset = manager_queryset
        self.fields["manager"].required = True
        self.fields["priority"].required = False
        self.initial.setdefault("priority", Order.Priority.NORMAL)

    def clean_priority(self):
        return self.cleaned_data.get("priority") or Order.Priority.NORMAL

    def clean_manager(self):
        manager = self.cleaned_data.get("manager")
        if get_user_role(self.user) == UserProfile.Role.SALES_MANAGER:
            return self.user
        return manager

    class Meta:
        model = Order
        fields = [
            "contact",
            "manager",
            "priority",
            "status",
            "deadline",
            "comment",
            "delivery_method",
            "shipping_address",
            "recipient",
            "recipient_phone",
            "tracking_number",
            "payment_type",
            "payment_terms",
            "payment_amount",
        ]
        widgets = {
            "deadline": forms.DateInput(attrs={"type": "date"}),
            "payment_amount": forms.NumberInput(attrs={"step": "0.01"}),
        }


class OrderItemForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.order_by("name")

    class Meta:
        model = OrderItem
        fields = ["product", "quantity", "unit_price", "comment"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": 1}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01"}),
        }


class RequiredOrderItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        active_forms = [
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("product")
        ]
        if not active_forms:
            raise forms.ValidationError("Замовлення має містити хоча б одну позицію.")


OrderItemFormSet = inlineformset_factory(
    Order,
    OrderItem,
    form=OrderItemForm,
    formset=RequiredOrderItemFormSet,
    extra=3,
    can_delete=True,
)


ProductProductionNormFormSet = inlineformset_factory(
    Product,
    ProductProductionNorm,
    form=ProductProductionNormForm,
    extra=5,
    can_delete=True,
)
