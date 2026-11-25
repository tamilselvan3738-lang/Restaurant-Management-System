import re
from django import forms
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from .models import Restaurant, PasswordResetOTP
from .services.delivery import normalize_email, normalize_phone_number, is_valid_e164
from kitchen.models import KitchenStation, KitchenStaffProfile

User = get_user_model()


class CustomLoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Username or Email",
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Username or Email Address',
            'autofocus': True
        })
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter your password'
        })
    )

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username is not None and password:
            from django.contrib.auth import authenticate
            self.user_cache = authenticate(
                self.request,
                username=username,
                password=password
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            else:
                self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class StaffCreationForm(UserCreationForm):
    employee_id = forms.CharField(
        max_length=30,
        required=False,
        label="Employee ID / Staff Code",
        widget=forms.TextInput(attrs={
            'class': 'form-control font-monospace fw-bold',
            'placeholder': 'e.g. EMP-1008 (Leave blank to auto-generate)'
        }),
        help_text="Unique staff ID used as a primary login credential"
    )
    role = forms.ChoiceField(
        label="Designation / Role",
        choices=[(r.value, r.label) for r in User.Role if r not in [User.Role.ADMIN, User.Role.CUSTOMER]],
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_role_select', 'onchange': 'toggleKitchenFields()'})
    )
    profile_picture = forms.ImageField(
        required=False,
        label="Profile Photo",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'})
    )
    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'})
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'email@restaurant.com'})
    )
    phone_number = forms.CharField(
        required=False,
        label="Mobile / Phone Number",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+1 234 567 8900'})
    )
    hire_date = forms.DateField(
        required=False,
        label="Joining Date",
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    salary = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'})
    )
    assigned_station = forms.ModelChoiceField(
        queryset=KitchenStation.objects.none(),
        required=False,
        label="Assigned Kitchen Station (Optional)",
        empty_label="-- All Stations / General Kitchen --",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label="Account Active",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    # Kitchen Permission Checkboxes
    can_view_all_orders = forms.BooleanField(required=False, initial=True, label="View All Restaurant Orders")
    can_accept_orders = forms.BooleanField(required=False, initial=True, label="Accept Incoming Orders")
    can_reject_orders = forms.BooleanField(required=False, initial=False, label="Reject Incoming Orders")
    can_update_status = forms.BooleanField(required=False, initial=True, label="Update Preparation Status (Cooking/Ready)")
    can_mark_ready = forms.BooleanField(required=False, initial=True, label="Mark Individual Items / Orders as Ready")
    can_cancel_items = forms.BooleanField(required=False, initial=True, label="Cancel Eligible Food Items")
    can_request_order_cancel = forms.BooleanField(required=False, initial=True, label="Request Entire Order Cancellation")
    can_approve_order_cancel = forms.BooleanField(required=False, initial=False, label="Approve Full Order Cancellation")
    can_report_unavailable = forms.BooleanField(required=False, initial=True, label="Report Food Items as Unavailable")
    can_view_kitchen_reports = forms.BooleanField(required=False, initial=True, label="View Kitchen Reports & Statistics")

    emergency_contact_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Emergency Contact Person'})
    )
    emergency_contact_phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Emergency Phone Number'})
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Residential address'})
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            'employee_id',
            'username',
            'first_name',
            'last_name',
            'email',
            'role',
            'phone_number',
            'profile_picture',
            'hire_date',
            'salary',
            'emergency_contact_name',
            'emergency_contact_phone',
            'address'
        )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Username'})
        
        # Prepopulate next Employee ID if creating fresh
        if not self.is_bound and not self.initial.get('employee_id'):
            self.initial['employee_id'] = User.generate_next_employee_id()

        # Fix password input styling so they render as proper form-control inputs
        if 'password1' in self.fields:
            self.fields['password1'].widget.attrs.update({
                'class': 'form-control',
                'placeholder': 'Create account password'
            })
        if 'password2' in self.fields:
            self.fields['password2'].widget.attrs.update({
                'class': 'form-control',
                'placeholder': 'Confirm account password'
            })

        # Filter stations by restaurant
        rest = getattr(self.user, 'restaurant', None)
        if rest:
            self.fields['assigned_station'].queryset = KitchenStation.objects.filter(restaurant=rest, is_active=True)
        else:
            self.fields['assigned_station'].queryset = KitchenStation.objects.filter(is_active=True)

        if self.user and self.user.is_superuser:
            self.fields['restaurant'] = forms.ModelChoiceField(
                queryset=Restaurant.objects.all(),
                required=False,
                empty_label="-- Select Restaurant (Optional) --",
                widget=forms.Select(attrs={'class': 'form-select'})
            )

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("Username is required.")

        target_restaurant = None
        if self.user and self.user.is_superuser:
            target_restaurant = self.cleaned_data.get('restaurant')
        elif self.user and getattr(self.user, 'restaurant', None):
            target_restaurant = self.user.restaurant

        norm = username.lower()
        if target_restaurant:
            existing = User.objects.filter(
                restaurant=target_restaurant,
                normalized_username=norm
            )
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "This username is already registered in your organization. Please choose another username."
                )
        else:
            existing = User.objects.filter(
                restaurant__isnull=True,
                normalized_username=norm
            )
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "This username is already registered. Please choose another username."
                )
        return username

    def clean_employee_id(self):
        emp_id = (self.cleaned_data.get('employee_id') or '').strip()
        if not emp_id:
            emp_id = User.generate_next_employee_id()
        existing = User.objects.filter(employee_id__iexact=emp_id)
        if existing.exists():
            raise forms.ValidationError(f"Employee ID '{emp_id}' is already registered to another staff member.")
        return emp_id

    def save(self, commit=True):
        user = super().save(commit=False)
        username = (self.cleaned_data.get('username') or getattr(user, 'username', '') or '').strip()
        user.username = username
        user.normalized_username = username.lower()

        # Enforce organization relationship and creator lineage from authenticated admin
        if self.user and not self.user.is_superuser and getattr(self.user, 'restaurant', None):
            user.restaurant = self.user.restaurant
            user.created_by = self.user
        elif not user.restaurant_id and self.user and getattr(self.user, 'restaurant', None):
            user.restaurant = self.user.restaurant
            user.created_by = self.user
        elif self.user and not user.created_by_id:
            user.created_by = self.user

        role = self.cleaned_data.get('role')
        if role in [User.Role.CHEF, User.Role.KITCHEN]:
            user.department = User.Department.KITCHEN
        elif role == User.Role.CASHIER:
            user.department = User.Department.BILLING

        user.is_active = self.cleaned_data.get('is_active', True)

        if commit:
            user.save()
            # If Chef or Kitchen Staff, create KitchenStaffProfile
            if role in [User.Role.CHEF, User.Role.KITCHEN]:
                st = self.cleaned_data.get('assigned_station')
                prof, _ = KitchenStaffProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        'restaurant': user.restaurant,
                        'designation': 'CHEF' if role == User.Role.CHEF else 'KITCHEN',
                        'assigned_station': st,
                        'can_view_all_orders': self.cleaned_data.get('can_view_all_orders', True),
                        'can_accept_orders': self.cleaned_data.get('can_accept_orders', True),
                        'can_reject_orders': self.cleaned_data.get('can_reject_orders', False),
                        'can_update_status': self.cleaned_data.get('can_update_status', True),
                        'can_mark_ready': self.cleaned_data.get('can_mark_ready', True),
                        'can_cancel_items': self.cleaned_data.get('can_cancel_items', True),
                        'can_request_order_cancel': self.cleaned_data.get('can_request_order_cancel', True),
                        'can_approve_order_cancel': self.cleaned_data.get('can_approve_order_cancel', False),
                        'can_report_unavailable': self.cleaned_data.get('can_report_unavailable', True),
                        'can_view_kitchen_reports': self.cleaned_data.get('can_view_kitchen_reports', True),
                    }
                )
                if st:
                    st.assigned_cooks.add(user)
        return user


