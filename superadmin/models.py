from decimal import Decimal
from django.db import models
from django.utils import timezone
from accounts.models import User

class RestaurantConfig(models.Model):
    restaurant = models.OneToOneField(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='config',
        help_text="The restaurant tenant this configuration applies to"
    )
    restaurant_name = models.CharField(max_length=150, default='The Grand Bistro')
    tagline = models.CharField(max_length=255, default='Fine Dining & Artisan Kitchen', blank=True)
    phone = models.CharField(max_length=50, default='+1 (555) 019-2834')
    email = models.EmailField(default='contact@grandbistro.com')
    address = models.TextField(default='123 Gourmet Boulevard, Food District')
    currency = models.CharField(max_length=5, default='$')
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.0825'))
    service_charge_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.05'))
    
    # Operational Status
    is_open = models.BooleanField(default=True, help_text="Is the restaurant currently open for business?")
    opening_time = models.TimeField(default='09:00:00')
    closing_time = models.TimeField(default='23:00:00')
    maintenance_mode = models.BooleanField(default=False, help_text="Put system into maintenance mode.")
    status_message = models.CharField(max_length=255, blank=True, default='Now Serving Lunch & Dinner')

    # Life-long Permanent Domain for Table QR Codes
    qr_domain = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Permanent public domain or base URL for table QR codes (e.g. https://growwpark.com or http://192.168.0.4:8000). Table QR stands will encode this URL permanently."
    )

    class Meta:
        verbose_name = 'Restaurant Configuration'
        verbose_name_plural = 'Restaurant Configurations'

    @classmethod
    def get_config(cls, restaurant=None):
        if restaurant:
            config = cls.objects.filter(restaurant=restaurant).first()
            if not config:
                base = cls.objects.filter(id=1).first()
                config = cls.objects.create(
                    restaurant=restaurant,
                    restaurant_name=restaurant.name,
                    phone=restaurant.phone or (base.phone if base else '+1 (555) 019-2834'),
                    email=restaurant.email or (base.email if base else 'contact@restaurant.com'),
                    address=restaurant.address or (base.address if base else ''),
                    currency=base.currency if base else '$',
                    tax_rate=base.tax_rate if base else Decimal('0.0825'),
                    service_charge_rate=base.service_charge_rate if base else Decimal('0.05'),
                    qr_domain=base.qr_domain if base else '',
                )
            return config
        config, _ = cls.objects.get_or_create(id=1)
        return config

    def __str__(self):
        return f"{self.restaurant_name} ({'Open' if self.is_open else 'Closed'})"


class AdminReport(models.Model):
    class ReportType(models.TextChoices):
        INCIDENT = 'INCIDENT', 'Kitchen / Dining Incident'
        EQUIPMENT = 'EQUIPMENT', 'Equipment / POS Hardware Issue'
        STAFF = 'STAFF', 'Staff / Personnel Matter'
        INVENTORY = 'INVENTORY', 'Supply / Inventory Discrepancy'
        FINANCIAL = 'FINANCIAL', 'Billing & Cash Discrepancy'
        GENERAL = 'GENERAL', 'General Report / Observation'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        CRITICAL = 'CRITICAL', 'Critical / Urgent'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Review'
        IN_REVIEW = 'IN_REVIEW', 'Under Review'
        RESOLVED = 'RESOLVED', 'Resolved'
        DISMISSED = 'DISMISSED', 'Dismissed'

    report_id = models.CharField(max_length=30, unique=True, editable=False)
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submitted_reports')
    report_type = models.CharField(max_length=30, choices=ReportType.choices, default=ReportType.GENERAL)
    title = models.CharField(max_length=200)
    description = models.TextField()
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    # Super Admin feedback & resolution
    super_admin_notes = models.TextField(blank=True, help_text="Notes/remarks from the Super Admin.")
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_reports')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.report_id:
            count = AdminReport.objects.count() + 1
            date_str = timezone.now().strftime('%Y%m%d')
            self.report_id = f"REP-{date_str}-{count:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.get_priority_display()}] {self.title} by {self.submitted_by.username}"

    @property
    def priority_badge_class(self):
        badges = {
            self.Priority.LOW: 'info',
            self.Priority.MEDIUM: 'warning',
            self.Priority.HIGH: 'danger',
            self.Priority.CRITICAL: 'dark',
        }
        return badges.get(self.priority, 'secondary')

    @property
    def status_badge_class(self):
        badges = {
            self.Status.PENDING: 'warning',
            self.Status.IN_REVIEW: 'primary',
            self.Status.RESOLVED: 'success',
            self.Status.DISMISSED: 'secondary',
        }
        return badges.get(self.status, 'secondary')


class SystemSetting(models.Model):
    session_timeout_minutes = models.PositiveIntegerField(default=60)
    enforce_strong_password = models.BooleanField(default=True)
    max_login_attempts = models.PositiveIntegerField(default=5)
    enable_audit_logging = models.BooleanField(default=True)
    auto_backup_enabled = models.BooleanField(default=True)
    backup_frequency_days = models.PositiveIntegerField(default=1)
    last_backup_date = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get_settings(cls):
        settings_obj, _ = cls.objects.get_or_create(id=1)
        return settings_obj

    def __str__(self):
        return "System Settings"


class AccountStatusAuditLog(models.Model):
    class Action(models.TextChoices):
        CASCADE_DEACTIVATE = 'CASCADE_DEACTIVATE', 'Cascade Deactivation'
        ADMIN_DEACTIVATE = 'ADMIN_DEACTIVATE', 'Admin Deactivation (Cascade)'
        ADMIN_REACTIVATE = 'ADMIN_REACTIVATE', 'Admin Reactivation'
        ADMIN_SUSPEND = 'ADMIN_SUSPEND', 'Admin Suspension'
        ADMIN_UNSUSPEND = 'ADMIN_UNSUSPEND', 'Admin Unsuspension'
        EMPLOYEE_BULK_DEACTIVATE = 'EMPLOYEE_BULK_DEACTIVATE', 'Employee Bulk Deactivation'
        EMPLOYEE_REACTIVATE = 'EMPLOYEE_REACTIVATE', 'Individual Employee Reactivation'
        EMPLOYEE_DEACTIVATE = 'EMPLOYEE_DEACTIVATE', 'Individual Employee Deactivation'

    target_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='status_audit_logs'
    )
    restaurant = models.ForeignKey(
        'accounts.Restaurant', on_delete=models.CASCADE, null=True, blank=True, related_name='status_audit_logs'
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='performed_status_audits'
    )
    affected_count = models.PositiveIntegerField(default=0, help_text="Number of employees affected")
    affected_employee_ids = models.JSONField(
        default=list, blank=True, help_text="IDs of employees who were active and bulk-deactivated in cascade"
    )
    reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Account Status Audit Log'
        verbose_name_plural = 'Account Status Audit Logs'

    def __str__(self):
        return f"[{self.get_action_display()}] on {self.target_user.username} by {self.performed_by} at {self.created_at}"
