from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from tables.models import DiningTable, CustomerSession
from billing.models import BillRequest
from orders.models import Order, ExtraFoodOrderLog


class NotificationsViewTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name="Test Bistro",
            code="REST-TEST"
        )
        self.user_with_restaurant = User.objects.create_user(
            username="rest_admin",
            password="password123",
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )
        self.user_without_restaurant = User.objects.create_user(
            username="global_admin",
            password="password123",
            role=User.Role.ADMIN,
            is_superuser=True
        )
        self.table = DiningTable.objects.create(
            table_number="T-99",
            restaurant=self.restaurant,
            capacity=4
        )
        self.session = CustomerSession.objects.create(
            table=self.table,
            session_name="Group Test",
            guest_count=2
        )
        self.bill_request = BillRequest.objects.create(
            restaurant=self.restaurant,
            table=self.table,
            session=self.session,
            total_amount=50.00
        )
        self.order = Order.objects.create(
            table=self.table,
            session=self.session,
            server=self.user_with_restaurant,
            customer_name="Alice",
            total_amount=50.00
        )
        self.extra_food = ExtraFoodOrderLog.objects.create(
            order=self.order,
            table=self.table,
            table_name="T-99",
            session_name="Group Test",
            item_name="Garlic Bread",
            quantity=2,
            unit_price=5.00,
            total_price=10.00
        )

    def test_notifications_view_authenticated_with_restaurant(self):
        client = Client()
        client.force_login(self.user_with_restaurant)
        response = client.get(reverse('dashboard:notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Real-Time Notifications")

    def test_notifications_view_authenticated_without_restaurant(self):
        client = Client()
        client.force_login(self.user_without_restaurant)
        response = client.get(reverse('dashboard:notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Real-Time Notifications")

