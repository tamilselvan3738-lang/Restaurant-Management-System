from decimal import Decimal
from django.db import models
from django.utils import timezone
from accounts.models import User, Restaurant
from orders.models import Order

class Invoice(models.Model):
    class Status(models.TextChoices):
        UNPAID = 'UNPAID', 'Unpaid'
        PAID = 'PAID', 'Paid'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        REFUNDED = 'REFUNDED', 'Refunded'
        MERGED = 'MERGED', 'Merged into Consolidated Bill'

    class InvoiceType(models.TextChoices):
        SESSION_BILL = 'SESSION_BILL', 'Table Session Bill'
        SINGLE_ORDER = 'SINGLE_ORDER', 'Single Order POS / Takeaway'

    invoice_number = models.CharField(max_length=30, unique=True, editable=False)
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='invoices',
        help_text="Restaurant tenant this invoice belongs to"
    )
    session = models.ForeignKey('tables.CustomerSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    table = models.ForeignKey('tables.DiningTable', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    consolidated_bill = models.ForeignKey('ConsolidatedBill', on_delete=models.SET_NULL, null=True, blank=True, related_name='original_invoices')
    merged_at = models.DateTimeField(null=True, blank=True)
    merged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='merged_invoices')
    invoice_type = models.CharField(max_length=20, choices=InvoiceType.choices, default=InvoiceType.SESSION_BILL)
    customer_name = models.CharField(max_length=100, default='Guest')
    customer_phone = models.CharField(max_length=20, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.0825'))
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    service_charge_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.05'))
    service_charge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_notes = models.CharField(max_length=150, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPAID)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    refund_status = models.CharField(max_length=20, choices=[
        ('NONE', 'No Refund'),
        ('PENDING', 'Refund Due'),
        ('COMPLETED', 'Refund Completed')
    ], default='NONE')
    split_count = models.PositiveIntegerField(default=1, help_text="Number of people splitting this bill")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count_today = Invoice.objects.filter(created_at__date=timezone.now().date()).count() + 1
            self.invoice_number = f"INV-{date_str}-{count_today:04d}"
        if not self.restaurant:
            if self.order and hasattr(self.order, 'restaurant') and self.order.restaurant:
                self.restaurant = self.order.restaurant
            elif self.table and hasattr(self.table, 'restaurant') and self.table.restaurant:
                self.restaurant = self.table.restaurant
            elif self.session and hasattr(self.session, 'restaurant') and self.session.restaurant:
                self.restaurant = self.session.restaurant
        super().save(*args, **kwargs)

    def __str__(self):
        desc = f"Session {self.session.session_name}" if self.session else (self.order.order_number if self.order else "General")
        return f"{self.invoice_number} - {desc} (${self.total_amount})"

    def recalculate(self, save=True):
        if self.session:
            sub = self.session.subtotal
            self.subtotal = sub
            self.tax_amount = round(sub * self.tax_rate, 2)
            self.service_charge_amount = round(sub * self.service_charge_rate, 2)
            self.total_amount = self.subtotal + self.tax_amount + self.service_charge_amount - self.discount_amount
            self.customer_name = self.session.customer_name
            self.customer_phone = self.session.customer_phone
            self.table = self.session.table
        elif self.order:
            sub = self.order.subtotal
            self.subtotal = sub
            self.tax_amount = round(sub * self.tax_rate, 2)
            self.service_charge_amount = round(sub * self.service_charge_rate, 2)
            self.total_amount = self.subtotal + self.tax_amount + self.service_charge_amount - self.discount_amount
            self.customer_name = self.order.customer_name
            self.customer_phone = self.order.customer_phone
            self.table = self.order.table

        # Check payment & refund status
        if self.total_paid > self.total_amount and self.total_amount >= Decimal('0.00'):
            self.status = self.Status.PAID
            self.refund_amount = self.total_paid - self.total_amount
            if self.refund_status == 'NONE':
                self.refund_status = 'PENDING'
            if not self.paid_at:
                self.paid_at = timezone.now()
        elif self.total_paid >= self.total_amount and self.total_amount > Decimal('0.00'):
            self.status = self.Status.PAID
            if self.refund_status != 'COMPLETED':
                self.refund_amount = Decimal('0.00')
                self.refund_status = 'NONE'
            if not self.paid_at:
                self.paid_at = timezone.now()
        elif self.total_paid > Decimal('0.00'):
            self.status = self.Status.PARTIALLY_PAID
            if self.refund_status != 'COMPLETED':
                self.refund_amount = Decimal('0.00')
                self.refund_status = 'NONE'
        else:
            self.status = self.Status.UNPAID
            if self.refund_status != 'COMPLETED':
                self.refund_amount = Decimal('0.00')
                self.refund_status = 'NONE'

        if save and self.pk:
            self.save(update_fields=[
                'subtotal', 'tax_amount', 'service_charge_amount', 'total_amount',
                'customer_name', 'customer_phone', 'table', 'status', 'paid_at',
                'refund_amount', 'refund_status'
            ])
        return self.total_amount

    @property
    def refund_due(self):
        if self.total_paid > self.total_amount:
            return self.total_paid - self.total_amount
        return self.refund_amount

    @property
    def total_paid(self):
        total = self.payments.filter(status='COMPLETED').aggregate(total=models.Sum('amount'))['total']
        return total or Decimal('0.00')

    @property
    def balance_due(self):
        return max(Decimal('0.00'), self.total_amount - self.total_paid)

    @property
    def split_amount_per_person(self):
        if self.split_count > 1:
            return round(self.total_amount / Decimal(self.split_count), 2)
        return self.total_amount

    @property
    def badge_class(self):
        badges = {
            self.Status.UNPAID: 'danger',
            self.Status.PAID: 'success',
            self.Status.PARTIALLY_PAID: 'warning',
            self.Status.REFUNDED: 'secondary',
            self.Status.MERGED: 'info',
        }
        return badges.get(self.status, 'secondary')


class Payment(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        UPI = 'UPI', 'QR Card'
        CREDIT_CARD = 'CREDIT_CARD', 'Credit Card'
        DEBIT_CARD = 'DEBIT_CARD', 'Debit Card'
        ONLINE = 'ONLINE', 'Online Payment'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='payments',
        help_text="Restaurant tenant this payment belongs to"
    )
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    tendered_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), help_text="Cash given by customer")
    change_returned = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), help_text="Change returned to customer")
    transaction_reference = models.CharField(max_length=100, blank=True, help_text="Card / UPI ref number")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    paid_by_name = models.CharField(max_length=100, blank=True, help_text="Group member or customer name who paid")
    split_type = models.CharField(max_length=30, blank=True, default='FULL', help_text="Payment split type: FULL, MY_ITEMS, EQUAL, CUSTOM")
    allocated_member = models.ForeignKey('tables.GroupMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    notes = models.CharField(max_length=255, blank=True)
    payment_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date']

    def __str__(self):
        return f"Payment of ${self.amount} ({self.get_payment_method_display()}) for {self.invoice.invoice_number}"

    @property
    def receipt_number(self):
        return self.transaction_reference or f"PAY-{self.id}"

    @property
    def created_at(self):
        return self.payment_date






    def save(self, *args, **kwargs):
        if not self.restaurant and self.invoice and hasattr(self.invoice, 'restaurant') and self.invoice.restaurant:
            self.restaurant = self.invoice.restaurant
        super().save(*args, **kwargs)
        # Automatically update parent invoice status
        if self.invoice:
            self.invoice.recalculate(save=True)
            if self.invoice.session and self.invoice.status == Invoice.Status.PAID:
                # Check if all orders in session are completed
                self.invoice.session.orders.filter(status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.READY, Order.Status.SERVED]).update(status=Order.Status.COMPLETED)
            
            # Synchronize linked BillRequest status
            for br in self.invoice.bill_requests.filter(status__in=[
                BillRequest.Status.PENDING,
                BillRequest.Status.ACCEPTED,
                BillRequest.Status.BILL_GENERATED,
                BillRequest.Status.PAYMENT_PENDING,
                BillRequest.Status.PARTIALLY_PAID,
            ]):
                br.paid_amount = self.invoice.total_paid
                br.balance_due = self.invoice.balance_due
                if self.invoice.status == Invoice.Status.PAID:
                    br.status = BillRequest.Status.PAID
                    br.completed_at = timezone.now()
                elif self.invoice.status == Invoice.Status.PARTIALLY_PAID:
                    br.status = BillRequest.Status.PARTIALLY_PAID
                br.save(update_fields=['paid_amount', 'balance_due', 'status', 'completed_at'])


