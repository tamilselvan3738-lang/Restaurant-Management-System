from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import Restaurant, User
from menu.models import Category, MenuItem
from tables.models import DiningTable, CustomerSession, RestaurantSeat, ServiceRequest
from orders.models import Order, OrderItem
from billing.models import Invoice, Payment


class MultiTenantIsolationTestSuite(TestCase):
    """
    Comprehensive Automated Unit Test Suite for Multi-Tenant SaaS Architecture.
    Covers the 14 mandatory isolation test cases:
      1. Admin Creation Test
      2. Dashboard Isolation Test
      3. Employee Isolation Test
      4. Menu Isolation Test
      5. Table Isolation Test
      6. Order Isolation Test
      7. Billing Isolation Test
      8. Service Request Isolation Test
      9. QR Isolation Test
      10. Notification Isolation Test
      11. API Isolation Test
      12. Super Admin Global Access & View as Restaurant Test
      13. Tenant-Aware Usernames Test
      14. Tenant Suspension Lifecycle Test
    """

    def setUp(self):
        # 1. Create Super Admin
        self.superadmin = User.objects.create_superuser(
            username='global_superadmin',
            email='superadmin@growwpark.com',
            password='SuperPassword123!',
            role=User.Role.ADMIN
        )

        # 2. Create Restaurant A & Restaurant B
        self.rest_a = Restaurant.objects.create(
            name="Grand Bistro",
            phone="111-222-3333",
            email="contact@bistro.com",
            address="100 First Ave",
            status=Restaurant.RestaurantStatus.ACTIVE,
            plan=Restaurant.SubscriptionPlan.PREMIUM
        )
        self.rest_b = Restaurant.objects.create(
            name="Spice Palace",
            phone="444-555-6666",
            email="contact@spicepalace.com",
            address="200 Second Ave",
            status=Restaurant.RestaurantStatus.ACTIVE,
            plan=Restaurant.SubscriptionPlan.STARTER
        )

        # 3. Create Restaurant Admins
        self.admin_a = User.objects.create_user(
            username='admin_bistro',
            email='admin@bistro.com',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            restaurant=self.rest_a
        )
        self.admin_b = User.objects.create_user(
            username='admin_spice',
            email='admin@spicepalace.com',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            restaurant=self.rest_b
        )

        # 4. Create Staff for each tenant
        self.waiter_a = User.objects.create_user(
            username='waiter01',
            email='waiter1@bistro.com',
            password='WaiterPassword123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )
        self.waiter_b = User.objects.create_user(
            username='waiter02',
            email='waiter2@spicepalace.com',
            password='WaiterPassword123!',
            role=User.Role.WAITER,
            restaurant=self.rest_b
        )

        # 5. Tables for each tenant (both can have table T-01)
        self.table_a = DiningTable.objects.create(
            restaurant=self.rest_a,
            table_number='T-01',
            capacity=4,
            status=DiningTable.Status.AVAILABLE
        )
        self.table_b = DiningTable.objects.create(
            restaurant=self.rest_b,
            table_number='T-01',
            capacity=4,
            status=DiningTable.Status.AVAILABLE
        )

        # 6. Categories & Menu Items for each tenant
        self.cat_a = Category.objects.create(
            restaurant=self.rest_a,
            name='Italian Dishes'
        )
        self.item_a = MenuItem.objects.create(
            restaurant=self.rest_a,
            category=self.cat_a,
            name='Truffle Pizza',
            price=Decimal('25.00')
        )

        self.cat_b = Category.objects.create(
            restaurant=self.rest_b,
            name='Indian Curries'
        )
        self.item_b = MenuItem.objects.create(
            restaurant=self.rest_b,
            category=self.cat_b,
            name='Butter Chicken',
            price=Decimal('18.00')
        )

        # 7. Orders for each tenant
        self.order_a = Order.objects.create(
            restaurant=self.rest_a,
            table=self.table_a,
            server=self.waiter_a,
            customer_name='Guest Bistro',
            status=Order.Status.PENDING,
            subtotal_amount=Decimal('25.00'),
            total_amount=Decimal('27.00')
        )
        OrderItem.objects.create(
            order=self.order_a,
            menu_item=self.item_a,
            quantity=1,
            unit_price=Decimal('25.00')
        )

        self.order_b = Order.objects.create(
            restaurant=self.rest_b,
            table=self.table_b,
            server=self.waiter_b,
            customer_name='Guest Spice',
            status=Order.Status.PENDING,
            subtotal_amount=Decimal('18.00'),
            total_amount=Decimal('19.50')
        )
        OrderItem.objects.create(
            order=self.order_b,
            menu_item=self.item_b,
            quantity=1,
            unit_price=Decimal('18.00')
        )

        # 8. Invoices for each tenant
        self.invoice_a = Invoice.objects.create(
            restaurant=self.rest_a,
            order=self.order_a,
            table=self.table_a,
            customer_name='Guest Bistro',
            subtotal=Decimal('25.00'),
            total_amount=Decimal('27.00'),
            status=Invoice.Status.PAID
        )
        self.invoice_b = Invoice.objects.create(
            restaurant=self.rest_b,
            order=self.order_b,
            table=self.table_b,
            customer_name='Guest Spice',
            subtotal=Decimal('18.00'),
            total_amount=Decimal('19.50'),
            status=Invoice.Status.UNPAID
        )

        # Dedicated test clients
        self.client_a = Client()
        self.client_a.force_login(self.admin_a)

        self.client_b = Client()
        self.client_b.force_login(self.admin_b)

        self.client_super = Client()
        self.client_super.force_login(self.superadmin)

    # -------------------------------------------------------------------------
    # TEST 1: Admin Creation Test
    # -------------------------------------------------------------------------
    def test_01_admin_creation_association(self):
        """Verify Admin A is associated with Restaurant A, Admin B with Restaurant B."""
        self.assertEqual(self.admin_a.restaurant, self.rest_a)
        self.assertEqual(self.admin_b.restaurant, self.rest_b)
        self.assertNotEqual(self.admin_a.restaurant, self.admin_b.restaurant)
        self.assertEqual(self.rest_a.status, Restaurant.RestaurantStatus.ACTIVE)
        self.assertEqual(self.rest_b.status, Restaurant.RestaurantStatus.ACTIVE)

    # -------------------------------------------------------------------------
    # TEST 2: Dashboard Isolation Test
    # -------------------------------------------------------------------------
    def test_02_dashboard_statistics_isolation(self):
        """Verify Restaurant A's dashboard displays only Restaurant A's metrics."""
        response_a = self.client_a.get(reverse('dashboard:index'))
        self.assertEqual(response_a.status_code, 200)
        # Restaurant A has paid revenue of 27.00
        self.assertContains(response_a, "Grand Bistro")
        self.assertNotContains(response_a, "Spice Palace")

        response_b = self.client_b.get(reverse('dashboard:index'))
        self.assertEqual(response_b.status_code, 200)
        self.assertContains(response_b, "Spice Palace")
        self.assertNotContains(response_b, "Grand Bistro")

    # -------------------------------------------------------------------------
    # TEST 3: Employee Isolation Test
    # -------------------------------------------------------------------------
    def test_03_employee_isolation(self):
        """Verify Admin A cannot view, edit or delete Admin B's employees."""
        # Admin A views employee list
        res_a = self.client_a.get(reverse('accounts:staff_list'))
        self.assertEqual(res_a.status_code, 200)
        self.assertContains(res_a, self.waiter_a.username)
        self.assertNotContains(res_a, self.waiter_b.username)

        # Admin A attempts to access Admin B's waiter edit page -> returns 404
        res_tamper = self.client_a.get(reverse('accounts:staff_edit', args=[self.waiter_b.id]))
        self.assertEqual(res_tamper.status_code, 404)

        # Admin A attempts to delete Admin B's waiter -> returns 404 and waiter survives
        res_del = self.client_a.post(reverse('accounts:staff_delete', args=[self.waiter_b.id]))
        self.assertEqual(res_del.status_code, 404)
        self.assertTrue(User.objects.filter(id=self.waiter_b.id).exists())

    # -------------------------------------------------------------------------
    # TEST 4: Menu Isolation Test
    # -------------------------------------------------------------------------
    def test_04_menu_isolation(self):
        """Verify a dish created by Restaurant A is not visible in Restaurant B's menu."""
        res_a = self.client_a.get(reverse('menu:list'))
        self.assertContains(res_a, 'Truffle Pizza')
        self.assertNotContains(res_a, 'Butter Chicken')

        res_b = self.client_b.get(reverse('menu:list'))
        self.assertContains(res_b, 'Butter Chicken')
        self.assertNotContains(res_b, 'Truffle Pizza')

        # Admin B tries to edit Admin A's menu item via URL ID manipulation -> returns 404
        res_edit = self.client_b.get(reverse('menu:item_edit', args=[self.item_a.id]))
        self.assertEqual(res_edit.status_code, 404)

    # -------------------------------------------------------------------------
    # TEST 5: Table Isolation Test
    # -------------------------------------------------------------------------
    def test_05_table_number_isolation(self):
        """Verify Restaurant A and Restaurant B can have Table T-01 independently."""
        self.assertEqual(self.table_a.table_number, 'T-01')
        self.assertEqual(self.table_b.table_number, 'T-01')
        self.assertEqual(self.table_a.restaurant, self.rest_a)
        self.assertEqual(self.table_b.restaurant, self.rest_b)

        # Floor plan for Admin A shows only 1 Table T-01
        res_a = self.client_a.get(reverse('tables:floor_plan'))
        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(len(res_a.context['tables']), 1)
        self.assertEqual(res_a.context['tables'][0].id, self.table_a.id)

    # -------------------------------------------------------------------------
    # TEST 6: Order Isolation Test
    # -------------------------------------------------------------------------
    def test_06_order_isolation_and_url_tampering(self):
        """Verify Admin B cannot view Restaurant A's order by changing the order ID in URL."""
        # Admin A can view own order
        res_a = self.client_a.get(reverse('orders:detail', args=[self.order_a.id]))
        self.assertEqual(res_a.status_code, 200)

        # Admin B requests Admin A's order ID -> strictly rejected with 404
        res_tamper = self.client_b.get(reverse('orders:detail', args=[self.order_a.id]))
        self.assertEqual(res_tamper.status_code, 404)

        # Admin B tries to update/cancel Admin A's order -> 404
        res_update = self.client_b.post(reverse('orders:update_status', args=[self.order_a.id]), {'status': Order.Status.CANCELLED})
        self.assertEqual(res_update.status_code, 404)

    # -------------------------------------------------------------------------
    # TEST 7: Billing Isolation Test
    # -------------------------------------------------------------------------
    def test_07_billing_isolation(self):
        """Verify bills/invoices generated in Restaurant A are invisible to Restaurant B."""
        # Invoice list for Admin A
        res_a = self.client_a.get(reverse('billing:list'))
        self.assertContains(res_a, self.invoice_a.invoice_number)
        self.assertNotContains(res_a, self.invoice_b.invoice_number)

        # Invoice list for Admin B
        res_b = self.client_b.get(reverse('billing:list'))
        self.assertContains(res_b, self.invoice_b.invoice_number)
        self.assertNotContains(res_b, self.invoice_a.invoice_number)

        # Admin B URL ID tampering on Admin A's invoice -> 404
        res_detail = self.client_b.get(reverse('billing:invoice_detail', args=[self.invoice_a.id]))
        self.assertEqual(res_detail.status_code, 404)

    # -------------------------------------------------------------------------
    # TEST 8: Service Request Isolation Test
    # -------------------------------------------------------------------------
    def test_08_service_request_isolation(self):
        """Verify service requests are routed and visible only to assigned restaurant's staff."""
        session_a = CustomerSession.objects.create(
            restaurant=self.rest_a,
            table=self.table_a,
            session_name="Group A",
            customer_name="Guest Bistro",
            status=CustomerSession.Status.ACTIVE
        )
        # Create service request in Restaurant A
        sr_a = ServiceRequest.objects.create(
            restaurant=self.rest_a,
            table=self.table_a,
            session=session_a,
            request_type=ServiceRequest.RequestType.WATER,
            quantity=2,
            status=ServiceRequest.Status.PENDING
        )

        client_waiter_a = Client()
        client_waiter_a.force_login(self.waiter_a)

        client_waiter_b = Client()
        client_waiter_b.force_login(self.waiter_b)

        # Waiter A sees request in waiter requests
        res_a = client_waiter_a.get(reverse('tables:waiter_requests'))
        self.assertContains(res_a, sr_a.request_number)

        # Waiter B does NOT see Restaurant A's request
        res_b = client_waiter_b.get(reverse('tables:waiter_requests'))
        self.assertNotContains(res_b, sr_a.request_number)

        # Waiter B attempts cross-tenant accept -> 404
        res_accept = client_waiter_b.post(reverse('tables:waiter_request_accept', args=[sr_a.id]))
        self.assertEqual(res_accept.status_code, 404)

    # -------------------------------------------------------------------------
    # TEST 9: QR Isolation Test
    # -------------------------------------------------------------------------
    def test_09_qr_code_menu_resolution(self):
        """Verify scanning Restaurant A's QR loads Restaurant A's menu and table."""
        anon_client = Client()

        # Customer scans Restaurant A's Table T-01 QR
        url_a = reverse('tables:customer_order', args=[self.table_a.table_number]) + f"?token={self.table_a.qr_token}"
        res_a = anon_client.get(url_a)
        self.assertEqual(res_a.status_code, 200)
        self.assertContains(res_a, "Grand Bistro")
        self.assertNotContains(res_a, "Spice Palace")

        # Start dining session and verify menu items are strictly isolated
        res_menu_a = anon_client.post(url_a, {'action': 'start_session', 'customer_name': 'Guest A', 'guest_count': 2}, follow=True)
        self.assertEqual(res_menu_a.status_code, 200)
        self.assertContains(res_menu_a, "Truffle Pizza")
        self.assertNotContains(res_menu_a, "Butter Chicken")

        # Customer scans Restaurant B's Table T-01 QR
        anon_client_b = Client()
        url_b = reverse('tables:customer_order', args=[self.table_b.table_number]) + f"?token={self.table_b.qr_token}"
        res_b = anon_client_b.get(url_b)
        self.assertEqual(res_b.status_code, 200)
        self.assertContains(res_b, "Spice Palace")
        self.assertNotContains(res_b, "Grand Bistro")

        res_menu_b = anon_client_b.post(url_b, {'action': 'start_session', 'customer_name': 'Guest B', 'guest_count': 2}, follow=True)
        self.assertEqual(res_menu_b.status_code, 200)
        self.assertContains(res_menu_b, "Butter Chicken")
        self.assertNotContains(res_menu_b, "Truffle Pizza")

    # -------------------------------------------------------------------------
    # TEST 10: Notification Isolation Test
    # -------------------------------------------------------------------------
    def test_10_notification_isolation(self):
        """Verify notifications and event logs are dispatched only to the target restaurant's staff."""
        # Order A exists in Restaurant A
        res_a = self.client_a.get(reverse('dashboard:notifications'))
        self.assertEqual(res_a.status_code, 200)
        self.assertContains(res_a, self.order_a.order_number)
        self.assertNotContains(res_a, self.order_b.order_number)

        # Order B exists in Restaurant B
        res_b = self.client_b.get(reverse('dashboard:notifications'))
        self.assertEqual(res_b.status_code, 200)
        self.assertContains(res_b, self.order_b.order_number)
        self.assertNotContains(res_b, self.order_a.order_number)

    # -------------------------------------------------------------------------
    # TEST 11: API Isolation Test
    # -------------------------------------------------------------------------
    def test_11_api_isolation_and_security(self):
        """Verify cross-tenant data requests on polling/live APIs return only tenant data."""
        # Kitchen API live feed
        res_k_a = self.client_a.get(reverse('kitchen:api_live'))
        data_a = res_k_a.json()
        order_numbers_a = [o['order_number'] for o in data_a.get('orders', [])]
        self.assertIn(self.order_a.order_number, order_numbers_a)
        self.assertNotIn(self.order_b.order_number, order_numbers_a)

        res_k_b = self.client_b.get(reverse('kitchen:api_live'))
        data_b = res_k_b.json()
        order_numbers_b = [o['order_number'] for o in data_b.get('orders', [])]
        self.assertIn(self.order_b.order_number, order_numbers_b)
        self.assertNotIn(self.order_a.order_number, order_numbers_b)

    # -------------------------------------------------------------------------
    # TEST 12: Super Admin Access & Impersonation Test
    # -------------------------------------------------------------------------
    def test_12_superadmin_global_access_and_view_as(self):
        """Verify Super Admin can view all restaurants and use 'View as Restaurant' mode."""
        # Global Super Admin restaurant management
        res_super = self.client_super.get(reverse('superadmin:restaurant_list'))
        self.assertEqual(res_super.status_code, 200)
        self.assertContains(res_super, "Grand Bistro")
        self.assertContains(res_super, "Spice Palace")

        # Super Admin initiates "View as Restaurant A"
        view_as_url = reverse('superadmin:view_as_restaurant', args=[self.rest_a.id])
        res_view_as = self.client_super.get(view_as_url, follow=True)
        self.assertEqual(res_view_as.status_code, 200)

        # Visiting Dashboard now shows Restaurant A data and top banner
        res_dash = self.client_super.get(reverse('dashboard:index'))
        self.assertContains(res_dash, "Super Admin View Mode:")
        self.assertContains(res_dash, "Grand Bistro")

        # Exit View-as Mode
        res_exit = self.client_super.get(reverse('superadmin:exit_view_as_restaurant'), follow=True)
        self.assertEqual(res_exit.status_code, 200)

    # -------------------------------------------------------------------------
    # TEST 13: Tenant-Aware Usernames Test
    # -------------------------------------------------------------------------
    def test_13_tenant_aware_usernames(self):
        """Verify two restaurants can have an employee with the exact same username."""
        # Restaurant A already has waiter01
        self.assertTrue(User.objects.filter(username='waiter01', restaurant=self.rest_a).exists())

        # Create waiter01 in Restaurant B
        waiter_b_duplicate_username = User.objects.create_user(
            username='waiter01',
            email='waiter01@spicepalace.com',
            password='SpicePassword123!',
            role=User.Role.WAITER,
            restaurant=self.rest_b
        )

        # Both users exist independently in the database
        users = User.objects.filter(username='waiter01')
        self.assertEqual(users.count(), 2)

        # Authenticate waiter01 of Restaurant A
        client_w1 = Client()
        login_a = client_w1.login(username='waiter01', password='WaiterPassword123!')
        self.assertTrue(login_a)

    # -------------------------------------------------------------------------
    # TEST 14: Tenant Suspension Lifecycle Test
    # -------------------------------------------------------------------------
    def test_14_tenant_suspension_lifecycle(self):
        """Verify suspended restaurant users are blocked from access with 403."""
        # Suspend Restaurant B
        self.rest_b.status = Restaurant.RestaurantStatus.SUSPENDED
        self.rest_b.suspension_reason = "Billing overdue"
        self.rest_b.save()

        # Admin B tries to access dashboard -> 403 Forbidden
        res_b = self.client_b.get(reverse('dashboard:index'))
        self.assertEqual(res_b.status_code, 403)
        self.assertContains(res_b, "Restaurant Account Suspended", status_code=403)

        # Reinstate Restaurant B
        self.rest_b.status = Restaurant.RestaurantStatus.ACTIVE
        self.rest_b.save()

        # Access restored
        res_b_restored = self.client_b.get(reverse('dashboard:index'))
        self.assertEqual(res_b_restored.status_code, 200)
