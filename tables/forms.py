from django import forms
from django.db import models
from .models import DiningTable, Reservation

class DiningTableForm(forms.ModelForm):
    class Meta:
        model = DiningTable
        fields = ['table_number', 'capacity', 'location', 'status', 'is_active']
        labels = {
            'location': 'Type of Table',
            'table_number': 'Table Number',
            'capacity': 'Seating Capacity',
            'status': 'Initial Status',
            'is_active': 'Active in Floor Plan',
        }
        widgets = {
            'table_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. T-01'}),
            'capacity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def clean_table_number(self):
        table_num = self.cleaned_data.get('table_number', '').strip()
        if self.restaurant:
            existing = DiningTable.objects.filter(restaurant=self.restaurant, table_number__iexact=table_num)
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(f"Table '{table_num}' already exists in your restaurant.")
        return table_num

    def save(self, commit=True):
        table = super().save(commit=False)
        if self.restaurant and not table.restaurant_id:
            table.restaurant = self.restaurant
        if commit:
            table.save()
        return table


class ReservationForm(forms.ModelForm):
    class Meta:
        model = Reservation
        fields = ['table', 'customer_name', 'customer_phone', 'customer_email', 'guests_count', 'reservation_date', 'reservation_time', 'status', 'special_requests']
        widgets = {
            'table': forms.Select(attrs={'class': 'form-select'}),
            'customer_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full Name'}),
            'customer_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone Number'}),
            'customer_email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Optional Email'}),
            'guests_count': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'reservation_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reservation_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'special_requests': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Allergies, high chair, birthday surprise...'}),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)
        if self.restaurant:
            self.fields['table'].queryset = DiningTable.objects.filter(restaurant=self.restaurant, is_active=True)
        else:
            self.fields['table'].queryset = DiningTable.objects.filter(is_active=True)

    def save(self, commit=True):
        res = super().save(commit=False)
        if self.restaurant and not res.restaurant_id:
            res.restaurant = self.restaurant
        if commit:
            res.save()
        return res


class WaiterAssignmentForm(forms.ModelForm):
    class Meta:
        from .models import WaiterTableAssignment
        model = WaiterTableAssignment
        fields = ['waiter', 'table', 'section_name', 'is_active']
        widgets = {
            'waiter': forms.Select(attrs={'class': 'form-select'}),
            'table': forms.Select(attrs={'class': 'form-select'}),
            'section_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Main Dining, Patio, VIP'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        restaurant = kwargs.pop('restaurant', None)
        super().__init__(*args, **kwargs)
        from accounts.models import User
        waiters_qs = User.objects.filter(role=User.Role.WAITER, is_active=True, is_suspended=False)
        tables_qs = DiningTable.objects.filter(is_active=True)
        if restaurant:
            waiters_qs = waiters_qs.filter(models.Q(restaurant=restaurant) | models.Q(restaurant__isnull=True))
            tables_qs = tables_qs.filter(models.Q(restaurant=restaurant) | models.Q(restaurant__isnull=True))
        self.fields['waiter'].queryset = waiters_qs
        self.fields['table'].queryset = tables_qs


class WaitingQueueForm(forms.ModelForm):
    class Meta:
        from .models import TableWaitingQueue
        model = TableWaitingQueue
        fields = ['customer_name', 'customer_phone', 'guest_count', 'special_requirements']
        widgets = {
            'customer_name': forms.TextInput(attrs={'class': 'form-control form-control-lg', 'placeholder': 'Enter your full name', 'required': True}),
            'customer_phone': forms.TextInput(attrs={'class': 'form-control form-control-lg', 'placeholder': 'e.g. 9876543210 or +91 9876543210', 'required': True}),
            'guest_count': forms.NumberInput(attrs={'class': 'form-control form-control-lg', 'min': 1, 'max': 30, 'value': 2, 'required': True}),
            'special_requirements': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'e.g. High chair for baby, booth preferred, wheelchair accessible...'}),
        }

    def clean_customer_phone(self):
        phone = (self.cleaned_data.get('customer_phone') or '').strip()
        cleaned_digits = ''.join(c for c in phone if c.isdigit() or c == '+')
        if len(cleaned_digits.replace('+', '')) < 7:
            raise forms.ValidationError("Please enter a valid mobile number with at least 7 digits.")
        return phone


class TableSectionForm(forms.ModelForm):
    class Meta:
        from .models import TableSection
        model = TableSection
        fields = ['name', 'code', 'description', 'tables', 'assigned_waiters', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Floor Section A - Main Hall', 'required': True}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. SEC-A'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Short description of this dining area'}),
            'tables': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 5}),
            'assigned_waiters': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 5}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        restaurant = kwargs.pop('restaurant', None)
        super().__init__(*args, **kwargs)
        from accounts.models import User
        from .models import DiningTable
        tables_qs = DiningTable.objects.filter(is_active=True).order_by('table_number')
        waiters_qs = User.objects.filter(role=User.Role.WAITER, is_active=True, is_suspended=False)
        if restaurant:
            tables_qs = tables_qs.filter(models.Q(restaurant=restaurant) | models.Q(restaurant__isnull=True))
            waiters_qs = waiters_qs.filter(models.Q(restaurant=restaurant) | models.Q(restaurant__isnull=True))
        self.fields['tables'].queryset = tables_qs
        self.fields['assigned_waiters'].queryset = waiters_qs


class ServiceSLAConfigForm(forms.ModelForm):
    class Meta:
        from .models import ServiceSLAConfig
        model = ServiceSLAConfig
        fields = ['request_type', 'target_minutes', 'warning_minutes', 'escalation_minutes', 'priority', 'is_active']
        widgets = {
            'request_type': forms.Select(attrs={'class': 'form-select'}),
            'target_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 60}),
            'warning_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 60}),
            'escalation_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 60}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

