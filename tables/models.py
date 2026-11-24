import uuid
import random
from decimal import Decimal
from django.db import models
from django.db.models import Q
from django.utils import timezone
from menu.models import MenuItem

def get_lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def build_table_qr_url(table, host=None):
    """
    Constructs a clean, production-ready, life-long URL for customer table ordering.
    Features:
    - Never doubles protocols (e.g. 'http://https://...').
    - If a permanent domain is stored in RestaurantConfig (e.g. 'https://growwpark.com'), uses it automatically.
    - If a valid non-loopback host is passed (e.g. custom domain/tunnel), respects it.
    - If loopback (127.0.0.1 or localhost) or no host is passed, falls back to LAN IP + port 8000 for local Wi-Fi testing.
    - Appends ?token=<qr_token> for anti-tampering verification.
    """
    target = None

    # 1. Custom or explicitly passed host
    if host and str(host).strip():
        h = str(host).strip()
        h_no_proto = h.replace('http://', '').replace('https://', '')
        if not (h_no_proto.startswith('127.0.0.1') or h_no_proto.startswith('localhost')):
            target = h

    # 2. Permanent Domain in RestaurantConfig
    if not target:
        try:
            from superadmin.models import RestaurantConfig
            config = RestaurantConfig.get_config()
            if config and config.qr_domain and config.qr_domain.strip():
                target = config.qr_domain.strip()
        except Exception:
            pass

    # 3. Dynamic Local Wi-Fi (LAN IP) Fallback
    if not target:
        lan = get_lan_ip()
        port = '8000'
        if host and ':' in str(host):
            parts = str(host).split(':')
            if parts[-1].isdigit():
                port = parts[-1]
        target = f"{lan}:{port}" if port and port not in ('80', '443') else f"{lan}:8000"

    target = target.strip().rstrip('/')

    # Format protocol cleanly
    if target.startswith('http://') or target.startswith('https://'):
        base_url = target
    else:
        raw_check = target.split(':')[0]
        if raw_check.startswith(('192.168.', '10.', '172.', '127.', 'localhost')):
            # Ensure local private IP includes port 8000 if no port specified
            if ':' not in target:
                target = f"{target}:8000"
            base_url = f"http://{target}"
        else:
            base_url = f"https://{target}"

    token_param = f"?token={table.qr_token}" if table.qr_token else ""
    return f"{base_url}/tables/{table.table_number}/order/{token_param}"


def generate_join_code():
    return str(random.randint(100000, 999999))


