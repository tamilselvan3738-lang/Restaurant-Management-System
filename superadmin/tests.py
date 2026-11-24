import io
from PIL import Image
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import User, Restaurant

def create_test_image(color='red', size=(100, 100)):
    file = io.BytesIO()
    image = Image.new('RGB', size, color=color)
    image.save(file, 'PNG')
    file.seek(0)
    return file.read()

class CompanyLogoManagementTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Super Admin
        self.superadmin = User.objects.create_superuser(
            username='tamil_super',
            email='tamil@grandbistro.com',
            password='Password123!',
            first_name='Tamil'
        )

    def test_superadmin_creates_admin_with_logo(self):
        self.client.force_login(self.superadmin)
        logo_data = create_test_image(color='blue')
        logo_file = SimpleUploadedFile('bella_logo.png', logo_data, content_type='image/png')

        post_data = {
            'restaurant_name': 'Bella Italia Bistro',
            'restaurant_status': 'ACTIVE',
            'restaurant_plan': 'STARTER',
            'restaurant_logo': logo_file,
            'restaurant_address': '101 Pasta Lane',
            'restaurant_phone': '+1 555-1234',
            'restaurant_email': 'contact@bellabistro.com',
            'first_name': 'Marco',
            'last_name': 'Rossi',
            'username': 'admin_marco',
            'email': 'marco@bellabistro.com',
            'phone_number': '+1 555-9876',
            'role': User.Role.ADMIN,
            'job_title': 'Managing Partner',
            'admin_access_level': User.AdminAccessLevel.FULL,
            'can_manage_staff': 'on',
            'can_manage_discounts': 'on',
            'can_view_financials': 'on',
            'can_edit_menu': 'on',
            'password1': 'AdminPass123!',
            'password2': 'AdminPass123!',
        }

        response = self.client.post(reverse('superadmin:admin_create'), post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Check Admin created
        admin_user = User.objects.get(username='admin_marco')
        self.assertIsNotNone(admin_user.restaurant)
        self.assertEqual(admin_user.restaurant.name, 'Bella Italia Bistro')
        self.assertTrue(bool(admin_user.restaurant.logo))
        self.assertTrue(admin_user.restaurant.logo.name.startswith('restaurant_logos/'))

    def test_admin_and_employee_logo_isolation(self):
        # Create Restaurant A with Logo A
        logo_a = SimpleUploadedFile('logo_a.png', create_test_image(color='red'), content_type='image/png')
        rest_a = Restaurant.objects.create(name='Trattoria Roma', logo=logo_a)
        admin_a = User.objects.create_user(
            username='admin_a',
            email='admin_a@trattoria.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=rest_a
        )
        emp_a = User.objects.create_user(
            username='waiter_a',
            email='waiter_a@trattoria.com',
            password='Password123!',
            role=User.Role.WAITER,
            restaurant=rest_a
        )

        # Create Restaurant B with Logo B
        logo_b = SimpleUploadedFile('logo_b.png', create_test_image(color='green'), content_type='image/png')
        rest_b = Restaurant.objects.create(name='Tokyo Sushi Bar', logo=logo_b)
        admin_b = User.objects.create_user(
            username='admin_b',
            email='admin_b@tokyosushi.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=rest_b
        )
        emp_b = User.objects.create_user(
            username='chef_b',
            email='chef_b@tokyosushi.com',
            password='Password123!',
            role=User.Role.KITCHEN,
            restaurant=rest_b
        )

        # Test Admin A view
        self.client.force_login(admin_a)
        res_a = self.client.get(reverse('dashboard:index'))
        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(res_a.context['RESTAURANT_NAME'], 'Trattoria Roma')
        self.assertIn(rest_a.logo.url, res_a.context['RESTAURANT_LOGO_URL'])
        self.assertContains(res_a, 'Trattoria Roma')
        self.assertContains(res_a, rest_a.logo.url)
        self.assertNotContains(res_a, 'Tokyo Sushi Bar')

        # Test Employee A view
        self.client.force_login(emp_a)
        res_emp_a = self.client.get(reverse('orders:list'))
        self.assertEqual(res_emp_a.status_code, 200)
        self.assertEqual(res_emp_a.context['RESTAURANT_NAME'], 'Trattoria Roma')
        self.assertContains(res_emp_a, rest_a.logo.url)
        self.assertNotContains(res_emp_a, 'Tokyo Sushi Bar')

        # Test Admin B view
        self.client.force_login(admin_b)
        res_b = self.client.get(reverse('dashboard:index'))
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_b.context['RESTAURANT_NAME'], 'Tokyo Sushi Bar')
        self.assertIn(rest_b.logo.url, res_b.context['RESTAURANT_LOGO_URL'])
        self.assertContains(res_b, 'Tokyo Sushi Bar')
        self.assertContains(res_b, rest_b.logo.url)
        self.assertNotContains(res_b, 'Trattoria Roma')

        # Staff isolation: Admin A should only see their own staff
        self.client.force_login(admin_a)
        staff_res = self.client.get(reverse('accounts:staff_list'))
        self.assertContains(staff_res, 'waiter_a')
        self.assertNotContains(staff_res, 'chef_b')

    def test_superadmin_replaces_logo(self):
        # Admin with initial logo
        logo_init = SimpleUploadedFile('initial.png', create_test_image(color='yellow'), content_type='image/png')
        rest = Restaurant.objects.create(name='Cafe Parisian', logo=logo_init)
        admin = User.objects.create_user(
            username='admin_paris',
            email='paris@cafe.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=rest
        )

        self.client.force_login(self.superadmin)
        # Edit Admin to upload new logo
        new_logo = SimpleUploadedFile('replaced.png', create_test_image(color='purple'), content_type='image/png')
        edit_data = {
            'restaurant_name': 'Cafe Parisian Deluxe',
            'restaurant_status': 'ACTIVE',
            'restaurant_plan': 'STARTER',
            'restaurant_logo': new_logo,
            'restaurant_address': '456 Champs-Elysees',
            'restaurant_phone': '+33 1 23 45 67 89',
            'restaurant_email': 'contact@cafeparisian.com',
            'first_name': 'Pierre',
            'last_name': 'Dubois',
            'email': 'pierre@cafeparisian.com',
            'phone_number': '+33 1 23 45 67 89',
            'role': User.Role.ADMIN,
            'job_title': 'General Director',
            'admin_access_level': User.AdminAccessLevel.FULL,
            'can_manage_staff': 'on',
            'can_manage_discounts': 'on',
            'can_view_financials': 'on',
            'can_edit_menu': 'on',
            'is_active': 'on',
        }

        edit_res = self.client.post(reverse('superadmin:admin_edit', args=[admin.id]), edit_data, follow=True)
        self.assertEqual(edit_res.status_code, 200)

        rest.refresh_from_db()
        self.assertEqual(rest.name, 'Cafe Parisian Deluxe')
        self.assertIn('replaced', rest.logo.name)

        # Login as Admin and verify new logo and updated name are displayed immediately
        self.client.force_login(admin)
        dash_res = self.client.get(reverse('dashboard:index'))
        self.assertEqual(dash_res.context['RESTAURANT_NAME'], 'Cafe Parisian Deluxe')
        self.assertIn('replaced', dash_res.context['RESTAURANT_LOGO_URL'])

    def test_default_placeholder_when_no_logo(self):
        # Restaurant without logo
        rest = Restaurant.objects.create(name='No Logo Diner')
        admin = User.objects.create_user(
            username='admin_nologo',
            email='nologo@diner.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=rest
        )
        self.client.force_login(admin)
        res = self.client.get(reverse('dashboard:index'))
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.context['RESTAURANT_LOGO_URL'])
        self.assertContains(res, 'brand-logo-placeholder')
        self.assertContains(res, 'No Logo Diner')


