from django.test import TestCase, RequestFactory
from django.conf import settings
from menu.models import MenuItem, Category, FoodCombo, FoodComboItem
from menu.dish_images import get_dish_image_url, get_dish_static_image
from restaurant_management.views_media import serve_media_with_fallback
from decimal import Decimal


class DishImageFallbacksTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.cat_veg, _ = Category.objects.get_or_create(name='Veg', slug='veg')
        self.cat_non_veg, _ = Category.objects.get_or_create(name='Non-Veg', slug='non-veg')
        self.cat_juice, _ = Category.objects.get_or_create(name='Fresh Juices', slug='juices')
        self.cat_dessert, _ = Category.objects.get_or_create(name='Desserts', slug='desserts')

    def test_dish_image_url_keyword_matching(self):
        # 1. Chicken Beriyani -> biryani.jpg
        item_biryani = MenuItem.objects.create(
            name='Chicken Beriyani',
            category=self.cat_non_veg,
            price=Decimal('200.00'),
        )
        self.assertIn('biryani.jpg', item_biryani.image_url)

        # 2. Full Measl -> meals.jpg
        item_meals = MenuItem.objects.create(
            name='Full Measl',
            category=self.cat_veg,
            price=Decimal('150.00'),
        )
        self.assertIn('meals.jpg', item_meals.image_url)

        # 3. Destert -> dessert.jpg
        item_dessert = MenuItem.objects.create(
            name='Destert',
            category=self.cat_dessert,
            price=Decimal('80.00'),
        )
        self.assertIn('dessert.jpg', item_dessert.image_url)

        # 4. Water Millan Juice -> watermelon.jpg
        item_juice = MenuItem.objects.create(
            name='Water Millan Juice',
            category=self.cat_juice,
            price=Decimal('50.00'),
        )
        self.assertIn('watermelon.jpg', item_juice.image_url)

    def test_combo_component_dishes_have_valid_images(self):
        item1 = MenuItem.objects.create(name='Destert', category=self.cat_dessert, price=Decimal('80.00'))
        item2 = MenuItem.objects.create(name='Water Millan Juice', category=self.cat_juice, price=Decimal('50.00'))
        item3 = MenuItem.objects.create(name='Chicken Beriyani', category=self.cat_non_veg, price=Decimal('200.00'))

        combo = FoodCombo.objects.create(
            name='Start Up',
            price=Decimal('280.00'),
            is_active=True
        )
        FoodComboItem.objects.create(combo=combo, menu_item=item1, quantity=1)
        FoodComboItem.objects.create(combo=combo, menu_item=item2, quantity=1)
        FoodComboItem.objects.create(combo=combo, menu_item=item3, quantity=1)

        components = combo.component_dishes_with_images
        self.assertEqual(len(components), 3)
        for comp in components:
            self.assertTrue(comp['image_url'].startswith('/static/images/dishes/'))
            self.assertTrue(comp['image_url'].endswith('.jpg'))

        # Check collage preview structure
        preview = combo.collage_preview
        self.assertTrue(preview['has_images'])
        self.assertIsNotNone(preview['first'])
        self.assertIn('dessert.jpg', preview['first']['image_url'])

    def test_serve_media_with_fallback_intelligent_matching(self):
        # Test missing biryani media file
        req = self.factory.get('/media/menu_items/chicken-beriyani.jpg')
        resp = serve_media_with_fallback(req, 'menu_items/chicken-beriyani.jpg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/jpeg')

        # Test missing meals media file
        req2 = self.factory.get('/media/menu_items/full-measl.jpg')
        resp2 = serve_media_with_fallback(req2, 'menu_items/full-measl.jpg')
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2['Content-Type'], 'image/jpeg')

        # Test missing logo media file
        req_logo = self.factory.get('/media/restaurant_logos/test_logo.png')
        resp_logo = serve_media_with_fallback(req_logo, 'restaurant_logos/test_logo.png')
        self.assertEqual(resp_logo.status_code, 200)
        self.assertEqual(resp_logo['Content-Type'], 'image/svg+xml')
