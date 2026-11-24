from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User

class ThemeToggleFunctionalityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='theme_tester',
            password='Password123!',
            role=User.Role.ADMIN
        )

    def test_theme_toggle_on_login_page(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        # Check pre-render anti-flicker script in head
        self.assertContains(response, "localStorage.getItem('rms-theme')")
        self.assertContains(response, 'data-bs-theme')
        # Check guest theme toggle
        self.assertContains(response, 'id="themeToggleGuest"')
        self.assertContains(response, 'themeIconGuest')

    def test_theme_toggle_laptop_and_mobile_in_authenticated_navbar_and_sidebar(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard:index'))
        self.assertEqual(response.status_code, 200)
        
        # 1. Laptop View: Navbar Toggle
        self.assertContains(response, 'id="themeToggleLaptop"')
        self.assertContains(response, 'd-none d-lg-inline-flex')
        self.assertContains(response, 'id="themeIconLaptop"')
        self.assertContains(response, 'id="themeLabelLaptop"')

        # 2. Mobile/Tablet View: Inside Hamburger Menu Sidebar
        self.assertContains(response, 'id="themeToggleMobile"')
        self.assertContains(response, 'sidebar-theme-panel')
        self.assertContains(response, 'd-lg-none')
        self.assertContains(response, 'id="themeIconMobile"')
        self.assertContains(response, 'id="themeLabelMobile"')
        self.assertContains(response, 'id="sidebarBackdrop"')

        # 3. JavaScript Theme Manager is loaded
        self.assertContains(response, 'function updateThemeUI')
        self.assertContains(response, 'function toggleTheme')


class EmployeeManagementAndAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username='admin_boss',
            email='admin@restaurant.com',
            phone_number='+919876543210',
            password='AdminPassword123!',
            role=User.Role.ADMIN
        )

        self.staff = User.objects.create_user(
            username='chef_gordon',
            email='chef@restaurant.com',
            phone_number='+919876543211',
            password='ChefPassword123!',
            role=User.Role.KITCHEN
        )

    def test_employee_id_auto_generation(self):
        # Verify employee_id auto-generation
        self.assertTrue(self.staff.employee_id.startswith('EMP-'))

    def test_multi_credential_backend_authentication(self):
        from django.contrib.auth import authenticate

        # 1. Login with Username + Password (Allowed)
        user_uname = authenticate(username='chef_gordon', password='ChefPassword123!')
        self.assertIsNotNone(user_uname)
        self.assertEqual(user_uname.username, 'chef_gordon')

        # 2. Login with Email + Password (Allowed)
        user_email = authenticate(username='chef@restaurant.com', password='ChefPassword123!')
        self.assertIsNotNone(user_email)
        self.assertEqual(user_email.username, 'chef_gordon')

        # 3. Login with Employee ID (Disallowed -> returns None)
        user_emp = authenticate(username=self.staff.employee_id, password='ChefPassword123!')
        self.assertIsNone(user_emp)

        # 4. Login with Phone Number (Disallowed -> returns None)
        user_phone = authenticate(username='+919876543211', password='ChefPassword123!')
        self.assertIsNone(user_phone)

        # 5. Invalid credentials return None
        bad_user = authenticate(username='chef_gordon', password='wrong_password')
        self.assertIsNone(bad_user)

    def test_login_view_multi_credential(self):
        # Login using Username and Password (Allowed)
        resp_username = self.client.post(reverse('accounts:login'), {
            'username': 'chef_gordon',
            'password': 'ChefPassword123!'
        })
        self.assertEqual(resp_username.status_code, 302)
        self.client.logout()

        # Login using Email and Password (Allowed)
        resp_email = self.client.post(reverse('accounts:login'), {
            'username': 'chef@restaurant.com',
            'password': 'ChefPassword123!'
        })
        self.assertEqual(resp_email.status_code, 302)
        self.client.logout()

        # Login using Employee ID (Disallowed -> does not log in)
        resp_emp = self.client.post(reverse('accounts:login'), {
            'username': self.staff.employee_id,
            'password': 'ChefPassword123!'
        })
        self.assertEqual(resp_emp.status_code, 200)
        self.assertContains(resp_emp, "Invalid credentials")

    def test_staff_list_and_search_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'chef_gordon')
        self.assertContains(response, self.staff.employee_id)

        # Search by employee ID
        search_res = self.client.get(reverse('accounts:staff_list'), {'q': self.staff.employee_id})
        self.assertEqual(search_res.status_code, 200)
        self.assertContains(search_res, 'chef_gordon')

        # Filter by role
        role_res = self.client.get(reverse('accounts:staff_list'), {'role': 'KITCHEN'})
        self.assertEqual(role_res.status_code, 200)
        self.assertContains(role_res, 'chef_gordon')

    def test_staff_create_and_edit_without_pin_and_dept_shift(self):
        self.client.force_login(self.admin)
        
        # 1. GET staff create form
        res_create_get = self.client.get(reverse('accounts:staff_create'))
        self.assertEqual(res_create_get.status_code, 200)
        self.assertNotContains(res_create_get, 'Quick Staff PIN')
        self.assertNotContains(res_create_get, 'Department / Station')
        self.assertNotContains(res_create_get, 'Assigned Work Shift')
        self.assertContains(res_create_get, 'id_employee_id')

        # 2. GET staff edit form
        res_edit_get = self.client.get(reverse('accounts:staff_edit', args=[self.staff.id]))
        self.assertEqual(res_edit_get.status_code, 200)
        self.assertNotContains(res_edit_get, 'Quick Staff PIN')
        self.assertNotContains(res_edit_get, 'Department / Station')
        self.assertNotContains(res_edit_get, 'Assigned Work Shift')


class UserProfileUpdateFunctionalityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username='admin_profile_test',
            first_name='OriginalFirst',
            last_name='OriginalLast',
            email='admin_orig@restaurant.com',
            phone_number='+919876540001',
            password='AdminPass123!',
            role=User.Role.ADMIN,
            address='Old Street 1, Chennai'
        )
        self.chef = User.objects.create_user(
            username='chef_profile_test',
            first_name='ChefFirst',
            last_name='ChefLast',
            email='chef_orig@restaurant.com',
            phone_number='+919876540002',
            password='ChefPass123!',
            role=User.Role.CHEF,
            address='Old Kitchen Lane, Chennai'
        )
        self.cashier = User.objects.create_user(
            username='cashier_profile_test',
            first_name='CashierFirst',
            last_name='CashierLast',
            email='cashier_orig@restaurant.com',
            phone_number='+919876540003',
            password='CashierPass123!',
            role=User.Role.CASHIER,
            address='Counter Desk 2, Chennai'
        )
        self.customer = User.objects.create_user(
            username='customer_profile_test',
            first_name='CustomerFirst',
            last_name='CustomerLast',
            email='customer_orig@restaurant.com',
            phone_number='+919876540004',
            password='CustomerPass123!',
            role=User.Role.CUSTOMER,
            address='Customer Apt 101, Chennai'
        )

    def test_admin_can_update_personal_information(self):
        self.client.force_login(self.admin)
        res = self.client.post(reverse('accounts:profile'), {
            'update_profile': '1',
            'first_name': 'UpdatedAdminFirst',
            'last_name': 'UpdatedAdminLast',
            'email': 'admin_updated@restaurant.com',
            'phone_number': '+919999888877',
            'address': 'New Luxury Villa, Chennai',
            'emergency_contact_name': 'Dr. Sharma',
            'emergency_contact_phone': '+919888777666',
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, 'UpdatedAdminFirst')
        self.assertEqual(self.admin.last_name, 'UpdatedAdminLast')
        self.assertEqual(self.admin.email, 'admin_updated@restaurant.com')
        self.assertEqual(self.admin.phone_number, '+919999888877')
        self.assertEqual(self.admin.address, 'New Luxury Villa, Chennai')
        self.assertEqual(self.admin.emergency_contact_name, 'Dr. Sharma')
        self.assertEqual(self.admin.emergency_contact_phone, '+919888777666')

    def test_chef_can_update_personal_information_via_chef_profile(self):
        self.client.force_login(self.chef)
        res = self.client.post(reverse('chef:profile'), {
            'action': 'update_profile',
            'first_name': 'MasterChef',
            'last_name': 'Ramsay',
            'email': 'masterchef_ramsay@restaurant.com',
            'phone_number': '+919876543999',
            'address': 'Culinary Suite 5, Marina Beach',
            'emergency_contact_name': 'Tana Ramsay',
            'emergency_contact_phone': '+919876543888',
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        self.chef.refresh_from_db()
        self.assertEqual(self.chef.first_name, 'MasterChef')
        self.assertEqual(self.chef.last_name, 'Ramsay')
        self.assertEqual(self.chef.email, 'masterchef_ramsay@restaurant.com')
        self.assertEqual(self.chef.phone_number, '+919876543999')
        self.assertEqual(self.chef.address, 'Culinary Suite 5, Marina Beach')

    def test_cashier_can_update_personal_information(self):
        self.client.force_login(self.cashier)
        res = self.client.post(reverse('accounts:profile'), {
            'update_profile': '1',
            'first_name': 'Jane',
            'last_name': 'Cashier',
            'email': 'jane_cashier@restaurant.com',
            'phone_number': '+919811223344',
            'address': 'Billing Residence, Sector 4',
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.first_name, 'Jane')
        self.assertEqual(self.cashier.last_name, 'Cashier')
        self.assertEqual(self.cashier.email, 'jane_cashier@restaurant.com')
        self.assertEqual(self.cashier.phone_number, '+919811223344')
        self.assertEqual(self.cashier.address, 'Billing Residence, Sector 4')

    def test_customer_can_update_personal_information(self):
        self.client.force_login(self.customer)
        res = self.client.post(reverse('accounts:profile'), {
            'update_profile': '1',
            'first_name': 'VIP',
            'last_name': 'Diner',
            'email': 'vip_diner@customer.com',
            'phone_number': '+919777666555',
            'address': 'Penthouse 12B, Central Avenue',
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.first_name, 'VIP')
        self.assertEqual(self.customer.last_name, 'Diner')
        self.assertEqual(self.customer.email, 'vip_diner@customer.com')
        self.assertEqual(self.customer.address, 'Penthouse 12B, Central Avenue')

    def test_email_uniqueness_enforced_on_profile_update(self):
        # Admin tries to change their email to the cashier's existing email
        self.client.force_login(self.admin)
        res = self.client.post(reverse('accounts:profile'), {
            'update_profile': '1',
            'first_name': 'AdminFirst',
            'last_name': 'AdminLast',
            'email': self.cashier.email,  # duplicate email!
            'phone_number': self.admin.phone_number,
            'address': 'Some address',
        })
        self.assertContains(res, 'already in use by another account')


class AdminWiseUniqueUsernameTests(TestCase):
    """
    Complete Test Suite for Admin-Wise Unique Username Management
    in Multi-Restaurant System (Phases 1-6 Requirements):
      1. Super Admin creates Admin A and Admin B with distinct restaurants.
      2. Admin A creates employee 'rahul' (Allowed).
      3. Admin A tries to create another 'rahul' -> Rejected with exact error.
      4. Admin A tries to create 'Rahul' -> Rejected (case-insensitive).
      5. Admin B creates 'rahul' -> Allowed across organizations.
      6. Admin B creates 'priya' -> Allowed.
      7. Admin A cannot view Admin B's employees.
      8. Admin B cannot edit, toggle duty, or delete Admin A's employees.
      9. Both 'rahul' accounts can authenticate via /login/ using organization identifier.
     10. Database-level constraint prevents concurrent duplicate creation.
     11. Admin & employee in same restaurant cannot share username.
     12. Username changes during employee editing validate organization uniqueness.
    """

    def setUp(self):
        self.client = Client()

        # 1. Super Admin account
        self.superadmin = User.objects.create_superuser(
            username='master_superadmin',
            email='superadmin@system.com',
            password='SuperPassword123!'
        )

        # 2. Restaurant A and Restaurant Admin A
        from accounts.models import Restaurant
        self.rest_a = Restaurant.objects.create(
            name='Bella Italia',
            code='REST-0001',
            address='123 Gourmet Blvd'
        )
        self.admin_a = User.objects.create_user(
            username='admin_a',
            email='admin_a@bellaitalia.com',
            password='AdminAPassword123!',
            role=User.Role.ADMIN,
            restaurant=self.rest_a
        )

        # 3. Restaurant B and Restaurant Admin B
        self.rest_b = Restaurant.objects.create(
            name='Ocean Grill',
            code='REST-0002',
            address='456 Seafood Way'
        )
        self.admin_b = User.objects.create_user(
            username='admin_b',
            email='admin_b@oceangrill.com',
            password='AdminBPassword123!',
            role=User.Role.ADMIN,
            restaurant=self.rest_b
        )

    def test_01_super_admin_creates_admin_a_and_admin_b(self):
        """Super Admin creates Restaurant Admin accounts for distinct restaurants."""
        from accounts.models import Restaurant
        rest_c = Restaurant.objects.create(name='Tokyo Sushi', code='REST-0003')
        admin_c = User.objects.create_user(
            username='admin_c',
            email='admin_c@tokyosushi.com',
            password='AdminCPass123!',
            role=User.Role.ADMIN,
            restaurant=rest_c
        )
        self.assertEqual(admin_c.restaurant, rest_c)
        self.assertEqual(admin_c.restaurant.code, 'REST-0003')
        self.assertEqual(admin_c.normalized_username, 'admin_c')

    def test_02_admin_a_creates_employee_rahul(self):
        """Admin A creates employee rahul (Allowed)."""
        self.client.force_login(self.admin_a)
        res = self.client.post(reverse('accounts:staff_create'), {
            'username': 'rahul',
            'first_name': 'Rahul',
            'last_name': 'Kumar',
            'email': 'rahul@bellaitalia.com',
            'role': User.Role.KITCHEN,
            'password1': 'StaffPass123!',
            'password2': 'StaffPass123!',
            'is_active': True,
        })
        self.assertEqual(res.status_code, 302)
        emp = User.objects.get(restaurant=self.rest_a, username='rahul')
        self.assertEqual(emp.normalized_username, 'rahul')
        self.assertEqual(emp.restaurant, self.rest_a)

    def test_03_admin_a_duplicate_rahul_rejected(self):
        """Admin A tries to create another rahul -> Rejected with exact message."""
        # Create first rahul
        User.objects.create_user(
            username='rahul',
            email='rahul1@bellaitalia.com',
            password='StaffPass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )

        self.client.force_login(self.admin_a)
        res = self.client.post(reverse('accounts:staff_create'), {
            'username': 'rahul',
            'first_name': 'Rahul',
            'last_name': 'Sharma',
            'email': 'rahul2@bellaitalia.com',
            'role': User.Role.KITCHEN,
            'password1': 'StaffPass123!',
            'password2': 'StaffPass123!',
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(
            res,
            "This username is already registered in your organization. Please choose another username."
        )

    def test_04_admin_a_case_insensitive_duplicate_rejected(self):
        """Admin A tries to create 'Rahul' when 'rahul' exists -> Rejected."""
        User.objects.create_user(
            username='rahul',
            email='rahul@bellaitalia.com',
            password='StaffPass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )

        self.client.force_login(self.admin_a)
        res = self.client.post(reverse('accounts:staff_create'), {
            'username': 'Rahul',
            'first_name': 'Rahul',
            'last_name': 'Verma',
            'email': 'rahul_verma@bellaitalia.com',
            'role': User.Role.CHEF,
            'password1': 'StaffPass123!',
            'password2': 'StaffPass123!',
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(
            res,
            "This username is already registered in your organization. Please choose another username."
        )

    def test_05_admin_b_creates_employee_rahul_allowed(self):
        """Admin B creates employee rahul while Admin A also has rahul -> Allowed!"""
        # Admin A has rahul
        emp_a = User.objects.create_user(
            username='rahul',
            email='rahul@bellaitalia.com',
            password='StaffPassA123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )

        # Admin B creates rahul via form
        self.client.force_login(self.admin_b)
        res = self.client.post(reverse('accounts:staff_create'), {
            'username': 'rahul',
            'first_name': 'Rahul',
            'last_name': 'Singh',
            'email': 'rahul@oceangrill.com',
            'role': User.Role.CASHIER,
            'password1': 'StaffPassB123!',
            'password2': 'StaffPassB123!',
            'is_active': True,
        })
        self.assertEqual(res.status_code, 302)

        # Both exist independently in their respective restaurants
        self.assertEqual(User.objects.filter(normalized_username='rahul').count(), 2)
        emp_b = User.objects.get(restaurant=self.rest_b, normalized_username='rahul')
        self.assertEqual(emp_b.restaurant, self.rest_b)
        self.assertEqual(emp_a.restaurant, self.rest_a)

    def test_06_admin_b_creates_five_employees(self):
        """Admin B can create employees: rahul, priya, arun, kumar, divya."""
        for uname in ['rahul', 'priya', 'arun', 'kumar', 'divya']:
            # Also create in Admin A
            User.objects.create_user(
                username=uname,
                email=f"{uname}@bellaitalia.com",
                password='Pass123!',
                role=User.Role.WAITER,
                restaurant=self.rest_a
            )
            # Admin B creates the same username
            emp_b = User.objects.create_user(
                username=uname,
                email=f"{uname}@oceangrill.com",
                password='Pass123!',
                role=User.Role.KITCHEN,
                restaurant=self.rest_b
            )
            self.assertEqual(emp_b.restaurant, self.rest_b)

        # 5 in rest_a + 5 in rest_b = 10 total
        self.assertEqual(User.objects.filter(restaurant=self.rest_a).count(), 6)  # 5 + admin_a
        self.assertEqual(User.objects.filter(restaurant=self.rest_b).count(), 6)  # 5 + admin_b

    def test_07_admin_a_cannot_view_admin_b_employees(self):
        """In Admin A's Employee Management, only Admin A's employees appear."""
        emp_a = User.objects.create_user(
            username='rahul_a',
            email='rahul_a@bellaitalia.com',
            password='Pass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )
        emp_b = User.objects.create_user(
            username='rahul_b',
            email='rahul_b@oceangrill.com',
            password='Pass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_b
        )

        self.client.force_login(self.admin_a)
        res = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'rahul_a')
        self.assertNotContains(res, 'rahul_b')

        # Searching for rahul_b in Admin A's portal returns 0 results
        res_search = self.client.get(reverse('accounts:staff_list'), {'q': 'rahul_b'})
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(len(res_search.context['staff_members']), 0)

    def test_08_admin_b_cannot_edit_or_delete_admin_a_employees(self):
        """Admin B cannot view, edit, toggle duty, or delete Admin A's employees."""
        emp_a = User.objects.create_user(
            username='rahul',
            email='rahul@bellaitalia.com',
            password='Pass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )

        self.client.force_login(self.admin_b)

        # GET edit Admin A's employee -> 404
        res_edit_get = self.client.get(reverse('accounts:staff_edit', args=[emp_a.id]))
        self.assertEqual(res_edit_get.status_code, 404)

        # POST edit Admin A's employee -> 404
        res_edit_post = self.client.post(reverse('accounts:staff_edit', args=[emp_a.id]), {
            'username': 'rahul_hacked',
            'first_name': 'Hacked',
            'last_name': 'User',
            'email': 'hacked@oceangrill.com',
            'role': User.Role.WAITER,
        })
        self.assertEqual(res_edit_post.status_code, 404)

        # POST toggle duty Admin A's employee -> 404
        res_toggle = self.client.post(reverse('accounts:staff_toggle_duty', args=[emp_a.id]))
        self.assertEqual(res_toggle.status_code, 404)

        # POST delete Admin A's employee -> 404
        res_del = self.client.post(reverse('accounts:staff_delete', args=[emp_a.id]))
        self.assertEqual(res_del.status_code, 404)

        # Ensure emp_a is completely untouched
        emp_a.refresh_from_db()
        self.assertEqual(emp_a.username, 'rahul')
        self.assertEqual(emp_a.restaurant, self.rest_a)

    def test_09_common_login_authentication_with_organization_identifier(self):
        """Both employees can authenticate through /login/ without needing an org code field."""
        from django.contrib.auth import authenticate

        emp_a = User.objects.create_user(
            username='rahul',
            email='rahul@bellaitalia.com',
            password='PasswordA123!',
            role=User.Role.KITCHEN,
            restaurant=self.rest_a
        )
        emp_b = User.objects.create_user(
            username='rahul',
            email='rahul@oceangrill.com',
            password='PasswordB123!',
            role=User.Role.CASHIER,
            restaurant=self.rest_b
        )

        # 1. Credentials authenticate directly based on unique account password
        user_a = authenticate(username='rahul', password='PasswordA123!')
        self.assertIsNotNone(user_a)
        self.assertEqual(user_a.restaurant, self.rest_a)

        # 2. Admin B's employee authenticates directly with their password
        user_b = authenticate(username='rahul', password='PasswordB123!')
        self.assertIsNotNone(user_b)
        self.assertEqual(user_b.restaurant, self.rest_b)

        # 3. Wrong password returns None
        bad_user = authenticate(username='rahul', password='WrongPassword!')
        self.assertIsNone(bad_user)

        # 4. Login with organization code REST-0001 also works
        user_a_org = authenticate(username='rahul', password='PasswordA123!', organization='REST-0001')
        self.assertIsNotNone(user_a_org)
        self.assertEqual(user_a_org.restaurant, self.rest_a)

        # 5. Embedded organization format (REST-0001/rahul) resolves
        user_embedded = authenticate(username='REST-0001/rahul', password='PasswordA123!')
        self.assertIsNotNone(user_embedded)
        self.assertEqual(user_embedded.restaurant, self.rest_a)

        # 6. Test login view POST with only username and password (no organization field)
        res_login_a = self.client.post(reverse('accounts:login'), {
            'username': 'rahul',
            'password': 'PasswordA123!',
        })
        self.assertEqual(res_login_a.status_code, 302)
        # Kitchen employee redirects to chef dashboard
        self.assertEqual(res_login_a.url, reverse('chef:dashboard'))
        self.client.logout()

        res_login_b = self.client.post(reverse('accounts:login'), {
            'username': 'rahul',
            'password': 'PasswordB123!',
        })
        self.assertEqual(res_login_b.status_code, 302)
        # Cashier employee redirects to cashier dashboard
        self.assertEqual(res_login_b.url, reverse('dashboard:cashier'))
        self.client.logout()

        # 7. Login with email also works seamlessly
        res_login_email = self.client.post(reverse('accounts:login'), {
            'username': 'rahul@bellaitalia.com',
            'password': 'PasswordA123!',
        })
        self.assertEqual(res_login_email.status_code, 302)
        self.assertEqual(res_login_email.url, reverse('chef:dashboard'))
        self.client.logout()

    def test_10_database_constraint_prevents_duplicate_in_same_restaurant(self):
        """Database constraint enforces uniqueness per restaurant and rejects duplicates."""
        from django.db import IntegrityError
        User.objects.create_user(
            username='rahul',
            email='rahul1@bellaitalia.com',
            password='Pass123!',
            restaurant=self.rest_a
        )

        with self.assertRaises(IntegrityError):
            User.objects.create(
                username='rahul',
                normalized_username='rahul',
                email='rahul2@bellaitalia.com',
                restaurant=self.rest_a
            )

    def test_11_admin_and_employee_conflict_in_same_restaurant(self):
        """Admin A and Employee under Admin A cannot have conflicting usernames."""
        # admin_a is already in rest_a
        self.client.force_login(self.admin_a)
        res = self.client.post(reverse('accounts:staff_create'), {
            'username': 'admin_a',
            'first_name': 'Duplicate',
            'last_name': 'Admin',
            'email': 'dup_admin@bellaitalia.com',
            'role': User.Role.WAITER,
            'password1': 'Pass123!',
            'password2': 'Pass123!',
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(
            res,
            "This username is already registered in your organization. Please choose another username."
        )

    def test_12_employee_username_change_validation_on_edit(self):
        """When editing employee, username changes are validated against organization uniqueness."""
        emp1 = User.objects.create_user(
            username='priya',
            email='priya@bellaitalia.com',
            password='Pass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )
        emp2 = User.objects.create_user(
            username='kumar',
            email='kumar@bellaitalia.com',
            password='Pass123!',
            role=User.Role.WAITER,
            restaurant=self.rest_a
        )

        self.client.force_login(self.admin_a)

        # Try to rename kumar to priya -> Rejected
        res = self.client.post(reverse('accounts:staff_edit', args=[emp2.id]), {
            'username': 'priya',
            'employee_id': emp2.employee_id,
            'first_name': emp2.first_name,
            'last_name': emp2.last_name,
            'email': emp2.email,
            'role': emp2.role,
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(
            res,
            "This username is already registered in your organization. Please choose another username."
        )

        # Rename kumar to kumar_senior -> Allowed
        res_ok = self.client.post(reverse('accounts:staff_edit', args=[emp2.id]), {
            'username': 'kumar_senior',
            'employee_id': emp2.employee_id,
            'first_name': emp2.first_name,
            'last_name': emp2.last_name,
            'email': emp2.email,
            'role': emp2.role,
        })
        self.assertEqual(res_ok.status_code, 302)
        emp2.refresh_from_db()
        self.assertEqual(emp2.username, 'kumar_senior')
        self.assertEqual(emp2.normalized_username, 'kumar_senior')



