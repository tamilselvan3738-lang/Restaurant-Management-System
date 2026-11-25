from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from billing.models import Invoice, ConsolidatedBill, ConsolidatedPayment
from tables.models import DiningTable, CustomerSession, RestaurantSeat, SessionSeatAllocation
from orders.models import Order, OrderItem
from menu.models import Category, MenuItem


class MergeBillsTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Restaurants
        self.restaurant_a = Restaurant.objects.create(name="Restaurant A", code="REST-A")
        self.restaurant_b = Restaurant.objects.create(name="Restaurant B", code="REST-B")

        # Admin User for Restaurant A
        self.admin = User.objects.create_user(
            username='admin_a',
            email='admin_a@rest.com',
            password='password123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant_a
        )
        self.client.force_login(self.admin)

        # Menu Category & Dishes
        self.category = Category.objects.create(name="Main Course", slug="mains")
        self.biryani = MenuItem.objects.create(category=self.category, name="Chicken Biryani", price=Decimal('240.00'))
        self.juice = MenuItem.objects.create(category=self.category, name="Fresh Lime Juice", price=Decimal('60.00'))
        self.paneer = MenuItem.objects.create(category=self.category, name="Paneer Butter Masala", price=Decimal('220.00'))
        self.naan = MenuItem.objects.create(category=self.category, name="Butter Naan", price=Decimal('50.00'))
        self.rice = MenuItem.objects.create(category=self.category, name="Veg Fried Rice", price=Decimal('180.00'))
        self.icecream = MenuItem.objects.create(category=self.category, name="Ice Cream", price=Decimal('80.00'))

        # Tables
        self.table_1 = DiningTable.objects.create(table_number='T01', capacity=4, restaurant=self.restaurant_a)
        self.table_2 = DiningTable.objects.create(table_number='T02', capacity=4, restaurant=self.restaurant_a)
        self.table_3 = DiningTable.objects.create(table_number='T03', capacity=4, restaurant=self.restaurant_a)

        # Customer Sessions & Seats
        self.session_a = CustomerSession.objects.create(table=self.table_1, restaurant=self.restaurant_a, session_name="Group A", customer_name="Alice", guest_count=2)
        self.session_b = CustomerSession.objects.create(table=self.table_2, restaurant=self.restaurant_a, session_name="Group B", customer_name="Bob", guest_count=2)
        self.session_c = CustomerSession.objects.create(table=self.table_3, restaurant=self.restaurant_a, session_name="Group C", customer_name="Charlie", guest_count=2)

        # Orders for each group
        self.order_a = Order.objects.create(table=self.table_1, session=self.session_a, restaurant=self.restaurant_a, order_type=Order.OrderType.DINE_IN)
        OrderItem.objects.create(order=self.order_a, menu_item=self.biryani, quantity=1, unit_price=Decimal('240.00'))
        OrderItem.objects.create(order=self.order_a, menu_item=self.juice, quantity=1, unit_price=Decimal('60.00'))
        self.order_a.calculate_totals(save=True)

        self.order_b = Order.objects.create(table=self.table_2, session=self.session_b, restaurant=self.restaurant_a, order_type=Order.OrderType.DINE_IN)
        OrderItem.objects.create(order=self.order_b, menu_item=self.paneer, quantity=1, unit_price=Decimal('220.00'))
        OrderItem.objects.create(order=self.order_b, menu_item=self.naan, quantity=1, unit_price=Decimal('50.00'))
        self.order_b.calculate_totals(save=True)

        self.order_c = Order.objects.create(table=self.table_3, session=self.session_c, restaurant=self.restaurant_a, order_type=Order.OrderType.DINE_IN)
        OrderItem.objects.create(order=self.order_c, menu_item=self.rice, quantity=1, unit_price=Decimal('180.00'))
        OrderItem.objects.create(order=self.order_c, menu_item=self.icecream, quantity=1, unit_price=Decimal('80.00'))
        self.order_c.calculate_totals(save=True)

        # Invoices
        self.invoice_a = Invoice.objects.create(table=self.table_1, session=self.session_a, order=self.order_a, restaurant=self.restaurant_a, invoice_type=Invoice.InvoiceType.SESSION_BILL)
        self.invoice_a.recalculate(save=True)

        self.invoice_b = Invoice.objects.create(table=self.table_2, session=self.session_b, order=self.order_b, restaurant=self.restaurant_a, invoice_type=Invoice.InvoiceType.SESSION_BILL)
        self.invoice_b.recalculate(save=True)

        self.invoice_c = Invoice.objects.create(table=self.table_3, session=self.session_c, order=self.order_c, restaurant=self.restaurant_a, invoice_type=Invoice.InvoiceType.SESSION_BILL)
        self.invoice_c.recalculate(save=True)

    def test_merge_two_bills_preview_and_confirm(self):
        """Test merging two unpaid customer bills generates unique CB- bill and preserves sessions."""
        # Step 2: Post selected invoices
        resp = self.client.post(reverse('billing:merge_bills_select'), {
            'selected_invoices': [self.invoice_a.id, self.invoice_b.id]
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('billing:merge_bills_preview'), resp.url)

        # Step 3: Check Preview page
        preview_resp = self.client.get(reverse('billing:merge_bills_preview'))
        self.assertEqual(preview_resp.status_code, 200)
        self.assertContains(preview_resp, "Group A")
        self.assertContains(preview_resp, "Group B")
        self.assertContains(preview_resp, "Chicken Biryani")
        self.assertContains(preview_resp, "Paneer Butter Masala")

        # Step 4: Confirm Merge
        confirm_resp = self.client.post(reverse('billing:merge_bills_confirm'))
        self.assertEqual(confirm_resp.status_code, 302)

        # Verify Consolidated Bill created
        cb = ConsolidatedBill.objects.first()
        self.assertIsNotNone(cb)
        self.assertTrue(cb.bill_number.startswith('CB-'))
        self.assertEqual(cb.original_invoices.count(), 2)

        # Original invoices marked as MERGED
        self.invoice_a.refresh_from_db()
        self.invoice_b.refresh_from_db()
        self.assertEqual(self.invoice_a.status, Invoice.Status.MERGED)
        self.assertEqual(self.invoice_b.status, Invoice.Status.MERGED)
        self.assertEqual(self.invoice_a.consolidated_bill, cb)
        self.assertEqual(self.invoice_b.consolidated_bill, cb)

        # Customer sessions and table assignments preserved untouched
        self.assertEqual(self.session_a.table.table_number, 'T01')
        self.assertEqual(self.session_b.table.table_number, 'T02')
        self.assertEqual(self.session_a.customer_name, "Alice")
        self.assertEqual(self.session_b.customer_name, "Bob")

    def test_merge_three_customer_bills(self):
        """Test merging 3 customer groups (Group A T01, Group B T02, Group C T03)."""
        session = self.client.session
        session['merge_invoice_ids'] = [self.invoice_a.id, self.invoice_b.id, self.invoice_c.id]
        session.save()

        confirm_resp = self.client.post(reverse('billing:merge_bills_confirm'))
        self.assertEqual(confirm_resp.status_code, 302)

        cb = ConsolidatedBill.objects.first()
        self.assertEqual(cb.original_invoices.count(), 3)
        self.assertIn('T01', cb.table_numbers_display)
        self.assertIn('T02', cb.table_numbers_display)
        self.assertIn('T03', cb.table_numbers_display)

    def test_reject_merge_fewer_than_two_bills(self):
        """Reject selecting fewer than 2 customer bills."""
        resp = self.client.post(reverse('billing:merge_bills_select'), {
            'selected_invoices': [self.invoice_a.id]
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ConsolidatedBill.objects.count(), 0)

    def test_cross_restaurant_merge_prevention(self):
        """Prevent merging bills belonging to different restaurants."""
        # Create an invoice belonging to Restaurant B
        table_b = DiningTable.objects.create(table_number='TB1', capacity=4)
        server_b = User.objects.create_user(username='waiter_b', email='wb@b.com', password='p', role=User.Role.WAITER, restaurant=self.restaurant_b)
        order_b = Order.objects.create(table=table_b, server=server_b)
        inv_other = Invoice.objects.create(table=table_b, order=order_b)

        # Try to preview with bills from both restaurants
        session = self.client.session
        session['merge_invoice_ids'] = [self.invoice_a.id, inv_other.id]
        session.save()

        resp = self.client.get(reverse('billing:merge_bills_preview'))
        self.assertEqual(resp.status_code, 302)
        # Should redirect back with error message
        self.assertEqual(ConsolidatedBill.objects.count(), 0)

    def test_prevent_already_merged_bill_from_second_merge(self):
        """Prevent merging a bill that is already linked to a consolidated bill."""
        cb = ConsolidatedBill.objects.create(
            restaurant=self.restaurant_a,
            subtotal=Decimal('500.00'),
            total_amount=Decimal('550.00'),
            created_by=self.admin
        )
        self.invoice_a.consolidated_bill = cb
        self.invoice_a.status = Invoice.Status.MERGED
        self.invoice_a.save()

        session = self.client.session
        session['merge_invoice_ids'] = [self.invoice_a.id, self.invoice_b.id]
        session.save()

        resp = self.client.get(reverse('billing:merge_bills_preview'))
        self.assertEqual(resp.status_code, 302)

    def test_consolidated_payment_collection_and_allocation(self):
        """Test single consolidated payment settles the merged bill and allocates to original invoices."""
        # Merge A and B
        session = self.client.session
        session['merge_invoice_ids'] = [self.invoice_a.id, self.invoice_b.id]
        session.save()
        self.client.post(reverse('billing:merge_bills_confirm'))

        cb = ConsolidatedBill.objects.first()
        initial_balance = cb.balance_due
        self.assertGreater(initial_balance, Decimal('0.00'))

        # Pay in full via CASH
        pay_resp = self.client.post(reverse('billing:consolidated_pay', args=[cb.id]), {
            'amount': str(initial_balance),
            'payment_method': 'CASH',
            'tendered_amount': str(initial_balance),
            'change_amount': '0.00',
            'transaction_id': 'TXN-CONS-001',
            'notes': 'Group paid by Alice'
        })
        self.assertEqual(pay_resp.status_code, 302)

        cb.refresh_from_db()
        self.assertEqual(cb.status, ConsolidatedBill.Status.PAID)
        self.assertEqual(cb.balance_due, Decimal('0.00'))

        # Check original invoices are marked PAID
        self.invoice_a.refresh_from_db()
        self.invoice_b.refresh_from_db()
        self.assertEqual(self.invoice_a.status, Invoice.Status.PAID)
        self.assertEqual(self.invoice_b.status, Invoice.Status.PAID)

    def test_void_consolidated_bill_restores_invoices(self):
        """Test voiding an unpaid consolidated bill restores original invoices to independent unpaid status."""
        session = self.client.session
        session['merge_invoice_ids'] = [self.invoice_a.id, self.invoice_b.id]
        session.save()
        self.client.post(reverse('billing:merge_bills_confirm'))

        cb = ConsolidatedBill.objects.first()
        void_resp = self.client.post(reverse('billing:consolidated_void', args=[cb.id]))
        self.assertEqual(void_resp.status_code, 302)

        cb.refresh_from_db()
        self.assertEqual(cb.status, ConsolidatedBill.Status.VOIDED)

        # Original invoices restored
        self.invoice_a.refresh_from_db()
        self.invoice_b.refresh_from_db()
        self.assertIsNone(self.invoice_a.consolidated_bill)
        self.assertIsNone(self.invoice_b.consolidated_bill)
        self.assertEqual(self.invoice_a.status, Invoice.Status.UNPAID)
        self.assertEqual(self.invoice_b.status, Invoice.Status.UNPAID)