class SuperAdminAddAdminAndCascadeDeactivationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username='tamil_super',
            email='tamil_super@saas.com',
            password='SuperPassword123!',
            first_name='Tamil',
            last_name='Selvan'
        )

    def test_add_admin_creates_restaurant_and_admin_successfully(self):
        self.client.force_login(self.superadmin)
        post_data = {
            'restaurant_name': 'Green Orchard Fine Dining',
            'restaurant_address': '789 Orchard Blvd, Suite 100',
            'restaurant_phone': '+1 555-4321',
            'restaurant_email': 'info@greenorchard.com',
            'restaurant_status': 'ACTIVE',
            'restaurant_plan': 'STARTER',
            'first_name': 'Tamil',
            'last_name': 'Selvan',
            'username': 'tamil',
            'email': 'tamilselvan2003s@gmail.com',
            'phone_number': '+91 9876543210',
            'role': User.Role.ADMIN,
            'job_title': 'Managing Director',
            'admin_access_level': User.AdminAccessLevel.FULL,
            'is_active': 'on',
            'can_manage_staff': 'on',
            'can_manage_discounts': 'on',
            'can_view_financials': 'on',
            'can_edit_menu': 'on',
            'password1': '23MCA093@t',
            'password2': '23MCA093@t',
        }

        response = self.client.post(reverse('superadmin:admin_create'), post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Verify success message
        messages_list = list(response.context['messages'])
        self.assertTrue(any("Restaurant Admin created successfully." in str(m) for m in messages_list))

        # Verify Restaurant record created
        restaurant = Restaurant.objects.filter(name='Green Orchard Fine Dining').first()
        self.assertIsNotNone(restaurant)
        self.assertEqual(restaurant.status, Restaurant.RestaurantStatus.ACTIVE)
        self.assertEqual(restaurant.phone, '+1 555-4321')
        self.assertEqual(restaurant.email, 'info@greenorchard.com')

        # Verify User record created and linked
        admin_user = User.objects.filter(username='tamil').first()
        self.assertIsNotNone(admin_user)
        self.assertEqual(admin_user.restaurant, restaurant)
        self.assertEqual(admin_user.email, 'tamilselvan2003s@gmail.com')
        self.assertEqual(admin_user.role, User.Role.ADMIN)
        self.assertTrue(admin_user.is_active)
        self.assertTrue(admin_user.is_staff)
        self.assertFalse(admin_user.is_superuser)
        self.assertTrue(admin_user.check_password('23MCA093@t'))

    def test_add_admin_rejects_duplicate_username_and_email(self):
        self.client.force_login(self.superadmin)
        rest = Restaurant.objects.create(name='Existing Bistro')
        User.objects.create_user(
            username='tamil_existing',
            email='duplicate@bistro.com',
            password='Password123!',
            restaurant=rest
        )

        post_data = {
            'restaurant_name': 'Another Bistro',
            'first_name': 'Tamil',
            'last_name': 'S',
            'username': 'tamil_existing',
            'email': 'duplicate@bistro.com',
            'password1': 'Password123!',
            'password2': 'Password123!',
        }
        response = self.client.post(reverse('superadmin:admin_create'), post_data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'email', 'This email address is already in use by another account.')

    def test_cascade_employee_deactivation_when_admin_deactivated(self):
        self.client.force_login(self.superadmin)

        # Setup Restaurant A with Admin A and 4 employees
        rest_a = Restaurant.objects.create(name='Restaurant Alpha', status=Restaurant.RestaurantStatus.ACTIVE)
        admin_a = User.objects.create_user(
            username='admin_alpha',
            email='admin@alpha.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=rest_a,
            is_active=True
        )
        cashier_a1 = User.objects.create_user(
            username='cashier_a1', email='cashier1@alpha.com', password='Password123!',
            role=User.Role.CASHIER, restaurant=rest_a, is_active=True
        )
        cashier_a2 = User.objects.create_user(
            username='cashier_a2', email='cashier2@alpha.com', password='Password123!',
            role=User.Role.CASHIER, restaurant=rest_a, is_active=True
        )
        waiter_a1 = User.objects.create_user(
            username='waiter_a1', email='waiter1@alpha.com', password='Password123!',
            role=User.Role.WAITER, restaurant=rest_a, is_active=True
        )
        already_inactive_a = User.objects.create_user(
            username='inactive_emp_a', email='inactive@alpha.com', password='Password123!',
            role=User.Role.KITCHEN, restaurant=rest_a, is_active=False
        )

        # Setup Restaurant B with Admin B and employees (Tenant Isolation Check)
        rest_b = Restaurant.objects.create(name='Restaurant Beta', status=Restaurant.RestaurantStatus.ACTIVE)
        admin_b = User.objects.create_user(
            username='admin_beta', email='admin@beta.com', password='Password123!',
            role=User.Role.ADMIN, restaurant=rest_b, is_active=True
        )
        waiter_b1 = User.objects.create_user(
            username='waiter_b1', email='waiter1@beta.com', password='Password123!',
            role=User.Role.WAITER, restaurant=rest_b, is_active=True
        )
        chef_b1 = User.objects.create_user(
            username='chef_b1', email='chef1@beta.com', password='Password123!',
            role=User.Role.KITCHEN, restaurant=rest_b, is_active=True
        )

        # Trigger deactivation of Admin Alpha
        toggle_url = reverse('superadmin:admin_toggle_status', args=[admin_a.id])
        deact_res = self.client.post(toggle_url, {
            'action': 'deactivate',
            'reason': 'Audit review policy violation'
        }, follow=True)
        self.assertEqual(deact_res.status_code, 200)

        # 1. Verify Admin Alpha is deactivated
        admin_a.refresh_from_db()
        self.assertFalse(admin_a.is_active)

        # 2. Verify Restaurant Alpha status is set to Inactive
        rest_a.refresh_from_db()
        self.assertEqual(rest_a.status, Restaurant.RestaurantStatus.INACTIVE)

        # 3. Verify all active employees under Restaurant Alpha are now deactivated
        cashier_a1.refresh_from_db()
        cashier_a2.refresh_from_db()
        waiter_a1.refresh_from_db()
        already_inactive_a.refresh_from_db()

        self.assertFalse(cashier_a1.is_active)
        self.assertFalse(cashier_a2.is_active)
        self.assertFalse(waiter_a1.is_active)
        self.assertFalse(already_inactive_a.is_active)

        # 4. Verify Multi-Tenant Isolation: Restaurant B and its employees are UNTOUCHED
        admin_b.refresh_from_db()
        rest_b.refresh_from_db()
        waiter_b1.refresh_from_db()
        chef_b1.refresh_from_db()

        self.assertTrue(admin_b.is_active)
        self.assertEqual(rest_b.status, Restaurant.RestaurantStatus.ACTIVE)
        self.assertTrue(waiter_b1.is_active)
        self.assertTrue(chef_b1.is_active)

        # 5. Verify AccountStatusAuditLog
        from superadmin.models import AccountStatusAuditLog
        log = AccountStatusAuditLog.objects.filter(target_user=admin_a, action='CASCADE_DEACTIVATE').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.affected_count, 3)  # Exactly 3 active employees cascade-deactivated
        self.assertIn(cashier_a1.id, log.affected_employee_ids)
        self.assertIn(cashier_a2.id, log.affected_employee_ids)
        self.assertIn(waiter_a1.id, log.affected_employee_ids)
        self.assertNotIn(already_inactive_a.id, log.affected_employee_ids)

        # 6. Verify Safe Reactivation: Reactivating Admin A restores Admin A & Restaurant A, but keeps employees inactive
        react_res = self.client.post(toggle_url, {'action': 'activate'}, follow=True)
        self.assertEqual(react_res.status_code, 200)

        admin_a.refresh_from_db()
        rest_a.refresh_from_db()
        self.assertTrue(admin_a.is_active)
        self.assertEqual(rest_a.status, Restaurant.RestaurantStatus.ACTIVE)

        # Employees MUST still be inactive (Safe Reactivation Principle)
        cashier_a1.refresh_from_db()
        cashier_a2.refresh_from_db()
        waiter_a1.refresh_from_db()
        self.assertFalse(cashier_a1.is_active)
        self.assertFalse(cashier_a2.is_active)
        self.assertFalse(waiter_a1.is_active)

    def test_inactive_employee_session_blocked_by_middleware(self):
        rest = Restaurant.objects.create(name='Cafe Bistro', status=Restaurant.RestaurantStatus.ACTIVE)
        emp = User.objects.create_user(
            username='waiter_joe', email='joe@cafe.com', password='Password123!',
            role=User.Role.WAITER, restaurant=rest, is_active=False
        )

        self.client.force_login(emp)
        # Attempt to access protected endpoint
        res = self.client.get(reverse('orders:list'))
        # Both Django auth backend and middleware ensure deactivated accounts cannot access and are redirected to login
        expected_url = f"{reverse('accounts:login')}?next={reverse('orders:list')}"
        self.assertRedirects(res, expected_url)

        # Also verify suspended user is caught and blocked by TenantContextMiddleware
        active_emp = User.objects.create_user(
            username='waiter_active', email='active@cafe.com', password='Password123!',
            role=User.Role.WAITER, restaurant=rest, is_active=True, is_suspended=True
        )
        self.client.force_login(active_emp)
        res_suspended = self.client.get(reverse('orders:list'))
        self.assertRedirects(res_suspended, reverse('accounts:login'))
