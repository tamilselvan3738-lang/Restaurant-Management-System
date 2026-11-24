#!/usr/bin/env bash

set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate --noinput

python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'restaurant_management.settings')
django.setup()
from restaurant_management.db_bootstrap import ensure_database_ready
ensure_database_ready()
"
