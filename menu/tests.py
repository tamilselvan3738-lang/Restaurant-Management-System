from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from menu.models import Category, MenuItem, DietaryTag

class MenuActiveDeactiveTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Test Resto', code='TR02')
        # Create Admin User
        self.admin = User.objects.create_user(
            username='admin_tester',
            email='admin@test.com',
            password='testpassword123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )
        # Create Staff Waiter User
        self.waiter = User.objects.create_user(
            username='waiter_tester',
            email='waiter@test.com',
            password='testpassword123',
            role=User.Role.WAITER,
            restaurant=self.restaurant
        )

        # Create Categories
        self.cat_active = Category.objects.create(
            name='Italian Mains',
            slug='italian-mains',
            display_order=1,
            is_active=True,
            restaurant=self.restaurant
        )
        self.cat_disabled = Category.objects.create(
            name='Desserts Special',
            slug='desserts-special',
            display_order=2,
            is_active=False,
            restaurant=self.restaurant
        )

        # Create Items
        self.item_active = MenuItem.objects.create(
            category=self.cat_active,
            name='Risotto Milanese',
            price=22.00,
            is_available=True,
            restaurant=self.restaurant
        )
        self.item_deactivated = MenuItem.objects.create(
            category=self.cat_active,
            name='Lasagna Tradizionale',
            price=18.50,
            is_available=False,
            restaurant=self.restaurant
        )
        self.item_in_disabled_cat = MenuItem.objects.create(
            category=self.cat_disabled,
            name='Tiramisu Classico',
            price=9.00,
            is_available=True,
            restaurant=self.restaurant
        )

    def test_menu_list_shows_only_active_items_and_categories_by_default(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('menu:list'))
        self.assertEqual(response.status_code, 200)

        # Active category should be in categories list, disabled should NOT
        categories_in_context = response.context['categories']
        self.assertIn(self.cat_active, categories_in_context)
        self.assertNotIn(self.cat_disabled, categories_in_context)

        # Only item_active should be in items
        items_in_context = response.context['items']
        self.assertIn(self.item_active, items_in_context)
        self.assertNotIn(self.item_deactivated, items_in_context)
        self.assertNotIn(self.item_in_disabled_cat, items_in_context)

    def test_search_does_not_show_deactivated_items_or_disabled_category_items(self):
        self.client.force_login(self.admin)

        # Search for deactivated item
        response = self.client.get(reverse('menu:list') + '?q=Lasagna')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['items']), 0)

        # Search for item in disabled category
        response = self.client.get(reverse('menu:list') + '?q=Tiramisu')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['items']), 0)

        # Search for active item
        response = self.client.get(reverse('menu:list') + '?q=Risotto')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['items']), 1)
        self.assertEqual(response.context['items'][0], self.item_active)

    def test_category_toggle_status_view(self):
        self.client.force_login(self.admin)

        # Deactivate active category
        response = self.client.post(reverse('menu:category_toggle_status', args=[self.cat_active.id]))
        self.assertRedirects(response, reverse('menu:categories'))
        self.cat_active.refresh_from_db()
        self.assertFalse(self.cat_active.is_active)

        # Now Risotto Milanese should NOT show on the menu or search
        menu_response = self.client.get(reverse('menu:list'))
        self.assertNotIn(self.item_active, menu_response.context['items'])

        # Reactivate category
        response = self.client.post(reverse('menu:category_toggle_status', args=[self.cat_active.id]))
        self.cat_active.refresh_from_db()
        self.assertTrue(self.cat_active.is_active)

        # Now Risotto Milanese shows up again
        menu_response = self.client.get(reverse('menu:list'))
        self.assertIn(self.item_active, menu_response.context['items'])

    def test_item_toggle_availability_deactivate_and_activate(self):
        self.client.force_login(self.admin)

        # Deactivate Risotto
        response = self.client.post(reverse('menu:item_toggle_availability', args=[self.item_active.id]))
        self.item_active.refresh_from_db()
        self.assertFalse(self.item_active.is_available)

        # It must no longer show in default active menu or search
        menu_resp = self.client.get(reverse('menu:list'))
        self.assertNotIn(self.item_active, menu_resp.context['items'])
        search_resp = self.client.get(reverse('menu:list') + '?q=Risotto')
        self.assertEqual(len(search_resp.context['items']), 0)

        # It appears under status=inactive for admin
        inactive_resp = self.client.get(reverse('menu:list') + '?status=inactive')
        self.assertIn(self.item_active, inactive_resp.context['items'])

        # Reactivate Risotto
        self.client.post(reverse('menu:item_toggle_availability', args=[self.item_active.id]))
        self.item_active.refresh_from_db()
        self.assertTrue(self.item_active.is_available)

        # Reappears in active menu
        menu_resp = self.client.get(reverse('menu:list'))
        self.assertIn(self.item_active, menu_resp.context['items'])

    def test_category_list_renders_table_without_switcher(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('menu:categories'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Current Categories table is present
        self.assertIn('Current Categories', content)
        self.assertIn('Italian Mains', content)
        self.assertIn('Desserts Special', content)

        # Toggle buttons switcher should NOT be present
        self.assertNotIn('btnViewCards', content)
        self.assertNotIn('btnViewTable', content)
        self.assertNotIn('toggleCatView', content)
        self.assertNotIn('id="categoriesCardsView"', content)

        # Deactivate / Activate action buttons exist
        self.assertIn('Deactivate', content)
        self.assertIn('Activate', content)