class DiningTable(models.Model):
    class Location(models.TextChoices):
        ROUND = 'ROUND', 'Round Table'
        RECTANGLE = 'RECTANGLE', 'Rectangle Table'
        COUPLE = 'COUPLE', 'Couples Table'
        FAMILY = 'FAMILY', 'Family Table'
        BOOTH = 'BOOTH', 'Booth Table'
        VIP = 'VIP', 'VIP Table'

    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        OCCUPIED = 'OCCUPIED', 'Occupied'
        PARTIALLY_OCCUPIED = 'PARTIALLY_OCCUPIED', 'Partially Occupied'
        BILL_REQUESTED = 'BILL_REQUESTED', 'Bill Requested'
        RESERVED = 'RESERVED', 'Reserved'
        CLEANING = 'CLEANING', 'Needs Cleaning'

    table_number = models.CharField(max_length=15, help_text="e.g. T-01, T-02, VIP-1")
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.SET_NULL, null=True, blank=True, related_name='dining_tables')
    capacity = models.PositiveIntegerField(default=4, help_text="Seating capacity")
    location = models.CharField(max_length=20, choices=Location.choices, default=Location.ROUND, verbose_name="Type of Table")
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.AVAILABLE)
    is_active = models.BooleanField(default=True)
    qr_token = models.CharField(max_length=64, unique=True, blank=True, null=True, help_text="Anti-tampering secure token for QR access")
    qr_code = models.ImageField(upload_to='table_qrs/', blank=True, null=True, help_text="QR Code for Table Digital Ordering")

    class Meta:
        ordering = ['table_number']
        constraints = [
            models.UniqueConstraint(
                fields=['restaurant', 'table_number'],
                name='unique_table_per_restaurant',
                condition=models.Q(restaurant__isnull=False),
            ),
        ]

    def __str__(self):
        return f"Table {self.table_number} ({self.capacity} seats - {self.get_location_display()})"

    @property
    def badge_class(self):
        badges = {
            self.Status.AVAILABLE: 'success',
            self.Status.OCCUPIED: 'danger',
            self.Status.PARTIALLY_OCCUPIED: 'warning',
            self.Status.BILL_REQUESTED: 'info',
            self.Status.RESERVED: 'secondary',
            self.Status.CLEANING: 'dark',
        }
        return badges.get(self.status, 'secondary')

    def get_order_path(self):
        if self.qr_token:
            return f"/tables/{self.table_number}/order/?token={self.qr_token}"
        return f"/tables/{self.table_number}/order/"

    @property
    def occupied_seats_count(self):
        return self.seats.filter(is_active=True, status='OCCUPIED').count()

    @property
    def occupied_seats(self):
        return self.occupied_seats_count

    @property
    def available_seats_count(self):
        return self.seats.filter(is_active=True, status='AVAILABLE').count()

    def sync_seats(self):
        if not self.pk:
            return
        existing_seats = {s.seat_number: s for s in self.seats.all()}
        for seat_num in range(1, self.capacity + 1):
            if seat_num not in existing_seats:
                RestaurantSeat.objects.create(
                    table=self,
                    seat_number=seat_num,
                    status=RestaurantSeat.Status.AVAILABLE
                )
        for seat_num, seat in existing_seats.items():
            if seat_num > self.capacity:
                if seat.status == RestaurantSeat.Status.AVAILABLE:
                    seat.delete()
                else:
                    seat.is_active = False
                    seat.save(update_fields=['is_active'])

    def active_sessions(self):
        return self.sessions.filter(status=CustomerSession.Status.ACTIVE)

    def total_active_guests(self):
        return sum(s.guest_count for s in self.active_sessions())

    def can_release(self):
        return not self.active_sessions().exists() and self.occupied_seats_count == 0

    def sync_status(self):
        if not self.pk:
            return self.status

        try:
            self.sync_seats()
        except Exception:
            pass

        # Release any seats held by completed, cancelled, or bill-requested sessions
        for s in self.sessions.filter(status__in=[CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED, CustomerSession.Status.BILL_REQUESTED]):
            if s.seat_allocations.filter(status='ACTIVE').exists():
                s.release_seats(reason="Bill requested or session closed")

        active = self.active_sessions()
        occ = self.occupied_seats_count
        cap = self.capacity

        if occ == 0:
            if self.status not in [self.Status.RESERVED, self.Status.CLEANING]:
                self.status = self.Status.AVAILABLE
        elif occ >= cap:
            self.status = self.Status.OCCUPIED
        else:
            self.status = self.Status.PARTIALLY_OCCUPIED

        self.save(update_fields=['status'])
        return self.status

    def release_and_set_available(self, performed_by=None):
        """
        Releases all seats, closes all active customer sessions,
        and sets table status to AVAILABLE across the entire system.
        """
        # 1. Close active customer sessions for this table
        for s in self.active_sessions():
            s.close_session(reason=f"Table {self.table_number} marked Available by staff")

        # 2. Release active seat allocations
        SessionSeatAllocation.objects.filter(
            seat__table=self,
            status=SessionSeatAllocation.AllocationStatus.ACTIVE
        ).update(
            status=SessionSeatAllocation.AllocationStatus.RELEASED,
            released_at=timezone.now(),
            release_reason=f"Table {self.table_number} marked Available by staff"
        )

        # 3. Set all seats to AVAILABLE
        for seat in self.seats.all():
            prev_status = seat.status
            if prev_status != RestaurantSeat.Status.AVAILABLE:
                seat.status = RestaurantSeat.Status.AVAILABLE
                seat.save(update_fields=['status', 'updated_at'])
                SeatAuditLog.objects.create(
                    seat=seat,
                    action="MANUAL_RELEASE",
                    performed_by=performed_by,
                    previous_status=prev_status,
                    new_status=RestaurantSeat.Status.AVAILABLE,
                    reason=f"Table {self.table_number} marked Available by staff"
                )

        # 4. Set table status to AVAILABLE
        self.status = self.Status.AVAILABLE
        self.save(update_fields=['status'])
        return self.status

    def set_occupied(self, performed_by=None):
        """
        Marks all seats and table status as OCCUPIED.
        """
        for seat in self.seats.all():
            prev_status = seat.status
            if prev_status != RestaurantSeat.Status.OCCUPIED:
                seat.status = RestaurantSeat.Status.OCCUPIED
                seat.save(update_fields=['status', 'updated_at'])
                SeatAuditLog.objects.create(
                    seat=seat,
                    action="STATUS_CHANGE",
                    performed_by=performed_by,
                    previous_status=prev_status,
                    new_status=RestaurantSeat.Status.OCCUPIED,
                    reason=f"Table {self.table_number} marked Occupied by staff"
                )
        self.status = self.Status.OCCUPIED
        self.save(update_fields=['status'])
        return self.status

    def regenerate_qr_token(self):
        self.qr_token = uuid.uuid4().hex[:20]
        self.save(update_fields=['qr_token'])
        self.generate_and_save_qr()

    def get_qr_base64(self, host=None):
        import qrcode
        import io
        import base64

        url = build_table_qr_url(self, host=host)

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1a1a1a", back_color="#ffffff")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/png;base64,{img_str}"

    def generate_and_save_qr(self, host=None):
        try:
            import qrcode
            import io
            from django.core.files.base import ContentFile

            url = build_table_qr_url(self, host=host)
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#1a1a1a", back_color="#ffffff")

            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            filename = f"qr_table_{self.restaurant_id or 'global'}_{self.table_number}.png"
            self.qr_code.save(filename, ContentFile(buffer.getvalue()), save=False)
            DiningTable.objects.filter(pk=self.pk).update(qr_code=self.qr_code.name)
        except Exception:
            pass

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not self.qr_token:
            self.qr_token = uuid.uuid4().hex[:20]
        super().save(*args, **kwargs)
        try:
            self.sync_seats()
        except Exception:
            pass
        if is_new or not self.qr_code:
            self.generate_and_save_qr()


