import os
import re
from django import forms
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from accounts.models import Restaurant
from .models import RestaurantConfig, AdminReport, SystemSetting, AccountStatusAuditLog
from restaurant_management.file_security import validate_image_file

User = get_user_model()

class AdminCreationForm(UserCreationForm):
    # Restaurant / Company Information
    restaurant_name = forms.CharField(
        max_length=150, required=True,
        label="Company / Restaurant Name",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Bella Italia Bistro, Ocean Grill'})
    )
    restaurant_logo = forms.FileField(
        required=False,
        label="Company Logo",
        widget=forms.FileInput(attrs={'class': 'form-control', 'id': 'logoInput', 'accept': 'image/png, image/jpeg, image/webp, image/svg+xml'})
    )
    restaurant_logo_icon = forms.CharField(
        max_length=50,
        required=False,
        initial='bi-fire',
        label="Brand Icon",
        widget=forms.HiddenInput(attrs={'id': 'logoIconInput'})
    )
    restaurant_address = forms.CharField(
        required=False,
        label="Restaurant Address",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '123 Gourmet Blvd, Suite 100'})
    )
    restaurant_phone = forms.CharField(
        required=False,
        label="Restaurant Phone",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+1 555 019 2834'})
    )
    restaurant_email = forms.EmailField(
        required=False,
        label="Restaurant Email",
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'contact@restaurant.com'})
    )
    restaurant_status = forms.ChoiceField(
        choices=Restaurant.RestaurantStatus.choices,
        initial=Restaurant.RestaurantStatus.ACTIVE,
        required=False,
        label="Restaurant Status",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    restaurant_plan = forms.ChoiceField(
        choices=Restaurant.SubscriptionPlan.choices,
        initial=Restaurant.SubscriptionPlan.STARTER,
        required=False,
        label="Subscription Plan Tier",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Admin Identity
    first_name = forms.CharField(required=True, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}))
    last_name = forms.CharField(required=True, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'admin@restaurant.com'}))
    phone_number = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+1 234 567 8900'}))

    # Relevant Administrative Profile Fields
    role = forms.ChoiceField(
        choices=[
            (User.Role.ADMIN, 'Administrator / Sub-Admin'),
            (User.Role.MANAGER, 'Restaurant Manager'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    job_title = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Operations Director, Managing Partner'})
    )
    admin_access_level = forms.ChoiceField(
        choices=User.AdminAccessLevel.choices,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Account Status
    is_active = forms.BooleanField(
        required=False, initial=True,
        label="Account Active (Permitted to log in)",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    # Administrative Permissions
    can_manage_staff = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    can_manage_discounts = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    can_view_financials = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    can_edit_menu = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            'username', 'first_name', 'last_name', 'email', 'phone_number',
            'role', 'job_title', 'admin_access_level',
            'is_active',
            'can_manage_staff', 'can_manage_discounts', 'can_view_financials', 'can_edit_menu'
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Login Username'})
        if 'password1' in self.fields:
            self.fields['password1'].widget.attrs.update({
                'class': 'form-control',
                'placeholder': 'Enter secure password'
            })
        if 'password2' in self.fields:
            self.fields['password2'].widget.attrs.update({
                'class': 'form-control',
                'placeholder': 'Confirm password'
            })
        if 'is_staff' in self.fields:
            self.fields['is_staff'].initial = True

    def clean_restaurant_name(self):
        name = (self.cleaned_data.get('restaurant_name') or '').strip()
        if not name:
            raise forms.ValidationError("Company / Restaurant Name is required.")
        return name

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("Username is required.")

        norm = username.lower()
        if User.objects.filter(is_superuser=True, normalized_username=norm).exists():
            raise forms.ValidationError("This username is reserved for platform administration. Please choose another username.")

        rest_name = (self.cleaned_data.get('restaurant_name') or self.data.get('restaurant_name') or '').strip()
        restaurant = Restaurant.objects.filter(name__iexact=rest_name).first() if rest_name else None

        if restaurant:
            existing = User.objects.filter(
                restaurant=restaurant,
                normalized_username=norm
            )
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    f"An administrator or employee with username '{username}' already exists in '{restaurant.name}'. Please choose another username."
                )
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError("Admin Email Address is required.")
        existing = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("This email address is already in use by another account.")
        return email

    def clean_phone_number(self):
        phone = (self.cleaned_data.get('phone_number') or '').strip()
        if phone:
            if not re.match(r'^[+\d\s().-]{7,25}$', phone):
                raise forms.ValidationError("Please enter a valid mobile number (e.g. +1 555-019-2834 or +91 9876543210).")
            existing = User.objects.filter(phone_number=phone)
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("This mobile number is already registered to another account.")
            return phone
        return None

    def clean_restaurant_logo(self):
        logo = self.cleaned_data.get('restaurant_logo')
        if logo:
            validate_image_file(logo, max_size_mb=5)
        return logo

    def clean(self):
        cleaned_data = super().clean()
        username = (cleaned_data.get('username') or '').strip()
        rest_name = (cleaned_data.get('restaurant_name') or self.data.get('restaurant_name') or '').strip()

        if username and rest_name:
            norm = username.lower()
            restaurant = Restaurant.objects.filter(name__iexact=rest_name).first()
            if restaurant:
                existing = User.objects.filter(
                    restaurant=restaurant,
                    normalized_username=norm
                )
                if self.instance and self.instance.pk:
                    existing = existing.exclude(pk=self.instance.pk)
                if existing.exists():
                    self.add_error(
                        'username',
                        f"An administrator or employee with username '{username}' already exists in '{restaurant.name}'. Please choose a unique username."
                    )

        # Cross-validate email
        email = (cleaned_data.get('email') or '').strip().lower()
        if email:
            existing_email = User.objects.filter(email__iexact=email)
            if self.instance and self.instance.pk:
                existing_email = existing_email.exclude(pk=self.instance.pk)
            if existing_email.exists():
                self.add_error('email', f"The email address '{email}' is already in use by another account.")

        return cleaned_data

    def save(self, commit=True):
        with transaction.atomic():
            user = super().save(commit=False)
            username = (self.cleaned_data.get('username') or getattr(user, 'username', '') or '').strip()
            user.username = username
            user.normalized_username = username.lower()
            user.is_staff = True
            user.is_superuser = False
            user.is_active = self.cleaned_data.get('is_active', True)
            user.email = (self.cleaned_data.get('email') or '').strip().lower()
            user.phone_number = self.cleaned_data.get('phone_number') or None
            user.role = self.cleaned_data.get('role', User.Role.ADMIN)

            rest_name = self.cleaned_data.get('restaurant_name')
            rest_logo = self.cleaned_data.get('restaurant_logo')
            rest_logo_icon = self.cleaned_data.get('restaurant_logo_icon') or 'bi-fire'
            rest_address = self.cleaned_data.get('restaurant_address', '')
            rest_phone = self.cleaned_data.get('restaurant_phone', '')
            rest_email = self.cleaned_data.get('restaurant_email', '')
            rest_status = self.cleaned_data.get('restaurant_status') or Restaurant.RestaurantStatus.ACTIVE
            rest_plan = self.cleaned_data.get('restaurant_plan') or Restaurant.SubscriptionPlan.STARTER

            if rest_name:
                restaurant, _ = Restaurant.objects.get_or_create(
                    name=rest_name,
                    defaults={
                        'address': rest_address,
                        'phone': rest_phone,
                        'email': rest_email,
                        'status': rest_status,
                        'plan': rest_plan,
                        'logo_icon': rest_logo_icon,
                    }
                )
                if rest_logo:
                    restaurant.logo = rest_logo
                    try:
                        import base64
                        import mimetypes
                        rest_logo.seek(0)
                        content = rest_logo.read()
                        rest_logo.seek(0)
                        mime = getattr(rest_logo, 'content_type', None) or mimetypes.guess_type(rest_logo.name)[0] or 'image/png'
                        b64 = base64.b64encode(content).decode('utf-8')
                        restaurant.logo_base64 = f"data:{mime};base64,{b64}"
                    except Exception:
                        pass
                if rest_logo_icon:
                    restaurant.logo_icon = rest_logo_icon
                if rest_address:
                    restaurant.address = rest_address
                if rest_phone:
                    restaurant.phone = rest_phone
                if rest_email:
                    restaurant.email = rest_email
                restaurant.status = rest_status
                restaurant.plan = rest_plan
                restaurant.save()
                user.restaurant = restaurant
                RestaurantConfig.get_config(restaurant=restaurant)

            if commit:
                user.save()
            return user


class AdminEditForm(forms.ModelForm):
    # Restaurant / Company Information
    restaurant_name = forms.CharField(
        max_length=150, required=True,
        label="Company / Restaurant Name",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    restaurant_logo = forms.FileField(
        required=False,
        label="Replace Company Logo",
        widget=forms.FileInput(attrs={'class': 'form-control', 'id': 'logoInput', 'accept': 'image/png, image/jpeg, image/webp, image/svg+xml'})
    )
    restaurant_logo_icon = forms.CharField(
        max_length=50,
        required=False,
        initial='bi-fire',
        label="Brand Icon",
        widget=forms.HiddenInput(attrs={'id': 'logoIconInput'})
    )
    restaurant_address = forms.CharField(
        required=False,
        label="Restaurant Address",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    restaurant_phone = forms.CharField(
        required=False,
        label="Restaurant Phone",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    restaurant_email = forms.EmailField(
        required=False,
        label="Restaurant Email",
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )
    restaurant_status = forms.ChoiceField(
        choices=Restaurant.RestaurantStatus.choices,
        label="Restaurant Status",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    restaurant_plan = forms.ChoiceField(
        choices=Restaurant.SubscriptionPlan.choices,
        label="Subscription Plan Tier",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = User
        fields = (
            'first_name', 'last_name', 'email', 'phone_number',
            'role', 'job_title', 'admin_access_level',
            'can_manage_staff', 'can_manage_discounts', 'can_view_financials', 'can_edit_menu',
            'is_active', 'is_suspended', 'suspension_reason'
        )
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'job_title': forms.TextInput(attrs={'class': 'form-control'}),
            'admin_access_level': forms.Select(attrs={'class': 'form-select'}),
            'can_manage_staff': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_manage_discounts': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_view_financials': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_edit_menu': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_suspended': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'suspension_reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Reason if suspending account...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.restaurant:
            rest = self.instance.restaurant
            self.fields['restaurant_name'].initial = rest.name
            self.fields['restaurant_address'].initial = rest.address
            self.fields['restaurant_phone'].initial = rest.phone
            self.fields['restaurant_email'].initial = rest.email
            self.fields['restaurant_status'].initial = rest.status
            self.fields['restaurant_plan'].initial = rest.plan
            self.fields['restaurant_logo_icon'].initial = rest.logo_icon or 'bi-fire'

    def clean_restaurant_logo(self):
        logo = self.cleaned_data.get('restaurant_logo')
        if logo:
            validate_image_file(logo, max_size_mb=5)
        return logo

    def save(self, commit=True):
        user = super().save(commit=False)
        rest_name = self.cleaned_data.get('restaurant_name')
        rest_logo = self.cleaned_data.get('restaurant_logo')
        rest_logo_icon = self.cleaned_data.get('restaurant_logo_icon') or 'bi-fire'
        rest_address = self.cleaned_data.get('restaurant_address', '')
        rest_phone = self.cleaned_data.get('restaurant_phone', '')
        rest_email = self.cleaned_data.get('restaurant_email', '')
        rest_status = self.cleaned_data.get('restaurant_status', Restaurant.RestaurantStatus.ACTIVE)
        rest_plan = self.cleaned_data.get('restaurant_plan', Restaurant.SubscriptionPlan.STARTER)

        if rest_name:
            if user.restaurant:
                restaurant = user.restaurant
                restaurant.name = rest_name
            else:
                restaurant, _ = Restaurant.objects.get_or_create(
                    name=rest_name,
                    defaults={
                        'address': rest_address,
                        'phone': rest_phone,
                        'email': rest_email,
                        'status': rest_status,
                        'plan': rest_plan,
                        'logo_icon': rest_logo_icon,
                    }
                )
                user.restaurant = restaurant

            if rest_logo:
                restaurant.logo = rest_logo
                try:
                    import base64
                    import mimetypes
                    rest_logo.seek(0)
                    content = rest_logo.read()
                    rest_logo.seek(0)
                    mime = getattr(rest_logo, 'content_type', None) or mimetypes.guess_type(rest_logo.name)[0] or 'image/png'
                    b64 = base64.b64encode(content).decode('utf-8')
                    restaurant.logo_base64 = f"data:{mime};base64,{b64}"
                except Exception:
                    pass
            if rest_logo_icon:
                restaurant.logo_icon = rest_logo_icon
            if rest_address:
                restaurant.address = rest_address
            if rest_phone:
                restaurant.phone = rest_phone
            if rest_email:
                restaurant.email = rest_email
            restaurant.status = rest_status
            restaurant.plan = rest_plan
            restaurant.save()
            RestaurantConfig.get_config(restaurant=restaurant)

        if commit:
            user.save()
        return user


CURRENCY_CHOICES = [
    ('₹', 'Indian Rupee (₹ - INR)'),
    ('$', 'US Dollar ($ - USD)'),
    ('€', 'Euro (€ - EUR)'),
    ('£', 'British Pound (£ - GBP)'),
]

class RestaurantDetailsForm(forms.ModelForm):
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select fw-semibold'}),
        help_text="Choose whether prices and amounts are displayed in Rupees (₹) or Dollars ($)"
    )

    class Meta:
        model = RestaurantConfig
        fields = ['restaurant_name', 'tagline', 'phone', 'email', 'address', 'currency', 'tax_rate', 'service_charge_rate', 'qr_domain']
        widgets = {
            'restaurant_name': forms.TextInput(attrs={'class': 'form-control'}),
            'tagline': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'service_charge_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
            'qr_domain': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. https://growwpark.com or http://192.168.0.4:8000'}),
        }


class RestaurantStatusForm(forms.ModelForm):
    class Meta:
        model = RestaurantConfig
        fields = ['is_open', 'status_message', 'opening_time', 'closing_time', 'maintenance_mode']
        widgets = {
            'is_open': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'status_message': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Open for Lunch & Dinner'}),
            'opening_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'closing_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'maintenance_mode': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AdminReportForm(forms.ModelForm):
    class Meta:
        model = AdminReport
        fields = ['report_type', 'title', 'priority', 'description']
        widgets = {
            'report_type': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brief summary of issue/report'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Detailed explanation...'}),
        }


class AdminReportReviewForm(forms.ModelForm):
    class Meta:
        model = AdminReport
        fields = ['status', 'super_admin_notes']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'super_admin_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Super Admin feedback, actions taken, or instructions...'}),
        }


class GeneralSettingsForm(forms.ModelForm):
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select fw-semibold'}),
        required=False,
        help_text="Choose whether the system displays prices and totals in Rupees (₹) or Dollars ($)"
    )

    class Meta:
        model = SystemSetting
        fields = ['session_timeout_minutes', 'enable_audit_logging']
        widgets = {
            'session_timeout_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 5}),
            'enable_audit_logging': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SecuritySettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = ['enforce_strong_password', 'max_login_attempts']
        widgets = {
            'enforce_strong_password': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'max_login_attempts': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 20}),
        }


class BackupSettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = ['auto_backup_enabled', 'backup_frequency_days']
        widgets = {
            'auto_backup_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'backup_frequency_days': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }
