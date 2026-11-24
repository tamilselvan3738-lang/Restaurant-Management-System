import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import Restaurant
from menu.models import Category, MenuItem, FoodCombo, FoodComboItem
from tables.models import DiningTable, CustomerSession, GroupMember, SessionCart, SessionCartItem
from orders.models import Order, OrderItem
from billing.models import Invoice


class ComboAndCartReduceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name="Grand Bistro", code="REST-GB")

        self.category = Category.objects.create(name="Mains", slug="mains")
        self.pizza = MenuItem.objects.create(
            category=self.category,
            name="Quattro Formaggi",
            price=Decimal('19.50'),
            is_available=True
        )
        self.pasta = MenuItem.objects.create(
            category=self.category,
            name="Carbonara",
            price=Decimal('18.00'),
            is_available=True
        )
        self.drink = MenuItem.objects.create(
            category=self.category,
            name="Mojito",
            price=Decimal('8.50'),
            is_available=True
        )

        self.combo = FoodCombo.objects.create(
            restaurant=self.restaurant,
            name="Italian Feast",
            price=Decimal('40.00'),
            is_available=True
        )
        FoodComboItem.objects.create(combo=self.combo, menu_item=self.pizza, quantity=1)
        FoodComboItem.objects.create(combo=self.combo, menu_item=self.pasta, quantity=1)
        FoodComboItem.objects.create(combo=self.combo, menu_item=self.drink, quantity=1)

        self.table = DiningTable.objects.create(table_number='T-01', capacity=4)
        self.session = CustomerSession.objects.create(
            table=self.table,
            session_name="Group A",
            customer_name="Dhanusha",
            status=CustomerSession.Status.ACTIVE
        )
        self.member = GroupMember.objects.create(
            session=self.session,
            name="Dhanusha",
            role=GroupMember.Role.LEADER,
            can_submit_order=True,
            is_active=True
        )

        # Set session and member cookies
        session_cookie = f'rms_session_{self.table.id}'
        member_cookie = f'rms_member_{self.table.id}'
        self.client.cookies[session_cookie] = self.session.session_token
        self.client.cookies[member_cookie] = self.member.member_token

    def test_add_and_reduce_standalone_item(self):
        """Test adding standalone menu item and reducing quantity with reduce endpoint."""
        add_url = reverse('tables:api_group_cart_add', args=[self.table.table_number])
        reduce_url = reverse('tables:api_group_cart_reduce', args=[self.table.table_number])

        # 1. Add item
        resp = self.client.post(
            add_url,
            data=json.dumps({'menu_item_id': self.pizza.id, 'quantity': 2}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'success')

        cart_item = SessionCartItem.objects.get(menu_item=self.pizza, cart__session=self.session)
        self.assertEqual(cart_item.quantity, 2)

        # 2. Reduce by 1 -> quantity becomes 1
        resp = self.client.post(
            reduce_url,
            data=json.dumps({'menu_item_id': self.pizza.id}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 1)

        # 3. Reduce by 1 -> item is deleted (quantity becomes 0)
        resp = self.client.post(
            reduce_url,
            data=json.dumps({'menu_item_id': self.pizza.id}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SessionCartItem.objects.filter(menu_item=self.pizza, cart__session=self.session).exists())

    def test_add_and_reduce_combo(self):
        """Test adding food combo and reducing combo with reduce endpoint."""
        add_url = reverse('tables:api_group_cart_add', args=[self.table.table_number])
        reduce_url = reverse('tables:api_group_cart_reduce', args=[self.table.table_number])

        # 1. Add combo
        resp = self.client.post(
            add_url,
            data=json.dumps({'combo_id': self.combo.id, 'is_combo': True, 'quantity': 1}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'success')

        cart_item = SessionCartItem.objects.get(combo=self.combo, is_combo=True, cart__session=self.session)
        self.assertEqual(cart_item.quantity, 1)
        self.assertEqual(cart_item.unit_price, Decimal('40.00'))

        # 2. Add combo again -> quantity becomes 2
        resp = self.client.post(
            add_url,
            data=json.dumps({'combo_id': self.combo.id, 'is_combo': True, 'quantity': 1}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 2)

        # 3. Reduce combo by 1 -> quantity becomes 1
        resp = self.client.post(
            reduce_url,
            data=json.dumps({'combo_id': self.combo.id}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 1)

        # 4. Reduce combo by 1 -> item deleted
        resp = self.client.post(
            reduce_url,
            data=json.dumps({'combo_id': self.combo.id}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SessionCartItem.objects.filter(combo=self.combo, is_combo=True, cart__session=self.session).exists())

    def test_submit_group_order_with_combo_and_standalone(self):
        """Test submitting group order with 1 Combo and 1 Standalone Dish."""
        add_url = reverse('tables:api_group_cart_add', args=[self.table.table_number])
        submit_url = reverse('tables:api_group_submit_order', args=[self.table.table_number])

        # Add 1 Combo (₹40.00)
        self.client.post(
            add_url,
            data=json.dumps({'combo_id': self.combo.id, 'is_combo': True, 'quantity': 1}),
            content_type='application/json'
        )
        # Add 1 Standalone Pizza (₹19.50)
        self.client.post(
            add_url,
            data=json.dumps({'menu_item_id': self.pizza.id, 'quantity': 1}),
            content_type='application/json'
        )

        # Submit group order
        resp = self.client.post(
            submit_url,
            data=json.dumps({'special_instructions': 'Crispy crust'}),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'success')

        order = Order.objects.filter(session=self.session).first()
        self.assertIsNotNone(order)

        # Combo header
        header = order.items.get(is_combo_header=True)
        self.assertEqual(header.unit_price, Decimal('40.00'))
        self.assertEqual(header.total_price, Decimal('40.00'))

        # Combo components (3 distinct dishes with 0.00 unit price)
        components = order.items.filter(is_combo_component=True)
        self.assertEqual(components.count(), 3)
        for comp in components:
            self.assertEqual(comp.unit_price, Decimal('0.00'))
            self.assertEqual(comp.total_price, Decimal('0.00'))

        # Standalone dish
        standalone = order.items.get(menu_item=self.pizza, is_combo_component=False, is_combo_header=False)
        self.assertEqual(standalone.unit_price, Decimal('19.50'))
        self.assertEqual(standalone.total_price, Decimal('19.50'))

        # Subtotal: 40.00 + 19.50 = 59.50
        self.assertEqual(order.subtotal_amount, Decimal('59.50'))

        invoice = Invoice.objects.filter(session=self.session).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.subtotal, Decimal('59.50'))