class RestaurantSeat(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        OCCUPIED = 'OCCUPIED', 'Occupied'
        RESERVED = 'RESERVED', 'Reserved'
        CLEANING = 'CLEANING', 'Cleaning'
        DISABLED = 'DISABLED', 'Disabled / Maintenance'

    table = models.ForeignKey(DiningTable, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.PositiveIntegerField(help_text="Seat number within this table (1, 2, ...)")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['table', 'seat_number']
        unique_together = ('table', 'seat_number')

    def __str__(self):
        return f"Table {self.table.table_number} - Seat {self.seat_number} ({self.get_status_display()})"

    @property
    def badge_class(self):
        badges = {
            self.Status.AVAILABLE: 'success',
            self.Status.OCCUPIED: 'danger',
            self.Status.RESERVED: 'primary',
            self.Status.CLEANING: 'warning',
            self.Status.DISABLED: 'secondary',
        }
        return badges.get(self.status, 'secondary')

    def current_allocation(self):
        return self.allocations.filter(status='ACTIVE').select_related('session').first()

    def current_session(self):
        alloc = self.current_allocation()
        return alloc.session if alloc else None


class CustomerSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        BILL_REQUESTED = 'BILL_REQUESTED', 'Bill Requested'
        COMPLETED = 'COMPLETED', 'Completed & Paid'
        CANCELLED = 'CANCELLED', 'Cancelled'

    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='customer_sessions',
        help_text="Restaurant tenant this customer session belongs to"
    )
    session_token = models.CharField(max_length=64, unique=True, db_index=True)
    join_code = models.CharField(max_length=10, db_index=True)
    table = models.ForeignKey(DiningTable, on_delete=models.CASCADE, related_name='sessions')
    session_name = models.CharField(max_length=60, default='Group 1', help_text="e.g. Group A, Group B, or Guest Name")
    customer_name = models.CharField(max_length=100, default='Guest')
    customer_phone = models.CharField(max_length=20, blank=True)
    guest_count = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.ACTIVE)
    special_requests = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.session_name} • Table {self.table.table_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.session_token:
            self.session_token = uuid.uuid4().hex
        if not self.join_code:
            self.join_code = generate_join_code()
            while CustomerSession.objects.filter(join_code=self.join_code, status=self.Status.ACTIVE).exists():
                self.join_code = generate_join_code()
        if not self.restaurant and self.table and hasattr(self.table, 'restaurant') and self.table.restaurant:
            self.restaurant = self.table.restaurant
        super().save(*args, **kwargs)

    @property
    def badge_class(self):
        badges = {
            self.Status.ACTIVE: 'primary',
            self.Status.BILL_REQUESTED: 'warning',
            self.Status.COMPLETED: 'success',
            self.Status.CANCELLED: 'danger',
        }
        return badges.get(self.status, 'secondary')

    @property
    def subtotal(self):
        from orders.models import Order
        valid_orders = self.orders.exclude(status=Order.Status.CANCELLED)
        total = Decimal('0.00')
        for order in valid_orders:
            total += order.subtotal
        return total

    @property
    def tax_rate(self):
        return Decimal('0.0825')

    @property
    def tax_amount(self):
        return round(self.subtotal * self.tax_rate, 2)

    @property
    def service_charge_rate(self):
        return Decimal('0.05')

    @property
    def service_charge_amount(self):
        return round(self.subtotal * self.service_charge_rate, 2)

    @property
    def discount_amount(self):
        inv = self.invoices.first()
        return inv.discount_amount if inv else Decimal('0.00')

    @property
    def total_amount(self):
        inv = self.invoices.first()
        if inv and inv.total_amount > Decimal('0.00'):
            return inv.total_amount
        return self.subtotal + self.tax_amount + self.service_charge_amount - self.discount_amount

    @property
    def total_paid(self):
        from billing.models import Payment
        paid = Decimal('0.00')
        for inv in self.invoices.all():
            paid += inv.total_paid
        return paid

    @property
    def balance_due(self):
        return max(Decimal('0.00'), self.total_amount - self.total_paid)

    @property
    def is_paid(self):
        return self.total_amount > Decimal('0.00') and self.balance_due == Decimal('0.00')

    @property
    def refund_due(self):
        inv = self.invoices.first()
        return inv.refund_due if inv else Decimal('0.00')

    @property
    def has_active_orders(self):
        from orders.models import Order
        return self.orders.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).exists()

    @property
    def total_items_count(self):
        from orders.models import Order
        valid_orders = self.orders.exclude(status=Order.Status.CANCELLED)
        return sum(order.total_items for order in valid_orders)

    def allocated_seats(self):
        seats = [alloc.seat for alloc in self.seat_allocations.filter(status='ACTIVE').select_related('seat')]
        return sorted(seats, key=lambda s: s.seat_number)

    def allocated_seat_numbers(self):
        seats = self.allocated_seats()
        if not seats:
            return "Unallocated"
        return ", ".join(f"Seat {s.seat_number}" for s in sorted(seats, key=lambda s: s.seat_number))

    def release_seats(self, reason="Bill paid & session closed"):
        for alloc in self.seat_allocations.filter(status='ACTIVE'):
            alloc.release(reason=reason)

    def close_session(self, reason="Bill paid & session closed"):
        self.status = self.Status.COMPLETED
        self.closed_at = timezone.now()
        self.save(update_fields=['status', 'closed_at'])
        self.release_seats(reason=reason)
        self.table.sync_status()

    @property
    def active_members(self):
        return self.members.filter(is_active=True).order_by('joined_at')

    @property
    def leader(self):
        ldr = self.members.filter(role=GroupMember.Role.LEADER, is_active=True).first()
        if not ldr:
            # Fallback 1: match session creator by name
            if self.customer_name:
                ldr = self.members.filter(name__iexact=self.customer_name.strip(), is_active=True).first()
            # Fallback 2: first active joined member
            if not ldr:
                ldr = self.members.filter(is_active=True).order_by('joined_at').first()
            # Promote to official leader if found
            if ldr:
                ldr.role = GroupMember.Role.LEADER
                ldr.can_submit_order = True
                ldr.save(update_fields=['role', 'can_submit_order'])
        return ldr

    def get_or_create_leader(self, name=None):
        ldr = self.leader
        if not ldr:
            ldr = self.members.create(
                name=name or self.customer_name or 'Leader',
                role=GroupMember.Role.LEADER,
                can_submit_order=True,
                is_active=True
            )
        return ldr


class SessionSeatAllocation(models.Model):
    class AllocationStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        RELEASED = 'RELEASED', 'Released'

    session = models.ForeignKey(CustomerSession, on_delete=models.CASCADE, related_name='seat_allocations')
    seat = models.ForeignKey(RestaurantSeat, on_delete=models.CASCADE, related_name='allocations')
    allocated_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=AllocationStatus.choices, default=AllocationStatus.ACTIVE)
    release_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-allocated_at']

    def __str__(self):
        return f"Table {self.seat.table.table_number} Seat {self.seat.seat_number} -> {self.session.session_name} ({self.get_status_display()})"

    def release(self, reason="Bill paid & session closed"):
        self.status = self.AllocationStatus.RELEASED
        self.released_at = timezone.now()
        self.release_reason = reason
        self.save(update_fields=['status', 'released_at', 'release_reason'])

        if self.seat.status == RestaurantSeat.Status.OCCUPIED:
            self.seat.status = RestaurantSeat.Status.AVAILABLE
            self.seat.save(update_fields=['status', 'updated_at'])


