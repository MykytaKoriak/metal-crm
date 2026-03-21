from django import forms
from django.contrib.auth import get_user_model
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import Client, Contact, Order, OrderItem, Product, Task


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
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.order_by("name")

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


class TaskForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        active_users = user_model.objects.filter(is_active=True).order_by("email", "username")
        self.fields["client"].queryset = Client.objects.order_by("name")
        self.fields["assigned_by"].queryset = active_users
        self.fields["assigned_to"].queryset = active_users

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

        contacts = Contact.objects.select_related("client")
        orders = Order.objects.select_related("contact", "contact__client")
        if selected_client_id and str(selected_client_id).isdigit():
            contacts = contacts.filter(client_id=int(selected_client_id))
            orders = orders.filter(contact__client_id=int(selected_client_id))

        self.fields["contact"].queryset = contacts.order_by("client__name", "full_name")
        self.fields["order"].queryset = orders.order_by("-created_at", "-id")

    class Meta:
        model = Task
        fields = ["client", "contact", "order", "title", "status", "assigned_by", "assigned_to", "date", "comment"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }


class OrderForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        self.fields["contact"].queryset = Contact.objects.select_related("client").order_by("client__name", "full_name")
        self.fields["manager"].queryset = user_model.objects.filter(is_active=True).order_by("email", "username")
        self.fields["manager"].required = True
        self.fields["priority"].required = False
        self.initial.setdefault("priority", Order.Priority.NORMAL)

    def clean_priority(self):
        return self.cleaned_data.get("priority") or Order.Priority.NORMAL

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
