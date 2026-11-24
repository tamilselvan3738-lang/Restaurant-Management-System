"""
WSGI config for restaurant_management project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'restaurant_management.settings')

application = get_wsgi_application()

# Automatically ensure database migrations and superadmin are ready
try:
    from restaurant_management.db_bootstrap import ensure_database_ready
    ensure_database_ready()
except Exception as e:
    print(f"[WSGI INIT NOTICE] {e}")