class StaffUpdateForm(forms.ModelForm):
    role = forms.ChoiceField(
        label="Designation / Role",
        choices=[(r.value, r.label) for r in User.Role if r not in [User.Role.ADMIN, User.Role.CUSTOMER]],
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_role_select', 'onchange': 'toggleKitchenFields()'})
    )
    assigned_station = forms.ModelChoiceField(
        queryset=KitchenStation.objects.none(),
        required=False,
        label="Assigned Kitchen Station (Optional)",
        empty_label="-- All Stations / General Kitchen --",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Kitchen Permission Checkboxes
    can_view_all_orders = forms.BooleanField(required=False, initial=True, label="View All Restaurant Orders")
    can_accept_orders = forms.BooleanField(required=False, initial=True, label="Accept Incoming Orders")
    can_reject_orders = forms.BooleanField(required=False, initial=False, label="Reject Incoming Orders")
    can_update_status = forms.BooleanField(required=False, initial=True, label="Update Preparation Status (Cooking/Ready)")
    can_mark_ready = forms.BooleanField(required=False, initial=True, label="Mark Individual Items / Orders as Ready")
    can_cancel_items = forms.BooleanField(required=False, initial=True, label="Cancel Eligible Food Items")
    can_request_order_cancel = forms.BooleanField(required=False, initial=True, label="Request Entire Order Cancellation")
    can_approve_order_cancel = forms.BooleanField(required=False, initial=False, label="Approve Full Order Cancellation")
    can_report_unavailable = forms.BooleanField(required=False, initial=True, label="Report Food Items as Unavailable")
    can_view_kitchen_reports = forms.BooleanField(required=False, initial=True, label="View Kitchen Reports & Statistics")

    class Meta:
        model = User
        fields = (
            'employee_id',
            'username',
            'first_name',
            'last_name',
            'email',
            'role',
            'phone_number',
            'profile_picture',
            'hire_date',
            'salary',
            'emergency_contact_name',
            'emergency_contact_phone',
            'address',
            'is_on_duty',
            'is_active'
        )
        widgets = {
            'employee_id': forms.TextInput(attrs={'class': 'form-control font-monospace fw-bold'}),
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control'}),
            'profile_picture': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'hire_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'salary': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'is_on_duty': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        rest = getattr(self.instance, 'restaurant', None) or getattr(self.user, 'restaurant', None)
        if rest:
            self.fields['assigned_station'].queryset = KitchenStation.objects.filter(restaurant=rest, is_active=True)
        else:
            self.fields['assigned_station'].queryset = KitchenStation.objects.filter(is_active=True)

        # Prepopulate kitchen profile if exists
        if hasattr(self.instance, 'kitchen_profile') and self.instance.kitchen_profile:
            kp = self.instance.kitchen_profile
            self.initial['assigned_station'] = kp.assigned_station
            self.initial['can_view_all_orders'] = kp.can_view_all_orders
            self.initial['can_accept_orders'] = kp.can_accept_orders
            self.initial['can_reject_orders'] = kp.can_reject_orders
            self.initial['can_update_status'] = kp.can_update_status
            self.initial['can_mark_ready'] = kp.can_mark_ready
            self.initial['can_cancel_items'] = kp.can_cancel_items
            self.initial['can_request_order_cancel'] = kp.can_request_order_cancel
            self.initial['can_approve_order_cancel'] = kp.can_approve_order_cancel
            self.initial['can_report_unavailable'] = kp.can_report_unavailable
            self.initial['can_view_kitchen_reports'] = kp.can_view_kitchen_reports

        if self.user and self.user.is_superuser:
            self.fields['restaurant'] = forms.ModelChoiceField(
                queryset=Restaurant.objects.all(),
                required=False,
                empty_label="-- Select Restaurant (Optional) --",
                widget=forms.Select(attrs={'class': 'form-select'})
            )

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("Username is required.")

        target_restaurant = getattr(self.instance, 'restaurant', None)
        if not target_restaurant and self.user and not self.user.is_superuser:
            target_restaurant = self.user.restaurant

        norm = username.lower()
        if target_restaurant:
            existing = User.objects.filter(
                restaurant=target_restaurant,
                normalized_username=norm
            ).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "This username is already registered in your organization. Please choose another username."
                )
        else:
            existing = User.objects.filter(
                restaurant__isnull=True,
                normalized_username=norm
            ).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "This username is already registered. Please choose another username."
                )
        return username

    def clean_employee_id(self):
        emp_id = (self.cleaned_data.get('employee_id') or '').strip()
        if not emp_id:
            emp_id = self.instance.employee_id or User.generate_next_employee_id()
        existing = User.objects.filter(employee_id__iexact=emp_id).exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError(f"Employee ID '{emp_id}' is already registered to another staff member.")
        return emp_id

    def save(self, commit=True):
        user = super().save(commit=False)
        if 'username' in self.cleaned_data:
            username = (self.cleaned_data['username'] or getattr(user, 'username', '') or '').strip()
            user.username = username
            user.normalized_username = username.lower()

        # Enforce tenant isolation from authenticated admin
        if self.user and not self.user.is_superuser and getattr(self.user, 'restaurant', None):
            user.restaurant = self.user.restaurant

        role = self.cleaned_data.get('role')
        if role in [User.Role.CHEF, User.Role.KITCHEN]:
            user.department = User.Department.KITCHEN
        elif role == User.Role.CASHIER:
            user.department = User.Department.BILLING

        if commit:
            user.save()
            st = self.cleaned_data.get('assigned_station')
            KitchenStaffProfile.objects.update_or_create(
                user=user,
                defaults={
                    'restaurant': user.restaurant,
                    'designation': 'CHEF' if role == User.Role.CHEF else 'KITCHEN',
                    'assigned_station': st,
                    'can_view_all_orders': self.cleaned_data.get('can_view_all_orders', True),
                    'can_accept_orders': self.cleaned_data.get('can_accept_orders', True),
                    'can_reject_orders': self.cleaned_data.get('can_reject_orders', False),
                    'can_update_status': self.cleaned_data.get('can_update_status', True),
                    'can_mark_ready': self.cleaned_data.get('can_mark_ready', True),
                    'can_cancel_items': self.cleaned_data.get('can_cancel_items', True),
                    'can_request_order_cancel': self.cleaned_data.get('can_request_order_cancel', True),
                    'can_approve_order_cancel': self.cleaned_data.get('can_approve_order_cancel', False),
                    'can_report_unavailable': self.cleaned_data.get('can_report_unavailable', True),
                    'can_view_kitchen_reports': self.cleaned_data.get('can_view_kitchen_reports', True),
                }
            )
            if st:
                st.assigned_cooks.add(user)
        return user


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            'first_name',
            'last_name',
            'email',
            'phone_number',
            'address',
            'emergency_contact_name',
            'emergency_contact_phone',
            'profile_picture',
        )
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@example.com'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+91 98765 43210'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Enter street address, locality, city, state, postal code'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Spouse, Parent, Guardian'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Emergency phone number'}),
            'profile_picture': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }
        labels = {
            'first_name': 'First Name',
            'last_name': 'Last Name',
            'email': 'Email Address',
            'phone_number': 'Phone Number',
            'address': 'Residential / Contact Address',
            'emergency_contact_name': 'Emergency Contact Person',
            'emergency_contact_phone': 'Emergency Contact Phone',
            'profile_picture': 'Profile Photo',
        }

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if email:
            existing = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("This email address is already in use by another account.")
        return email

    def clean_phone_number(self):
        phone = (self.cleaned_data.get('phone_number') or '').strip()
        if phone:
            existing = User.objects.filter(phone_number=phone).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("This phone number is already registered to another account.")
            return phone
        return None



