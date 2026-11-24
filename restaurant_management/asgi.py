import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'restaurant_management.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import tables.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            tables.routing.websocket_urlpatterns
        )
    ),
})

# Automatically ensure database migrations and superadmin are ready
try:
    from restaurant_management.db_bootstrap import ensure_database_ready
    ensure_database_ready()
except Exception as e:
    print(f"[ASGI INIT NOTICE] {e}")
