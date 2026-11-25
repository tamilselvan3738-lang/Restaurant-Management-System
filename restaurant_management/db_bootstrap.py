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

        # 5. Backfill any unlinked/orphaned BillRequest, DiningTable, CustomerSession, or Invoice records
        try:
            from billing.models import BillRequest, Invoice
            from tables.models import DiningTable, CustomerSession
            DiningTable.objects.filter(restaurant__isnull=True).update(restaurant=restaurant)
            CustomerSession.objects.filter(restaurant__isnull=True).update(restaurant=restaurant)
            Invoice.objects.filter(restaurant__isnull=True).update(restaurant=restaurant)
            for br in BillRequest.objects.filter(restaurant__isnull=True):
                r = (
                    getattr(br.table, 'restaurant', None)
                    or getattr(br.session, 'restaurant', None)
                    or getattr(br.invoice, 'restaurant', None)
                    or restaurant
                )
                br.restaurant = r
                br.save(update_fields=['restaurant'])
        except Exception:
            pass

        # 6. Ensure media directory exists and seed default dishes
        try:
            from django.conf import settings
            import shutil
            media_menu_dir = os.path.join(str(settings.MEDIA_ROOT), 'menu_items')
            os.makedirs(media_menu_dir, exist_ok=True)
            static_dishes_dir = os.path.join(str(settings.BASE_DIR), 'static', 'images', 'dishes')
            if os.path.isdir(static_dishes_dir):
                for fname in os.listdir(static_dishes_dir):
                    src_file = os.path.join(static_dishes_dir, fname)
                    dest_file = os.path.join(media_menu_dir, fname)
                    if os.path.isfile(src_file) and not os.path.exists(dest_file):
                        shutil.copy2(src_file, dest_file)
        except Exception:
            pass

        _INITIALIZED = True
        print(f"[BOOTSTRAP] Database initialized: Superadmin '{username}' and Admin 'tamil' ready.")
    except Exception as exc:
        print(f"[BOOTSTRAP NOTICE] Database setup: {exc}")
