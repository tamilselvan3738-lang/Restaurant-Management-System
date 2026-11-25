"""
Dish Image Resolver & Intelligent Food Visual Matcher.
Guarantees every dish, combo, and category gets a vibrant, appetizing food photo
even when running on ephemeral environments (e.g. Render) or when an admin creates
a menu item without uploading a picture.
"""

import os
import re
from django.conf import settings

# Keyword to static image mapping
FOOD_KEYWORD_MAP = [
    # Biryani / Rice specialties
    (('biryani', 'beriyani', 'briyani', 'pulao', 'dum biryani'), 'biryani.jpg'),
    # Watermelon / Coolers / Fresh beverages
    (('water millan', 'watermelon', 'water melon', 'millan', 'melon', 'cooler', 'mojito'), 'watermelon.jpg'),
    # Juices & Smoothies
    (('orange', 'juice', 'juices', 'smoothie', 'smoothies', 'shake', 'shakes', 'beverage', 'beverages', 'mocktail', 'mocktails'), 'juice.jpg'),
    # Lassi & Traditional drinks
    (('mango', 'lassi', 'lassis', 'badam'), 'lassi.jpg'),
    # Coffee & Tea
    (('coffee', 'espresso', 'tea', 'latte', 'cappuccino', 'mocha', 'brew'), 'coffee.jpg'),
    # Cocktails & Bar drinks
    (('cocktail', 'cocktails', 'drink', 'drinks', 'negroni', 'bar', 'wine', 'beer', 'whiskey', 'gin'), 'cocktail.jpg'),
    # Full meals / Thali / Indian lunch
    (('meal', 'meals', 'measl', 'measls', 'thali', 'thalis', 'south', 'lunch', 'dinner', 'combo meal'), 'meals.jpg'),
    # Chicken / Poultry
    (('chicken', 'murgh', 'koli', 'poultry', 'tikka', 'tandoori'), 'chicken.jpg'),
    # Paneer / Vegetarian curry
    (('paneer', 'cottage', 'butter masala', 'shahi paneer'), 'paneer.jpg'),
    # Pizza
    (('pizza', 'pizzas', 'margherita', 'pepperoni', 'crust', 'calzone'), 'pizza.jpg'),
    # Pasta
    (('pasta', 'pastas', 'spaghetti', 'tagliolini', 'carbonara', 'penne', 'macaroni', 'noodle', 'noodles'), 'pasta.jpg'),
    # Steaks & Red Meats
    (('steak', 'steaks', 'ribeye', 'beef', 'mutton', 'lamb', 'tenderloin', 'meat'), 'steak.jpg'),
    # Seafood
    (('salmon', 'fish', 'calamari', 'seafood', 'prawn', 'prawns', 'shrimp', 'shrimps', 'crab', 'crabs'), 'salmon.jpg'),
    # Starters & Appetizers
    (('starter', 'starters', 'bruschetta', 'arancini', 'appetizer', 'appetizers', 'finger', 'crispy', 'fry', 'snack', 'snacks', 'roll', 'rolls'), 'starter.jpg'),
    # Desserts & Sweets
    (('dessert', 'desserts', 'destert', 'desterts', 'sweet', 'sweets', 'cake', 'cakes', 'tiramisu', 'pudding', 'ice cream', 'chocolate', 'lava', 'pastry', 'pastries', 'halwa', 'gulab', 'kulfi'), 'dessert.jpg'),
]

# Category slug / name mapping fallback
CATEGORY_MAP = {
    'pizzas': 'pizza.jpg',
    'pastas': 'pasta.jpg',
    'juices': 'juice.jpg',
    'desserts': 'dessert.jpg',
    'starters': 'starter.jpg',
    'beverages': 'cocktail.jpg',
    'veg': 'meals.jpg',
    'non-veg': 'chicken.jpg',
    'mains': 'biryani.jpg',
}

DEFAULT_DISH_IMAGE = '/static/images/dishes/default_dish.jpg'


def _matches_keyword(kw, text):
    if ' ' in kw or '-' in kw:
        return kw in text
    return bool(re.search(r'\b' + re.escape(kw), text))


def get_dish_static_image(name='', category_name='', category_slug=''):
    """
    Finds the most relevant static dish photo URL based on name and category keywords.
    """
    search_str = f"{name or ''} {category_name or ''} {category_slug or ''}".lower()

    for keywords, filename in FOOD_KEYWORD_MAP:
        if any(_matches_keyword(kw, search_str) for kw in keywords):
            return f"/static/images/dishes/{filename}"

    # Try category fallback
    cat_slug_lower = (category_slug or '').lower().strip()
    if cat_slug_lower in CATEGORY_MAP:
        return f"/static/images/dishes/{CATEGORY_MAP[cat_slug_lower]}"

    cat_name_lower = (category_name or '').lower().strip()
    for cat_key, cat_file in CATEGORY_MAP.items():
        if cat_key in cat_name_lower:
            return f"/static/images/dishes/{cat_file}"

    return DEFAULT_DISH_IMAGE


def get_dish_image_url(dish_name, category_name='', category_slug='', image_field=None):
    """
    Returns the URL for a dish.
    If image_field has a valid file, returns image_field.url.
    If image_field is missing or unassigned, dynamically returns the matched static food photo.
    """
    if image_field:
        try:
            if hasattr(image_field, 'url') and getattr(image_field, 'name', None):
                return image_field.url
        except Exception:
            pass

    return get_dish_static_image(
        name=dish_name,
        category_name=category_name,
        category_slug=category_slug
    )
