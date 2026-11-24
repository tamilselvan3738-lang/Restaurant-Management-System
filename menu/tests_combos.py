import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Restaurant
from menu.models import Category, MenuItem, FoodCombo, FoodComboItem
from tables.models import DiningTable, CustomerSession
from orders.models import Order, OrderItem
from billing.models import Invoice


class FoodComboTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.restaurant = Restaurant.objects.create(name="Grand Bistro", code="REST-GB")

        self.admin = User.objects.create_user(
            username='admin_gb',
            email='admin@gb.com',
            password='password123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )
        self.client.force_login(self.admin)

        # Category and 5 Menu Items
        self.category = Category.objects.create(name="Dinner Special", slug="dinner")
        self.biryani = MenuItem.objects.create(category=self.category, name="Chicken Biryani", price=Decimal('240.00'), is_available=True)
        self.starter = MenuItem.objects.create(category=self.category, name="Chicken 65", price=Decimal('200.00'), is_available=True)
        self.juice = MenuItem.objects.create(category=self.category, name="Fresh Lime Juice", price=Decimal('60.00'), is_available=True)
        self.dessert = MenuItem.objects.create(category=self.category, name="Gulab Jamun", price=Decimal('80.00'), is_available=True)
        self.naan = MenuItem.objects.create(category=self.category, name="Butter Naan", price=Decimal('50.00'), is_available=True)

        # Dining Table for QR Ordering
        self.table = DiningTable.objects.create(table_number='T-01', capacity=4)

    def test_create_combo_three_items_and_savings(self):
        """Create combo containing 3 items; verify individual total, combo price, and savings calculation."""
        combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Weekend Feast",
            price=Decimal('420.00'),
            is_available=True
        )
        FoodComboItem.objects.create(combo=combo, menu_item=self.biryani, quantity=1, unit_price_snapshot=self.biryani.price)  # 240
        FoodComboItem.objects.create(combo=combo, menu_item=self.starter, quantity=1, unit_price_snapshot=self.starter.price)  # 200
        FoodComboItem.objects.create(combo=combo, menu_item=self.juice, quantity=1, unit_price_snapshot=self.juice.price)      # 60

        # Individual Total = 240 + 200 + 60 = 500.00
        self.assertEqual(combo.individual_total_price, Decimal('500.00'))
        # Savings = 500 - 420 = 80.00
        self.assertEqual(combo.savings_amount, Decimal('80.00'))
        self.assertEqual(combo.discount_percentage, 16.0)
        self.assertTrue(combo.is_valid_combo)

    def test_create_combo_five_items(self):
        """Create combo containing 5 items; verify totals and validity."""
        combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Mega Family Platter",
            price=Decimal('550.00'),
            is_available=True
        )
        for item in [self.biryani, self.starter, self.juice, self.dessert, self.naan]:
            FoodComboItem.objects.create(combo=combo, menu_item=item, quantity=1)

        # 240 + 200 + 60 + 80 + 50 = 630
        self.assertEqual(combo.individual_total_price, Decimal('630.00'))
        self.assertEqual(combo.savings_amount, Decimal('80.00'))
        self.assertEqual(combo.items.count(), 5)

    def test_enforce_minimum_three_items_constraint(self):
        """Reject creating a combo with fewer than 3 distinct food items."""
        resp = self.client.post(reverse('menu:combo_create'), {
            'name': 'Invalid Two Item Combo',
            'price': '300.00',
            'is_available': True,
            'item_id[]': [self.biryani.id, self.starter.id],
            'quantity[]': [1, 1]
        })
        # Should re-render with error message and NOT create combo
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "must contain at least three (3) distinct food items")
        self.assertEqual(FoodCombo.objects.filter(name='Invalid Two Item Combo').count(), 0)

    def test_order_combo_via_qr_menu_and_kitchen_separation(self):
        """
        Customer places order with combo via QR menu:
        - Kitchen receives individual food dishes for cooking.
        - Bill charges strictly the single fixed combo price (no duplicate billing).
        """
        combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Biryani Combo Platter",
            price=Decimal('450.00'),
            is_available=True
        )
        FoodComboItem.objects.create(combo=combo, menu_item=self.biryani, quantity=2)  # 2 biryani
        FoodComboItem.objects.create(combo=combo, menu_item=self.starter, quantity=1)  # 1 starter
        FoodComboItem.objects.create(combo=combo, menu_item=self.juice, quantity=2)    # 2 juice

        # Customer QR submission (cart contains the combo)
        cart_data = json.dumps([
            {
                'item_id': None,
                'is_combo': True,
                'combo_id': combo.id,
                'quantity': 1,
                'notes': 'Spicy'
            }
        ])

        customer_client = Client()
        resp = customer_client.post(
            reverse('tables:customer_order', args=[self.table.table_number]),
            data={
                'cart_data': cart_data,
                'customer_name': 'Rahul',
                'customer_phone': '9876543210',
                'guest_count': '2',
                'special_instructions': 'Extra raita'
            }
        )
        self.assertEqual(resp.status_code, 302)

        # Check Order was created
        order = Order.objects.first()
        self.assertIsNotNone(order)

        # Kitchen check: Items available for cooking
        kitchen_cookable_items = order.items.filter(is_combo_component=True)
        self.assertEqual(kitchen_cookable_items.count(), 3)
        # Check quantities: Biryani=2, Starter=1, Juice=2
        biryani_item = kitchen_cookable_items.get(menu_item=self.biryani)
        self.assertEqual(biryani_item.quantity, 2)
        starter_item = kitchen_cookable_items.get(menu_item=self.starter)
        self.assertEqual(starter_item.quantity, 1)

        # Financial check: Header carries the combo price, components carry 0.00
        header_item = order.items.get(is_combo_header=True)
        self.assertEqual(header_item.unit_price, Decimal('450.00'))
        self.assertEqual(header_item.total_price, Decimal('450.00'))

        for comp in kitchen_cookable_items:
            self.assertEqual(comp.unit_price, Decimal('0.00'))
            self.assertEqual(comp.total_price, Decimal('0.00'))

        # Subtotal of order must be strictly ₹450.00
        self.assertEqual(order.subtotal_amount, Decimal('450.00'))

        # Check Invoice generated for the session
        invoice = Invoice.objects.first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.subtotal, Decimal('450.00'))

    def test_standalone_item_alongside_combo(self):
        """Ordering standalone items alongside combos charges combo price + standalone items correctly."""
        combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Mini Combo",
            price=Decimal('400.00'),
            is_available=True
        )
        FoodComboItem.objects.create(combo=combo, menu_item=self.biryani, quantity=1)
        FoodComboItem.objects.create(combo=combo, menu_item=self.starter, quantity=1)
        FoodComboItem.objects.create(combo=combo, menu_item=self.juice, quantity=1)

        # Cart: 1 Mini Combo (₹400) + 1 Standalone Gulab Jamun (₹80)
        cart_data = json.dumps([
            {
                'item_id': None,
                'is_combo': True,
                'combo_id': combo.id,
                'quantity': 1,
            },
            {
                'item_id': self.dessert.id,
                'is_combo': False,
                'quantity': 1,
            }
        ])

        customer_client = Client()
        customer_client.post(
            reverse('tables:customer_order', args=[self.table.table_number]),
            data={'cart_data': cart_data, 'guest_count': 1}
        )

        order = Order.objects.first()
        # Subtotal must be ₹400 + ₹80 = ₹480.00
        self.assertEqual(order.subtotal_amount, Decimal('480.00'))

    def test_deactivated_combo_cannot_be_ordered(self):
        """Deactivated combo is hidden and rejected from ordering."""
        combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Deactivated Combo",
            price=Decimal('350.00'),
            is_available=False
        )
        FoodComboItem.objects.create(combo=combo, menu_item=self.biryani, quantity=1)
        FoodComboItem.objects.create(combo=combo, menu_item=self.starter, quantity=1)
        FoodComboItem.objects.create(combo=combo, menu_item=self.juice, quantity=1)

        # QR Menu should not show this combo
        resp = self.client.get(reverse('tables:customer_order', args=[self.table.table_number]))
        self.assertNotIn("Deactivated Combo", resp.content.decode('utf-8'))

    def test_price_snapshot_preserved_on_historical_orders(self):
        """Menu item price changes do not alter historical combo bills or orders."""
        combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Snapshot Combo",
            price=Decimal('450.00'),
            is_available=True
        )
        FoodComboItem.objects.create(combo=combo, menu_item=self.biryani, quantity=1, unit_price_snapshot=Decimal('240.00'))
        FoodComboItem.objects.create(combo=combo, menu_item=self.starter, quantity=1, unit_price_snapshot=Decimal('200.00'))
        FoodComboItem.objects.create(combo=combo, menu_item=self.juice, quantity=1, unit_price_snapshot=Decimal('60.00'))

        cart_data = json.dumps([{'item_id': None, 'is_combo': True, 'combo_id': combo.id, 'quantity': 1}])
        Client().post(reverse('tables:customer_order', args=[self.table.table_number]), data={'cart_data': cart_data, 'guest_count': 1})

        order = Order.objects.first()
        self.assertEqual(order.subtotal_amount, Decimal('450.00'))

        # Change individual menu item price and combo price in menu
        self.biryani.price = Decimal('300.00')
        self.biryani.save()
        combo.price = Decimal('550.00')
        combo.save()

        # Historical order and invoice must remain unchanged
        order.refresh_from_db()
        self.assertEqual(order.subtotal_amount, Decimal('450.00'))
        header_item = order.items.get(is_combo_header=True)
        self.assertEqual(header_item.unit_price, Decimal('450.00'))
