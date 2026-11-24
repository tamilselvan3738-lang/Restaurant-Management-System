from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import time, timedelta
from accounts.models import User
from menu.models import Category, DietaryTag, MenuItem, Ingredient, MenuItemIngredient
from tables.models import DiningTable, Reservation
from orders.models import Order, OrderItem
from billing.models import Invoice, Payment

class Command(BaseCommand):
    help = "Seeds initial realistic production data for the Restaurant Management System"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding Restaurant Management System data..."))

        # 1. Staff & User Accounts
        users_data = [
            {'username': 'admin', 'email': 'admin@grandbistro.com', 'role': User.Role.ADMIN, 'first_name': 'Alexander', 'last_name': 'Vance', 'is_staff': True, 'is_superuser': True},
            {'username': 'manager', 'email': 'manager@grandbistro.com', 'role': User.Role.MANAGER, 'first_name': 'Eleanor', 'last_name': 'Pena', 'is_staff': True, 'is_superuser': False},
            {'username': 'chef', 'email': 'chef@grandbistro.com', 'role': User.Role.KITCHEN, 'first_name': 'Marco', 'last_name': 'Pierre', 'is_staff': False, 'is_superuser': False},
            {'username': 'waiter', 'email': 'waiter@grandbistro.com', 'role': User.Role.WAITER, 'first_name': 'Lucas', 'last_name': 'Scott', 'is_staff': False, 'is_superuser': False},
            {'username': 'cashier', 'email': 'cashier@grandbistro.com', 'role': User.Role.CASHIER, 'first_name': 'Sarah', 'last_name': 'Connor', 'is_staff': False, 'is_superuser': False},
        ]

        created_users = {}
        for u in users_data:
            user, created = User.objects.get_or_create(
                username=u['username'],
                defaults={
                    'email': u['email'],
                    'role': u['role'],
                    'first_name': u['first_name'],
                    'last_name': u['last_name'],
                    'is_staff': u['is_staff'],
                    'is_superuser': u['is_superuser'],
                    'is_on_duty': True
                }
            )
            # Default password
            user.set_password(f"{u['username']}123")
            user.save()
            created_users[u['username']] = user
            self.stdout.write(f"  User ready: {user.username} (Password: {user.username}123)")

        # 2. Dietary Tags
        tags_data = [
            {'name': 'Vegetarian', 'badge_color': 'success'},
            {'name': 'Vegan', 'badge_color': 'success'},
            {'name': 'Gluten-Free', 'badge_color': 'warning'},
            {'name': 'Spicy', 'badge_color': 'danger'},
            {'name': 'Chef Special', 'badge_color': 'primary'},
            {'name': 'Halal', 'badge_color': 'info'},
        ]
        created_tags = {}
        for t in tags_data:
            tag, _ = DietaryTag.objects.get_or_create(name=t['name'], defaults={'badge_color': t['badge_color']})
            created_tags[t['name']] = tag

        # 3. Categories
        categories_data = [
            {'name': 'Appetizers & Starters', 'slug': 'starters', 'icon': 'bi-egg', 'order': 1},
            {'name': 'Artisan Pizzas', 'slug': 'pizzas', 'icon': 'bi-pie-chart', 'order': 2},
            {'name': 'Handmade Pastas', 'slug': 'pastas', 'icon': 'bi-circle', 'order': 3},
            {'name': 'Signature Steaks & Mains', 'slug': 'mains', 'icon': 'bi-fire', 'order': 4},
            {'name': 'Artisanal Desserts', 'slug': 'desserts', 'icon': 'bi-cake2', 'order': 5},
            {'name': 'Cocktails & Beverages', 'slug': 'beverages', 'icon': 'bi-cup-straw', 'order': 6},
        ]
        created_categories = {}
        for c in categories_data:
            cat, _ = Category.objects.get_or_create(
                slug=c['slug'],
                defaults={'name': c['name'], 'icon': c['icon'], 'display_order': c['order'], 'is_active': True}
            )
            created_categories[c['slug']] = cat

        # 4. Ingredients & Inventory
        ingredients_data = [
            {'name': 'San Marzano Tomatoes', 'unit': 'kg', 'current_stock': Decimal('45.0'), 'low_stock_threshold': Decimal('15.0'), 'cost_per_unit': Decimal('2.50')},
            {'name': 'Buffalo Mozzarella', 'unit': 'kg', 'current_stock': Decimal('22.5'), 'low_stock_threshold': Decimal('8.0'), 'cost_per_unit': Decimal('7.20')},
            {'name': 'Prime Ribeye Beef', 'unit': 'kg', 'current_stock': Decimal('14.0'), 'low_stock_threshold': Decimal('6.0'), 'cost_per_unit': Decimal('22.00')},
            {'name': 'Fresh Atlantic Salmon', 'unit': 'kg', 'current_stock': Decimal('3.5'), 'low_stock_threshold': Decimal('5.0'), 'cost_per_unit': Decimal('18.50')}, # Low stock
            {'name': 'Black Truffle Oil', 'unit': 'l', 'current_stock': Decimal('1.2'), 'low_stock_threshold': Decimal('2.0'), 'cost_per_unit': Decimal('45.00')}, # Low stock
            {'name': 'Parmigiano Reggiano', 'unit': 'kg', 'current_stock': Decimal('12.0'), 'low_stock_threshold': Decimal('5.0'), 'cost_per_unit': Decimal('16.00')},
            {'name': 'Semolina Flour (00)', 'unit': 'kg', 'current_stock': Decimal('50.0'), 'low_stock_threshold': Decimal('20.0'), 'cost_per_unit': Decimal('1.80')},
            {'name': 'Arabica Coffee Beans', 'unit': 'kg', 'current_stock': Decimal('10.0'), 'low_stock_threshold': Decimal('4.0'), 'cost_per_unit': Decimal('14.00')},
        ]
        created_ingredients = {}
        for ing in ingredients_data:
            item, _ = Ingredient.objects.get_or_create(
                name=ing['name'],
                defaults={
                    'unit': ing['unit'],
                    'current_stock': ing['current_stock'],
                    'low_stock_threshold': ing['low_stock_threshold'],
                    'cost_per_unit': ing['cost_per_unit']
                }
            )
            created_ingredients[ing['name']] = item

        # 5. Menu Items
        menu_items_data = [
            # Starters
            {
                'category': 'starters', 'name': 'Bruschetta al Pomodoro', 'price': Decimal('9.50'), 'cost': Decimal('2.80'),
                'desc': 'Toasted sourdough with vine ripe heirloom tomatoes, basil, garlic and extra virgin olive oil.',
                'prep': 10, 'cal': 320, 'tags': ['Vegetarian', 'Vegan'], 'special': False
            },
            {
                'category': 'starters', 'name': 'Crispy Calamari Fritti', 'price': Decimal('14.00'), 'cost': Decimal('4.50'),
                'desc': 'Lightly battered calamari rings served with house made spicy marinara and lemon aioli.',
                'prep': 12, 'cal': 480, 'tags': ['Chef Special ⭐'], 'special': True
            },
            # Pizzas
            {
                'category': 'pizzas', 'name': 'Classic Margherita D.O.P.', 'price': Decimal('16.50'), 'cost': Decimal('4.20'),
                'desc': 'San Marzano tomato sauce, fresh buffalo mozzarella, aromatic sweet basil, cold-pressed olive oil.',
                'prep': 15, 'cal': 780, 'tags': ['Vegetarian'], 'special': False
            },
            {
                'category': 'pizzas', 'name': 'Diavola Pepperoni & Hot Honey', 'price': Decimal('18.50'), 'cost': Decimal('5.10'),
                'desc': 'Spicy Calabrese salami, mozzarella, chili flakes, and hot habanero infused blossom honey drizzle.',
                'prep': 15, 'cal': 890, 'tags': ['Spicy', 'Chef Special'], 'special': True
            },
            # Pastas
            {
                'category': 'pastas', 'name': 'Truffle Tagliolini', 'price': Decimal('24.00'), 'cost': Decimal('6.80'),
                'desc': 'Handmade ribbon pasta tossed in rich butter sauce, aged Parmigiano Reggiano, and black winter truffles.',
                'prep': 18, 'cal': 650, 'tags': ['Vegetarian', 'Chef Special ⭐'], 'special': True
            },
            {
                'category': 'pastas', 'name': 'Spaghetti Alla Carbonara', 'price': Decimal('19.50'), 'cost': Decimal('5.20'),
                'desc': 'Traditional Roman style with crispy guanciale, pecorino romano, egg yolk, and cracked black pepper.',
                'prep': 16, 'cal': 740, 'tags': [], 'special': False
            },
            # Mains
            {
                'category': 'mains', 'name': 'Prime Dry-Aged Ribeye Steak', 'price': Decimal('38.00'), 'cost': Decimal('14.50'),
                'desc': '12oz charbroiled prime beef ribeye served with roasted garlic rosemary butter and truffled fries.',
                'prep': 22, 'cal': 950, 'tags': ['Chef Special ⭐', 'Gluten-Free'], 'special': True
            },
            {
                'category': 'mains', 'name': 'Pan-Seared Atlantic Salmon', 'price': Decimal('28.50'), 'cost': Decimal('9.80'),
                'desc': 'Crispy skin salmon fillet atop creamy lemon dill risotto and grilled baby asparagus spears.',
                'prep': 20, 'cal': 620, 'tags': ['Gluten-Free', 'Halal'], 'special': False
            },
            # Desserts
            {
                'category': 'desserts', 'name': 'Classic Venetian Tiramisu', 'price': Decimal('10.00'), 'cost': Decimal('2.90'),
                'desc': 'Espresso-soaked ladyfingers layered with whipped mascarpone cream and dusted Valrhona cocoa.',
                'prep': 8, 'cal': 420, 'tags': ['Vegetarian'], 'special': True
            },
            {
                'category': 'desserts', 'name': 'Pistachio Lava Cake', 'price': Decimal('11.50'), 'cost': Decimal('3.40'),
                'desc': 'Warm molten Sicilian pistachio cake served with Tahitian vanilla bean gelato.',
                'prep': 14, 'cal': 510, 'tags': ['Vegetarian'], 'special': False
            },
            # Beverages
            {
                'category': 'beverages', 'name': 'Classic Negroni Cocktail', 'price': Decimal('13.00'), 'cost': Decimal('2.50'),
                'desc': 'Botanical gin, Campari bitter aperitif, and sweet vermouth stirred over crystal ice with orange twist.',
                'prep': 5, 'cal': 190, 'tags': [], 'special': False
            },
            {
                'category': 'beverages', 'name': 'Artisanal Double Espresso', 'price': Decimal('4.50'), 'cost': Decimal('0.60'),
                'desc': 'Single origin roasted Arabica beans pulled fresh with velvety golden crema.',
                'prep': 4, 'cal': 10, 'tags': ['Vegan', 'Gluten-Free', 'Halal'], 'special': False
            },
        ]

        created_menu_items = {}
        for m in menu_items_data:
            item, _ = MenuItem.objects.get_or_create(
                name=m['name'],
                defaults={
                    'category': created_categories[m['category']],
                    'price': m['price'],
                    'cost_price': m['cost'],
                    'description': m['desc'],
                    'preparation_time': m['prep'],
                    'calories': m['cal'],
                    'is_special': m['special'],
                    'is_available': True,
                }
            )
            for tag_name in m['tags']:
                if tag_name in created_tags:
                    item.dietary_tags.add(created_tags[tag_name])
            created_menu_items[m['name']] = item

        # 6. Dining Tables
        tables_data = [
            {'number': 'T-01', 'capacity': 2, 'loc': DiningTable.Location.COUPLE, 'status': DiningTable.Status.OCCUPIED},
            {'number': 'T-02', 'capacity': 4, 'loc': DiningTable.Location.ROUND, 'status': DiningTable.Status.OCCUPIED},
            {'number': 'T-03', 'capacity': 4, 'loc': DiningTable.Location.ROUND, 'status': DiningTable.Status.AVAILABLE},
            {'number': 'T-04', 'capacity': 6, 'loc': DiningTable.Location.FAMILY, 'status': DiningTable.Status.RESERVED},
            {'number': 'P-01', 'capacity': 2, 'loc': DiningTable.Location.COUPLE, 'status': DiningTable.Status.AVAILABLE},
            {'number': 'P-02', 'capacity': 4, 'loc': DiningTable.Location.RECTANGLE, 'status': DiningTable.Status.OCCUPIED},
            {'number': 'P-03', 'capacity': 4, 'loc': DiningTable.Location.BOOTH, 'status': DiningTable.Status.CLEANING},
            {'number': 'ROOF-1', 'capacity': 4, 'loc': DiningTable.Location.ROUND, 'status': DiningTable.Status.AVAILABLE},
            {'number': 'ROOF-2', 'capacity': 6, 'loc': DiningTable.Location.RECTANGLE, 'status': DiningTable.Status.AVAILABLE},
            {'number': 'VIP-1', 'capacity': 10, 'loc': DiningTable.Location.VIP, 'status': DiningTable.Status.RESERVED},
            {'number': 'BAR-1', 'capacity': 2, 'loc': DiningTable.Location.COUPLE, 'status': DiningTable.Status.AVAILABLE},
            {'number': 'BAR-2', 'capacity': 2, 'loc': DiningTable.Location.COUPLE, 'status': DiningTable.Status.AVAILABLE},
        ]

        created_tables = {}
        for t in tables_data:
            table, _ = DiningTable.objects.get_or_create(
                table_number=t['number'],
                defaults={
                    'capacity': t['capacity'],
                    'location': t['loc'],
                    'status': t['status'],
                    'is_active': True
                }
            )
            created_tables[t['number']] = table

        # 7. Today's Reservations
        today = timezone.localdate()
        res_data = [
            {'table': created_tables['T-04'], 'name': 'Dr. Robert Oppenheimer', 'phone': '+1 (555) 234-5678', 'guests': 5, 'time': time(19, 30), 'status': Reservation.Status.CONFIRMED, 'req': 'Anniversary dinner, window view preferred'},
            {'table': created_tables['VIP-1'], 'name': 'Evelyn St. Claire Corp', 'phone': '+1 (555) 890-1234', 'guests': 8, 'time': time(20, 0), 'status': Reservation.Status.CONFIRMED, 'req': 'VIP private boardroom dinner, wine pairing required'},
        ]
        for r in res_data:
            Reservation.objects.get_or_create(
                table=r['table'],
                reservation_date=today,
                reservation_time=r['time'],
                defaults={
                    'customer_name': r['name'],
                    'customer_phone': r['phone'],
                    'guests_count': r['guests'],
                    'status': r['status'],
                    'special_requests': r['req']
                }
            )

        # 8. Sample Active & Completed Orders + Invoices
        waiter_user = created_users['waiter']
        cashier_user = created_users['cashier']

        # Order 1 (In Kitchen)
        ord1, created = Order.objects.get_or_create(
            order_number=f"ORD-{today.strftime('%Y%m%d')}-0001",
            defaults={
                'order_type': Order.OrderType.DINE_IN,
                'table': created_tables['T-01'],
                'server': waiter_user,
                'customer_name': 'James Miller',
                'status': Order.Status.IN_KITCHEN,
                'special_instructions': 'Steak cooked medium-rare please.'
            }
        )
        if created:
            OrderItem.objects.create(order=ord1, menu_item=created_menu_items['Bruschetta al Pomodoro'], quantity=1, unit_price=Decimal('9.50'), status=OrderItem.ItemStatus.READY)
            OrderItem.objects.create(order=ord1, menu_item=created_menu_items['Prime Dry-Aged Ribeye Steak'], quantity=1, unit_price=Decimal('38.00'), status=OrderItem.ItemStatus.COOKING, special_notes='Medium Rare')
            OrderItem.objects.create(order=ord1, menu_item=created_menu_items['Classic Negroni Cocktail'], quantity=2, unit_price=Decimal('13.00'), status=OrderItem.ItemStatus.SERVED)

        # Order 2 (Placed)
        ord2, created = Order.objects.get_or_create(
            order_number=f"ORD-{today.strftime('%Y%m%d')}-0002",
            defaults={
                'order_type': Order.OrderType.DINE_IN,
                'table': created_tables['T-02'],
                'server': waiter_user,
                'customer_name': 'Sophia Loren',
                'status': Order.Status.PLACED,
            }
        )
        if created:
            OrderItem.objects.create(order=ord2, menu_item=created_menu_items['Classic Margherita D.O.P.'], quantity=1, unit_price=Decimal('16.50'))
            OrderItem.objects.create(order=ord2, menu_item=created_menu_items['Truffle Tagliolini'], quantity=1, unit_price=Decimal('24.00'))
            OrderItem.objects.create(order=ord2, menu_item=created_menu_items['Classic Venetian Tiramisu'], quantity=1, unit_price=Decimal('10.00'))

        # Order 3 (Completed & Paid with Invoice)
        ord3, created = Order.objects.get_or_create(
            order_number=f"ORD-{today.strftime('%Y%m%d')}-0003",
            defaults={
                'order_type': Order.OrderType.DINE_IN,
                'table': created_tables['P-02'],
                'server': waiter_user,
                'customer_name': 'Arthur Pendelton',
                'status': Order.Status.COMPLETED,
            }
        )
        if created:
            OrderItem.objects.create(order=ord3, menu_item=created_menu_items['Diavola Pepperoni & Hot Honey'], quantity=2, unit_price=Decimal('18.50'), status=OrderItem.ItemStatus.SERVED)
            OrderItem.objects.create(order=ord3, menu_item=created_menu_items['Crispy Calamari Fritti'], quantity=1, unit_price=Decimal('14.00'), status=OrderItem.ItemStatus.SERVED)
            OrderItem.objects.create(order=ord3, menu_item=created_menu_items['Artisanal Double Espresso'], quantity=2, unit_price=Decimal('4.50'), status=OrderItem.ItemStatus.SERVED)

            # Invoice
            subtotal = ord3.subtotal
            tax = round(subtotal * Decimal('0.0825'), 2)
            service = round(subtotal * Decimal('0.05'), 2)
            total = subtotal + tax + service

            inv, _ = Invoice.objects.get_or_create(
                order=ord3,
                defaults={
                    'subtotal': subtotal,
                    'tax_rate': Decimal('0.0825'),
                    'tax_amount': tax,
                    'service_charge_rate': Decimal('0.05'),
                    'service_charge_amount': service,
                    'total_amount': total,
                    'status': Invoice.Status.PAID,
                    'paid_at': timezone.now()
                }
            )

            # Payment
            Payment.objects.create(
                invoice=inv,
                payment_method=Payment.PaymentMethod.CREDIT_CARD,
                amount=total,
                tendered_amount=total,
                change_returned=Decimal('0.00'),
                transaction_reference='TXN-998812',
                cashier=cashier_user
            )

        self.stdout.write(self.style.SUCCESS("Restaurant Management System data seeded successfully!"))
