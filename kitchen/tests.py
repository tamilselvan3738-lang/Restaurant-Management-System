from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Restaurant, User
from kitchen.models import ItemAvailabilityReport, KitchenNotification, KitchenStaffProfile, KitchenStation
from menu.models import Category, MenuItem
from orders.models import Order, OrderCancellationLog, OrderItem, OrderStatusHistory
from tables.models import DiningTable


class ChefPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name="Grand Test Bistro")

        # Admin user
        self.admin_user = User.objects.create_user(
            username="restaurant_admin",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            restaurant=self.restaurant,
            is_staff=True
        )

        # Station
        self.station = KitchenStation.objects.create(
            restaurant=self.restaurant,
            name="Grill & Hot Station",
            is_active=True
        )

        # Chef user
        self.chef_user = User.objects.create_user(
            username="head_chef_pierre",
            password="ChefPassword123!",
            role=User.Role.CHEF,
            restaurant=self.restaurant,
            first_name="Pierre",
            last_name="Gagnaire"
        )
        self.chef_profile = KitchenStaffProfile.objects.create(
            user=self.chef_user,
            restaurant=self.restaurant,
            designation=KitchenStaffProfile.Designation.CHEF,
            assigned_station=self.station,
            can_view_all_orders=True,
            can_accept_orders=True,
            can_update_status=True,
            can_mark_ready=True,
            can_cancel_items=True,
            can_request_order_cancel=True,
            can_report_unavailable=True
        )

        # Table & Menu
        self.table = DiningTable.objects.create(
            restaurant=self.restaurant,
            table_number="T-99",
            capacity=4
        )
        self.category = Category.objects.create(
            restaurant=self.restaurant,
            name="Main Course",
            display_order=1
        )
        self.item1 = MenuItem.objects.create(
            restaurant=self.restaurant,
            name="Chicken Biryani",
            category=self.category,
            price=Decimal("15.00"),
            preparation_time=25,
            is_available=True
        )
        self.item2 = MenuItem.objects.create(
            restaurant=self.restaurant,
            name="Paneer Tikka",
            category=self.category,
            price=Decimal("12.00"),
            preparation_time=20,
            is_available=True
        )

        # Create Order
        self.order = Order.objects.create(
            restaurant=self.restaurant,
            order_type=Order.OrderType.DINE_IN,
            table=self.table,
            customer_name="John Doe",
            status=Order.Status.PENDING,
            special_instructions="No onions please"
        )
        self.order_item1 = OrderItem.objects.create(
            order=self.order,
            menu_item=self.item1,
            quantity=2,
            unit_price=Decimal("15.00"),
            status=OrderItem.ItemStatus.PENDING
        )
        self.order_item2 = OrderItem.objects.create(
            order=self.order,
            menu_item=self.item2,
            quantity=1,
            unit_price=Decimal("12.00"),
            status=OrderItem.ItemStatus.PENDING
        )
        self.order.calculate_totals()

    def test_chef_login_redirects_to_chef_dashboard(self):
        """When Chef logs in via common login URL, redirect to /chef/dashboard/"""
        login_url = reverse('accounts:login')
        response = self.client.post(login_url, {
            'username': 'head_chef_pierre',
            'password': 'ChefPassword123!'
        }, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('chef:dashboard'))

    def test_chef_forbidden_from_admin_and_hr(self):
        """Chef cannot access Employee Management or Admin-only views"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        
        # Try accessing staff management
        staff_list_url = reverse('accounts:staff_list')
        response = self.client.get(staff_list_url)
        # Should redirect back to chef dashboard or deny access
        self.assertIn(response.status_code, [302, 403])
        if response.status_code == 302:
            self.assertEqual(response.url, reverse('chef:dashboard'))

    def test_chef_dashboard_renders_metrics(self):
        """Chef dashboard renders summary cards and recent orders"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        response = self.client.get(reverse('chef:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chef Command Center")
        self.assertContains(response, self.order.order_number)
        self.assertContains(response, "Table T-99")

    def test_chef_order_detail_view(self):
        """Chef order detail renders items, special instructions and status"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        response = self.client.get(reverse('chef:order_detail', args=[self.order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chicken Biryani")
        self.assertContains(response, "Paneer Tikka")
        self.assertContains(response, "No onions please")

    def test_chef_accept_order(self):
        """Chef can accept order atomically, sets PREPARING (Start Cooking), timestamps, and logs to history"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        accept_url = reverse('chef:order_accept', args=[self.order.id])
        response = self.client.post(accept_url)
        self.assertEqual(response.status_code, 302)
        
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PREPARING)
        self.assertEqual(self.order.accepted_by, self.chef_user)
        self.assertIsNotNone(self.order.accepted_at)
        self.assertIsNotNone(self.order.cooking_started_at)
        
        # Check history
        self.assertTrue(OrderStatusHistory.objects.filter(order=self.order, to_status=Order.Status.PREPARING).exists())

    def test_exclusive_acceptance_blocks_second_kitchen_staff(self):
        """
        STEP 2 REQUIREMENT:
        Only one kitchen staff employee can accept an order.
        When Staff A accepts, Staff B cannot accept it and receives an informative rejection.
        """
        # Create second kitchen staff
        chef2 = User.objects.create_user(
            username="line_cook_marco",
            password="CookPassword123!",
            role=User.Role.KITCHEN,
            restaurant=self.restaurant,
            first_name="Marco",
            last_name="White"
        )
        KitchenStaffProfile.objects.create(
            user=chef2,
            restaurant=self.restaurant,
            designation=KitchenStaffProfile.Designation.KITCHEN,
            can_accept_orders=True,
            can_update_status=True
        )

        # Staff A accepts the order
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        accept_url = reverse('chef:order_accept', args=[self.order.id])
        resp_a = self.client.post(accept_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp_a.status_code, 200)
        data_a = resp_a.json()
        self.assertTrue(data_a['success'])
        self.assertIn("Pierre", data_a['accepted_by'])

        # Staff B attempts to accept the already-accepted order
        self.client.logout()
        self.client.login(username='line_cook_marco', password='CookPassword123!')
        resp_b = self.client.post(accept_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp_b.status_code, 409)
        data_b = resp_b.json()
        self.assertFalse(data_b['success'])
        self.assertIn("already been accepted", data_b['error'])

        # Order remains exclusively assigned to Staff A
        self.order.refresh_from_db()
        self.assertEqual(self.order.accepted_by, self.chef_user)

    def test_unauthorized_staff_cannot_update_other_staff_order(self):
        """
        Only assigned staff or Admin/Manager can update an accepted order.
        """
        chef2 = User.objects.create_user(
            username="line_cook_clara",
            password="CookPassword123!",
            role=User.Role.KITCHEN,
            restaurant=self.restaurant,
            first_name="Clara",
            last_name="Oswald"
        )
        KitchenStaffProfile.objects.create(
            user=chef2,
            restaurant=self.restaurant,
            designation=KitchenStaffProfile.Designation.KITCHEN,
            can_update_status=True
        )

        # Assign order to Chef Pierre
        self.order.accepted_by = self.chef_user
        self.order.status = Order.Status.PREPARING
        self.order.save()

        # Clara tries to mark order as READY
        self.client.login(username='line_cook_clara', password='CookPassword123!')
        status_url = reverse('chef:order_status', args=[self.order.id])
        resp = self.client.post(status_url, {'status': Order.Status.READY}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Only the assigned kitchen staff member", resp.json()['error'])

        # Assigned Chef Pierre can mark it as READY
        self.client.logout()
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        resp_pierre = self.client.post(status_url, {'status': Order.Status.READY})
        self.assertEqual(resp_pierre.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.READY)
        self.assertIsNotNone(self.order.ready_at)

    def test_five_minute_serving_countdown_and_delayed_flag(self):
        """
        STEP 5 & 6: 5-minute serving countdown logic and delayed serving alert.
        Verification that expiring countdown does NOT automatically mark order as SERVED.
        """
        now = timezone.now()
        self.order.status = Order.Status.READY
        self.order.ready_at = now - timezone.timedelta(minutes=2)  # 2 minutes ago
        self.order.save()

        # Under 5 minutes: not delayed
        self.assertFalse(self.order.is_serving_delayed)
        self.assertGreater(self.order.serving_countdown_remaining_seconds, 0)

        # Over 5 minutes (e.g. 7 minutes ago): delayed
        self.order.ready_at = now - timezone.timedelta(minutes=7)
        self.order.save()
        self.assertTrue(self.order.is_serving_delayed)
        self.assertEqual(self.order.serving_countdown_remaining_seconds, 0)
        self.assertGreaterEqual(self.order.serving_delay_minutes, 2)

        # Status must STILL be READY, never automatically changed to SERVED
        self.assertEqual(self.order.status, Order.Status.READY)

    def test_mark_as_served_by_service_staff(self):
        """
        Only staff member action transitions READY -> SERVED.
        """
        self.order.status = Order.Status.READY
        self.order.ready_at = timezone.now()
        self.order.save()

        # Admin marks as served
        self.client.login(username='restaurant_admin', password='AdminPassword123!')
        serve_url = reverse('orders:update_status', args=[self.order.id])
        resp = self.client.post(serve_url, {'status': Order.Status.SERVED})
        self.assertEqual(resp.status_code, 302)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SERVED)
        self.assertIsNotNone(self.order.served_at)

    def test_customer_order_status_live_api(self):
        """
        Customer live API provides real-time 5-stage tracker and countdown data.
        """
        table_num = self.table.table_number
        order_id = self.order.id
        live_url = reverse('tables:customer_order_status_live', args=[table_num, order_id])

        # Stage 1: Placed / Pending
        resp = self.client.get(live_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['stage'], 1)

        # Stage 3: Start Cooking (Preparing)
        self.order.status = Order.Status.PREPARING
        self.order.accepted_by = self.chef_user
        self.order.accepted_at = timezone.now()
        self.order.save()

        resp = self.client.get(live_url)
        data = resp.json()
        self.assertEqual(data['stage'], 3)
        self.assertIn("Pierre", data['chef_name'])
        self.assertIn("Start Cooking", data['stage_title'])

        # Stage 4: Food Ready with 5-minute countdown
        self.order.status = Order.Status.READY
        self.order.ready_at = timezone.now()
        self.order.save()

        resp = self.client.get(live_url)
        data = resp.json()
        self.assertEqual(data['stage'], 4)
        self.assertGreater(data['countdown_seconds'], 250)
        self.assertIn("within 5 minutes", data['status_message'])

        # Stage 5: Served
        self.order.status = Order.Status.SERVED
        self.order.served_at = timezone.now()
        self.order.save()

        resp = self.client.get(live_url)
        data = resp.json()
        self.assertEqual(data['stage'], 5)
        self.assertIn("Served", data['stage_title'])

    def test_order_status_progression(self):
        """Order moves PENDING -> PREPARING -> READY and items cascade"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        
        # Advance to PREPARING
        status_url = reverse('chef:order_status', args=[self.order.id])
        response = self.client.post(status_url, {'status': Order.Status.PREPARING})
        self.assertEqual(response.status_code, 302)
        
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PREPARING)
        
        # Items should be COOKING with cooking_started_at set
        self.order_item1.refresh_from_db()
        self.assertEqual(self.order_item1.status, OrderItem.ItemStatus.COOKING)
        self.assertIsNotNone(self.order_item1.cooking_started_at)

        # Mark all ready
        response = self.client.post(status_url, {'status': Order.Status.READY})
        self.assertEqual(response.status_code, 302)
        
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.READY)
        self.order_item1.refresh_from_db()
        self.assertEqual(self.order_item1.status, OrderItem.ItemStatus.READY)
        self.assertIsNotNone(self.order_item1.ready_at)

    def test_individual_item_cancellation(self):
        """Chef cancels one item: item is cancelled, other items remain, bill recalculated"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        
        initial_total = self.order.total_amount
        cancel_url = reverse('chef:item_cancel', args=[self.order_item2.id])
        response = self.client.post(cancel_url, {
            'reason_code': 'Ingredient unavailable',
            'reason_notes': 'Paneer is spoiled'
        })
        self.assertEqual(response.status_code, 302)
        
        # Check item2 cancelled
        self.order_item2.refresh_from_db()
        self.assertEqual(self.order_item2.status, OrderItem.ItemStatus.CANCELLED)
        self.assertIn("Ingredient unavailable", self.order_item2.cancellation_reason)

        # Check item1 remains active
        self.order_item1.refresh_from_db()
        self.assertEqual(self.order_item1.status, OrderItem.ItemStatus.PENDING)

        # Check order total recalculated
        self.order.refresh_from_db()
        self.assertLess(self.order.total_amount, initial_total)
        self.assertEqual(self.order.status, Order.Status.PENDING)

        # Check cancellation log created
        log = OrderCancellationLog.objects.filter(order=self.order, order_item=self.order_item2).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.cancellation_type, OrderCancellationLog.CancellationType.ITEM)
        self.assertEqual(log.status, OrderCancellationLog.LogStatus.CANCELLED)

    def test_request_entire_order_cancellation(self):
        """Chef submits request for full order cancellation"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        
        # Remove direct manager cancel permission to test request flow
        self.chef_profile.can_approve_order_cancel = False
        self.chef_profile.save()

        cancel_url = reverse('chef:order_request_cancel', args=[self.order.id])
        response = self.client.post(cancel_url, {'reason': 'Gas leak in kitchen section'})
        self.assertEqual(response.status_code, 302)

        self.order.refresh_from_db()
        self.assertTrue(self.order.has_unseen_cancellation)

        log = OrderCancellationLog.objects.filter(order=self.order, cancellation_type=OrderCancellationLog.CancellationType.ORDER).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, OrderCancellationLog.LogStatus.REQUESTED)
        self.assertIn("Gas leak", log.reason)

    def test_report_unavailable_item(self):
        """Chef can report menu item as unavailable (86 item)"""
        self.client.login(username='head_chef_pierre', password='ChefPassword123!')
        
        url = reverse('chef:report_unavailable')
        response = self.client.post(url, {
            'menu_item_id': self.item1.id,
            'reason': 'Chicken stock depleted for tonight'
        })
        self.assertEqual(response.status_code, 302)

        report = ItemAvailabilityReport.objects.filter(menu_item=self.item1).first()
        self.assertIsNotNone(report)
        self.assertEqual(report.status, ItemAvailabilityReport.ReportStatus.PENDING)
        self.assertIn("Chicken stock", report.reason)