class ConsolidatedBill(models.Model):
    class Status(models.TextChoices):
        UNPAID = 'UNPAID', 'Unpaid'
        PAID = 'PAID', 'Paid'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        VOIDED = 'VOIDED', 'Voided / Unmerged'

    bill_number = models.CharField(max_length=35, unique=True, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, null=True, blank=True, related_name='consolidated_bills')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_consolidated_bills')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    service_charge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPAID)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.bill_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count_today = ConsolidatedBill.objects.filter(created_at__date=timezone.now().date()).count() + 1
            candidate = f"CB-{date_str}-{count_today:04d}"
            while ConsolidatedBill.objects.filter(bill_number=candidate).exists():
                count_today += 1
                candidate = f"CB-{date_str}-{count_today:04d}"
            self.bill_number = candidate
        super().save(*args, **kwargs)

    def recalculate(self, save=True):
        active_invoices = self.original_invoices.all()
        sub = sum(inv.subtotal for inv in active_invoices)
        tax = sum(inv.tax_amount for inv in active_invoices)
        service = sum(inv.service_charge_amount for inv in active_invoices)
        discount = sum(inv.discount_amount for inv in active_invoices) + self.discount_amount
        self.subtotal = sub
        self.tax_amount = tax
        self.service_charge_amount = service
        self.total_amount = max(Decimal('0.00'), sub + tax + service - discount)

        if self.status != self.Status.VOIDED:
            if self.total_paid >= self.total_amount and self.total_amount > Decimal('0.00'):
                self.status = self.Status.PAID
                if not self.paid_at:
                    self.paid_at = timezone.now()
            elif self.total_paid > Decimal('0.00'):
                self.status = self.Status.PARTIALLY_PAID
            else:
                self.status = self.Status.UNPAID

        if save and self.pk:
            self.save(update_fields=['subtotal', 'tax_amount', 'service_charge_amount', 'total_amount', 'status', 'paid_at'])
        return self.total_amount

    @property
    def total_paid(self):
        total = self.payments.filter(status='COMPLETED').aggregate(total=models.Sum('amount'))['total']
        return total or Decimal('0.00')

    @property
    def balance_due(self):
        return max(Decimal('0.00'), self.total_amount - self.total_paid)

    @property
    def included_tables(self):
        tables = []
        for inv in self.original_invoices.all():
            if inv.table and inv.table.table_number not in tables:
                tables.append(inv.table.table_number)
        return tables

    @property
    def table_numbers_display(self):
        tables = self.included_tables
        return ", ".join(f"Table {t}" for t in tables) if tables else "Walk-in"

    @property
    def included_sessions(self):
        sessions = []
        for inv in self.original_invoices.all():
            name = inv.session.session_name if inv.session else (inv.customer_name or "Guest")
            if name not in sessions:
                sessions.append(name)
        return sessions

    @property
    def customer_names_display(self):
        sessions = self.included_sessions
        return ", ".join(sessions) if sessions else "Multiple Guests"

    @property
    def merged_by(self):
        return self.created_by

    @property
    def badge_class(self):
        badges = {
            self.Status.UNPAID: 'danger',
            self.Status.PAID: 'success',
            self.Status.PARTIALLY_PAID: 'warning',
            self.Status.VOIDED: 'secondary',
        }
        return badges.get(self.status, 'secondary')

    def __str__(self):
        return f"{self.bill_number} - {len(self.included_tables)} Table(s) (₹{self.total_amount})"


