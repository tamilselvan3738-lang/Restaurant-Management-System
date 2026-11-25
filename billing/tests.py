from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from billing.models import Invoice, Payment
from tables.models import DiningTable

class BillingPaymentMethodTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Test Resto', code='TR01')
        self.admin = User.objects.create_user(
            username='cashier_admin',
            email='cashier@restaurant.com',
            password='testpassword123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )
        self.client.force_login(self.admin)

        self.table = DiningTable.objects.create(table_number='T-10', capacity=4, restaurant=self.restaurant)
        self.invoice = Invoice.objects.create(
            restaurant=self.restaurant,
            table=self.table,
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('8.25'),
            service_charge_amount=Decimal('5.00'),
            total_amount=Decimal('113.25'),
            invoice_type=Invoice.InvoiceType.SESSION_BILL
        )

    def test_payment_method_dropdown_has_only_cash_and_qr_card(self):
        """Verify that payment modal dropdown contains ONLY Cash and QR Card options."""
        response = self.client.get(reverse('billing:invoice_detail', args=[self.invoice.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Must have Cash and QR Card
        self.assertIn('<option value="CASH">Cash</option>', content)
        self.assertIn('<option value="UPI">QR Card</option>', content)

        # Must NOT have Credit Card, Debit Card, or Online Portal
        self.assertNotIn('<option value="CREDIT_CARD">', content)
        self.assertNotIn('<option value="DEBIT_CARD">', content)
        self.assertNotIn('<option value="ONLINE">', content)

    def test_settle_invoice_with_qr_card(self):
        """Verify recording settlement using QR Card."""
        resp = self.client.post(
            reverse('billing:process_payment', args=[self.invoice.id]),
            data={
                'payment_method': 'UPI',
                'amount': '113.25',
                'tendered_amount': '113.25',
                'transaction_reference': 'UPI-REF-987654'
            }
        )
        self.assertEqual(resp.status_code, 302)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.invoice.balance_due, Decimal('0.00'))

        payment = self.invoice.payments.first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.payment_method, Payment.PaymentMethod.UPI)
        self.assertEqual(payment.get_payment_method_display(), 'QR Card')
        self.assertEqual(payment.transaction_reference, 'UPI-REF-987654')

    def test_settle_invoice_with_cash(self):
        """Verify recording settlement using Cash with change return."""
        resp = self.client.post(
            reverse('billing:process_payment', args=[self.invoice.id]),
            data={
                'payment_method': 'CASH',
                'amount': '113.25',
                'tendered_amount': '120.00',
            }
        )
        self.assertEqual(resp.status_code, 302)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        payment = self.invoice.payments.first()
        self.assertEqual(payment.payment_method, Payment.PaymentMethod.CASH)
        self.assertEqual(payment.change_returned, Decimal('6.75'))

    def test_settle_invoice_auto_close_popup_and_order_management_redirect(self):
        """
        Verify that after full payment settlement:
        1. User is redirected to invoice detail with ?paid_success=1
        2. Pop notice message is present
        3. Countdown redirect banner targeting Order Management (/orders/) is rendered
        """
        resp = self.client.post(
            reverse('billing:process_payment', args=[self.invoice.id]),
            data={
                'payment_method': 'UPI',
                'amount': '113.25',
                'tendered_amount': '113.25',
                'transaction_reference': 'UPI-REF-112233'
            },
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['paid_success'])

        content = resp.content.decode('utf-8')
        # Pop notice message
        self.assertIn("Full payment of ₹113.25 received!", content)
        # Auto-redirect banner and target URL
        self.assertIn("redirectNoticeBanner", content)
        self.assertIn("Order Management", content)
        self.assertIn(reverse('orders:list'), content)
        self.assertIn("redirectCountdown", content)
        # JavaScript timers: 2000ms for pop notice, 5000ms for redirect
        self.assertIn("2000", content)
        self.assertIn("secondsRemaining", content)