class SeatAuditLog(models.Model):
    seat = models.ForeignKey(RestaurantSeat, on_delete=models.CASCADE, related_name='audit_logs')
    action = models.CharField(max_length=50)
    performed_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    session = models.ForeignKey(CustomerSession, on_delete=models.SET_NULL, null=True, blank=True)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    reason = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} on Table {self.seat.table.table_number} Seat {self.seat.seat_number} at {self.timestamp}"


class GroupMember(models.Model):
    class Role(models.TextChoices):
        LEADER = 'LEADER', 'Group Leader'
        MEMBER = 'MEMBER', 'Member'

    session = models.ForeignKey(CustomerSession, on_delete=models.CASCADE, related_name='members')
    member_token = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=100, default='Guest')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    can_submit_order = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['joined_at']

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) • {self.session.session_name}"

    def save(self, *args, **kwargs):
        if not self.member_token:
            self.member_token = uuid.uuid4().hex
        if self.role == self.Role.LEADER:
            self.can_submit_order = True
        super().save(*args, **kwargs)

    @property
    def is_leader(self):
        if self.role == self.Role.LEADER:
            return True
        # If this member has the same name as the session creator
        if self.session and self.session.customer_name and self.name:
            if self.name.strip().lower() == self.session.customer_name.strip().lower():
                return True
        # If this session has only 1 active member, they are the leader
        if self.session:
            active_m = self.session.members.filter(is_active=True)
            if active_m.count() <= 1:
                return True
            # If no member has role=LEADER, first joined active member is leader
            if not active_m.filter(role=self.Role.LEADER).exists():
                first_m = active_m.order_by('joined_at').first()
                if first_m and first_m.id == self.id:
                    return True
        return False

    @property
    def can_submit(self):
        return self.is_leader or self.can_submit_order


class SessionCart(models.Model):
    session = models.OneToOneField(CustomerSession, on_delete=models.CASCADE, related_name='cart')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart for {self.session}"

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.filter(status='ACTIVE'))

    @property
    def subtotal(self):
        return sum(item.total_price for item in self.items.filter(status='ACTIVE'))

    @property
    def active_items(self):
        return self.items.filter(status='ACTIVE')


class SessionCartItem(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        SUBMITTED = 'SUBMITTED', 'Submitted'

    cart = models.ForeignKey(SessionCart, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, null=True, blank=True)
    combo = models.ForeignKey('menu.FoodCombo', on_delete=models.CASCADE, null=True, blank=True, related_name='cart_items')
    combo_name = models.CharField(max_length=150, blank=True)
    is_combo = models.BooleanField(default=False)
    added_by = models.ForeignKey(GroupMember, on_delete=models.SET_NULL, null=True, blank=True, related_name='cart_items')
    added_by_name = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_snapshot = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    special_notes = models.CharField(max_length=255, blank=True)
    customization_notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['added_at']

    def __str__(self):
        item_name = self.combo_name if self.is_combo else (self.menu_item.name if self.menu_item else 'Unknown Item')
        return f"{self.quantity}x {item_name} (Cart #{self.cart_id})"

    def save(self, *args, **kwargs):
        if not self.unit_price_snapshot or self.unit_price_snapshot == Decimal('0.00'):
            if self.is_combo and self.combo:
                self.unit_price_snapshot = self.combo.price
                if not self.combo_name:
                    self.combo_name = self.combo.name
            elif self.menu_item_id:
                self.unit_price_snapshot = self.menu_item.price
        if self.is_combo and not self.combo_name and self.combo:
            self.combo_name = self.combo.name
        if self.added_by and not self.added_by_name:
            self.added_by_name = self.added_by.name
        super().save(*args, **kwargs)

    @property
    def unit_price(self):
        if self.unit_price_snapshot > Decimal('0.00'):
            return self.unit_price_snapshot
        if self.is_combo and self.combo:
            return self.combo.price
        if self.menu_item:
            return self.menu_item.price
        return Decimal('0.00')

    @property
    def total_price(self):
        return self.quantity * self.unit_price

    @property
    def total_price(self):
        return self.quantity * self.unit_price


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        SEATED = 'SEATED', 'Seated'
        CANCELLED = 'CANCELLED', 'Cancelled'
        COMPLETED = 'COMPLETED', 'Completed'

    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reservations',
        help_text="Restaurant tenant this reservation belongs to"
    )
    table = models.ForeignKey(DiningTable, on_delete=models.CASCADE, related_name='reservations')
    customer_name = models.CharField(max_length=100)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.EmailField(blank=True, null=True)
    guests_count = models.PositiveIntegerField(default=2)
    reservation_date = models.DateField()
    reservation_time = models.TimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    special_requests = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['reservation_date', 'reservation_time']

    def save(self, *args, **kwargs):
        if not self.restaurant and self.table and hasattr(self.table, 'restaurant') and self.table.restaurant:
            self.restaurant = self.table.restaurant
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer_name} - Table {self.table.table_number} ({self.reservation_date} {self.reservation_time})"

    @property
    def badge_class(self):
        badges = {
            self.Status.PENDING: 'warning',
            self.Status.CONFIRMED: 'primary',
            self.Status.SEATED: 'success',
            self.Status.CANCELLED: 'danger',
            self.Status.COMPLETED: 'secondary',
        }
        return badges.get(self.status, 'secondary')


# ============================================================================
# MODULE 1: SMART WAITER ASSISTANCE & TABLE ASSIGNMENTS
# ============================================================================

