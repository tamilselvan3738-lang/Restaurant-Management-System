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
