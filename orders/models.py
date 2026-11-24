from decimal import Decimal
from django.db import models
from django.utils import timezone
from accounts.models import User
from menu.models import MenuItem
from tables.models import DiningTable

class Order(models.Model):
    class OrderType(models.TextChoices):
        DINE_IN = 'DINE_IN', 'Dine-In'
        TAKEAWAY = 'TAKEAWAY', 'Takeaway'
        DELIVERY = 'DELIVERY', 'Delivery'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        PREPARING = 'PREPARING', 'Preparing'
        READY = 'READY', 'Ready to Serve'
        SERVED = 'SERVED', 'Served'
        COMPLETED = 'COMPLETED', 'Completed & Paid'
        CANCELLED = 'CANCELLED', 'Cancelled'
        # Backwards compatibility
        ACTIVE = 'ACTIVE', 'Active'
        PLACED = 'PLACED', 'Order Placed'
        IN_KITCHEN = 'IN_KITCHEN', 'Cooking in Kitchen'

    order_number = models.CharField(max_length=30, unique=True, editable=False)
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='orders',
        help_text="Restaurant tenant this order belongs to"
    )
    order_type = models.CharField(max_length=20, choices=OrderType.choices, default=OrderType.DINE_IN)
    table = models.ForeignKey(DiningTable, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    session = models.ForeignKey('tables.CustomerSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    server = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders_taken')
    customer_name = models.CharField(max_length=100, default='Guest')
    customer_phone = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.PENDING)
    special_instructions = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=64, blank=True, null=True, unique=True)
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    service_charge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    has_unseen_extra = models.BooleanField(default=False, help_text="True if extra items were added and not yet viewed by staff/admin")
    has_unseen_cancellation = models.BooleanField(default=False, help_text="True if order or item was cancelled and not yet viewed by staff/admin")
    
    # Kitchen Staff Exclusive Assignment & Preparation Lifecycle
    accepted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accepted_orders',
        help_text="Kitchen Staff employee who exclusively accepted and owns this order."
    )
    accepted_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when order was accepted by kitchen staff")
    cooking_started_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when cooking/preparation began")
    ready_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when food was marked Cook Complete - Ready")
    served_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when food was physically served to customer")

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    cancellation_requested_by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.order_number:
            date_str = timezone.now().strftime('%Y%m%d')
            prefix = f"ORD-{date_str}-"
            last_order = Order.objects.filter(order_number__startswith=prefix).order_by('-order_number').first()
            if last_order:
                try:
                    last_seq = int(last_order.order_number.split('-')[-1])
                    next_seq = last_seq + 1
                except (ValueError, IndexError):
                    next_seq = Order.objects.filter(created_at__date=timezone.now().date()).count() + 1
            else:
                next_seq = 1

            candidate = f"{prefix}{next_seq:04d}"
            while Order.objects.filter(order_number=candidate).exists():
                next_seq += 1
                candidate = f"{prefix}{next_seq:04d}"
            self.order_number = candidate
        if not self.restaurant:
            if self.table and hasattr(self.table, 'restaurant') and self.table.restaurant:
                self.restaurant = self.table.restaurant
            elif self.session and hasattr(self.session, 'restaurant') and self.session.restaurant:
                self.restaurant = self.session.restaurant
            elif self.server and hasattr(self.server, 'restaurant') and self.server.restaurant:
                self.restaurant = self.server.restaurant
        super().save(*args, **kwargs)

    def __str__(self):
        session_info = f" • {self.session.session_name}" if self.session else ""
        return f"{self.order_number}{session_info} ({self.get_order_type_display()} - {self.get_status_display()})"

    @property
    def subtotal(self):
        items_sum = sum(item.total_price for item in self.items.exclude(status='CANCELLED'))
        return items_sum if items_sum > Decimal('0.00') else (Decimal('0.00') if self.status == self.Status.CANCELLED else self.subtotal_amount)

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.exclude(status='CANCELLED'))

    @property
    def active_items(self):
        return self.items.exclude(status='CANCELLED')

    @property
    def cancelled_items(self):
        return self.items.filter(status='CANCELLED')

    @property
    def cancellation_requested_items(self):
        return self.items.filter(status='CANCELLATION_REQUESTED')

    @property
    def has_cancellations(self):
        return self.items.filter(status='CANCELLED').exists()

    @property
    def has_cancellation_requests(self):
        return self.items.filter(status='CANCELLATION_REQUESTED').exists()

    @property
    def has_extra_items(self):
        return any((item.extra_quantity > 0 or item.is_extra) and item.status != 'CANCELLED' for item in self.items.all())

    @property
    def extra_items(self):
        return [item for item in self.items.all() if (item.extra_quantity > 0 or item.is_extra) and item.status != 'CANCELLED']

    @property
    def total_extra_quantity(self):
        return sum(item.extra_quantity for item in self.items.all() if item.status != 'CANCELLED')

    @property
    def is_accepted(self):
        return self.accepted_by is not None

    @property
    def is_serving_delayed(self):
        """True if food is marked READY and has exceeded the 5-minute expected serving window without being served."""
        if self.status == self.Status.READY and self.ready_at:
            return (timezone.now() - self.ready_at).total_seconds() > 300
        return False

    @property
    def serving_delay_minutes(self):
        if self.is_serving_delayed:
            return int(((timezone.now() - self.ready_at).total_seconds() - 300) // 60) + 1
        return 0

    @property
    def serving_countdown_remaining_seconds(self):
        """Seconds remaining in 5-minute expected serving window (300 seconds total)."""
        if self.status == self.Status.READY and self.ready_at:
            elapsed = (timezone.now() - self.ready_at).total_seconds()
            return max(0, 300 - int(elapsed))
        return 0

    @property
    def accepted_by_name(self):
        if self.accepted_by:
            return self.accepted_by.get_full_name() or self.accepted_by.username
        return None

    @property
    def accepted_by_designation(self):
        if self.accepted_by:
            profile = getattr(self.accepted_by, 'kitchen_profile', None)
            if profile:
                return profile.get_designation_display()
            return self.accepted_by.get_role_display()
        return None

    @property
    def accepted_by_employee_id(self):
        if self.accepted_by:
            return self.accepted_by.employee_id or f"STAFF-{self.accepted_by.id:04d}"
        return None

    @property
    def is_delayed(self):
        if self.status == self.Status.PREPARING and self.created_at:
            elapsed = (timezone.now() - self.created_at).total_seconds() / 60
            return elapsed > 30 or any(it.is_delayed for it in self.items.all())
        return False

    @property
    def badge_class(self):
        badges = {
            self.Status.PENDING: 'warning',
            self.Status.ACCEPTED: 'info',
            self.Status.PREPARING: 'primary',
            self.Status.READY: 'success',
            self.Status.SERVED: 'secondary',
            self.Status.COMPLETED: 'success',
            self.Status.CANCELLED: 'danger',
            self.Status.ACTIVE: 'primary',
            'PLACED': 'warning',
            'IN_KITCHEN': 'primary',
        }
        return badges.get(self.status, 'primary')

    def calculate_totals(self, tax_rate=Decimal('0.0825'), service_rate=Decimal('0.05'), save=True):
        if self.status == self.Status.CANCELLED and not self.items.exclude(status='CANCELLED').exists():
            self.subtotal_amount = Decimal('0.00')
            self.tax_amount = Decimal('0.00')
            self.service_charge_amount = Decimal('0.00')
            self.total_amount = Decimal('0.00')
        else:
            self.subtotal_amount = sum(item.total_price for item in self.items.exclude(status='CANCELLED'))
            self.tax_amount = round(self.subtotal_amount * tax_rate, 2)
            self.service_charge_amount = round(self.subtotal_amount * service_rate, 2)
            self.total_amount = max(Decimal('0.00'), self.subtotal_amount + self.tax_amount + self.service_charge_amount - self.discount_amount)
        if save and self.pk:
            self.save(update_fields=['subtotal_amount', 'tax_amount', 'service_charge_amount', 'total_amount'])
        return self.total_amount


class OrderItem(models.Model):
    class ItemStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COOKING = 'COOKING', 'Cooking'
        READY = 'READY', 'Ready'
        SERVED = 'SERVED', 'Served'
        CANCELLATION_REQUESTED = 'CANCELLATION_REQUESTED', 'Cancellation Requested'
        CANCELLED = 'CANCELLED', 'Cancelled'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    initial_quantity = models.PositiveIntegerField(default=0, help_text="Quantity in initial order placement")
    extra_quantity = models.PositiveIntegerField(default=0, help_text="Quantity added as extra in subsequent additions")
    is_extra = models.BooleanField(default=False, help_text="True if this item was added as extra")
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    special_notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=25, choices=ItemStatus.choices, default=ItemStatus.PENDING)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    cancelled_by = models.CharField(max_length=100, blank=True)
    cooking_started_at = models.DateTimeField(null=True, blank=True, help_text="Time preparation began")
    ready_at = models.DateTimeField(null=True, blank=True, help_text="Time preparation completed")
    kitchen_station = models.ForeignKey('kitchen.KitchenStation', on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    assigned_cook = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_order_items')
    combo = models.ForeignKey('menu.FoodCombo', on_delete=models.SET_NULL, null=True, blank=True, related_name='ordered_items')
    combo_name = models.CharField(max_length=150, blank=True, help_text="Snapshot of combo name when ordered")
    is_combo_component = models.BooleanField(default=False, help_text="True if this dish is part of a combo")
    is_combo_header = models.BooleanField(default=False, help_text="True if this line item represents the combo pricing header")
    added_by_member = models.ForeignKey('tables.GroupMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='ordered_items')
    added_by_name = models.CharField(max_length=100, blank=True, help_text="Name of group member who ordered this item")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name} for {self.order.order_number}"

    @property
    def total_price(self):
        if self.status == self.ItemStatus.CANCELLED:
            return Decimal('0.00')
        return self.quantity * self.unit_price

    @property
    def original_total_price(self):
        return self.quantity * self.unit_price

    @property
    def is_cancelled(self):
        return self.status == self.ItemStatus.CANCELLED

    @property
    def is_cancellation_requested(self):
        return self.status == self.ItemStatus.CANCELLATION_REQUESTED

    @property
    def can_cancel_direct(self):
        return self.status == self.ItemStatus.PENDING

    @property
    def can_request_cancel(self):
        return self.status == self.ItemStatus.COOKING

    @property
    def extra_total_price(self):
        if self.status == self.ItemStatus.CANCELLED:
            return Decimal('0.00')
        return self.extra_quantity * self.unit_price

    @property
    def initial_total_price(self):
        if self.status == self.ItemStatus.CANCELLED:
            return Decimal('0.00')
        return self.initial_quantity * self.unit_price

    @property
    def is_delayed(self):
        if self.status == self.ItemStatus.COOKING:
            start = self.cooking_started_at or self.created_at
            if start:
                prep_time = getattr(self.menu_item, 'preparation_time', 20) or 20
                return (timezone.now() - start).total_seconds() / 60 > prep_time
        return False

    @property
    def elapsed_cooking_minutes(self):
        if self.cooking_started_at:
            end = self.ready_at or timezone.now()
            return int((end - self.cooking_started_at).total_seconds() // 60)
        return 0

    @property
    def badge_class(self):
        badges = {
            self.ItemStatus.PENDING: 'secondary',
            self.ItemStatus.COOKING: 'warning',
            self.ItemStatus.READY: 'success',
            self.ItemStatus.SERVED: 'info',
            self.ItemStatus.CANCELLATION_REQUESTED: 'danger',
            self.ItemStatus.CANCELLED: 'danger',
        }
        return badges.get(self.status, 'secondary')


class OrderCancellationLog(models.Model):
    class CancellationType(models.TextChoices):
        ITEM = 'ITEM', 'Single Item'
        ORDER = 'ORDER', 'Entire Order'

    class LogStatus(models.TextChoices):
        CANCELLED = 'CANCELLED', 'Cancelled'
        REQUESTED = 'REQUESTED', 'Requested (Pending Staff Approval)'
        REJECTED = 'REJECTED', 'Rejected by Staff'
        APPROVED = 'APPROVED', 'Approved by Staff'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='cancellation_logs')
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='cancellation_logs',
        help_text="Restaurant tenant this cancellation log belongs to"
    )
    order_item = models.ForeignKey(OrderItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancellation_logs')
    cancellation_type = models.CharField(max_length=20, choices=CancellationType.choices, default=CancellationType.ITEM)
    item_name = models.CharField(max_length=150, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    amount_deducted = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    reason = models.CharField(max_length=255, blank=True)
    requested_by = models.CharField(max_length=100, default='Customer')
    status = models.CharField(max_length=25, choices=LogStatus.choices, default=LogStatus.CANCELLED)
    is_seen = models.BooleanField(default=False, help_text="True if staff/admin has viewed this cancellation notification")
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_cancellations')
    review_notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_cancellation_type_display()} Cancellation for {self.order.order_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.restaurant and self.order and hasattr(self.order, 'restaurant') and self.order.restaurant:
            self.restaurant = self.order.restaurant
        super().save(*args, **kwargs)


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.order_id}: {self.from_status} -> {self.to_status}"


class ExtraFoodOrderLog(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='extra_food_logs')
    order_item = models.ForeignKey(OrderItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='extra_food_logs')
    table = models.ForeignKey('tables.DiningTable', on_delete=models.SET_NULL, null=True, blank=True, related_name='extra_food_logs')
    table_name = models.CharField(max_length=50, blank=True, help_text="Table identifier (e.g. T-01)")
    session_name = models.CharField(max_length=100, blank=True)
    item_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    added_by_name = models.CharField(max_length=100, default='Customer')
    special_notes = models.CharField(max_length=255, blank=True)
    is_seen = models.BooleanField(default=False, help_text="True if staff/admin has viewed this extra food notification")
    seen_at = models.DateTimeField(null=True, blank=True)
    seen_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='seen_extra_foods')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Extra {self.quantity}x {self.item_name} for Table {self.table_name or 'N/A'}"