class TableSection(models.Model):
    """
    Physical or logical dining floor section (e.g., Section A, Main Hall, Patio, VIP).
    Groups tables for structured waiter floor coverage and automatic request routing.
    """
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='table_sections'
    )
    name = models.CharField(max_length=60, help_text="e.g. Section A, Main Hall, Patio, VIP")
    code = models.CharField(max_length=20, blank=True, default='', help_text="e.g. SEC-A")
    description = models.CharField(max_length=255, blank=True, default='')
    tables = models.ManyToManyField(
        DiningTable,
        blank=True,
        related_name='sections'
    )
    assigned_waiters = models.ManyToManyField(
        'accounts.User',
        blank=True,
        related_name='assigned_sections',
        limit_choices_to={'role': 'WAITER'}
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.tables.count()} tables)"


class WaiterTableAssignment(models.Model):
    """
    Assigns a Waiter / Server to specific dining tables or table sections.
    Used for routing service requests and tracking server floor coverage.
    """
    waiter = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='table_assignments',
        limit_choices_to={'role': 'WAITER'}
    )
    table = models.ForeignKey(
        DiningTable,
        on_delete=models.CASCADE,
        related_name='waiter_assignments'
    )
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='waiter_table_assignments'
    )
    section = models.ForeignKey(
        TableSection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='table_assignments'
    )
    section_name = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text="Optional section name, e.g. Main Hall, Patio, First Floor, VIP"
    )
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['table__table_number']
        unique_together = ('waiter', 'table')

    def __str__(self):
        return f"{self.waiter.get_full_name() or self.waiter.username} -> Table {self.table.table_number}"


