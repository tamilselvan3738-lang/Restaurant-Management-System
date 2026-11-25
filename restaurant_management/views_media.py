import mimetypes
import os
from django.conf import settings
from django.http import FileResponse
from django.views.static import serve


# Food keyword mapping for intelligent image resolution
FOOD_KEYWORD_MAP = [
    (('biryani', 'beriyani', 'briyani', 'pulao', 'dum biryani'), 'biryani.jpg'),
    (('meal', 'measl', 'thali', 'south', 'lunch', 'dinner', 'combo meal'), 'meals.jpg'),
    (('chicken', 'murgh', 'koli', 'poultry', 'tikka', 'tandoori'), 'chicken.jpg'),
    (('paneer', 'cottage', 'butter masala', 'shahi paneer'), 'paneer.jpg'),
    (('pizza', 'margherita', 'pepperoni', 'crust', 'calzone'), 'pizza.jpg'),
    (('pasta', 'spaghetti', 'tagliolini', 'carbonara', 'penne', 'macaroni', 'noodle'), 'pasta.jpg'),
    (('steak', 'ribeye', 'beef', 'mutton', 'lamb', 'tenderloin', 'meat'), 'steak.jpg'),
    (('salmon', 'fish', 'calamari', 'seafood', 'prawn', 'shrimp', 'crab'), 'salmon.jpg'),
    (('starter', 'bruschetta', 'arancini', 'appetizer', 'finger', 'crispy', 'fry', 'snack', 'roll'), 'starter.jpg'),
    (('dessert', 'destert', 'sweet', 'cake', 'tiramisu', 'pudding', 'ice cream', 'ice', 'chocolate', 'lava', 'pastry', 'halwa', 'gulab'), 'dessert.jpg'),
    (('water', 'millan', 'melon', 'watermelon', 'cooler'), 'watermelon.jpg'),
    (('orange', 'juice', 'smoothie', 'shake', 'beverage', 'mocktail'), 'juice.jpg'),
    (('mango', 'lassi', 'badam'), 'lassi.jpg'),
    (('coffee', 'espresso', 'tea', 'latte', 'cappuccino', 'mocha', 'brew'), 'coffee.jpg'),
    (('cocktail', 'drink', 'negroni', 'bar', 'wine', 'beer', 'whiskey', 'gin'), 'cocktail.jpg'),
]


def serve_media_with_fallback(request, path):
    """
    Serves user-uploaded media files from settings.MEDIA_ROOT.
    If the requested file does not exist on disk (e.g. following an ephemeral container
    restart or missing asset on Render), gracefully returns a matching high-resolution
    real food photograph or brand asset with HTTP 200 instead of a broken 404 or generic cloche.
    """
    full_path = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), path))
    media_root_str = os.path.normpath(str(settings.MEDIA_ROOT))

    # Security check: ensure path does not traverse outside MEDIA_ROOT
    if full_path.startswith(media_root_str) and os.path.isfile(full_path):
        return serve(request, path, document_root=settings.MEDIA_ROOT)

    static_images_dir = os.path.join(str(settings.BASE_DIR), 'static', 'images')
    dishes_dir = os.path.join(static_images_dir, 'dishes')
    basename = os.path.basename(path)

    # 1. Check if the exact filename exists in static/images/dishes or static/images
    exact_dish_file = os.path.join(dishes_dir, basename)
    if os.path.isfile(exact_dish_file):
        content_type, _ = mimetypes.guess_type(exact_dish_file)
        response = FileResponse(open(exact_dish_file, 'rb'), content_type=content_type or 'image/jpeg')
        response['Cache-Control'] = 'public, max-age=86400'
        return response

    exact_static_file = os.path.join(static_images_dir, basename)
    if os.path.isfile(exact_static_file):
        content_type, _ = mimetypes.guess_type(exact_static_file)
        response = FileResponse(open(exact_static_file, 'rb'), content_type=content_type or 'image/jpeg')
        response['Cache-Control'] = 'public, max-age=86400'
        return response

    # 2. Contextual analysis based on requested path and query parameters
    query_name = request.GET.get('name', '')
    query_cat = request.GET.get('category', '')
    search_str = f"{path} {query_name} {query_cat}".lower()

    if 'logo' in search_str or 'restaurant' in search_str:
        fallback_file = os.path.join(static_images_dir, 'default_restaurant_logo.svg')
    elif 'profile' in search_str or 'avatar' in search_str:
        fallback_file = os.path.join(static_images_dir, 'default_avatar.svg')
    else:
        # Intelligent Food Visual Matcher
        matched_dish = None
        for keywords, filename in FOOD_KEYWORD_MAP:
            if any(kw in search_str for kw in keywords):
                candidate = os.path.join(dishes_dir, filename)
                if os.path.isfile(candidate):
                    matched_dish = candidate
                    break

        if matched_dish:
            fallback_file = matched_dish
        else:
            # Fallback to high-resolution delicious food photo
            default_dish_candidate = os.path.join(dishes_dir, 'default_dish.jpg')
            if os.path.isfile(default_dish_candidate):
                fallback_file = default_dish_candidate
            else:
                fallback_file = os.path.join(static_images_dir, 'default_dish.svg')

    if os.path.isfile(fallback_file):
        content_type, _ = mimetypes.guess_type(fallback_file)
        if fallback_file.endswith('.svg'):
            content_type = 'image/svg+xml'
        elif fallback_file.endswith('.jpg') or fallback_file.endswith('.jpeg'):
            content_type = 'image/jpeg'
        elif fallback_file.endswith('.png'):
            content_type = 'image/png'

        response = FileResponse(open(fallback_file, 'rb'), content_type=content_type or 'image/jpeg')
        response['Cache-Control'] = 'public, max-age=86400'
        return response

    # Fall back to standard serve if fallback itself cannot be read
    return serve(request, path, document_root=settings.MEDIA_ROOT)