class ConsolidatedBillItem(models.Model):
    consolidated_bill = models.ForeignKey(ConsolidatedBill, on_delete=models.CASCADE, related_name='bill_items')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='consolidated_links')
    original_amount = models.DecimalField(max_digits=10, decimal_places=2)
    outstanding_amount = models.DecimalField(max_digits=10, decimal_places=2)
    allocated_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.consolidated_bill.bill_number} -> {self.invoice.invoice_number} (₹{self.original_amount})"


class ConsolidatedPayment(models.Model):
    consolidated_bill = models.ForeignKey(ConsolidatedBill, on_delete=models.CASCADE, related_name='payments')
    payment_method = models.CharField(max_length=20, choices=Payment.PaymentMethod.choices, default=Payment.PaymentMethod.CASH)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    tendered_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), help_text="Cash given by customer")
    change_returned = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), help_text="Change returned to customer")
    transaction_reference = models.CharField(max_length=100, blank=True, help_text="Card / UPI ref number")
    status = models.CharField(max_length=20, choices=Payment.Status.choices, default=Payment.Status.COMPLETED)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    paid_by_name = models.CharField(max_length=100, blank=True, help_text="Group member or customer name who paid")
    split_type = models.CharField(max_length=30, blank=True, default='FULL', help_text="Payment split type: FULL, MY_ITEMS, EQUAL, CUSTOM")
    allocated_member = models.ForeignKey('tables.GroupMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='consolidated_payments')
    notes = models.CharField(max_length=255, blank=True)
    payment_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date']

    def __str__(self):
        return f"Payment of ₹{self.amount} ({self.get_payment_method_display()}) for {self.consolidated_bill.bill_number}"

    @property
    def receipt_number(self):
        return self.transaction_reference or f"PAY-{self.id}"

    @property
    def created_at(self):
        return self.payment_date


class BillRequest(models.Model):
    """
    Customer-Initiated Bill Request for Admin & Cashier.
    Allows customers to request their bill from their mobile device.
    Staff can view, accept, generate bill, and track payment status without confusing
    multiple customer groups sharing the same table.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        BILL_GENERATED = 'BILL_GENERATED', 'Bill Generated'
        PAYMENT_PENDING = 'PAYMENT_PENDING', 'Payment Pending'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        PAID = 'PAID', 'Paid'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    request_number = models.CharField(max_length=32, unique=True, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.SET_NULL, null=True, blank=True, related_name='bill_requests')
    table = models.ForeignKey('tables.DiningTable', on_delete=models.CASCADE, related_name='bill_requests')
    session = models.ForeignKey('tables.CustomerSession', on_delete=models.CASCADE, related_name='bill_requests')
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='bill_requests')

    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    balance_due = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    status = models.CharField(max_length=25, choices=Status.choices, default=Status.PENDING)

    requested_at = models.DateTimeField(auto_now_add=True)
    accepted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='accepted_bill_requests')
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    cancellation_reason = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-requested_at']
        verbose_name = 'Customer Bill Request'
        verbose_name_plural = 'Customer Bill Requests'

    def __str__(self):
        return f"{self.request_number} - Table {self.table.table_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.request_number:
            date_str = timezone.now().strftime('%Y%m%d')
            prefix = f"REQ-{date_str}"
            last_req = BillRequest.objects.filter(request_number__startswith=prefix).order_by('-request_number').first()
            if last_req and len(last_req.request_number.split('-')) == 3:
                try:
                    last_num = int(last_req.request_number.split('-')[-1])
                    seq = last_num + 1
                except ValueError:
                    seq = BillRequest.objects.filter(request_number__startswith=prefix).count() + 1
            else:
                seq = BillRequest.objects.filter(request_number__startswith=prefix).count() + 1
            self.request_number = f"{prefix}-{seq:04d}"
        super().save(*args, **kwargs)

    @property
    def badge_class(self):
        mapping = {
            self.Status.PENDING: 'warning',
            self.Status.ACCEPTED: 'primary',
            self.Status.BILL_GENERATED: 'info',
            self.Status.PAYMENT_PENDING: 'danger',
            self.Status.PARTIALLY_PAID: 'secondary',
            self.Status.PAID: 'success',
            self.Status.COMPLETED: 'success',
            self.Status.CANCELLED: 'dark',
        }
        return mapping.get(self.status, 'secondary')

    @property
    def is_active(self):
        return self.status in [
            self.Status.PENDING,
            self.Status.ACCEPTED,
            self.Status.BILL_GENERATED,
            self.Status.PAYMENT_PENDING,
            self.Status.PARTIALLY_PAID,
        ]

    def items_summary(self):
        """Returns list of item summary strings for this customer session."""
        items = []
        if self.session:
            for ord_obj in self.session.orders.exclude(status='CANCELLED'):
                for it in ord_obj.items.exclude(status='CANCELLED'):
                    items.append(f"{it.quantity}x {it.menu_item.name}")
        return items

    def orders_list(self):
        if self.session:
            return list(self.session.orders.exclude(status='CANCELLED'))
        return []