class ServiceRequest(models.Model):
    """
    Customer-Initiated Smart Waiter Assistance Request.
    Allows dining guests at a table session to request items (Water, Cutlery, Cleaning, etc.)
    Routes directly to assigned on-duty waiters with real-time lifecycle tracking.
    """
    class RequestCategory(models.TextChoices):
        WATER = 'WATER', 'Drinking Water'
        CUTLERY = 'CUTLERY', 'Plates & Cutlery'
        TISSUE_CONDIMENT = 'TISSUE_CONDIMENT', 'Tissues & Condiments'
        TABLE_ASSIST = 'TABLE_ASSIST', 'Table Assistance'
        WAITER_ASSIST = 'WAITER_ASSIST', 'Waiter Assistance'
        BILLING = 'BILLING', 'Billing Assistance'
        OTHER = 'OTHER', 'Other / Custom Assistance'

    class RequestType(models.TextChoices):
        # Water
        WATER = 'WATER', 'Request Water'
        WATER_COLD = 'WATER_COLD', 'Request Cold Water'
        WATER_NORMAL = 'WATER_NORMAL', 'Request Normal Water'
        WATER_BOTTLE = 'WATER_BOTTLE', 'Request Bottled Water'
        # Plates & Cutlery
        PLATES = 'PLATES', 'Extra Plates'
        SPOONS = 'SPOONS', 'Extra Spoons'
        FORKS = 'FORKS', 'Extra Forks'
        KNIVES = 'KNIVES', 'Extra Knives'
        BOWLS = 'BOWLS', 'Extra Bowls'
        CUTLERY = 'CUTLERY', 'Extra Cutlery'
        # Tissues & Condiments
        TISSUE = 'TISSUE', 'Request Tissue'
        KETCHUP = 'KETCHUP', 'Request Ketchup'
        MAYONNAISE = 'MAYONNAISE', 'Request Mayonnaise'
        CHILLI_SAUCE = 'CHILLI_SAUCE', 'Request Chilli Sauce'
        GRAVY = 'GRAVY', 'Request Additional Gravy'
        SAUCE = 'SAUCE', 'Request Ketchup / Sauces'
        SAUCE_OTHER = 'SAUCE_OTHER', 'Request Other Available Sauce'
        # Table Assistance
        CLEANING = 'CLEANING', 'Request Table Cleaning'
        CLEAN_TABLE = 'CLEAN_TABLE', 'Clean Table'
        REMOVE_PLATES = 'REMOVE_PLATES', 'Remove Empty Plates'
        ARRANGE_TABLE = 'ARRANGE_TABLE', 'Arrange Table'
        CLEAN_SPILL = 'CLEAN_SPILL', 'Clean Spilled Food or Water'
        # Waiter Assistance
        WAITER = 'WAITER', 'Call Waiter'
        HELP_MENU = 'HELP_MENU', 'Help With Menu'
        FOOD_INFO = 'FOOD_INFO', 'Food Information'
        CUSTOM_ASSIST = 'CUSTOM_ASSIST', 'Custom Assistance'
        # Billing Assistance
        BILL = 'BILL', 'Request Bill'
        CLARIFY_BILL = 'CLARIFY_BILL', 'Clarify Bill'
        PAYMENT_ASSIST = 'PAYMENT_ASSIST', 'Payment Assistance'
        # Other
        OTHER = 'OTHER', 'Other Assistance'

    class Priority(models.TextChoices):
        NORMAL = 'NORMAL', 'Normal'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'
        UNABLE_TO_FULFIL = 'UNABLE_TO_FULFIL', 'Unable to Fulfil'
        CANCELLED = 'CANCELLED', 'Cancelled'

    request_number = models.CharField(max_length=32, unique=True, editable=False)
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_requests'
    )
    table = models.ForeignKey(
        DiningTable,
        on_delete=models.CASCADE,
        related_name='service_requests'
    )
    session = models.ForeignKey(
        CustomerSession,
        on_delete=models.CASCADE,
        related_name='service_requests'
    )
    member = models.ForeignKey(
        GroupMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_requests'
    )
    customer_name = models.CharField(max_length=100, default='Guest')
    request_category = models.CharField(max_length=30, choices=RequestCategory.choices, default=RequestCategory.WAITER_ASSIST)
    request_type = models.CharField(max_length=40, choices=RequestType.choices, default=RequestType.WAITER)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=30, blank=True, default='')
    custom_message = models.CharField(max_length=255, blank=True, default='')
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.PENDING)
    is_seen = models.BooleanField(default=False, db_index=True, help_text="True if staff has opened and seen this request notification")

    assigned_waiter = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_service_requests'
    )
    accepted_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accepted_service_requests'
    )
    completed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_service_requests'
    )

    requested_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Timestamp when request was dispatched/assigned to the waiter")
    accepted_at = models.DateTimeField(null=True, blank=True)
    in_progress_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True, default='')
    unable_to_fulfil_reason = models.CharField(max_length=255, blank=True, default='')
    alternative_suggestion = models.CharField(max_length=255, blank=True, default='')
    estimated_duration_minutes = models.PositiveIntegerField(default=4)

    # SLA Tracking & Delay Breaches
    is_delayed = models.BooleanField(default=False, db_index=True)
    sla_breached_at = models.DateTimeField(null=True, blank=True)
    table_section = models.ForeignKey(
        TableSection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_requests'
    )

    # Reassignment Audit Fields
    reassigned_from = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reassigned_service_requests'
    )
    reassignment_reason = models.CharField(max_length=255, blank=True, default='')
    reassigned_at = models.DateTimeField(null=True, blank=True)
    reassigned_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_reassignments_made'
    )

    # Customer Feedback & Review
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback_notes = models.TextField(blank=True, default='')
    feedback_at = models.DateTimeField(null=True, blank=True)

    # Customer Re-Notice / Reminder Tracking (After 2 minutes)
    re_notice_count = models.PositiveIntegerField(default=0, help_text="Number of times customer sent a re-notice after 2+ minutes waiting")
    last_renotice_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of most recent customer re-notice")
    staff_apology_message = models.CharField(max_length=255, blank=True, default='', help_text="Apology message sent to customer when staff acknowledges delay notification")
    staff_apology_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when staff sent delay apology message")

    # Report Not Fulfilled / Escalation
    reported_not_fulfilled = models.BooleanField(default=False)
    report_reason = models.CharField(max_length=255, blank=True, default='')
    reported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.request_number} • Table {self.table.table_number} ({self.get_request_type_display()} - {self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.request_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count = ServiceRequest.objects.filter(requested_at__date=timezone.now().date()).count() + 1
            self.request_number = f"SR-{date_str}-{count:04d}"
        if not self.request_category:
            self.request_category = self.infer_category()
        super().save(*args, **kwargs)

    def infer_category(self):
        t = self.request_type
        if t in [self.RequestType.WATER, self.RequestType.WATER_COLD, self.RequestType.WATER_NORMAL, self.RequestType.WATER_BOTTLE]:
            return self.RequestCategory.WATER
        elif t in [self.RequestType.PLATES, self.RequestType.SPOONS, self.RequestType.FORKS, self.RequestType.KNIVES, self.RequestType.BOWLS, self.RequestType.CUTLERY]:
            return self.RequestCategory.CUTLERY
        elif t in [self.RequestType.TISSUE, self.RequestType.KETCHUP, self.RequestType.MAYONNAISE, self.RequestType.CHILLI_SAUCE, self.RequestType.GRAVY, self.RequestType.SAUCE, self.RequestType.SAUCE_OTHER]:
            return self.RequestCategory.TISSUE_CONDIMENT
        elif t in [self.RequestType.CLEANING, self.RequestType.CLEAN_TABLE, self.RequestType.REMOVE_PLATES, self.RequestType.ARRANGE_TABLE, self.RequestType.CLEAN_SPILL]:
            return self.RequestCategory.TABLE_ASSIST
        elif t in [self.RequestType.BILL, self.RequestType.CLARIFY_BILL, self.RequestType.PAYMENT_ASSIST]:
            return self.RequestCategory.BILLING
        elif t in [self.RequestType.WAITER, self.RequestType.HELP_MENU, self.RequestType.FOOD_INFO, self.RequestType.CUSTOM_ASSIST]:
            return self.RequestCategory.WAITER_ASSIST
        return self.RequestCategory.OTHER

    @property
    def badge_class(self):
        badges = {
            self.Status.PENDING: 'warning',
            self.Status.ACCEPTED: 'primary',
            self.Status.IN_PROGRESS: 'info',
            self.Status.COMPLETED: 'success',
            self.Status.UNABLE_TO_FULFIL: 'danger',
            self.Status.CANCELLED: 'secondary',
        }
        return badges.get(self.status, 'secondary')

    @property
    def priority_badge_class(self):
        p_map = {
            self.Priority.NORMAL: 'secondary',
            self.Priority.HIGH: 'warning',
            self.Priority.URGENT: 'danger',
        }
        return p_map.get(self.priority, 'secondary')

    @property
    def estimated_time_display(self):
        t = self.request_type
        if t in [self.RequestType.WATER, self.RequestType.WATER_COLD, self.RequestType.WATER_NORMAL, self.RequestType.WATER_BOTTLE]:
            return "3–5 mins"
        elif t in [self.RequestType.TISSUE]:
            return "2–4 mins"
        elif t in [self.RequestType.PLATES, self.RequestType.SPOONS, self.RequestType.FORKS, self.RequestType.KNIVES, self.RequestType.BOWLS, self.RequestType.CUTLERY]:
            return "3–5 mins"
        elif t in [self.RequestType.CLEANING, self.RequestType.CLEAN_TABLE, self.RequestType.CLEAN_SPILL]:
            return "5–8 mins"
        elif t in [self.RequestType.BILL, self.RequestType.CLARIFY_BILL, self.RequestType.PAYMENT_ASSIST]:
            return "3–6 mins"
        return "3–5 mins"

    @property
    def icon_class(self):
        icons = {
            self.RequestType.WATER: 'bi-droplet-fill',
            self.RequestType.WATER_COLD: 'bi-snow',
            self.RequestType.WATER_NORMAL: 'bi-droplet',
            self.RequestType.WATER_BOTTLE: 'bi-cup-straw',
            self.RequestType.PLATES: 'bi-disc',
            self.RequestType.SPOONS: 'bi-slash-circle',
            self.RequestType.FORKS: 'bi-list',
            self.RequestType.KNIVES: 'bi-dash-lg',
            self.RequestType.BOWLS: 'bi-circle',
            self.RequestType.CUTLERY: 'bi-scissors',
            self.RequestType.TISSUE: 'bi-box',
            self.RequestType.KETCHUP: 'bi-droplet-half',
            self.RequestType.MAYONNAISE: 'bi-egg',
            self.RequestType.CHILLI_SAUCE: 'bi-fire',
            self.RequestType.GRAVY: 'bi-cup-hot-fill',
            self.RequestType.SAUCE: 'bi-egg-fried',
            self.RequestType.SAUCE_OTHER: 'bi-cup-hot',
            self.RequestType.CLEANING: 'bi-brush-fill',
            self.RequestType.CLEAN_TABLE: 'bi-brush',
            self.RequestType.REMOVE_PLATES: 'bi-trash3',
            self.RequestType.ARRANGE_TABLE: 'bi-grid-fill',
            self.RequestType.CLEAN_SPILL: 'bi-water',
            self.RequestType.WAITER: 'bi-person-raised-hand',
            self.RequestType.HELP_MENU: 'bi-book-half',
            self.RequestType.FOOD_INFO: 'bi-info-circle-fill',
            self.RequestType.CUSTOM_ASSIST: 'bi-person-gear',
            self.RequestType.BILL: 'bi-receipt',
            self.RequestType.CLARIFY_BILL: 'bi-question-circle',
            self.RequestType.PAYMENT_ASSIST: 'bi-credit-card-2-front',
            self.RequestType.OTHER: 'bi-chat-dots-fill',
        }
        return icons.get(self.request_type, 'bi-bell-fill')

    @property
    def waiting_duration_seconds(self):
        end_time = self.completed_at or self.cancelled_at or timezone.now()
        return max(0, int((end_time - self.requested_at).total_seconds()))

    @property
    def waiting_duration_display(self):
        sec = self.waiting_duration_seconds
        m = sec // 60
        s = sec % 60
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    @property
    def target_sla_minutes(self):
        """
        Returns configured target SLA minutes from ServiceSLAConfig for this request type & restaurant,
        or falls back to estimated_duration_minutes.
        """
        config = getattr(self, '_cached_sla_config', None)
        if config is None:
            config = ServiceSLAConfig.objects.filter(
                Q(restaurant=self.restaurant) | Q(restaurant__isnull=True),
                request_type=self.request_type,
                is_active=True
            ).order_by('-restaurant_id').first()
            self._cached_sla_config = config
        if config:
            return config.target_minutes
        return self.estimated_duration_minutes or 4

    @property
    def escalation_sla_minutes(self):
        config = getattr(self, '_cached_sla_config', None)
        if config is None:
            config = ServiceSLAConfig.objects.filter(
                Q(restaurant=self.restaurant) | Q(restaurant__isnull=True),
                request_type=self.request_type,
                is_active=True
            ).order_by('-restaurant_id').first()
            self._cached_sla_config = config
        if config:
            return config.escalation_minutes
        return max(5, self.target_sla_minutes + 2)

    @property
    def is_sla_breached(self):
        """
        Checks if the request is unresolved and has exceeded its configured SLA threshold.
        """
        if self.status in [self.Status.COMPLETED, self.Status.CANCELLED, self.Status.UNABLE_TO_FULFIL]:
            return bool(self.is_delayed)
        elapsed_mins = (timezone.now() - self.requested_at).total_seconds() / 60.0
        return elapsed_mins >= self.escalation_sla_minutes

    @property
    def acceptance_duration_seconds(self):
        if self.accepted_at and self.requested_at:
            return max(0, int((self.accepted_at - self.requested_at).total_seconds()))
        return None

    @property
    def acceptance_duration_display(self):
        sec = self.acceptance_duration_seconds
        if sec is None:
            return "-"
        m, s = divmod(sec, 60)
        return f"{m}m {s}s" if m > 0 else f"{s}s"

    @property
    def completion_duration_seconds(self):
        if self.completed_at and self.requested_at:
            return max(0, int((self.completed_at - self.requested_at).total_seconds()))
        return None

    @property
    def completion_duration_display(self):
        sec = self.completion_duration_seconds
        if sec is None:
            return "-"
        m, s = divmod(sec, 60)
        return f"{m}m {s}s" if m > 0 else f"{s}s"

    @property
    def service_duration_seconds(self):
        if self.completed_at and self.in_progress_at:
            return max(0, int((self.completed_at - self.in_progress_at).total_seconds()))
        return None

    @property
    def service_duration_display(self):
        sec = self.service_duration_seconds
        if sec is None:
            return "-"
        m, s = divmod(sec, 60)
        return f"{m}m {s}s" if m > 0 else f"{s}s"


class ServiceRequestAuditLog(models.Model):
    """
    Immutable audit history log of all service request lifecycle events.
    Records every status transition, who performed it, and reason/notes.
    """
    service_request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.CASCADE,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=50)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    performed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    notes = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.service_request.request_number}: {self.action} ({self.previous_status} -> {self.new_status})"


