"""
Database bootstrap and self-healing initialization.
Guarantees that on production boot (Render, Railway, etc.):
1. All pending database migrations are automatically applied (preventing 'relation does not exist' errors).
2. The initial Superadmin account ('Dhanusha') is provisioned and active.
3. The default restaurant tenant ('Growwpark') is provisioned if none exists.
"""

import os
import sys

_INITIALIZED = False


def ensure_database_ready():
    global _INITIALIZED
    if _INITIALIZED:
        return

    # Skip during commands like collectstatic, makemigrations, or test runs
    argv_str = ' '.join(sys.argv)
    if any(cmd in argv_str for cmd in ['collectstatic', 'makemigrations', 'test']):
        return

    try:
        from django.core.management import call_command

        # 1. Apply all migrations non-interactively
        call_command('migrate', interactive=False)

        # 2. Provision Superadmin
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
                is_active=True,
            )
        user.set_password(password)
        user.role = 'ADMIN'
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()

        # 3. Provision Default Restaurant if none exists
        from accounts.models import Restaurant
        restaurant, _ = Restaurant.objects.get_or_create(
            name='Growwpark',
            defaults={
                'code': 'REST-0001',
                'status': Restaurant.RestaurantStatus.ACTIVE,
                'plan': Restaurant.SubscriptionPlan.ENTERPRISE,
            }
        )

        # 4. Provision Restaurant Admin 'tamil'
        t_user = User.objects.filter(normalized_username='tamil', restaurant=restaurant).first()
        if not t_user:
            t_user = User.objects.filter(normalized_username='tamil').first()
        if not t_user:
            t_user = User(
                username='tamil',
                email='tamilselvan3738@gmail.com',
                first_name='Tamil',
                last_name='S',
                restaurant=restaurant,
                role='ADMIN',
                is_staff=True,
                is_active=True,
                employee_id='EMP-1001',
            )
        else:
            if not t_user.restaurant:
                t_user.restaurant = restaurant
        t_user.set_password('23MCA095@t')
        t_user.role = 'ADMIN'
        t_user.is_superuser = False
        t_user.is_staff = True
        t_user.is_active = True
        t_user.save()

        _INITIALIZED = True
        print(f"[BOOTSTRAP] Database initialized: Superadmin '{username}' and Admin 'tamil' ready.")
    except Exception as exc:
        print(f"[BOOTSTRAP NOTICE] Database setup: {exc}")
