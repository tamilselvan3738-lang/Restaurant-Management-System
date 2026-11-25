from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import User, Restaurant
from tables.models import DiningTable
from menu.models import Category, MenuItem
from orders.models import Order, OrderItem, ExtraFoodOrderLog

class OrderFoodPicDisplayTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Pizza Place', code='PP01')
        self.user = User.objects.create_user(
            username='staff_user',
            email='staff@test.com',
            password='testpassword123',
            role=User.Role.WAITER,
            restaurant=self.restaurant
        )
        self.category = Category.objects.create(name='Pizzas', slug='pizzas', is_active=True, restaurant=self.restaurant)
        small_gif = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff'
            b'\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
            b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        )
        self.image = SimpleUploadedFile("pizza.gif", small_gif, content_type="image/gif")
        self.menu_item = MenuItem.objects.create(
            category=self.category,
            name='Classic Margherita D.O.P.',
            price=16.50,
            image=self.image,
            is_available=True,
            restaurant=self.restaurant
        )
        self.table = DiningTable.objects.create(table_number='T-01', capacity=4, restaurant=self.restaurant)
        self.order = Order.objects.create(
            order_number='ORD-TEST-001',
            order_type=Order.OrderType.DINE_IN,
            table=self.table,
            customer_name='Sophia Loren',
            server=self.user,
            status=Order.Status.ACTIVE,
            restaurant=self.restaurant
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            menu_item=self.menu_item,
            quantity=1,
            unit_price=16.50
        )

    def test_order_card_displays_food_pic_and_no_cooking_badges(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('orders:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.menu_item.image.url)
        self.assertContains(response, 'Classic Margherita D.O.P.')
        # Ensure intermediate kitchen badges are NOT present on card
        self.assertNotContains(response, 'Order Placed')
        self.assertNotContains(response, 'Cooking in Kitchen')
        self.assertNotContains(response, 'Ready to Serve')

        # Ensure POS button and Card/Table view switcher are removed
        self.assertNotContains(response, 'New Order (POS)')
        self.assertNotContains(response, 'btnOrderCards')
        self.assertNotContains(response, 'btnOrderTable')
        self.assertNotContains(response, 'ordersTableContainer')

    def test_order_detail_displays_food_pic(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('orders:detail', args=[self.order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.menu_item.image.url)


class CustomerShopAndCancellationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Curry House', code='CH01')
        self.admin = User.objects.create_user(
            username='admin_boss',
            email='admin@test.com',
            password='testpassword123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )
        self.staff = User.objects.create_user(
            username='kitchen_cook',
            email='cook@test.com',
            password='testpassword123',
            role=User.Role.KITCHEN,
            restaurant=self.restaurant
        )
        self.category = Category.objects.create(name='Main Course', slug='main-course', is_active=True, restaurant=self.restaurant)
        self.item1 = MenuItem.objects.create(
            category=self.category,
            name='Paneer Butter Masala',
            price=15.00,
            is_available=True,
            restaurant=self.restaurant
        )
        self.item2 = MenuItem.objects.create(
            category=self.category,
            name='Garlic Naan',
            price=5.00,
            is_available=True,
            restaurant=self.restaurant
        )
        # Create table with 4 seats
        self.table = DiningTable.objects.create(table_number='T-10', capacity=4, restaurant=self.restaurant)
        self.table.sync_seats()

        # Group A
        from tables.models import CustomerSession, RestaurantSeat, SessionSeatAllocation
        from billing.models import Invoice
        self.session_a = CustomerSession.objects.create(
            table=self.table,
            restaurant=self.restaurant,
            session_name='Group A',
            customer_name='Alice',
            guest_count=2,
            status=CustomerSession.Status.ACTIVE
        )
        # Allocate 2 seats for Group A
        seats = list(self.table.seats.filter(status=RestaurantSeat.Status.AVAILABLE).order_by('seat_number')[:2])
        for s in seats:
            s.status = RestaurantSeat.Status.OCCUPIED
            s.save()
            SessionSeatAllocation.objects.create(session=self.session_a, seat=s)
        self.table.sync_status()

        # Order for Group A
        self.order_a = Order.objects.create(
            order_type=Order.OrderType.DINE_IN,
            table=self.table,
            session=self.session_a,
            customer_name='Alice',
            status=Order.Status.PENDING,
            restaurant=self.restaurant
        )
        self.order_item_a1 = OrderItem.objects.create(
            order=self.order_a,
            menu_item=self.item1,
            quantity=1,
            unit_price=15.00,
            status=OrderItem.ItemStatus.PENDING
        )
        self.order_item_a2 = OrderItem.objects.create(
            order=self.order_a,
            menu_item=self.item2,
            quantity=2,
            unit_price=5.00,
            status=OrderItem.ItemStatus.PENDING
        )
        self.order_a.calculate_totals()
        self.invoice_a = Invoice.objects.create(
            session=self.session_a,
            table=self.table,
            restaurant=self.restaurant,
            invoice_type=Invoice.InvoiceType.SESSION_BILL,
            customer_name='Alice'
        )
        self.invoice_a.recalculate()

    def test_cancel_individual_pending_item_removes_charge_from_bill(self):
        """Cancelling a pending item immediately removes its charge from order and session invoice."""
        self.client.cookies[f'rms_session_{self.table.id}'] = self.session_a.session_token

        initial_subtotal = self.order_a.subtotal  # 15 + 10 = 25.00
        self.assertEqual(initial_subtotal, 25.00)

        # Cancel item 2 (Garlic Naan: 2x 5.00 = 10.00)
        url = reverse('tables:customer_cancel_item', kwargs={
            'table_number': self.table.table_number,
            'order_id': self.order_a.id,
            'item_id': self.order_item_a2.id
        })
        response = self.client.post(url, {'reason': 'Ordered too much'})
        self.assertEqual(response.status_code, 302)

        self.order_item_a2.refresh_from_db()
        self.assertEqual(self.order_item_a2.status, OrderItem.ItemStatus.CANCELLED)
        self.assertEqual(self.order_item_a2.cancellation_reason, 'Ordered too much')

        # Bill must now reflect ONLY Paneer Butter Masala (15.00)
        self.order_a.refresh_from_db()
        self.invoice_a.refresh_from_db()
        self.assertEqual(self.order_a.subtotal, 15.00)
        self.assertEqual(self.order_a.total_items, 1)
        self.assertEqual(self.invoice_a.subtotal, 15.00)

        # Seats must remain occupied
        self.table.refresh_from_db()
        self.assertEqual(self.table.occupied_seats_count, 2)

    def test_cancel_cooking_item_requires_kitchen_approval(self):
        """Cancelling an item in COOKING status sets it to CANCELLATION_REQUESTED."""
        self.client.cookies[f'rms_session_{self.table.id}'] = self.session_a.session_token
        self.order_item_a1.status = OrderItem.ItemStatus.COOKING
        self.order_item_a1.save()

        url = reverse('tables:customer_cancel_item', kwargs={
            'table_number': self.table.table_number,
            'order_id': self.order_a.id,
            'item_id': self.order_item_a1.id
        })
        response = self.client.post(url, {'reason': 'Taking too long'})
        self.assertEqual(response.status_code, 302)

        self.order_item_a1.refresh_from_db()
        self.assertEqual(self.order_item_a1.status, OrderItem.ItemStatus.CANCELLATION_REQUESTED)

        # Staff logs in and approves cancellation
        self.client.force_login(self.staff)
        approve_url = reverse('kitchen:approve_cancellation', kwargs={'item_id': self.order_item_a1.id})
        response = self.client.post(approve_url)
        self.assertEqual(response.status_code, 302)

        self.order_item_a1.refresh_from_db()
        self.assertEqual(self.order_item_a1.status, OrderItem.ItemStatus.CANCELLED)

        # Deducted from invoice
        self.order_a.refresh_from_db()
        self.assertEqual(self.order_a.subtotal, 10.00)

    def test_cancel_entire_order_updates_bill_and_preserves_seats(self):
        """Cancelling an entire order cancels all items, zeroes bill, but leaves seats occupied."""
        self.client.cookies[f'rms_session_{self.table.id}'] = self.session_a.session_token

        url = reverse('tables:customer_cancel_order', kwargs={
            'table_number': self.table.table_number,
            'order_id': self.order_a.id
        })
        response = self.client.post(url, {'reason': 'Emergency departure'})
        self.assertEqual(response.status_code, 302)

        self.order_a.refresh_from_db()
        self.invoice_a.refresh_from_db()
        self.assertEqual(self.order_a.status, Order.Status.CANCELLED)
        self.assertEqual(self.order_a.subtotal, 0.00)
        self.assertEqual(self.invoice_a.subtotal, 0.00)

        # Seats remain occupied until explicit session close
        self.table.refresh_from_db()
        self.assertEqual(self.table.occupied_seats_count, 2)

    def test_close_session_releases_only_group_seats(self):
        """Closing a session releases only that session's seats and leaves other groups intact."""
        from tables.models import CustomerSession, RestaurantSeat, SessionSeatAllocation
        # Group B occupies remaining 2 seats
        session_b = CustomerSession.objects.create(
            table=self.table,
            restaurant=self.restaurant,
            session_name='Group B',
            customer_name='Bob',
            guest_count=2,
            status=CustomerSession.Status.ACTIVE
        )
        remaining_seats = list(self.table.seats.filter(status=RestaurantSeat.Status.AVAILABLE).order_by('seat_number')[:2])
        for s in remaining_seats:
            s.status = RestaurantSeat.Status.OCCUPIED
            s.save()
            SessionSeatAllocation.objects.create(session=session_b, seat=s)
        self.table.sync_status()
        self.assertEqual(self.table.occupied_seats_count, 4)

        # Group A cancels order or pays bill before leaving
        self.order_a.status = Order.Status.CANCELLED
        self.order_a.save()
        self.invoice_a.recalculate()

        # Group A closes session
        self.client.cookies[f'rms_session_{self.table.id}'] = self.session_a.session_token
        close_url = reverse('tables:customer_close_session', kwargs={'table_number': self.table.table_number})
        response = self.client.post(close_url)
        self.assertEqual(response.status_code, 302)

        self.session_a.refresh_from_db()
        self.assertEqual(self.session_a.status, CustomerSession.Status.COMPLETED)

        # Table should now have 2 seats available and 2 occupied by Group B
        self.table.refresh_from_db()
        self.assertEqual(self.table.occupied_seats_count, 2)
        self.assertEqual(self.table.available_seats_count, 2)
        self.assertEqual(self.table.status, DiningTable.Status.PARTIALLY_OCCUPIED)

    def test_session_isolation_prevents_cross_group_cancellation(self):
        """Group B cannot cancel Group A's order."""
        from tables.models import CustomerSession
        session_b = CustomerSession.objects.create(
            table=self.table,
            restaurant=self.restaurant,
            session_name='Group B',
            customer_name='Bob',
            guest_count=1,
            status=CustomerSession.Status.ACTIVE
        )
        # Attempt to cancel Group A's order using Group B's cookie
        self.client.cookies[f'rms_session_{self.table.id}'] = session_b.session_token
        url = reverse('tables:customer_cancel_item', kwargs={
            'table_number': self.table.table_number,
            'order_id': self.order_a.id,
            'item_id': self.order_item_a1.id
        })
        response = self.client.post(url, {'reason': 'Unauthorized hack attempt'})
        # Must not cancel Group A's item
        self.order_item_a1.refresh_from_db()
        self.assertEqual(self.order_item_a1.status, OrderItem.ItemStatus.PENDING)

    def test_cancelled_orders_view_and_notification_badge_reset(self):
        """When an order item is cancelled, notification counter increments. When viewed, resets to 0."""
        from orders.models import OrderCancellationLog
        self.client.cookies[f'rms_session_{self.table.id}'] = self.session_a.session_token

        # Cancel item
        url = reverse('tables:customer_cancel_item', kwargs={
            'table_number': self.table.table_number,
            'order_id': self.order_a.id,
            'item_id': self.order_item_a1.id
        })
        self.client.post(url, {'reason': 'Customer requested cancellation'})

        # Log created with is_seen=False
        log = OrderCancellationLog.objects.filter(order_item=self.order_item_a1).first()
        self.assertIsNotNone(log)
        self.assertFalse(log.is_seen)

        # Login as Admin
        self.client.force_login(self.admin)

        # Check that context processor reports unseen_cancellations_count >= 1
        resp_before = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp_before.status_code, 200)
        self.assertGreaterEqual(resp_before.context['unseen_cancellations_count'], 1)
        self.assertContains(resp_before, 'sidebar-cancellation-badge')

        # Admin navigates to Cancelled Orders page
        cancelled_url = reverse('orders:cancelled_orders')
        resp_cancelled = self.client.get(cancelled_url)
        self.assertEqual(resp_cancelled.status_code, 200)
        self.assertContains(resp_cancelled, 'Cancelled Orders & Items')
        self.assertContains(resp_cancelled, self.order_a.order_number)
        self.assertContains(resp_cancelled, 'Customer requested cancellation')

        # Log must now be marked is_seen=True
        log.refresh_from_db()
        self.assertTrue(log.is_seen)

        # Subsequent page load has notification count = 0
        resp_after = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp_after.context['unseen_cancellations_count'], 0)
        self.assertNotContains(resp_after, 'id="sidebar-cancellation-badge"')

    def test_extra_food_orders_view_and_notification_badge_countdown_and_vanish(self):
        """
        Verify:
        1. When a user adds extra food, ExtraFoodOrderLog is created with table name and is_seen=False.
        2. Sidebar and context processor show unseen_extra_food_count >= 1.
        3. Extra Food Orders page displays the table name, dish name, and member attribution.
        4. Acknowledging / marking seen reduces the count down.
        5. When count reaches 0, the badge vanishes.
        """
        from tables.models import GroupMember, SessionCart, SessionCartItem
        import json
        from decimal import Decimal

        # Create extra food item on order_a
        extra_item = OrderItem.objects.create(
            order=self.order_a,
            menu_item=self.item2,
            quantity=1,
            initial_quantity=0,
            extra_quantity=1,
            is_extra=True,
            unit_price=Decimal('5.00'),
            added_by_name='Dhanusha',
            status=OrderItem.ItemStatus.PENDING
        )
        self.order_a.has_unseen_extra = True
        self.order_a.calculate_totals(save=True)

        log = ExtraFoodOrderLog.objects.create(
            order=self.order_a,
            order_item=extra_item,
            table=self.table,
            table_name=self.table.table_number,
            session_name=self.session_a.session_name,
            item_name=self.item2.name,
            quantity=1,
            unit_price=Decimal('5.00'),
            total_price=Decimal('5.00'),
            added_by_name='Dhanusha',
            is_seen=False
        )

        # Login as Admin
        self.client.force_login(self.admin)

        # Step 1: Context processor reports unseen_extra_food_count >= 1
        resp_dashboard = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp_dashboard.status_code, 200)
        self.assertGreaterEqual(resp_dashboard.context['unseen_extra_food_count'], 1)
        self.assertContains(resp_dashboard, 'sidebar-extra-food-badge')
        self.assertContains(resp_dashboard, 'Extra Food Orders')

        # Step 2: Extra Food Orders page loads and displays Table Name
        extra_url = reverse('orders:extra_food_orders')
        resp_extra = self.client.get(extra_url)
        self.assertEqual(resp_extra.status_code, 200)
        self.assertContains(resp_extra, f'Table {self.table.table_number}')
        self.assertContains(resp_extra, self.item2.name)
        self.assertContains(resp_extra, 'Dhanusha')

        # Step 3: Real-time poll API returns unseen_count and latest info with table_name
        resp_poll = self.client.get(reverse('orders:poll_extra_food'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp_poll.status_code, 200)
        data = resp_poll.json()
        self.assertGreaterEqual(data['unseen_count'], 1)
        self.assertEqual(data['latest']['table_name'], self.table.table_number)

        # Step 4: Mark single extra food log as seen (reduces countdown)
        resp_seen = self.client.post(
            reverse('orders:mark_single_extra_food_seen', kwargs={'log_id': log.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_seen.status_code, 200)
        self.assertTrue(resp_seen.json()['success'])
        self.assertEqual(resp_seen.json()['remaining_count'], 0)

        log.refresh_from_db()
        self.assertTrue(log.is_seen)

        # Step 5: When count reaches 0, sidebar badge vanishes
        resp_after = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp_after.context['unseen_extra_food_count'], 0)
        # Badge has style="display: none;"
        self.assertContains(resp_after, 'display: none;')