class ServiceSLAConfig(models.Model):
    """
    Configurable SLA targets per service request type and restaurant.
    Defines target fulfillment times, warning thresholds, and escalation limits.
    """
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='service_sla_configs'
    )
    request_type = models.CharField(
        max_length=40,
        choices=ServiceRequest.RequestType.choices
    )
    target_minutes = models.PositiveIntegerField(
        default=4,
        help_text="Expected target completion time in minutes"
    )
    warning_minutes = models.PositiveIntegerField(
        default=3,
        help_text="Warning threshold in minutes before breach"
    )
    escalation_minutes = models.PositiveIntegerField(
        default=5,
        help_text="Minutes after which request is marked delayed/SLA breached"
    )
    acceptance_timeout_seconds = models.PositiveIntegerField(
        default=60,
        help_text="Timeout in seconds before an unaccepted pending request is automatically reassigned to another available waiter."
    )
    priority = models.CharField(
        max_length=20,
        choices=ServiceRequest.Priority.choices,
        default=ServiceRequest.Priority.NORMAL
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('restaurant', 'request_type')
        ordering = ['request_type']

    def __str__(self):
        return f"{self.get_request_type_display()} SLA: {self.target_minutes}m (Warn: {self.warning_minutes}m, Breach: {self.escalation_minutes}m)"


class ServiceRequestEscalation(models.Model):
    """
    Formal Guest Escalation record when customer reports a request problem
    (e.g., 'I Have Not Received This Request').
    Tracks investigation, reassignment to supervisor/waiter, resolution, and guest notifications.
    """
    class EscalationStatus(models.TextChoices):
        NEW = 'NEW', 'New Escalation'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        REASSIGNED = 'REASSIGNED', 'Reassigned to Staff'
        RESOLVED = 'RESOLVED', 'Resolved'
        CLOSED = 'CLOSED', 'Closed'

    escalation_number = models.CharField(max_length=32, unique=True, editable=False)
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='service_escalations'
    )
    service_request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.CASCADE,
        related_name='escalations'
    )
    table = models.ForeignKey(
        DiningTable,
        on_delete=models.CASCADE,
        related_name='service_escalations'
    )
    session = models.ForeignKey(
        CustomerSession,
        on_delete=models.CASCADE,
        related_name='service_escalations'
    )
    customer_name = models.CharField(max_length=100, default='Guest')
    original_waiter = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='original_escalations'
    )
    assigned_staff = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_escalations'
    )
    complaint_notes = models.TextField(help_text="Customer reported issue or reason for escalation")
    status = models.CharField(
        max_length=25,
        choices=EscalationStatus.choices,
        default=EscalationStatus.NEW
    )
    resolution_notes = models.TextField(blank=True, default='')
    resolved_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_escalations'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.escalation_number} - Table {self.table.table_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.escalation_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count = ServiceRequestEscalation.objects.filter(created_at__date=timezone.now().date()).count() + 1
            self.escalation_number = f"ESC-{date_str}-{count:04d}"
        super().save(*args, **kwargs)

    @property
    def badge_class(self):
        badges = {
            self.EscalationStatus.NEW: 'danger',
            self.EscalationStatus.UNDER_REVIEW: 'warning',
            self.EscalationStatus.REASSIGNED: 'info',
            self.EscalationStatus.RESOLVED: 'success',
            self.EscalationStatus.CLOSED: 'secondary',
        }
        return badges.get(self.status, 'secondary')