COUNTRY_CODE_CHOICES = [
    ('+91', '🇮🇳 India (+91)'),
    ('+1', '🇺🇸 United States / Canada (+1)'),
    ('+44', '🇬🇧 United Kingdom (+44)'),
    ('+971', '🇦🇪 UAE (+971)'),
    ('+65', '🇸🇬 Singapore (+65)'),
    ('+61', '🇦🇺 Australia (+61)'),
    ('+49', '🇩🇪 Germany (+49)'),
    ('+33', '🇫🇷 France (+33)'),
    ('+966', '🇸🇦 Saudi Arabia (+966)'),
    ('+60', '🇲🇾 Malaysia (+60)'),
]


class UsernameVerificationForm(forms.Form):
    username = forms.CharField(
        label="Registered Username",
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter registered username or Employee ID',
            'autocomplete': 'username',
            'id': 'id_username',
            'autofocus': True
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.errors.get('username'):
            curr = self.fields['username'].widget.attrs.get('class', '')
            if 'is-invalid' not in curr:
                self.fields['username'].widget.attrs['class'] = f"{curr} is-invalid"

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError("Please enter your registered username.")

        user = User.objects.filter(is_active=True).filter(
            models.Q(username__iexact=username) |
            models.Q(employee_id__iexact=username)
        ).first()

        if not user:
            raise forms.ValidationError(f"The username or Employee ID '{username}' is not registered in our database. Please check your credentials and try again.")

        self.user = user
        return username


class ForgotPasswordRequestForm(forms.Form):
    channel = forms.CharField(
        initial='EMAIL',
        required=False,
        widget=forms.HiddenInput
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter your registered email address',
            'autocomplete': 'email',
            'id': 'id_recovery_email'
        })
    )
    country_code = forms.ChoiceField(
        choices=COUNTRY_CODE_CHOICES,
        required=False,
        initial='+91',
        widget=forms.HiddenInput
    )
    phone_number = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.HiddenInput
    )

    def clean(self):
        cleaned_data = super().clean()
        channel = cleaned_data.get('channel') or 'EMAIL'
        cleaned_data['channel'] = channel

        if channel == 'EMAIL':
            email = cleaned_data.get('email')
            if not email:
                self.add_error('email', 'Please enter your registered email address.')
            else:
                cleaned_data['email'] = normalize_email(email)
        elif channel == 'SMS':
            phone = cleaned_data.get('phone_number')
            country = cleaned_data.get('country_code') or '+91'
            if not phone:
                self.add_error('phone_number', 'Please enter your registered mobile number.')
            else:
                formatted_phone = normalize_phone_number(phone, country)
                if not is_valid_e164(formatted_phone):
                    self.add_error('phone_number', 'Please enter a valid mobile number with country code.')
                else:
                    cleaned_data['phone_number'] = formatted_phone

        return cleaned_data


class OTPVerifyForm(forms.Form):
    otp = forms.CharField(
        max_length=10,
        required=True,
        widget=forms.HiddenInput(attrs={'id': 'id_final_otp'})
    )

    def clean_otp(self):
        otp = (self.cleaned_data.get('otp') or '').strip()
        otp = re.sub(r'\D', '', otp)
        if len(otp) != 4:
            raise forms.ValidationError('Please enter all 4 digits of the verification code.')
        return otp


class SetNewPasswordForm(forms.Form):
    new_password = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter new password',
            'id': 'id_new_password',
            'autocomplete': 'new-password'
        })
    )
    confirm_password = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Re-enter new password',
            'id': 'id_confirm_password',
            'autocomplete': 'new-password'
        })
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('new_password')
        p2 = cleaned_data.get('confirm_password')

        if p1 and p2:
            if p1 != p2:
                self.add_error('confirm_password', 'Passwords do not match. Please verify and try again.')
            else:
                if self.user and self.user.check_password(p1):
                    self.add_error('new_password', 'New password cannot be the same as your current password.')
                else:
                    try:
                        validate_password(p1, self.user)
                    except forms.ValidationError as e:
                        self.add_error('new_password', e)

        return cleaned_data

