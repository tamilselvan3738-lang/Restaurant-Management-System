import mimetypes
import os
import re
from django.conf import settings
from django.http import FileResponse
from django.views.static import serve


# Food keyword mapping for intelligent image resolution
FOOD_KEYWORD_MAP = [
    (('biryani', 'beriyani', 'briyani', 'pulao', 'dum biryani'), 'biryani.jpg'),
    (('water millan', 'watermelon', 'water melon', 'millan', 'melon', 'cooler', 'mojito'), 'watermelon.jpg'),
    (('orange', 'juice', 'juices', 'smoothie', 'smoothies', 'shake', 'shakes', 'beverage', 'beverages', 'mocktail', 'mocktails'), 'juice.jpg'),
    (('mango', 'lassi', 'lassis', 'badam'), 'lassi.jpg'),
    (('coffee', 'espresso', 'tea', 'latte', 'cappuccino', 'mocha', 'brew'), 'coffee.jpg'),
    (('cocktail', 'cocktails', 'drink', 'drinks', 'negroni', 'bar', 'wine', 'beer', 'whiskey', 'gin'), 'cocktail.jpg'),
    (('meal', 'meals', 'measl', 'measls', 'thali', 'thalis', 'south', 'lunch', 'dinner', 'combo meal'), 'meals.jpg'),
    (('chicken', 'murgh', 'koli', 'poultry', 'tikka', 'tandoori'), 'chicken.jpg'),
    (('paneer', 'cottage', 'butter masala', 'shahi paneer'), 'paneer.jpg'),
    (('pizza', 'pizzas', 'margherita', 'pepperoni', 'crust', 'calzone'), 'pizza.jpg'),
    (('pasta', 'pastas', 'spaghetti', 'tagliolini', 'carbonara', 'penne', 'macaroni', 'noodle', 'noodles'), 'pasta.jpg'),
    (('steak', 'steaks', 'ribeye', 'beef', 'mutton', 'lamb', 'tenderloin', 'meat'), 'steak.jpg'),
    (('salmon', 'fish', 'calamari', 'seafood', 'prawn', 'prawns', 'shrimp', 'shrimps', 'crab', 'crabs'), 'salmon.jpg'),
    (('starter', 'starters', 'bruschetta', 'arancini', 'appetizer', 'appetizers', 'finger', 'crispy', 'fry', 'snack', 'snacks', 'roll', 'rolls'), 'starter.jpg'),
    (('dessert', 'desserts', 'destert', 'desterts', 'sweet', 'sweets', 'cake', 'cakes', 'tiramisu', 'pudding', 'ice cream', 'chocolate', 'lava', 'pastry', 'pastries', 'halwa', 'gulab', 'kulfi'), 'dessert.jpg'),
]


def _matches_keyword(kw, text):
    if ' ' in kw or '-' in kw:
        return kw in text
    return bool(re.search(r'\b' + re.escape(kw), text))


def serve_media_with_fallback(request, path):
    """
    Serves user-uploaded media files from settings.MEDIA_ROOT.
    If the requested file does not exist on disk (e.g. following an ephemeral container
    restart or missing asset on Render), gracefully returns a matching high-resolution
    real food photograph or brand asset with HTTP 200 instead of a broken 404 or generic cloche.
    """
    from urllib.parse import unquote
    unquoted_path = unquote(path).strip()
    full_path = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), unquoted_path))
    media_root_str = os.path.normpath(str(settings.MEDIA_ROOT))

    # 1. Direct file in MEDIA_ROOT
    if full_path.lower().startswith(media_root_str.lower()) and os.path.isfile(full_path):
        return serve(request, unquoted_path, document_root=settings.MEDIA_ROOT)

    basename = os.path.basename(unquoted_path)

    # 2. Check under media/menu_items/ or media/combos/
    for sub in ['menu_items', 'combos']:
        sub_path = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), sub, basename))
        if sub_path.lower().startswith(media_root_str.lower()) and os.path.isfile(sub_path):
            return serve(request, f"{sub}/{basename}", document_root=settings.MEDIA_ROOT)

    static_images_dir = os.path.join(str(settings.BASE_DIR), 'static', 'images')
    dishes_dir = os.path.join(static_images_dir, 'dishes')

    # 3. Check if the exact filename exists in static/images/dishes or static/images
    exact_dish_file = os.path.join(dishes_dir, basename)
    if os.path.isfile(exact_dish_file):
        content_type, _ = mimetypes.guess_type(exact_dish_file)
        response = FileResponse(open(exact_dish_file, 'rb'), content_type=content_type or 'image/jpeg')
        response['Cache-Control'] = 'public, max-age=300, must-revalidate'
        return response

    exact_static_file = os.path.join(static_images_dir, basename)
    if os.path.isfile(exact_static_file):
        content_type, _ = mimetypes.guess_type(exact_static_file)
        response = FileResponse(open(exact_static_file, 'rb'), content_type=content_type or 'image/jpeg')
        response['Cache-Control'] = 'public, max-age=300, must-revalidate'
        return response

    # 4. Contextual analysis based on requested path and query parameters
    query_name = request.GET.get('name', '')
    query_cat = request.GET.get('category', '')

    # If query parameters are not supplied, attempt database lookup by image filename
    if not query_name:
        try:
            from menu.models import MenuItem, FoodCombo
            matched_item = MenuItem.objects.filter(image__endswith=basename).first()
            if matched_item:
                query_name = matched_item.name
                if matched_item.category:
                    query_cat = matched_item.category.name
            else:
                matched_combo = FoodCombo.objects.filter(image__endswith=basename).first()
                if matched_combo:
                    query_name = matched_combo.name
        except Exception:
            pass

    search_str = f"{unquoted_path} {basename} {query_name} {query_cat}".lower().replace('-', ' ').replace('_', ' ')

    if 'logo' in search_str or 'restaurant' in search_str:
        # Check if any Restaurant in the database has a persistent base64 logo
        try:
            from accounts.models import Restaurant
            matched_resto = Restaurant.objects.filter(logo__endswith=basename).first()
            if not matched_resto:
                matched_resto = Restaurant.objects.exclude(logo_base64='').first()
            if matched_resto and matched_resto.logo_base64:
                import base64
                from django.http import HttpResponse
                parts = matched_resto.logo_base64.split(',', 1)
                if len(parts) == 2:
                    header, b64_data = parts
                    c_type = header.split(';')[0].replace('data:', '') or 'image/png'
                    raw_bytes = base64.b64decode(b64_data)
                    try:
                        dest_dir = os.path.dirname(full_path)
                        os.makedirs(dest_dir, exist_ok=True)
                        with open(full_path, 'wb') as f:
                            f.write(raw_bytes)
                    except Exception:
                        pass
                    resp = HttpResponse(raw_bytes, content_type=c_type)
                    resp['Cache-Control'] = 'public, max-age=300, must-revalidate'
                    return resp
        except Exception:
            pass
        fallback_file = os.path.join(static_images_dir, 'default_restaurant_logo.svg')
    elif 'profile' in search_str or 'avatar' in search_str:
        fallback_file = os.path.join(static_images_dir, 'default_avatar.svg')
    else:
        # Intelligent Food Visual Matcher
        matched_dish = None
        for keywords, filename in FOOD_KEYWORD_MAP:
            if any(_matches_keyword(kw, search_str) for kw in keywords):
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
        response['Cache-Control'] = 'public, max-age=300, must-revalidate'
        return response

    # Fall back to standard serve if fallback itself cannot be read
    return serve(request, path, document_root=settings.MEDIA_ROOT)
