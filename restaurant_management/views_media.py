import os
from django.conf import settings
from django.http import FileResponse
from django.views.static import serve


def serve_media_with_fallback(request, path):
    """
    Serves user-uploaded media files from settings.MEDIA_ROOT.
    If the requested file does not exist on disk (e.g. following an ephemeral container
    restart or missing asset), gracefully returns the corresponding high-resolution SVG fallback
    with HTTP 200 instead of a broken 404, preventing broken image placeholders across all views.
    """
    full_path = os.path.normpath(os.path.join(str(settings.MEDIA_ROOT), path))
    media_root_str = os.path.normpath(str(settings.MEDIA_ROOT))

    # Security check: ensure path does not traverse outside MEDIA_ROOT
    if full_path.startswith(media_root_str) and os.path.isfile(full_path):
        return serve(request, path, document_root=settings.MEDIA_ROOT)

    # File does not exist on disk - determine best fallback SVG asset
    static_images_dir = os.path.join(str(settings.BASE_DIR), 'static', 'images')

    lower_path = path.lower()
    if 'logo' in lower_path or 'restaurant' in lower_path:
        fallback_file = os.path.join(static_images_dir, 'default_restaurant_logo.svg')
    elif 'profile' in lower_path or 'avatar' in lower_path:
        fallback_file = os.path.join(static_images_dir, 'default_avatar.svg')
    else:
        # Default for dishes, combos, menu items, drinks, pizzas
        fallback_file = os.path.join(static_images_dir, 'default_dish.svg')

    if os.path.isfile(fallback_file):
        response = FileResponse(open(fallback_file, 'rb'), content_type='image/svg+xml')
        response['Cache-Control'] = 'public, max-age=86400'
        return response

    # Fall back to standard serve if fallback itself cannot be read
    return serve(request, path, document_root=settings.MEDIA_ROOT)
