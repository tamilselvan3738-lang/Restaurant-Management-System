import json
import uuid
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, Restaurant
from menu.models import Category, MenuItem
from tables.models import (
    DiningTable, RestaurantSeat, CustomerSession, GroupMember,
    SessionCart, SessionCartItem, SessionSeatAllocation
)
from orders.models import Order, OrderItem
from billing.models import Invoice, Payment


class GroupOrderingAndSharedCartTests(TestCase):
    """
    Comprehensive Automated Test Suite covering all 20 Mandatory Functional Tests
    for Group Ordering, Shared Cart, Multi-Customer Table Isolation, Permission Controls,
    Kitchen Routing, Bill Splitting, and Table Seat Lifecycle.
    """

    def setUp(self):
        # 1. Setup Restaurant & Admin
        self.restaurant = Restaurant.objects.create(
            name="Growwpark Gourmet",
            code="REST-001"
        )
        self.restaurant_other = Restaurant.objects.create(
            name="Other Restaurant",
            code="REST-002"
        )

        self.admin = User.objects.create_user(
            username='admin_group_pos',
            email='admin@growwpark.com',
            password='testpassword123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )

        # 2. Setup Menu Items
        self.category = Category.objects.create(
            name='Specialties',
            slug='specialties',
            is_active=True,
            display_order=1
        )
        self.item_biryani = MenuItem.objects.create(
            category=self.category,
            name='Hyderabadi Chicken Dum Biryani',
            price=Decimal('240.00'),
            is_available=True
        )
        self.item_lime_juice = MenuItem.objects.create(
            category=self.category,
            name='Fresh Lime Juice',
            price=Decimal('60.00'),
            is_available=True
        )
        self.item_paneer = MenuItem.objects.create(
            category=self.category,
            name='Paneer Butter Masala',
            price=Decimal('220.00'),
            is_available=True
        )
        self.item_naan = MenuItem.objects.create(
            category=self.category,
            name='Butter Naan',
            price=Decimal('50.00'),
            is_available=True
        )

        # 3. Setup Tables
        # Table T-02 has capacity 4
        self.table_t02 = DiningTable.objects.create(
            table_number='T-02',
            capacity=4,
            location=DiningTable.Location.ROUND,
            restaurant=self.restaurant
        )
        # Table T-08 has capacity 8 (for multi-group testing)
        self.table_t08 = DiningTable.objects.create(
            table_number='T-08',
            capacity=8,
            location=DiningTable.Location.BOOTH,
            restaurant=self.restaurant
        )
        # Table at Other Restaurant
        self.table_other = DiningTable.objects.create(
            table_number='T-99',
            capacity=4,
            location=DiningTable.Location.RECTANGLE,
            restaurant=self.restaurant_other
        )

        # HTTP Clients for different members
        self.client_rahul = Client()
        self.client_arun = Client()
        self.client_priya = Client()
        self.client_kavin = Client()

    def _set_member_cookies(self, client, table, session, member):
        """Helper to simulate authenticated group session cookies for a client."""
        client.cookies[f'rms_session_{table.id}'] = session.session_token
        client.cookies[f'rms_member_{table.id}'] = member.member_token

    # ------------------------------------------------------------------------
    # Test 1: Customer creates a new group (session created, leader assigned, cart created, seats NOT occupied)
    # ------------------------------------------------------------------------
    def test_01_customer_creates_new_group(self):
        """Test Case 1: Scanning table QR and creating a group creates session, leader, cart, and leaves seats unallocated."""
        response = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'create_group',
                'session_name': 'Group A',
                'customer_name': 'Rahul',
                'guest_count': 4
            }
        )
        self.assertEqual(response.status_code, 302)

        session = CustomerSession.objects.filter(table=self.table_t02, session_name='Group A').first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, CustomerSession.Status.ACTIVE)
        self.assertTrue(len(session.join_code) >= 6)

        leader = session.members.filter(role=GroupMember.Role.LEADER).first()
        self.assertIsNotNone(leader)
        self.assertEqual(leader.name, 'Rahul')
        self.assertTrue(leader.can_submit_order)

        # Verify shared cart created
        cart = SessionCart.objects.filter(session=session).first()
        self.assertIsNotNone(cart)

        # CRITICAL OCCUPANCY RULE: Cart creation or QR scanning does NOT allocate seats!
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 0)
        self.assertEqual(self.table_t02.status, DiningTable.Status.AVAILABLE)

    # ------------------------------------------------------------------------
    # Test 2: Multiple customers join the same group
    # ------------------------------------------------------------------------
    def test_02_multiple_customers_join_same_group(self):
        """Test Case 2: Arun, Priya, and Kavin join Rahul's Group A via join code."""
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Group A',
            customer_name='Rahul',
            guest_count=4,
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(
            session=session,
            name='Rahul',
            role=GroupMember.Role.LEADER,
            can_submit_order=True
        )
        SessionCart.objects.create(session=session)

        # Arun joins using join_code
        resp_arun = self.client_arun.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': session.join_code,
                'customer_name': 'Arun'
            }
        )
        self.assertEqual(resp_arun.status_code, 302)

        # Priya joins
        resp_priya = self.client_priya.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': session.join_code,
                'customer_name': 'Priya'
            }
        )
        self.assertEqual(resp_priya.status_code, 302)

        # Kavin joins
        resp_kavin = self.client_kavin.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': session.join_code,
                'customer_name': 'Kavin'
            }
        )
        self.assertEqual(resp_kavin.status_code, 302)

        active_members = session.members.filter(is_active=True)
        self.assertEqual(active_members.count(), 4)

        member_names = list(active_members.values_list('name', flat=True))
        self.assertIn('Rahul', member_names)
        self.assertIn('Arun', member_names)
        self.assertIn('Priya', member_names)
        self.assertIn('Kavin', member_names)

        # Verify regular members cannot submit orders by default
        arun_member = active_members.get(name='Arun')
        self.assertEqual(arun_member.role, GroupMember.Role.MEMBER)
        self.assertFalse(arun_member.can_submit_order)

    # ------------------------------------------------------------------------
    # Test 3: All members see the same shared cart
    # ------------------------------------------------------------------------
    def test_03_all_members_see_same_shared_cart(self):
        """Test Case 3: Both Leader and regular member retrieve identical shared cart data."""
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Group A',
            customer_name='Rahul',
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        member = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)
        cart = SessionCart.objects.create(session=session)

        # Add item directly to shared cart
        SessionCartItem.objects.create(
            cart=cart,
            menu_item=self.item_biryani,
            quantity=2,
            added_by=leader,
            added_by_name='Rahul'
        )

        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        self._set_member_cookies(self.client_arun, self.table_t02, session, member)

        resp_rahul = self.client_rahul.get(reverse('tables:api_group_cart_get', kwargs={'table_number': self.table_t02.table_number}))
        resp_arun = self.client_arun.get(reverse('tables:api_group_cart_get', kwargs={'table_number': self.table_t02.table_number}))

        self.assertEqual(resp_rahul.status_code, 200)
        self.assertEqual(resp_arun.status_code, 200)

        data_rahul = resp_rahul.json()
        data_arun = resp_arun.json()

        self.assertEqual(len(data_rahul['items']), 1)
        self.assertEqual(len(data_arun['items']), 1)
        self.assertEqual(data_rahul['items'][0]['name'], 'Hyderabadi Chicken Dum Biryani')
        self.assertEqual(data_arun['items'][0]['name'], 'Hyderabadi Chicken Dum Biryani')
        self.assertEqual(data_rahul['items'][0]['quantity'], 2)
        self.assertEqual(data_arun['items'][0]['quantity'], 2)

    # ------------------------------------------------------------------------
    # Test 4: Customer adds food and cart updates with added_by
    # ------------------------------------------------------------------------
    def test_04_customer_adds_food_and_cart_updates_with_added_by(self):
        """Test Case 4: Items added by Arun and Priya reflect respective added_by member."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)
        priya = GroupMember.objects.create(session=session, name='Priya', role=GroupMember.Role.MEMBER, can_submit_order=False)
        cart = SessionCart.objects.create(session=session)

        self._set_member_cookies(self.client_arun, self.table_t02, session, arun)
        self._set_member_cookies(self.client_priya, self.table_t02, session, priya)

        # Arun adds Biryani
        resp_arun = self.client_arun.post(
            reverse('tables:api_group_cart_add', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({'item_id': self.item_biryani.id, 'quantity': 1, 'notes': 'Extra spicy'}),
            content_type='application/json'
        )
        self.assertEqual(resp_arun.status_code, 200)

        # Priya adds Lime Juice
        resp_priya = self.client_priya.post(
            reverse('tables:api_group_cart_add', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({'item_id': self.item_lime_juice.id, 'quantity': 2, 'notes': 'Less sugar'}),
            content_type='application/json'
        )
        self.assertEqual(resp_priya.status_code, 200)

        items = SessionCartItem.objects.filter(cart=cart, status=SessionCartItem.Status.ACTIVE)
        self.assertEqual(items.count(), 2)

        biryani_item = items.get(menu_item=self.item_biryani)
        self.assertEqual(biryani_item.added_by, arun)
        self.assertEqual(biryani_item.added_by_name, 'Arun')
        self.assertEqual(biryani_item.special_notes, 'Extra spicy')

        juice_item = items.get(menu_item=self.item_lime_juice)
        self.assertEqual(juice_item.added_by, priya)
        self.assertEqual(juice_item.added_by_name, 'Priya')
        self.assertEqual(juice_item.special_notes, 'Less sugar')

    # ------------------------------------------------------------------------
    # Test 5: Customer cannot edit/remove another member's items
    # ------------------------------------------------------------------------
    def test_05_customer_cannot_edit_or_remove_another_members_items(self):
        """Test Case 5: Regular member Arun gets 403 trying to modify or delete Priya's item."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)
        priya = GroupMember.objects.create(session=session, name='Priya', role=GroupMember.Role.MEMBER, can_submit_order=False)
        cart = SessionCart.objects.create(session=session)

        priya_item = SessionCartItem.objects.create(
            cart=cart,
            menu_item=self.item_paneer,
            quantity=1,
            added_by=priya,
            added_by_name='Priya'
        )

        self._set_member_cookies(self.client_arun, self.table_t02, session, arun)

        # Arun tries to edit Priya's item quantity -> 403 Forbidden
        resp_update = self.client_arun.post(
            reverse('tables:api_group_cart_update', kwargs={'table_number': self.table_t02.table_number, 'item_id': priya_item.id}),
            data=json.dumps({'quantity': 5}),
            content_type='application/json'
        )
        self.assertEqual(resp_update.status_code, 403)

        # Arun tries to remove Priya's item -> 403 Forbidden
        resp_remove = self.client_arun.post(
            reverse('tables:api_group_cart_remove', kwargs={'table_number': self.table_t02.table_number, 'item_id': priya_item.id}),
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(resp_remove.status_code, 403)

        # Priya CAN update her own item -> 200 OK
        self._set_member_cookies(self.client_priya, self.table_t02, session, priya)
        resp_priya_edit = self.client_priya.post(
            reverse('tables:api_group_cart_update', kwargs={'table_number': self.table_t02.table_number, 'item_id': priya_item.id}),
            data=json.dumps({'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(resp_priya_edit.status_code, 200)

        # Leader Rahul CAN edit/remove any item -> 200 OK
        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        resp_leader_edit = self.client_rahul.post(
            reverse('tables:api_group_cart_update', kwargs={'table_number': self.table_t02.table_number, 'item_id': priya_item.id}),
            data=json.dumps({'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(resp_leader_edit.status_code, 200)

    # ------------------------------------------------------------------------
    # Test 6: Group Leader can submit the group order
    # ------------------------------------------------------------------------
    def test_06_group_leader_can_submit_order(self):
        """Test Case 6: Group Leader submits shared cart; seats are allocated atomically."""
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Group A',
            customer_name='Rahul',
            guest_count=4,
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)
        cart = SessionCart.objects.create(session=session)

        SessionCartItem.objects.create(cart=cart, menu_item=self.item_biryani, quantity=2, added_by=arun, added_by_name='Arun')
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_naan, quantity=4, added_by=leader, added_by_name='Rahul')

        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)

        resp = self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({'notes': 'Please serve hot'}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')

        # Check Order
        order = Order.objects.filter(session=session).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.table, self.table_t02)
        self.assertEqual(order.items.count(), 2)

        # Check item added_by attribution
        biryani_ord_item = order.items.get(menu_item=self.item_biryani)
        self.assertEqual(biryani_ord_item.added_by_name, 'Arun')
        self.assertEqual(biryani_ord_item.added_by_member, arun)

        # OCCUPANCY VERIFICATION: Order submission allocates seats atomically!
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 4)
        self.assertEqual(self.table_t02.status, DiningTable.Status.OCCUPIED)

    # ------------------------------------------------------------------------
    # Test 7: Unauthorized regular members cannot submit the order
    # ------------------------------------------------------------------------
    def test_07_regular_member_cannot_submit_order(self):
        """Test Case 7: Regular member Arun receives 403 Forbidden when trying to submit order."""
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Group A',
            customer_name='Rahul',
            guest_count=4,
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)
        cart = SessionCart.objects.create(session=session)

        SessionCartItem.objects.create(cart=cart, menu_item=self.item_biryani, quantity=1, added_by=arun, added_by_name='Arun')

        self._set_member_cookies(self.client_arun, self.table_t02, session, arun)

        resp = self.client_arun.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({'notes': 'Test'}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.json()
        self.assertEqual(data['status'], 'error')
        self.assertIn('Only the Group Leader', data['message'])

        # Order must NOT be created
        self.assertFalse(Order.objects.filter(session=session).exists())

    # ------------------------------------------------------------------------
    # Test 8: Duplicate submissions do not create duplicate orders (Idempotency)
    # ------------------------------------------------------------------------
    def test_08_duplicate_submissions_prevented(self):
        """Test Case 8: Repeated POST requests with same idempotency_key return existing order without duplicates."""
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Group A',
            customer_name='Rahul',
            guest_count=2,
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart = SessionCart.objects.create(session=session)
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_biryani, quantity=1, added_by=leader, added_by_name='Rahul')

        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)

        idempotency_key = "IDEMP-KEY-TEST-12345"
        resp1 = self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({'idempotency_key': idempotency_key}),
            content_type='application/json'
        )
        self.assertEqual(resp1.status_code, 200)
        order_count1 = Order.objects.filter(session=session).count()
        self.assertEqual(order_count1, 1)

        # Send second request with identical idempotency key
        resp2 = self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({'idempotency_key': idempotency_key}),
            content_type='application/json'
        )
        self.assertEqual(resp2.status_code, 200)
        order_count2 = Order.objects.filter(session=session).count()
        self.assertEqual(order_count2, 1)  # No duplicate order created!

    # ------------------------------------------------------------------------
    # Test 9: Additional food attaches to same session without double seat allocation
    # ------------------------------------------------------------------------
    def test_09_additional_food_attaches_to_same_group_session(self):
        """Test Case 9: Ordering more food attaches to existing session without doubling table seats."""
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Group A',
            customer_name='Rahul',
            guest_count=4,
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart = SessionCart.objects.create(session=session)
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_biryani, quantity=2, added_by=leader, added_by_name='Rahul')

        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)

        # Initial order
        self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 4)

        # Arun (regular member with can_submit_order=False) joins group
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)

        # Arun adds additional food to cart
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_naan, quantity=2, added_by=arun, added_by_name='Arun')

        # Regular member Arun submits extra food without needing permission from the leader
        self._set_member_cookies(self.client_arun, self.table_t02, session, arun)
        resp2 = self.client_arun.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(resp2.status_code, 200)

        # Single unified order exists under the session (extra food merged into active order/bill)
        orders = Order.objects.filter(session=session)
        self.assertEqual(orders.count(), 1)
        active_order = orders.first()
        self.assertEqual(active_order.items.count(), 2)
        self.assertEqual(active_order.total_items, 4)
        self.assertTrue(active_order.has_unseen_extra)
        self.assertTrue(active_order.has_extra_items)
        self.assertEqual(active_order.total_extra_quantity, 2)

        # Session invoice matches the single order total
        invoice = session.invoices.first()
        self.assertEqual(invoice.total_amount, active_order.total_amount)

        # CRITICAL OCCUPANCY: Seats must NOT be double allocated (remains 4, not 8)
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 4)

    # ------------------------------------------------------------------------
    # Test 10: Group A and Group B operate independently at the same physical table
    # ------------------------------------------------------------------------
    def test_10_group_a_and_group_b_operate_independently_at_same_table(self):
        """Test Case 10: Table T-08 (capacity 8) supports Group A (3 guests) and Group B (3 guests) concurrently."""
        session_a = CustomerSession.objects.create(
            table=self.table_t08,
            session_name='Group A',
            customer_name='Leader A',
            guest_count=3,
            status=CustomerSession.Status.ACTIVE
        )
        leader_a = GroupMember.objects.create(session=session_a, name='Leader A', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart_a = SessionCart.objects.create(session=session_a)
        SessionCartItem.objects.create(cart=cart_a, menu_item=self.item_biryani, quantity=1, added_by=leader_a, added_by_name='Leader A')

        session_b = CustomerSession.objects.create(
            table=self.table_t08,
            session_name='Group B',
            customer_name='Leader B',
            guest_count=3,
            status=CustomerSession.Status.ACTIVE
        )
        leader_b = GroupMember.objects.create(session=session_b, name='Leader B', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart_b = SessionCart.objects.create(session=session_b)
        SessionCartItem.objects.create(cart=cart_b, menu_item=self.item_paneer, quantity=1, added_by=leader_b, added_by_name='Leader B')

        # Submit Group A
        self._set_member_cookies(self.client_rahul, self.table_t08, session_a, leader_a)
        self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t08.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )

        # Submit Group B
        self._set_member_cookies(self.client_arun, self.table_t08, session_b, leader_b)
        self.client_arun.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t08.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )

        self.table_t08.refresh_from_db()
        self.assertEqual(self.table_t08.occupied_seats, 6)
        self.assertEqual(self.table_t08.status, DiningTable.Status.PARTIALLY_OCCUPIED)
        self.assertEqual(self.table_t08.active_sessions().count(), 2)

    # ------------------------------------------------------------------------
    # Test 11: Group A cannot access Group B's cart, items, or bill
    # ------------------------------------------------------------------------
    def test_11_group_a_cannot_access_group_b_data(self):
        """Test Case 11: Group A member sees only Group A items in shared cart API; cannot view or edit Group B."""
        session_a = CustomerSession.objects.create(table=self.table_t08, session_name='Group A', status=CustomerSession.Status.ACTIVE)
        leader_a = GroupMember.objects.create(session=session_a, name='Leader A', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart_a = SessionCart.objects.create(session=session_a)
        SessionCartItem.objects.create(cart=cart_a, menu_item=self.item_biryani, quantity=1, added_by=leader_a, added_by_name='Leader A')

        session_b = CustomerSession.objects.create(table=self.table_t08, session_name='Group B', status=CustomerSession.Status.ACTIVE)
        leader_b = GroupMember.objects.create(session=session_b, name='Leader B', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart_b = SessionCart.objects.create(session=session_b)
        item_b = SessionCartItem.objects.create(cart=cart_b, menu_item=self.item_paneer, quantity=1, added_by=leader_b, added_by_name='Leader B')

        # Leader A queries cart
        self._set_member_cookies(self.client_rahul, self.table_t08, session_a, leader_a)
        resp = self.client_rahul.get(reverse('tables:api_group_cart_get', kwargs={'table_number': self.table_t08.table_number}))
        data = resp.json()

        items = [it['name'] for it in data['items']]
        self.assertIn('Hyderabadi Chicken Dum Biryani', items)
        self.assertNotIn('Paneer Butter Masala', items)

        # Leader A attempts to update Group B's cart item -> 404 (not in Group A's cart)
        resp_edit_b = self.client_rahul.post(
            reverse('tables:api_group_cart_update', kwargs={'table_number': self.table_t08.table_number, 'item_id': item_b.id}),
            data=json.dumps({'quantity': 10}),
            content_type='application/json'
        )
        self.assertEqual(resp_edit_b.status_code, 404)

    # ------------------------------------------------------------------------
    # Test 12: Different restaurants cannot access each other's groups
    # ------------------------------------------------------------------------
    def test_12_cross_restaurant_isolation(self):
        """Test Case 12: Group session from Restaurant 1 cannot be joined or accessed on Restaurant 2 table."""
        session_r1 = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Rest 1 Group',
            customer_name='Chef R1',
            status=CustomerSession.Status.ACTIVE
        )
        GroupMember.objects.create(session=session_r1, name='Chef R1', role=GroupMember.Role.LEADER, can_submit_order=True)

        # Attempt to join Restaurant 1 session code on Restaurant 2 table
        resp = self.client_arun.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_other.table_number}),
            data={
                'action': 'join_group',
                'join_code': session_r1.join_code,
                'customer_name': 'Hacker'
            }
        )
        # Should fail or redirect with error because code does not belong to table_other
        self.assertEqual(resp.status_code, 302)
        # Verify no member was added to session_r1
        self.assertEqual(session_r1.members.filter(name='Hacker').count(), 0)

    # ------------------------------------------------------------------------
    # Test 13: Kitchen receives each food item separately with added_by_name
    # ------------------------------------------------------------------------
    def test_13_kitchen_receives_separate_items_with_added_by_name(self):
        """Test Case 13: Kitchen display view receives order items with added_by_name."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)

        order = Order.objects.create(
            restaurant=self.restaurant,
            table=self.table_t02,
            session=session,
            status=Order.Status.PLACED
        )
        item1 = OrderItem.objects.create(
            order=order,
            menu_item=self.item_biryani,
            quantity=1,
            unit_price=self.item_biryani.price,
            added_by_member=arun,
            added_by_name='Arun'
        )
        item2 = OrderItem.objects.create(
            order=order,
            menu_item=self.item_lime_juice,
            quantity=1,
            unit_price=self.item_lime_juice.price,
            added_by_member=leader,
            added_by_name='Rahul'
        )

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('orders:kitchen_display'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Arun')
        self.assertContains(resp, 'Rahul')
        self.assertContains(resp, 'Group A')

    # ------------------------------------------------------------------------
    # Test 14: Customizations and special instructions reach order items
    # ------------------------------------------------------------------------
    def test_14_customizations_reach_order_items(self):
        """Test Case 14: Special instructions on cart item persist to OrderItem upon order submission."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', guest_count=2, status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart = SessionCart.objects.create(session=session)
        SessionCartItem.objects.create(
            cart=cart,
            menu_item=self.item_paneer,
            quantity=1,
            special_notes="Make it mild spicy, no coriander",
            added_by=leader,
            added_by_name='Rahul'
        )

        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )

        order = Order.objects.filter(session=session).first()
        ord_item = order.items.get(menu_item=self.item_paneer)
        self.assertEqual(ord_item.special_notes, "Make it mild spicy, no coriander")

    # ------------------------------------------------------------------------
    # Test 15: Item cancellation follows existing permissions
    # ------------------------------------------------------------------------
    def test_15_item_cancellation_follows_existing_permissions(self):
        """Test Case 15: Cancellation permissions and status on submitted orders."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        order = Order.objects.create(
            restaurant=self.restaurant,
            table=self.table_t02,
            session=session,
            status=Order.Status.PLACED
        )
        ord_item = OrderItem.objects.create(
            order=order,
            menu_item=self.item_biryani,
            quantity=1,
            unit_price=self.item_biryani.price,
            added_by_member=leader,
            added_by_name='Rahul'
        )

        # Customer or Staff cancels pending item
        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        resp = self.client_rahul.post(
            reverse('tables:customer_cancel_item', kwargs={'table_number': self.table_t02.table_number, 'order_id': order.id, 'item_id': ord_item.id}),
            data={'reason': 'Customer requested cancellation'}
        )
        self.assertEqual(resp.status_code, 302)
        ord_item.refresh_from_db()
        self.assertEqual(ord_item.status, OrderItem.ItemStatus.CANCELLED)

    # ------------------------------------------------------------------------
    # Test 16: Group bill matches combined order total
    # ------------------------------------------------------------------------
    def test_16_group_bill_matches_combined_order_total(self):
        """Test Case 16: Session invoice accurately aggregates multiple orders belonging to the group."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', status=CustomerSession.Status.ACTIVE)
        # Order 1: Biryani (₹240) + Lime Juice (₹60) = ₹300
        order1 = Order.objects.create(table=self.table_t02, session=session, status=Order.Status.SERVED)
        OrderItem.objects.create(order=order1, menu_item=self.item_biryani, quantity=1, unit_price=self.item_biryani.price)
        OrderItem.objects.create(order=order1, menu_item=self.item_lime_juice, quantity=1, unit_price=self.item_lime_juice.price)

        # Order 2: Paneer (₹220) + Naan (₹50) = ₹270
        order2 = Order.objects.create(table=self.table_t02, session=session, status=Order.Status.SERVED)
        OrderItem.objects.create(order=order2, menu_item=self.item_paneer, quantity=1, unit_price=self.item_paneer.price)
        OrderItem.objects.create(order=order2, menu_item=self.item_naan, quantity=1, unit_price=self.item_naan.price)

        invoice, _ = Invoice.objects.get_or_create(session=session, defaults={'table': self.table_t02, 'invoice_type': Invoice.InvoiceType.SESSION_BILL})
        invoice.recalculate(save=True)

        expected_subtotal = Decimal('300.00') + Decimal('270.00')  # ₹570.00
        self.assertEqual(invoice.subtotal, expected_subtotal)
        self.assertEqual(invoice.total_amount, invoice.subtotal + invoice.tax_amount + invoice.service_charge_amount)

    # ------------------------------------------------------------------------
    # Test 17: Split Bill Options (My Items vs Equal Split)
    # ------------------------------------------------------------------------
    def test_17_split_bill_allocations(self):
        """Test Case 17: Individual bill splitting records payments with split_type and allocated member."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', guest_count=2, status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        arun = GroupMember.objects.create(session=session, name='Arun', role=GroupMember.Role.MEMBER, can_submit_order=False)

        order = Order.objects.create(table=self.table_t02, session=session, status=Order.Status.SERVED)
        OrderItem.objects.create(order=order, menu_item=self.item_biryani, quantity=1, unit_price=Decimal('240.00'), added_by_member=arun, added_by_name='Arun')
        OrderItem.objects.create(order=order, menu_item=self.item_lime_juice, quantity=1, unit_price=Decimal('60.00'), added_by_member=leader, added_by_name='Rahul')

        invoice, _ = Invoice.objects.get_or_create(session=session, defaults={'table': self.table_t02, 'invoice_type': Invoice.InvoiceType.SESSION_BILL})
        invoice.recalculate(save=True)

        # Arun pays for Option A: My Items
        self._set_member_cookies(self.client_arun, self.table_t02, session, arun)
        resp_arun_pay = self.client_arun.post(
            reverse('tables:customer_pay_bill', kwargs={'table_number': self.table_t02.table_number, 'session_token': session.session_token}),
            data={
                'amount': '250.00',
                'payment_method': Payment.PaymentMethod.UPI,
                'split_type': 'MY_ITEMS'
            }
        )
        self.assertEqual(resp_arun_pay.status_code, 302)

        pay = Payment.objects.filter(invoice=invoice, allocated_member=arun).first()
        self.assertIsNotNone(pay)
        self.assertEqual(pay.split_type, 'MY_ITEMS')
        self.assertEqual(pay.paid_by_name, 'Arun')
        self.assertEqual(pay.amount, Decimal('250.00'))

    # ------------------------------------------------------------------------
    # Test 18: Payment does not release the table automatically
    # ------------------------------------------------------------------------
    def test_18_payment_does_not_release_table_automatically(self):
        """Test Case 18: Full payment settlement does NOT release seats or close session."""
        session = CustomerSession.objects.create(table=self.table_t02, session_name='Group A', guest_count=4, status=CustomerSession.Status.ACTIVE)
        leader = GroupMember.objects.create(session=session, name='Rahul', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart = SessionCart.objects.create(session=session)
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_biryani, quantity=2, added_by=leader, added_by_name='Rahul')

        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )

        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 4)

        invoice, _ = Invoice.objects.get_or_create(session=session, defaults={'table': self.table_t02, 'invoice_type': Invoice.InvoiceType.SESSION_BILL})
        invoice.recalculate(save=True)

        # Full settlement via payment endpoint
        resp_pay = self.client_rahul.post(
            reverse('tables:customer_pay_bill', kwargs={'table_number': self.table_t02.table_number, 'session_token': session.session_token}),
            data={
                'amount': str(invoice.balance_due),
                'payment_method': Payment.PaymentMethod.UPI,
                'split_type': 'FULL'
            }
        )
        self.assertEqual(resp_pay.status_code, 302)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        # CRITICAL RULE: Table seats remain OCCUPIED until staff explicitly closes session
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 4)
        self.assertEqual(self.table_t02.status, DiningTable.Status.OCCUPIED)

    # ------------------------------------------------------------------------
    # Test 19: Closing Group A does not close Group B
    # ------------------------------------------------------------------------
    def test_19_closing_group_a_does_not_close_group_b(self):
        """Test Case 19: Explicitly closing Group A releases Group A's seats while Group B remains active."""
        session_a = CustomerSession.objects.create(table=self.table_t08, session_name='Group A', guest_count=3, status=CustomerSession.Status.ACTIVE)
        leader_a = GroupMember.objects.create(session=session_a, name='Leader A', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart_a = SessionCart.objects.create(session=session_a)
        SessionCartItem.objects.create(cart=cart_a, menu_item=self.item_biryani, quantity=1, added_by=leader_a, added_by_name='Leader A')

        session_b = CustomerSession.objects.create(table=self.table_t08, session_name='Group B', guest_count=3, status=CustomerSession.Status.ACTIVE)
        leader_b = GroupMember.objects.create(session=session_b, name='Leader B', role=GroupMember.Role.LEADER, can_submit_order=True)
        cart_b = SessionCart.objects.create(session=session_b)
        SessionCartItem.objects.create(cart=cart_b, menu_item=self.item_paneer, quantity=1, added_by=leader_b, added_by_name='Leader B')

        # Submit both groups
        self._set_member_cookies(self.client_rahul, self.table_t08, session_a, leader_a)
        self.client_rahul.post(reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t08.table_number}), data=json.dumps({}), content_type='application/json')

        self._set_member_cookies(self.client_arun, self.table_t08, session_b, leader_b)
        self.client_arun.post(reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t08.table_number}), data=json.dumps({}), content_type='application/json')

        self.table_t08.refresh_from_db()
        self.assertEqual(self.table_t08.occupied_seats, 6)

        # Explicitly close Group A
        session_a.close_session(reason="Group A departed")

        self.table_t08.refresh_from_db()
        session_b.refresh_from_db()

        # Group B remains ACTIVE with its 3 seats occupied
        self.assertEqual(session_b.status, CustomerSession.Status.ACTIVE)
        self.assertEqual(self.table_t08.occupied_seats, 3)

    # ------------------------------------------------------------------------
    # Test 20: Cart endpoint access is restricted to authorized group session
    # ------------------------------------------------------------------------
    def test_20_access_restricted_to_authorized_group(self):
        """Test Case 20: Requests without active session or member cookies return 403 Forbidden."""
        client_stranger = Client()
        resp = client_stranger.get(reverse('tables:api_group_cart_get', kwargs={'table_number': self.table_t02.table_number}))
        self.assertEqual(resp.status_code, 403)
        data = resp.json()
        self.assertEqual(data['status'], 'error')

    # ------------------------------------------------------------------------
    # Test 21: Party Size Selection & Capacity Validation
    # ------------------------------------------------------------------------
    def test_21_party_size_selection_validation(self):
        """Test Case 21: Validates party size selection against table capacity (single shared group)."""
        # Valid party size (4 on Table T-02 with capacity 4)
        resp_valid = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'create_group',
                'party_size': 4,
                'customer_name': 'Rahul Party',
                'session_name': 'Family Dinner'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_valid.status_code, 200)
        data = resp_valid.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['group']['guest_count'], 4)
        self.assertEqual(data['group']['session_name'], 'Family Dinner')
        self.assertTrue(len(data['group']['join_code']) >= 6)

        # Exceeding table capacity (5 on Table T-02 with capacity 4)
        resp_invalid = self.client_arun.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'create_group',
                'party_size': 5,
                'customer_name': 'Arun Overcapacity'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_invalid.status_code, 400)
        data_err = resp_invalid.json()
        self.assertEqual(data_err['status'], 'error')
        self.assertIn('exceeds table capacity', data_err['message'])

    # ------------------------------------------------------------------------
    # Test 22: Create Multiple Independent Split Groups
    # ------------------------------------------------------------------------
    def test_22_create_split_groups_success(self):
        """Test Case 22: Creates multiple split groups at the same table with unique codes and carts."""
        groups_payload = [
            {'name': 'Team Alpha', 'guest_count': 3, 'leader_name': 'Alice'},
            {'name': 'Team Beta', 'guest_count': 3, 'leader_name': 'Bob'}
        ]
        resp = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t08.table_number}),
            data={
                'action': 'create_split_groups',
                'party_size': 6,
                'groups_data': json.dumps(groups_payload)
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(len(data['groups']), 2)

        # Check groups in database
        sessions = CustomerSession.objects.filter(table=self.table_t08)
        self.assertEqual(sessions.count(), 2)

        sess_alpha = sessions.get(session_name='Team Alpha')
        sess_beta = sessions.get(session_name='Team Beta')

        self.assertEqual(sess_alpha.guest_count, 3)
        self.assertEqual(sess_alpha.customer_name, 'Alice')
        self.assertEqual(sess_beta.guest_count, 3)
        self.assertEqual(sess_beta.customer_name, 'Bob')

        # Codes must be distinct
        self.assertNotEqual(sess_alpha.join_code, sess_beta.join_code)
        self.assertEqual(len(sess_alpha.join_code), 6)
        self.assertEqual(len(sess_beta.join_code), 6)

        # Carts created for both
        self.assertTrue(SessionCart.objects.filter(session=sess_alpha).exists())
        self.assertTrue(SessionCart.objects.filter(session=sess_beta).exists())

        # Initiating client is bound to Team Alpha (first group)
        self.assertEqual(resp.cookies[f'rms_session_{self.table_t08.id}'].value, sess_alpha.session_token)

    # ------------------------------------------------------------------------
    # Test 23: Split Groups Member Sum Mismatch Rejected
    # ------------------------------------------------------------------------
    def test_23_create_split_groups_sum_mismatch_rejected(self):
        """Test Case 23: Split groups setup fails if sum of group members doesn't match confirmed party size."""
        # Sum of 2 + 3 = 5, but party_size is 6
        groups_payload = [
            {'name': 'Team A', 'guest_count': 2, 'leader_name': 'Alice'},
            {'name': 'Team B', 'guest_count': 3, 'leader_name': 'Bob'}
        ]
        resp = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t08.table_number}),
            data={
                'action': 'create_split_groups',
                'party_size': 6,
                'groups_data': json.dumps(groups_payload)
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data['status'], 'error')
        self.assertIn('Allocated members (5) must match confirmed party size (6)', data['message'])

        # Over table capacity: party_size 10 on Table T-08 (capacity 8)
        groups_payload_over = [
            {'name': 'Team A', 'guest_count': 5, 'leader_name': 'Alice'},
            {'name': 'Team B', 'guest_count': 5, 'leader_name': 'Bob'}
        ]
        resp_over = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t08.table_number}),
            data={
                'action': 'create_split_groups',
                'party_size': 10,
                'groups_data': json.dumps(groups_payload_over)
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_over.status_code, 400)
        data_over = resp_over.json()
        self.assertEqual(data_over['status'], 'error')
        self.assertIn('exceeds table capacity', data_over['message'])

    # ------------------------------------------------------------------------
    # Test 24: Table & Seat Non-Occupancy On Group Setup Rule
    # ------------------------------------------------------------------------
    def test_24_table_non_occupancy_on_group_setup(self):
        """Test Case 24: Critical Rule - Setting up group or party size leaves table AVAILABLE and 0 seats occupied until order submission."""
        # 1. Setup single group
        resp_create = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'create_group',
                'party_size': 4,
                'customer_name': 'Rahul'
            }
        )
        self.assertEqual(resp_create.status_code, 302)

        # Table and seats must remain completely unallocated
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 0)
        self.assertEqual(self.table_t02.status, DiningTable.Status.AVAILABLE)
        self.assertEqual(self.table_t02.seats.filter(status=RestaurantSeat.Status.OCCUPIED).count(), 0)

        # 2. Add item to cart
        session = CustomerSession.objects.get(table=self.table_t02, customer_name='Rahul')
        leader = session.leader
        cart = SessionCart.objects.get(session=session)
        SessionCartItem.objects.create(
            cart=cart,
            menu_item=self.item_biryani,
            quantity=2,
            added_by=leader,
            added_by_name='Rahul'
        )

        # Even with items in cart, table must NOT be occupied
        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 0)
        self.assertEqual(self.table_t02.status, DiningTable.Status.AVAILABLE)

        # 3. Only submitting order to kitchen occupies table and seats
        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        resp_submit = self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(resp_submit.status_code, 200)

        self.table_t02.refresh_from_db()
        self.assertEqual(self.table_t02.occupied_seats, 4)
        self.assertEqual(self.table_t02.status, DiningTable.Status.OCCUPIED)
        self.assertEqual(self.table_t02.seats.filter(status=RestaurantSeat.Status.OCCUPIED).count(), 4)

    # ------------------------------------------------------------------------
    # Test 25: 1 Person Direct Menu Flow (Flow A)
    # ------------------------------------------------------------------------
    def test_25_single_person_direct_menu_entry(self):
        """Test Case 25: 1 Person selection directly creates individual table session and enters menu without group prompts."""
        resp = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'create_group',
                'party_size': 1,
                'customer_name': 'Solo Diner'
            }
        )
        self.assertEqual(resp.status_code, 302)
        sess = CustomerSession.objects.filter(table=self.table_t02, customer_name='Solo Diner').first()
        self.assertIsNotNone(sess)
        self.assertEqual(sess.guest_count, 1)
        self.assertTrue(sess.session_name.startswith('Table'))

    # ------------------------------------------------------------------------
    # Test 26: Split Table Direct 6-Digit Code Joining (Flow C)
    # ------------------------------------------------------------------------
    def test_26_split_table_code_joining_validation(self):
        """Test Case 26: Split Table customer joins existing group with 6-digit code with no extra configuration."""
        # Leader creates group with capacity 3
        sess = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Friends',
            customer_name='Leader Alex',
            guest_count=3,
            status=CustomerSession.Status.ACTIVE
        )
        GroupMember.objects.create(session=sess, name='Leader Alex', role=GroupMember.Role.LEADER, can_submit_order=True)

        # Companion enters only the 6-digit code
        resp_join = self.client_arun.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': sess.join_code,
                'customer_name': ''  # No display name requested
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_join.status_code, 200)
        data = resp_join.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('Successfully joined your group', data['message'])

        # Verify membership
        self.assertEqual(sess.members.filter(is_active=True).count(), 2)

    # ------------------------------------------------------------------------
    # Test 27: Split Table Rejection Rules (Other Table, Other Restaurant, Full Capacity)
    # ------------------------------------------------------------------------
    def test_27_split_table_rejection_rules(self):
        """Test Case 27: Code belonging to other table, other restaurant, or full group is securely rejected."""
        # 1. Other table session
        sess_t08 = CustomerSession.objects.create(
            table=self.table_t08,
            session_name='T08 Group',
            customer_name='T08 Leader',
            guest_count=4,
            status=CustomerSession.Status.ACTIVE
        )
        # Attempt to use T-08 code at Table T-02
        resp_wrong_table = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': sess_t08.join_code
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_wrong_table.status_code, 400)
        self.assertIn('belongs to Table T-08', resp_wrong_table.json()['message'])

        # 2. Other restaurant session
        sess_other_rest = CustomerSession.objects.create(
            table=self.table_other,
            session_name='Other Rest Group',
            customer_name='Other Leader',
            guest_count=4,
            status=CustomerSession.Status.ACTIVE
        )
        resp_wrong_rest = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': sess_other_rest.join_code
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_wrong_rest.status_code, 400)
        self.assertEqual(resp_wrong_rest.json()['message'], 'Invalid invitation code. Please check the code and try again.')

        # 3. Maximum member capacity reached
        sess_full = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='Duo',
            customer_name='Leader',
            guest_count=2,
            status=CustomerSession.Status.ACTIVE
        )
        GroupMember.objects.create(session=sess_full, name='Leader', role=GroupMember.Role.LEADER)
        GroupMember.objects.create(session=sess_full, name='Companion', role=GroupMember.Role.MEMBER)

        # 3rd person attempts to join full 2-person group
        resp_full = self.client_priya.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': sess_full.join_code
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_full.status_code, 400)
        self.assertIn('full', resp_full.json()['message'].lower())

    def test_leader_3_member_capacity_and_remove_member_flow(self):
        """
        Verify that:
        1. A group created for 3 members allows exactly 3 members (Leader + 2 companions).
        2. A 4th member is blocked with an informative message.
        3. The leader can remove a member.
        4. The removed member is deactivated and redirected to the session_select page.
        5. Removing a member frees up the slot so another companion can join.
        """
        # Step 1: Leader (Rahul) creates a group for 3 members at Table T-02
        resp_create = self.client_rahul.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'create_group',
                'party_size': 3,
                'guest_count': 3,
                'customer_name': 'Rahul'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_create.status_code, 200)
        group_data = resp_create.json()['group']
        join_code = group_data['join_code']
        session_id = group_data['id']
        session = CustomerSession.objects.get(id=session_id)
        self.assertEqual(session.guest_count, 3)
        self.assertEqual(session.active_members.count(), 1)

        # Step 2: 2nd member (Arun) joins using the 6-digit code
        resp_arun = self.client_arun.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': join_code,
                'customer_name': 'Arun'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_arun.status_code, 200)
        self.assertEqual(session.active_members.count(), 2)
        arun_member = session.members.get(name='Arun')
        self.assertTrue(arun_member.is_active)

        # Step 3: 3rd member (Priya) joins using the 6-digit code
        resp_priya = self.client_priya.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': join_code,
                'customer_name': 'Priya'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_priya.status_code, 200)
        self.assertEqual(session.active_members.count(), 3)
        priya_member = session.members.get(name='Priya')
        self.assertTrue(priya_member.is_active)

        # Step 4: 4th member (Client New) attempts to join -> MUST BE BLOCKED
        client_new = Client()
        resp_4th = client_new.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': join_code,
                'customer_name': 'David'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_4th.status_code, 400)
        self.assertIn('full', resp_4th.json()['message'].lower())
        self.assertIn('3 members', resp_4th.json()['message'].lower())
        self.assertEqual(session.active_members.count(), 3)

        # Step 5: Non-leader (Arun) tries to remove Priya -> BLOCKED (403)
        resp_unauth_remove = self.client_arun.post(
            reverse('tables:api_group_member_remove', kwargs={'table_number': self.table_t02.table_number, 'member_id': priya_member.id})
        )
        self.assertEqual(resp_unauth_remove.status_code, 403)

        # Step 6: Leader (Rahul) removes Priya from the group
        resp_remove = self.client_rahul.post(
            reverse('tables:api_group_member_remove', kwargs={'table_number': self.table_t02.table_number, 'member_id': priya_member.id})
        )
        self.assertEqual(resp_remove.status_code, 200)
        priya_member.refresh_from_db()
        self.assertFalse(priya_member.is_active)
        self.assertEqual(session.active_members.count(), 2)

        # Step 7: Removed member (Priya) tries to access the table order page
        # Should be returned to the table start page (session_select.html)
        resp_priya_order = self.client_priya.get(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number})
        )
        self.assertEqual(resp_priya_order.status_code, 200)
        self.assertTemplateUsed(resp_priya_order, 'tables/session_select.html')

        # Polling by removed member returns 'removed' status
        resp_priya_poll = self.client_priya.get(
            reverse('tables:api_group_cart_sync', kwargs={'table_number': self.table_t02.table_number})
        )
        self.assertEqual(resp_priya_poll.status_code, 200)
        self.assertEqual(resp_priya_poll.json()['status'], 'removed')

        # Step 8: With Priya removed, capacity is 2/3. Now 4th member (David) CAN join!
        resp_david = client_new.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': join_code,
                'customer_name': 'David'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_david.status_code, 200)
        self.assertEqual(session.active_members.count(), 3)
        david_member = session.members.get(name='David')
        self.assertTrue(david_member.is_active)

        # Step 9: 5th person is now blocked
        client_5th = Client()
        resp_5th = client_5th.post(
            reverse('tables:customer_order', kwargs={'table_number': self.table_t02.table_number}),
            data={
                'action': 'join_group',
                'join_code': join_code,
                'customer_name': 'Eve'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp_5th.status_code, 400)
        self.assertIn('full', resp_5th.json()['message'].lower())

    def test_28_three_member_group_extra_food_single_bill(self):
        """
        Verify:
        If a user creates a 3 members group and one of the persons adds extra food,
        do NOT show a separate bill; everything is consolidated into a single bill and active order.
        """
        # Step 1: Leader (Tamilselvan) creates a 3-member group session
        session = CustomerSession.objects.create(
            table=self.table_t02,
            session_name='The Trio',
            customer_name='Tamilselvan',
            guest_count=3,
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(session=session, name='Tamilselvan', role=GroupMember.Role.LEADER, can_submit_order=True)
        # Dhanusha and John join
        dhanusha = GroupMember.objects.create(session=session, name='Dhanusha', role=GroupMember.Role.MEMBER, can_submit_order=False)
        john = GroupMember.objects.create(session=session, name='John', role=GroupMember.Role.MEMBER, can_submit_order=False)

        cart = SessionCart.objects.create(session=session)
        # Tamilselvan adds 1x Biryani, Dhanusha adds 1x Paneer
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_biryani, quantity=1, added_by=leader, added_by_name='Tamilselvan')
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_paneer, quantity=1, added_by=dhanusha, added_by_name='Dhanusha')

        # Leader submits initial order
        self._set_member_cookies(self.client_rahul, self.table_t02, session, leader)
        resp_submit1 = self.client_rahul.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(resp_submit1.status_code, 200)

        orders = Order.objects.filter(session=session)
        self.assertEqual(orders.count(), 1)
        initial_order = orders.first()
        self.assertEqual(initial_order.items.count(), 2)

        # Step 2: Dhanusha adds extra food (1x Lime Juice)
        SessionCartItem.objects.create(cart=cart, menu_item=self.item_lime_juice, quantity=1, added_by=dhanusha, added_by_name='Dhanusha')

        # Dhanusha submits the extra food
        client_dhanusha = Client()
        self._set_member_cookies(client_dhanusha, self.table_t02, session, dhanusha)
        resp_submit2 = client_dhanusha.post(
            reverse('tables:api_group_submit_order', kwargs={'table_number': self.table_t02.table_number}),
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(resp_submit2.status_code, 200)

        # VERIFY: Exactly 1 order in the session (no duplicate order, no separate bill)
        orders_after = Order.objects.filter(session=session)
        self.assertEqual(orders_after.count(), 1)

        single_order = orders_after.first()
        self.assertEqual(single_order.id, initial_order.id)
        self.assertEqual(single_order.order_number, initial_order.order_number)

        # Single order contains 3 items (2 initial + 1 extra)
        self.assertEqual(single_order.items.count(), 3)
        self.assertTrue(single_order.has_unseen_extra)
        self.assertTrue(single_order.has_extra_items)

        extra_item = single_order.items.filter(menu_item=self.item_lime_juice).first()
        self.assertIsNotNone(extra_item)
        self.assertTrue(extra_item.is_extra)
        self.assertEqual(extra_item.added_by_name, 'Dhanusha')

        # Session invoice has single bill matching order total
        invoice = session.invoices.first()
        self.assertEqual(invoice.total_amount, single_order.total_amount)

        # Verify Customer Shop page renders exactly 1 order card and 1 bill
        resp_shop = client_dhanusha.get(reverse('tables:customer_shop', kwargs={'table_number': self.table_t02.table_number}))
        self.assertEqual(resp_shop.status_code, 200)
        self.assertEqual(len(resp_shop.context['session_orders']), 1)
        content = resp_shop.content.decode('utf-8')
        self.assertIn('My Consolidated Bill', content)
        self.assertIn('Added by Dhanusha', content)
        self.assertIn('+EXTRA', content)

