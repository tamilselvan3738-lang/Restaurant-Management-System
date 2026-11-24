import secrets
from datetime import timedelta
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.hashers import make_password, check_password
from django.db import models
from django.utils import timezone


class Restaurant(models.Model):
    class RestaurantStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        SUSPENDED = 'SUSPENDED', 'Suspended'

    class SubscriptionPlan(models.TextChoices):
        FREE = 'FREE', 'Free Trial'
        STARTER = 'STARTER', 'Starter Plan'
        PREMIUM = 'PREMIUM', 'Premium Plan'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise Plan'

    name = models.CharField(max_length=150, unique=True, help_text="Company or Restaurant name")
    code = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        db_index=True,
        help_text="Unique organization code (e.g. REST-0001 or restaurant identifier)"
    )
    logo = models.ImageField(upload_to='restaurant_logos/', blank=True, null=True, help_text="Company logo image (PNG, JPG, WEBP)")
    address = models.TextField(blank=True, default='')
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=RestaurantStatus.choices,
        default=RestaurantStatus.ACTIVE,
        db_index=True,
        help_text="Operational status of the restaurant tenant"
    )
    plan = models.CharField(
        max_length=20,
        choices=SubscriptionPlan.choices,
        default=SubscriptionPlan.STARTER,
        help_text="SaaS subscription or license plan tier"
    )
    suspension_reason = models.TextField(blank=True, default='', help_text="Reason for suspension by Super Admin")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Restaurant'
        verbose_name_plural = 'Restaurants'

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name

    @property
    def is_operational(self):
        return self.status == self.RestaurantStatus.ACTIVE

    @classmethod
    def generate_next_code(cls):
        prefix = "REST-"
        existing = cls.objects.filter(code__startswith=prefix).values_list('code', flat=True)
        max_num = 0
        for c in existing:
            if c and c.startswith(prefix):
                try:
                    num = int(c[len(prefix):])
                    if num > max_num:
                        max_num = num
                except (ValueError, TypeError):
                    pass
        return f"{prefix}{max_num + 1:04d}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_next_code()
        super().save(*args, **kwargs)

    @property
    def logo_url(self):
        if self.logo and hasattr(self.logo, 'url'):
            return self.logo.url
        return None


