from django.db import models
from django.utils import timezone
from accounts.models import User, Restaurant

class KitchenStation(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True, related_name='kitchen_stations')
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    assigned_cooks = models.ManyToManyField(User, blank=True, related_name='kitchen_stations')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.restaurant.name if self.restaurant else 'All'})"


class KitchenStaffProfile(models.Model):
    class Designation(models.TextChoices):
        CHEF = 'CHEF', 'Chef'
        KITCHEN = 'KITCHEN', 'Kitchen Staff'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='kitchen_profile')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True, related_name='kitchen_staff_profiles')
    designation = models.CharField(max_length=30, choices=Designation.choices, default=Designation.CHEF)
    assigned_station = models.ForeignKey(KitchenStation, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_members')
    
    # Configurable kitchen permissions
    can_view_all_orders = models.BooleanField(default=True, help_text="View all restaurant orders vs assigned station only")
    can_accept_orders = models.BooleanField(default=True, help_text="Accept incoming new orders")
    can_reject_orders = models.BooleanField(default=False, help_text="Reject incoming new orders")
    can_update_status = models.BooleanField(default=True, help_text="Transition status from Accepted to Preparing and Ready")
    can_mark_ready = models.BooleanField(default=True, help_text="Mark individual items and orders as Ready")
    can_cancel_items = models.BooleanField(default=True, help_text="Directly cancel eligible individual food items")
    can_request_order_cancel = models.BooleanField(default=True, help_text="Submit request to cancel entire order")
    can_approve_order_cancel = models.BooleanField(default=False, help_text="Approve cancellation of an entire order")
    can_report_unavailable = models.BooleanField(default=True, help_text="Report ingredients/menu items as unavailable")
    can_view_kitchen_reports = models.BooleanField(default=True, help_text="View kitchen performance statistics")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.get_designation_display()}"


class KitchenNotification(models.Model):
    class NotificationType(models.TextChoices):
        NEW_ORDER = 'NEW_ORDER', 'New Order Arrived'
        EXTRA_ITEMS = 'EXTRA_ITEMS', 'Additional Items Added'
        CANCELLATION_REQUEST = 'CANCELLATION_REQUEST', 'Cancellation Request'
        ORDER_CANCELLED = 'ORDER_CANCELLED', 'Order Cancelled'
        DELAYED_ORDER = 'DELAYED_ORDER', 'Order Delayed'
        ITEM_UNAVAILABLE = 'ITEM_UNAVAILABLE', 'Food Item Unavailable'
        CANCEL_RESOLVED = 'CANCEL_RESOLVED', 'Cancellation Request Resolved'
        PRIORITY_ORDER = 'PRIORITY_ORDER', 'Priority Order Alert'
        GENERAL = 'GENERAL', 'Kitchen Announcement'

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True, related_name='kitchen_notifications')
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='kitchen_notifications')
    notification_type = models.CharField(max_length=35, choices=NotificationType.choices, default=NotificationType.NEW_ORDER)
    title = models.CharField(max_length=150)
    message = models.TextField()
    related_order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, null=True, blank=True, related_name='kitchen_notifications')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_notification_type_display()}] {self.title}"


class ItemAvailabilityReport(models.Model):
    class ReportStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending Admin Approval'
        APPROVED = 'APPROVED', 'Approved (Menu Item Disabled)'
        REJECTED = 'REJECTED', 'Rejected (Menu Item Kept Active)'

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True, related_name='item_availability_reports')
    menu_item = models.ForeignKey('menu.MenuItem', on_delete=models.CASCADE, related_name='availability_reports')
    reported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='reported_unavailabilities')
    reason = models.TextField(help_text="Reason why this item cannot be prepared (e.g. ingredient depleted)")
    status = models.CharField(max_length=20, choices=ReportStatus.choices, default=ReportStatus.PENDING)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_unavailabilities')
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Availability Report: {self.menu_item.name} ({self.get_status_display()})"
