from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from billing.models import Invoice, Payment, BillRequest
from tables.models import DiningTable, CustomerSession, RestaurantSeat, SessionSeatAllocation, GroupMember
from orders.models import Order, OrderItem
from menu.models import Category, MenuItem


class BillRequestManagementTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Restaurants
        self.restaurant_a = Restaurant.objects.create(name="Restaurant A", code="REST-A")
        self.restaurant_b = Restaurant.objects.create(name="Restaurant B", code="REST-B")

        # Admin & Cashier for Restaurant A
        self.admin_a = User.objects.create_user(
            username='admin_a',
            email='admin_a@rest.com',
            password='password123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant_a
        )
        self.cashier_a = User.objects.create_user(
            username='cashier_a',
            email='cashier_a@rest.com',
            password='password123',
            role=User.Role.CASHIER,
            restaurant=self.restaurant_a
        )

        # Admin for Restaurant B
        self.admin_b = User.objects.create_user(
            username='admin_b',
            email='admin_b@rest.com',
            password='password123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant_b
        )

        # Category & Menu Items
        self.category = Category.objects.create(name="Mains", slug="mains")
        self.biryani = MenuItem.objects.create(category=self.category, name="Chicken Biryani", price=Decimal('240.00'))
        self.juice = MenuItem.objects.create(category=self.category, name="Fresh Lime Juice", price=Decimal('60.00'))
        self.paneer = MenuItem.objects.create(category=self.category, name="Paneer Butter Masala", price=Decimal('220.00'))
        self.naan = MenuItem.objects.create(category=self.category, name="Butter Naan", price=Decimal('50.00'))

        # Tables
        self.table_1 = DiningTable.objects.create(table_number='T05', capacity=6)
        self.table_2 = DiningTable.objects.create(table_number='T06', capacity=4)

        # Customer Sessions at Table T05 (Multiple independent groups at same table)
        self.session_a = CustomerSession.objects.create(
            table=self.table_1,
            session_name="Group A",
            customer_name="Alice",
            guest_count=2
        )
        self.session_b = CustomerSession.objects.create(
            table=self.table_1,
            session_name="Group B",
            customer_name="Bob",
            guest_count=2
        )

        # Orders for Group A: 2x Biryani (480) + 1x Juice (60) = 540
        self.order_a = Order.objects.create(
            table=self.table_1,
            session=self.session_a,
            order_type=Order.OrderType.DINE_IN,
            server=self.admin_a
        )
        OrderItem.objects.create(order=self.order_a, menu_item=self.biryani, quantity=2, unit_price=Decimal('240.00'))
        OrderItem.objects.create(order=self.order_a, menu_item=self.juice, quantity=1, unit_price=Decimal('60.00'))
        self.order_a.calculate_totals(save=True)

        # Orders for Group B: 1x Paneer (220) + 2x Naan (100) = 320
        self.order_b = Order.objects.create(
            table=self.table_1,
            session=self.session_b,
            order_type=Order.OrderType.DINE_IN,
            server=self.admin_a
        )
        OrderItem.objects.create(order=self.order_b, menu_item=self.paneer, quantity=1, unit_price=Decimal('220.00'))
        OrderItem.objects.create(order=self.order_b, menu_item=self.naan, quantity=2, unit_price=Decimal('50.00'))
        self.order_b.calculate_totals(save=True)

    def test_01_customer_requests_bill_and_prevents_duplicate(self):
        """Customer submits bill request; subsequent request does not duplicate."""
        url = reverse('tables:customer_request_bill', args=[self.table_1.table_number, self.session_a.session_token])
        resp = self.client.post(url, {'from': 'shop'})
        self.assertEqual(resp.status_code, 302)

        # Check BillRequest created
        self.assertEqual(BillRequest.objects.filter(session=self.session_a).count(), 1)
        req = BillRequest.objects.get(session=self.session_a)
        self.assertEqual(req.status, BillRequest.Status.PENDING)
        self.assertEqual(req.table, self.table_1)
        self.assertGreater(req.total_amount, Decimal('500.00'))

        # Submitting again should NOT create duplicate request
        resp2 = self.client.post(url, {'from': 'shop'})
        self.assertEqual(BillRequest.objects.filter(session=self.session_a).count(), 1)

        # Verify customer_shop redirects to thank-you departure confirmation screen
        self.client.cookies[f'rms_session_{self.table_1.id}'] = self.session_a.session_token
        shop_resp = self.client.get(reverse('tables:customer_shop', args=[self.table_1.table_number]))
        self.assertEqual(shop_resp.status_code, 302)
        self.assertIn('thank-you', shop_resp.url)
        self.assertIn('bill_requested=1', shop_resp.url)

        # Verify customer_thank_you displays the requested confirmation details
        thank_you_resp = self.client.get(shop_resp.url)
        self.assertEqual(thank_you_resp.status_code, 200)
        self.assertTrue(thank_you_resp.context['bill_requested'])
        self.assertIsNotNone(thank_you_resp.context['active_bill_request'])
        self.assertEqual(thank_you_resp.context['active_bill_request'].id, req.id)
        self.assertContains(thank_you_resp, "Welcome Back!")
        self.assertContains(thank_you_resp, "Your bill will arrive at your table within 5 mins.")
        self.assertContains(thank_you_resp, "After collecting your bill, go and pay your amount to cashiers.")

        # Verify table seats are freed and table status becomes AVAILABLE immediately
        self.table_1.refresh_from_db()
        self.assertEqual(self.table_1.status, DiningTable.Status.AVAILABLE)
        self.assertEqual(self.table_1.occupied_seats_count, 0)

    def test_02_multiple_groups_at_same_table_independent_requests(self):
        """Group A and Group B at Table T05 generate independent requests."""
        url_a = reverse('tables:customer_request_bill', args=[self.table_1.table_number, self.session_a.session_token])
        url_b = reverse('tables:customer_request_bill', args=[self.table_1.table_number, self.session_b.session_token])

        self.client.post(url_a)
        self.client.post(url_b)

        self.assertEqual(BillRequest.objects.count(), 2)
        req_a = BillRequest.objects.get(session=self.session_a)
        req_b = BillRequest.objects.get(session=self.session_b)

        # Both at Table T05 but with different session names & amounts
        self.assertEqual(req_a.table, self.table_1)
        self.assertEqual(req_b.table, self.table_1)
        self.assertEqual(req_a.session.session_name, "Group A")
        self.assertEqual(req_b.session.session_name, "Group B")
        self.assertNotEqual(req_a.total_amount, req_b.total_amount)

    def test_03_admin_and_cashier_can_view_bill_requests(self):
        """Both Admin and Cashier can view the bill requests dashboard and details."""
        # Create a pending request
        BillRequest.objects.create(
            restaurant=self.restaurant_a,
            table=self.table_1,
            session=self.session_a,
            total_amount=Decimal('540.00'),
            balance_due=Decimal('540.00'),
            status=BillRequest.Status.PENDING
        )

        list_url = reverse('billing:bill_request_list')

        # Admin A views dashboard
        self.client.force_login(self.admin_a)
        resp_admin = self.client.get(list_url)
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Table T05")
        self.assertContains(resp_admin, "Group A")

        # Cashier A views dashboard
        self.client.force_login(self.cashier_a)
        resp_cashier = self.client.get(list_url)
        self.assertEqual(resp_cashier.status_code, 200)
        self.assertContains(resp_cashier, "Table T05")

    def test_04_admin_accepts_request_and_cashier_cannot_accept_again(self):
        """Admin accepts request; Cashier sees accepted by Admin and cannot re-accept."""
        req = BillRequest.objects.create(
            restaurant=self.restaurant_a,
            table=self.table_1,
            session=self.session_a,
            total_amount=Decimal('540.00'),
            balance_due=Decimal('540.00'),
            status=BillRequest.Status.PENDING
        )

        accept_url = reverse('billing:bill_request_accept', args=[req.pk])

        # Admin accepts
        self.client.force_login(self.admin_a)
        resp = self.client.post(accept_url)
        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, BillRequest.Status.ACCEPTED)
        self.assertEqual(req.accepted_by, self.admin_a)
        self.assertIsNotNone(req.accepted_at)

        # Cashier tries to accept same request
        self.client.force_login(self.cashier_a)
        resp_cashier = self.client.post(accept_url, follow=True)
        # Should display warning that it was already accepted
        self.assertContains(resp_cashier, "already been accepted")
        req.refresh_from_db()
        self.assertEqual(req.accepted_by, self.admin_a)  # Still admin A

    def test_05_cashier_accepts_different_pending_request(self):
        """Cashier can accept a different pending request."""
        req2 = BillRequest.objects.create(
            restaurant=self.restaurant_a,
            table=self.table_1,
            session=self.session_b,
            total_amount=Decimal('320.00'),
            balance_due=Decimal('320.00'),
            status=BillRequest.Status.PENDING
        )

        accept_url = reverse('billing:bill_request_accept', args=[req2.pk])
        self.client.force_login(self.cashier_a)
        resp = self.client.post(accept_url)
        self.assertEqual(resp.status_code, 302)

        req2.refresh_from_db()
        self.assertEqual(req2.status, BillRequest.Status.ACCEPTED)
        self.assertEqual(req2.accepted_by, self.cashier_a)

    def test_06_generate_bill_and_payment_status_synchronization(self):
        """Generating bill sets BILL_GENERATED; payment completion synchronizes status to PAID."""
        req = BillRequest.objects.create(
            restaurant=self.restaurant_a,
            table=self.table_1,
            session=self.session_a,
            total_amount=Decimal('540.00'),
            balance_due=Decimal('540.00'),
            status=BillRequest.Status.ACCEPTED,
            accepted_by=self.cashier_a
        )

        self.client.force_login(self.cashier_a)

        # Generate bill
        gen_url = reverse('billing:bill_request_generate_bill', args=[req.pk])
        resp = self.client.post(gen_url)
        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, BillRequest.Status.BILL_GENERATED)
        self.assertIsNotNone(req.invoice)

        # Settle invoice with payment
        invoice = req.invoice
        Payment.objects.create(
            invoice=invoice,
            amount=invoice.total_amount,
            payment_method=Payment.PaymentMethod.CASH,
            status=Payment.Status.COMPLETED
        )

        req.refresh_from_db()
        self.assertEqual(req.status, BillRequest.Status.PAID)
        self.assertEqual(req.balance_due, Decimal('0.00'))
        self.assertIsNotNone(req.completed_at)

    def test_07_cross_restaurant_isolation(self):
        """Admin B cannot view or accept bill requests belonging to Restaurant A."""
        req = BillRequest.objects.create(
            restaurant=self.restaurant_a,
            table=self.table_1,
            session=self.session_a,
            total_amount=Decimal('540.00'),
            balance_due=Decimal('540.00'),
            status=BillRequest.Status.PENDING
        )

        self.client.force_login(self.admin_b)

        # List view should not show Restaurant A's request
        list_resp = self.client.get(reverse('billing:bill_request_list'))
        self.assertNotContains(list_resp, req.request_number)

        # Detail view should be forbidden
        detail_resp = self.client.get(reverse('billing:bill_request_detail', args=[req.pk]))
        self.assertEqual(detail_resp.status_code, 302)

        # Accept action should be blocked
        accept_resp = self.client.post(reverse('billing:bill_request_accept', args=[req.pk]))
        self.assertEqual(accept_resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, BillRequest.Status.PENDING)

    def test_08_three_member_group_bill_request_redirects_all_members_to_table_page(self):
        """
        When 1 member of a 3-member dining group requests the bill:
        1. The requesting member is redirected to customer_thank_you?bill_requested=1.
        2. Real-time broadcast fires with redirect destination.
        3. Member 2 (menu polling) and Member 3 (shop polling) both receive bill_requested status and redirect URL.
        4. Customer thank-you page displays the required departure messages and countdown/link to /tables/<table_number>/order/?switch=1.
        5. Table session cookies are wiped so visiting /tables/<table_number>/order/?switch=1 displays table setup (session_select.html).
        """
        # Create 3-member dining session
        session_3 = CustomerSession.objects.create(
            table=self.table_1,
            guest_count=3,
            session_name="Group 3",
            customer_name="Member 1 (Leader)",
            status=CustomerSession.Status.ACTIVE
        )
        leader = GroupMember.objects.create(
            session=session_3,
            name="Member 1 (Leader)",
            role=GroupMember.Role.LEADER,
            is_active=True
        )
        member_2 = GroupMember.objects.create(
            session=session_3,
            name="Member 2",
            role=GroupMember.Role.MEMBER,
            is_active=True
        )
        member_3 = GroupMember.objects.create(
            session=session_3,
            name="Member 3",
            role=GroupMember.Role.MEMBER,
            is_active=True
        )

        client_1 = Client()
        client_1.cookies[f'rms_session_{self.table_1.id}'] = session_3.session_token
        client_1.cookies[f'rms_member_{self.table_1.id}'] = leader.member_token

        client_2 = Client()
        client_2.cookies[f'rms_session_{self.table_1.id}'] = session_3.session_token
        client_2.cookies[f'rms_member_{self.table_1.id}'] = member_2.member_token

        client_3 = Client()
        client_3.cookies[f'rms_session_{self.table_1.id}'] = session_3.session_token
        client_3.cookies[f'rms_member_{self.table_1.id}'] = member_3.member_token

        # Step 1: Member 1 clicks Request for Bill
        req_url = reverse('tables:customer_request_bill', args=[self.table_1.table_number, session_3.session_token])
        resp_1 = client_1.post(req_url, {'from': 'shop'})
        self.assertEqual(resp_1.status_code, 302)
        expected_dest = reverse('tables:customer_thank_you', args=[self.table_1.table_number]) + '?bill_requested=1'
        self.assertEqual(resp_1.url, expected_dest)

        # Verify session status is updated to BILL_REQUESTED
        session_3.refresh_from_db()
        self.assertEqual(session_3.status, CustomerSession.Status.BILL_REQUESTED)

        # Step 2: Member 2 on menu polling receives bill_requested status
        sync_url = reverse('tables:group_cart_sync', args=[self.table_1.table_number])
        resp_2 = client_2.get(sync_url)
        self.assertEqual(resp_2.status_code, 200)
        data_2 = resp_2.json()
        self.assertEqual(data_2.get('status'), 'bill_requested')
        self.assertTrue(data_2.get('bill_requested'))
        self.assertEqual(data_2.get('redirect_url'), expected_dest)

        # Step 3: Member 3 on shop live polling receives bill_requested status
        shop_live_url = reverse('tables:customer_shop_live', args=[self.table_1.table_number])
        resp_3 = client_3.get(shop_live_url)
        self.assertEqual(resp_3.status_code, 200)
        data_3 = resp_3.json()
        self.assertTrue(data_3.get('bill_requested'))
        self.assertEqual(data_3.get('redirect_url'), expected_dest)

        # Step 4: Member 2 and Member 3 navigate to the thank-you screen
        thank_you_2 = client_2.get(expected_dest)
        self.assertEqual(thank_you_2.status_code, 200)
        self.assertContains(thank_you_2, "Welcome Back!")
        self.assertContains(thank_you_2, "Your bill will arrive at your table within 5 mins.")
        self.assertContains(thank_you_2, "After collecting your bill, go and pay your amount to cashiers.")
        # Target URL points to table setup page
        self.assertContains(thank_you_2, f"/tables/{self.table_1.table_number}/order/?switch=1")

        # Step 5: After cookies are wiped, navigating to table page shows table setup (session_select.html)
        order_page_2 = client_2.get(reverse('tables:customer_order', args=[self.table_1.table_number]) + '?switch=1')
        self.assertEqual(order_page_2.status_code, 200)
        self.assertContains(order_page_2, "How many guests?")