class User(AbstractUser):
    # Override username to allow duplicate usernames across different restaurants
    username = models.CharField(
        max_length=150,
        unique=False,
        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
        validators=[AbstractUser.username_validator],
    )
    normalized_username = models.CharField(
        max_length=150,
        blank=True,
        default='',
        db_index=True,
        help_text="Normalized lowercase username for organization-scoped uniqueness."
    )

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Administrator / Owner'
        MANAGER = 'MANAGER', 'Restaurant Manager'
        CHEF = 'CHEF', 'Head Chef'
        KITCHEN = 'KITCHEN', 'Kitchen Staff'
        WAITER = 'WAITER', 'Waiter / Server'
        CASHIER = 'CASHIER', 'Cashier'
        CUSTOMER = 'CUSTOMER', 'Customer'

    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        help_text="The company/restaurant this Admin or Employee belongs to."
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.WAITER,
        help_text="Designates the system role and permission level."
    )
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    is_on_duty = models.BooleanField(
        default=True,
        help_text="Designates whether staff member is currently on duty/shift."
    )
    class AvailabilityStatus(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        BUSY = 'BUSY', 'Busy'
        ON_BREAK = 'ON_BREAK', 'On Break'
        OFF_DUTY = 'OFF_DUTY', 'Off Duty'

    availability_status = models.CharField(
        max_length=20,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.AVAILABLE,
        db_index=True,
        help_text="Real-time availability status for automated service request routing."
    )
    is_suspended = models.BooleanField(
        default=False,
        help_text="Designates whether this admin or staff member is suspended."
    )
    suspension_reason = models.TextField(blank=True, default='')

    # Unique Staff Login Credentials & Identifiers
    employee_id = models.CharField(
        max_length=30,
        unique=True,
        blank=True,
        null=True,
        db_index=True,
        help_text="Unique Employee Code / Staff Badge ID (e.g. EMP-1001) used for fast login and identification."
    )
    pin_code = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Hashed 4-6 digit quick login PIN for rapid POS, terminal, and kitchen switch access."
    )

    # Department & Operational Schedule
    class Department(models.TextChoices):
        SERVICE = 'SERVICE', 'Service & Dining Floor (FOH)'
        KITCHEN = 'KITCHEN', 'Kitchen & Food Prep (BOH)'
        BAR = 'BAR', 'Bar & Beverages'
        BILLING = 'BILLING', 'Billing & Cash Desk'
        MANAGEMENT = 'MANAGEMENT', 'Store Management'
        MAINTENANCE = 'MAINTENANCE', 'Housekeeping & Support'

    class Shift(models.TextChoices):
        MORNING = 'MORNING', 'Morning Shift (07:00 - 15:00)'
        EVENING = 'EVENING', 'Evening Shift (15:00 - 23:00)'
        FULL_DAY = 'FULL_DAY', 'Full Day Shift (10:00 - 22:00)'
        NIGHT = 'NIGHT', 'Night Shift (22:00 - 06:00)'
        FLEXIBLE = 'FLEXIBLE', 'Flexible / Rotational'

    department = models.CharField(
        max_length=30,
        choices=Department.choices,
        default=Department.SERVICE,
        blank=True,
        help_text="Restaurant operational department."
    )
    shift = models.CharField(
        max_length=30,
        choices=Shift.choices,
        default=Shift.FULL_DAY,
        blank=True,
        help_text="Assigned work shift schedule."
    )
    hire_date = models.DateField(
        blank=True,
        null=True,
        help_text="Date employee joined the restaurant."
    )
    salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Monthly salary or base compensation rate."
    )

    # Emergency Contact & Personal Records
    emergency_contact_name = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Name of emergency contact person."
    )
    emergency_contact_phone = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text="Emergency contact telephone number."
    )
    address = models.TextField(
        blank=True,
        default='',
        help_text="Residential / local address."
    )

    # Administrative Scope & Role Details
    class AdminAccessLevel(models.TextChoices):
        FULL = 'FULL', 'Full Administrative Access'
        OPERATIONS = 'OPERATIONS', 'Operations & Floor Management'
        FINANCE = 'FINANCE', 'Finance & Billing Management'
        MENU_INVENTORY = 'MENU_INVENTORY', 'Menu & Inventory Management'

    job_title = models.CharField(max_length=100, blank=True, default='', help_text="e.g. Operations Admin, Assistant Manager")
    admin_access_level = models.CharField(
        max_length=30,
        choices=AdminAccessLevel.choices,
        default=AdminAccessLevel.FULL,
        help_text="Defines the operational scope for this admin."
    )
    
    # Granular Administrative Privileges
    can_manage_staff = models.BooleanField(default=True, help_text="Permission to add and manage staff accounts")
    can_manage_discounts = models.BooleanField(default=True, help_text="Permission to apply discounts & comps on bills")
    can_view_financials = models.BooleanField(default=True, help_text="Permission to view revenue, invoices & reports")
    can_edit_menu = models.BooleanField(default=True, help_text="Permission to add/edit menu items and prices")

    class Meta:
        ordering = ['username']
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        constraints = [
            models.UniqueConstraint(
                fields=['restaurant', 'normalized_username'],
                name='unique_username_per_restaurant',
                condition=models.Q(restaurant__isnull=False),
            ),
            models.UniqueConstraint(
                fields=['normalized_username'],
                name='unique_username_superadmin',
                condition=models.Q(restaurant__isnull=True),
            ),
        ]

    def __str__(self):
        full_name = self.get_full_name()
        emp_badge = f" [{self.employee_id}]" if self.employee_id else ""
        return f"{full_name or self.username}{emp_badge} ({self.get_role_display()})"

    def set_pin(self, raw_pin):
        if raw_pin and str(raw_pin).strip():
            self.pin_code = make_password(str(raw_pin).strip())
        else:
            self.pin_code = None

    def check_pin(self, raw_pin):
        if not self.pin_code or not raw_pin:
            return False
        return check_password(str(raw_pin).strip(), self.pin_code)

    @property
    def has_pin(self):
        return bool(self.pin_code)

    @classmethod
    def generate_next_employee_id(cls):
        prefix = "EMP-"
        existing = cls.objects.filter(employee_id__startswith=prefix).values_list('employee_id', flat=True)
        max_num = 1000
        for emp_id in existing:
            if emp_id and emp_id.startswith(prefix):
                try:
                    num = int(emp_id[len(prefix):])
                    if num > max_num:
                        max_num = num
                except (ValueError, TypeError):
                    pass
        return f"{prefix}{max_num + 1:04d}"

    def clean(self):
        super().clean()
        if self.username:
            self.normalized_username = self.username.strip().lower()

    def save(self, *args, **kwargs):
        if self.username:
            self.normalized_username = self.username.strip().lower()
        if not self.employee_id and self.role != self.Role.CUSTOMER:
            self.employee_id = self.generate_next_employee_id()
        if not self.is_on_duty and self.availability_status == self.AvailabilityStatus.AVAILABLE:
            self.availability_status = self.AvailabilityStatus.OFF_DUTY
        elif self.is_on_duty and self.availability_status == self.AvailabilityStatus.OFF_DUTY:
            self.availability_status = self.AvailabilityStatus.AVAILABLE
        super().save(*args, **kwargs)

    @property
    def is_available_for_service(self):
        """Returns True if the staff member is active, on duty, and marked as AVAILABLE."""
        return (
            self.is_active and
            not self.is_suspended and
            self.is_on_duty and
            self.availability_status == self.AvailabilityStatus.AVAILABLE
        )

    @property
    def has_profile_picture(self):
        if not self.profile_picture:
            return False
        try:
            return bool(self.profile_picture.name and self.profile_picture.storage.exists(self.profile_picture.name))
        except Exception:
            return False

    @property
    def profile_picture_url(self):
        if self.has_profile_picture:
            try:
                return self.profile_picture.url
            except Exception:
                return None
        return None

    @property
    def admin_status(self):
        if self.is_suspended:
            return 'Suspended'
        if not self.is_active:
            return 'Inactive'
        return 'Active'

    @property
    def status_badge_class(self):
        if self.is_suspended:
            return 'danger'
        if not self.is_active:
            return 'secondary'
        return 'success'

    @property
    def is_super_admin(self):
        return self.is_superuser

    @property
    def is_super_or_admin(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_admin_or_manager(self):
        return self.is_superuser or self.role in [self.Role.ADMIN, self.Role.MANAGER, self.Role.CASHIER]

    @property
    def is_chef(self):
        return self.role == self.Role.CHEF

    @property
    def is_kitchen_staff(self):
        return self.role in [self.Role.CHEF, self.Role.KITCHEN]

    @property
    def is_waiter_staff(self):
        return self.role == self.Role.WAITER

    @property
    def is_cashier_staff(self):
        return self.role == self.Role.CASHIER


class PasswordResetOTP(models.Model):
    class Channel(models.TextChoices):
        EMAIL = 'EMAIL', 'Email'
        SMS = 'SMS', 'SMS'

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='password_reset_otps',
        help_text="The user account requesting password recovery."
    )
    channel = models.CharField(
        max_length=10,
        choices=Channel.choices,
        help_text="Delivery channel used to send the OTP (EMAIL or SMS)."
    )
    destination = models.CharField(
        max_length=255,
        help_text="Destination email address or E.164 phone number."
    )
    otp_hash = models.CharField(
        max_length=128,
        help_text="Secure hash of the OTP code (never plain text)."
    )
    reset_token = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="One-time cryptographically random token authorizing password reset after OTP verification."
    )
    expires_at = models.DateTimeField(
        help_text="Timestamp when the current OTP code expires (5 minutes)."
    )
    reset_token_expires_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Timestamp when the reset authorization token expires (10 minutes)."
    )
    attempts = models.PositiveIntegerField(
        default=0,
        help_text="Count of verification attempts made against this OTP challenge (max 5)."
    )
    resend_count = models.PositiveIntegerField(
        default=0,
        help_text="Count of resend requests made for this challenge (max 3 per 15 minutes)."
    )
    last_resend_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp of the most recent OTP dispatch."
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Indicates whether the OTP was verified successfully."
    )
    is_used = models.BooleanField(
        default=False,
        help_text="Indicates whether the password was successfully reset using this challenge."
    )
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="IP address of the requester for rate-limiting and auditing."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Password Reset OTP'
        verbose_name_plural = 'Password Reset OTPs'

    def __str__(self):
        return f"OTP for {self.user.username} ({self.channel}) - {'Used' if self.is_used else 'Verified' if self.is_verified else 'Pending'}"

    def set_otp(self, raw_otp):
        self.otp_hash = make_password(str(raw_otp).strip())

    def check_otp(self, raw_otp):
        if not self.otp_hash or not raw_otp:
            return False
        return check_password(str(raw_otp).strip(), self.otp_hash)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def remaining_seconds(self):
        diff = (self.expires_at - timezone.now()).total_seconds()
        return max(0, int(diff))

    @property
    def can_attempt(self):
        return self.attempts < 5 and not self.is_expired and not self.is_used and not self.is_verified

    @property
    def can_resend(self):
        if self.is_used or self.is_verified:
            return False
        if self.resend_count >= 3:
            return False
        time_since_resend = (timezone.now() - self.last_resend_at).total_seconds()
        return time_since_resend >= 60

    @property
    def resend_cooldown_remaining(self):
        time_since_resend = (timezone.now() - self.last_resend_at).total_seconds()
        return max(0, int(60 - time_since_resend))

    def generate_reset_token(self):
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expires_at = timezone.now() + timedelta(minutes=10)
        self.is_verified = True
        self.save(update_fields=['reset_token', 'reset_token_expires_at', 'is_verified'])
        return self.reset_token

    def is_reset_token_valid(self, token):
        if not token or self.is_used or not self.is_verified:
            return False
        if not self.reset_token or self.reset_token != token:
            return False
        if not self.reset_token_expires_at or timezone.now() > self.reset_token_expires_at:
            return False
        return True

    def mark_used(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=['is_used', 'used_at'])

    @property
    def masked_destination(self):
        dest = self.destination or ''
        if self.channel == self.Channel.EMAIL and '@' in dest:
            parts = dest.split('@', 1)
            name = parts[0]
            domain = parts[1]
            if len(name) <= 2:
                masked_name = name[0] + '*'
            else:
                masked_name = name[0] + ('*' * (len(name) - 2)) + name[-1]
            return f"{masked_name}@{domain}"
        elif self.channel == self.Channel.SMS:
            cleaned = dest.replace(' ', '').replace('-', '')
            if len(cleaned) > 6:
                return f"{cleaned[:3]} {'*' * (len(cleaned) - 7)} {cleaned[-4:]}"
            return dest
        return dest