# ============================================================================
# MODULE 2: LIVE TABLE WAITING TIME & DIGITAL QUEUE MANAGEMENT
# ============================================================================

class TableWaitingQueue(models.Model):
    """
    Digital Waiting List & Live Queue Management for Guests.
    Allows customers to join the waiting queue before being seated,
    tracks estimated wait time, party size, compatible tables, and seating lifecycle.
    """
    class Status(models.TextChoices):
        WAITING = 'WAITING', 'Waiting'
        CALLED = 'CALLED', 'Called / Ready Soon'
        TABLE_ASSIGNED = 'TABLE_ASSIGNED', 'Table Assigned'
        SEATED = 'SEATED', 'Seated'
        CANCELLED = 'CANCELLED', 'Cancelled'
        NO_SHOW = 'NO_SHOW', 'No Show'

    queue_token = models.CharField(max_length=20, unique=True, editable=False)
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='waiting_queues'
    )
    customer_name = models.CharField(max_length=100)
    customer_phone = models.CharField(max_length=20)
    guest_count = models.PositiveIntegerField(default=2)
    special_requirements = models.TextField(blank=True, default='')

    status = models.CharField(max_length=25, choices=Status.choices, default=Status.WAITING)
    estimated_wait_minutes_min = models.PositiveIntegerField(default=15)
    estimated_wait_minutes_max = models.PositiveIntegerField(default=25)

    assigned_table = models.ForeignKey(
        DiningTable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='queue_assignments'
    )
    table_assigned_at = models.DateTimeField(null=True, blank=True)
    table_hold_expires_at = models.DateTimeField(null=True, blank=True)

    called_at = models.DateTimeField(null=True, blank=True)
    called_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='called_queue_entries'
    )

    seated_at = models.DateTimeField(null=True, blank=True)
    seated_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='seated_queue_entries'
    )
    seated_session = models.ForeignKey(
        CustomerSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='queue_entries'
    )

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.queue_token} • {self.customer_name} ({self.guest_count} guests) - {self.get_status_display()}"

    @classmethod
    def generate_next_token(cls, restaurant=None):
        prefix = "Q-"
        today = timezone.now().date()
        qs = cls.objects.filter(created_at__date=today)
        if restaurant:
            qs = qs.filter(restaurant=restaurant)
        count = qs.count() + 1
        return f"{prefix}{count:03d}"

    def save(self, *args, **kwargs):
        if not self.queue_token:
            self.queue_token = self.generate_next_token(self.restaurant)
        super().save(*args, **kwargs)

    @property
    def badge_class(self):
        badges = {
            self.Status.WAITING: 'warning',
            self.Status.CALLED: 'info',
            self.Status.TABLE_ASSIGNED: 'primary',
            self.Status.SEATED: 'success',
            self.Status.CANCELLED: 'secondary',
            self.Status.NO_SHOW: 'danger',
        }
        return badges.get(self.status, 'secondary')

    @property
    def queue_position(self):
        if self.status not in [self.Status.WAITING, self.Status.CALLED]:
            return 0
        qs = TableWaitingQueue.objects.filter(
            status__in=[self.Status.WAITING, self.Status.CALLED],
            created_at__lt=self.created_at
        )
        if self.restaurant:
            qs = qs.filter(restaurant=self.restaurant)
        return qs.count() + 1

    @property
    def wait_time_display(self):
        if self.estimated_wait_minutes_min == self.estimated_wait_minutes_max:
            return f"~{self.estimated_wait_minutes_min} mins"
        return f"{self.estimated_wait_minutes_min}–{self.estimated_wait_minutes_max} mins"
