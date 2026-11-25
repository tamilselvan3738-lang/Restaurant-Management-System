from django.test import TestCase, RequestFactory
from django.urls import reverse
from accounts.models import Restaurant, User
from tables.models import DiningTable, CustomerSession, GroupMember
from tables.services import get_dining_table_or_404


class MultiTenantTableLookupTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        # Create two distinct restaurants
        self.restaurant_a = Restaurant.objects.create(name="Restaurant Alpha", code="alpha")
        self.restaurant_b = Restaurant.objects.create(name="Restaurant Beta", code="beta")

        # Create two active dining tables BOTH named 'Table 1'
        self.table_a = DiningTable.objects.create(
            restaurant=self.restaurant_a,
            table_number="Table 1",
            capacity=4,
            is_active=True,
            qr_token="token_alpha_1"
        )
        self.table_b = DiningTable.objects.create(
            restaurant=self.restaurant_b,
            table_number="Table 1",
            capacity=6,
            is_active=True,
            qr_token="token_beta_1"
        )

    def test_no_multiple_objects_returned_unauthenticated(self):
        """
        Ensures that accessing a table without tenant context resolves deterministically
        and never raises MultipleObjectsReturned.
        """
        request = self.factory.get('/tables/Table 1/shop/')
        # Session simulation
        request.session = {}

        # Calling get_dining_table_or_404 should succeed and NOT raise MultipleObjectsReturned
        resolved = get_dining_table_or_404(request, "Table 1", is_active=True)
        self.assertIsNotNone(resolved)
        self.assertIn(resolved.id, [self.table_a.id, self.table_b.id])

    def test_qr_token_disambiguates_table(self):
        """
        Passing a QR token resolves the exact table even when another table shares the same number.
        """
        request = self.factory.get('/tables/Table 1/shop/?token=token_beta_1')
        request.session = {}

        resolved = get_dining_table_or_404(request, "Table 1", is_active=True)
        self.assertEqual(resolved.id, self.table_b.id)
        self.assertEqual(resolved.restaurant, self.restaurant_b)

    def test_tenant_context_disambiguates_table(self):
        """
        When tenant context (e.g. request.restaurant) is present, resolves to that restaurant's table.
        """
        request = self.factory.get('/tables/Table 1/shop/')
        request.restaurant = self.restaurant_a
        request.session = {}

        resolved = get_dining_table_or_404(request, "Table 1", is_active=True)
        self.assertEqual(resolved.id, self.table_a.id)
        self.assertEqual(resolved.restaurant, self.restaurant_a)

    def test_customer_cookie_disambiguates_table(self):
        """
        When an active customer has an rms_session_<id> cookie, resolves directly to that table.
        """
        request = self.factory.get('/tables/Table 1/shop/')
        request.COOKIES = {f'rms_session_{self.table_b.id}': 'sess_xyz123'}
        request.session = {}

        resolved = get_dining_table_or_404(request, "Table 1", is_active=True)
        self.assertEqual(resolved.id, self.table_b.id)

    def test_customer_shop_view_does_not_crash(self):
        """
        Directly calling customer_shop via Django test client does not raise MultipleObjectsReturned (500).
        """
        # GET /tables/Table 1/shop/
        response = self.client.get('/tables/Table 1/shop/')
        # Without an active session/member, it redirects (302) to order page with removed=1, NOT crashing with 500
        self.assertEqual(response.status_code, 302)
        self.assertIn('/tables/Table%201/order/', response.url)

    def test_customer_order_view_does_not_crash(self):
        """
        Directly calling customer_table_order via Django test client does not raise MultipleObjectsReturned.
        """
        response = self.client.get('/tables/Table 1/order/')
        self.assertEqual(response.status_code, 200)

    def test_customer_shop_live_does_not_crash(self):
        """
        Live shop polling does not raise MultipleObjectsReturned.
        """
        response = self.client.get('/tables/Table 1/shop/live/')
        # Returns 200 JSON with empty orders or 403/302, but NEVER 500 MultipleObjectsReturned
        self.assertIn(response.status_code, [200, 403, 302])

    def test_api_group_cart_get_does_not_crash(self):
        """
        Group cart API does not raise MultipleObjectsReturned.
        """
        response = self.client.get('/tables/Table 1/group/cart/')
        self.assertEqual(response.status_code, 403)

    def test_thank_you_page_does_not_crash(self):
        """
        Thank you page does not crash when multiple tables share the name.
        """
        response = self.client.get('/tables/Table 1/thank-you/')
        self.assertEqual(response.status_code, 200)

