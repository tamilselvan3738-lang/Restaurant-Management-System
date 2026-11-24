#!/usr/bin/env bash

set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate --noinput

python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'restaurant_management.settings')
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
username = 'Dhanusha'
password = '23MCA093@t'
norm = username.strip().lower()
user = User.objects.filter(normalized_username=norm, restaurant__isnull=True).first()
if not user:
    user = User(
        username=username,
        email='dhanusha@growwpark.com',
        role='ADMIN',
        is_staff=True,
        is_superuser=True,
        is_active=True
    )
user.set_password(password)
user.role = 'ADMIN'
user.is_staff = True
user.is_superuser = True
user.is_active = True
user.save()
print(f'*** SUPERADMIN {username} IS READY ***')
"
