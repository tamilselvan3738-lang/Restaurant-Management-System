from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from billing.models import Invoice, Payment, BillRequest
from tables.models import DiningTable, CustomerSession

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


class BillRequestAcceptAndLookupTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Grand Bistro', code='GB01')
        self.admin = User.objects.create_user(
            username='bistro_admin',
            email='admin@grandbistro.com',
            password='testpassword123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )
        self.client.force_login(self.admin)

        self.table = DiningTable.objects.create(table_number='Table 1', capacity=4, restaurant=self.restaurant)
        self.session = CustomerSession.objects.create(
            table=self.table,
            session_name='Family Dinner',
            customer_name='Dhanusha',
            restaurant=self.restaurant,
            status=CustomerSession.Status.BILL_REQUESTED
        )
        self.invoice = Invoice.objects.create(
            restaurant=self.restaurant,
            table=self.table,
            session=self.session,
            subtotal=Decimal('250.00'),
            tax_amount=Decimal('12.50'),
            total_amount=Decimal('262.50')
        )

    def test_bill_request_accept_with_orphan_null_restaurant(self):
        """
        Verify that accepting a BillRequest with restaurant=None (orphan) does NOT crash with 404,
        auto-heals the restaurant foreign key, and marks status as ACCEPTED.
        """
        orphan_req = BillRequest.objects.create(
            restaurant=None,  # Exact bug scenario from Render
            table=self.table,
            session=self.session,
            invoice=self.invoice,
            total_amount=Decimal('262.50'),
            paid_amount=Decimal('0.00'),
            balance_due=Decimal('262.50'),
            status=BillRequest.Status.PENDING
        )

        resp = self.client.post(
            reverse('billing:bill_request_accept', args=[orphan_req.pk]),
            follow=False
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('billing:bill_request_detail', args=[orphan_req.pk]), resp.url)

        orphan_req.refresh_from_db()
        self.assertEqual(orphan_req.status, BillRequest.Status.ACCEPTED)
        self.assertEqual(orphan_req.accepted_by, self.admin)
        self.assertEqual(orphan_req.restaurant, self.restaurant)  # Auto-healed

    def test_bill_request_accept_nonexistent_pk_graceful_redirect(self):
        """
        Verify that POSTing to accept a non-existent BillRequest pk does NOT raise an unhandled 404,
        and redirects to the bill request list with a warning message.
        """
        resp = self.client.post(
            reverse('billing:bill_request_accept', args=[99999]),
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'billing/bill_request_list.html')
        messages = list(resp.context['messages'])
        self.assertTrue(any("was not found" in str(m) for m in messages))

    def test_bill_request_detail_with_orphan_null_restaurant(self):
        """
        Verify that viewing detail of an orphan BillRequest auto-heals and renders successfully.
        """
        orphan_req = BillRequest.objects.create(
            restaurant=None,
            table=self.table,
            session=self.session,
            invoice=self.invoice,
            total_amount=Decimal('262.50'),
            paid_amount=Decimal('0.00'),
            balance_due=Decimal('262.50'),
            status=BillRequest.Status.PENDING
        )

        resp = self.client.get(reverse('billing:bill_request_detail', args=[orphan_req.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'billing/bill_request_detail.html')

        orphan_req.refresh_from_db()
        self.assertEqual(orphan_req.restaurant, self.restaurant)  # Auto-healed

    def test_bill_request_generate_bill_with_orphan_null_restaurant(self):
        """
        Verify that POSTing to generate bill on an orphan BillRequest (restaurant=None)
        does NOT crash with 404, auto-heals, updates status to BILL_GENERATED, and redirects to invoice detail.
        """
        orphan_req = BillRequest.objects.create(
            restaurant=None,
            table=self.table,
            session=self.session,
            invoice=None,
            total_amount=Decimal('262.50'),
            paid_amount=Decimal('0.00'),
            balance_due=Decimal('262.50'),
            status=BillRequest.Status.ACCEPTED
        )

        resp = self.client.post(
            reverse('billing:bill_request_generate_bill', args=[orphan_req.pk]),
            follow=False
        )
        self.assertEqual(resp.status_code, 302)
        orphan_req.refresh_from_db()
        self.assertEqual(orphan_req.status, BillRequest.Status.BILL_GENERATED)
        self.assertIsNotNone(orphan_req.invoice)
        self.assertEqual(orphan_req.restaurant, self.restaurant)
        self.assertIn(reverse('billing:invoice_detail', args=[orphan_req.invoice.id]), resp.url)

    def test_bill_request_generate_bill_nonexistent_pk_graceful_redirect(self):
        """
        Verify that POSTing to generate bill for non-existent pk does NOT raise an unhandled 404,
        and gracefully redirects to bill request list with a warning.
        """
        resp = self.client.post(
            reverse('billing:bill_request_generate_bill', args=[99999]),
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'billing/bill_request_list.html')
        messages = list(resp.context['messages'])
        self.assertTrue(any("was not found" in str(m) for m in messages))

