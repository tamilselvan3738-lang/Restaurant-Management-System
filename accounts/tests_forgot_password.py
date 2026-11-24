import json
import re
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasswordResetOTP, Restaurant
from accounts.services.delivery import normalize_phone_number, normalize_email

User = get_user_model()


class ForgotPasswordIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name="Grand Test Bistro")
        
        # Create user accounts across various roles
        self.admin_user = User.objects.create_user(
            username="admin_user",
            email="admin@grandbistro.com",
            password="OldPassword123!",
            role=User.Role.ADMIN,
            phone_number="+919876543210",
            restaurant=self.restaurant
        )
        self.chef_user = User.objects.create_user(
            username="chef_user",
            email="chef@grandbistro.com",
            password="OldPassword123!",
            role=User.Role.CHEF,
            phone_number="+919876543211",
            restaurant=self.restaurant
        )
        self.cashier_user = User.objects.create_user(
            username="cashier_user",
            email="cashier@grandbistro.com",
            password="OldPassword123!",
            role=User.Role.CASHIER,
            phone_number="+919876543212",
            restaurant=self.restaurant
        )
        self.customer_user = User.objects.create_user(
            username="customer_user",
            email="customer@example.com",
            password="OldPassword123!",
            role=User.Role.CUSTOMER,
            phone_number="+919876543213",
            restaurant=self.restaurant
        )

    # 1. Existing registered email receives a real OTP
    def test_01_existing_email_receives_otp(self):
        mail.outbox = []
        response = self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'admin@grandbistro.com',
        }, follow=True)

        self.assertRedirects(response, reverse('accounts:forgot_password_verify'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject)
        self.assertIn("admin@grandbistro.com", mail.outbox[0].to)

        # Check DB challenge created
        otp_rec = PasswordResetOTP.objects.filter(user=self.admin_user, channel='EMAIL').first()
        self.assertIsNotNone(otp_rec)
        self.assertEqual(otp_rec.destination, 'admin@grandbistro.com')
        # Check that OTP was hashed, not plain text
        self.assertNotEqual(otp_rec.otp_hash, "")
        self.assertTrue(otp_rec.otp_hash.startswith("pbkdf2_"))

    # 2. Existing registered mobile number receives a real SMS OTP
    def test_02_existing_mobile_receives_sms_otp(self):
        response = self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'SMS',
            'country_code': '+91',
            'phone_number': '9876543210',
        }, follow=True)

        self.assertRedirects(response, reverse('accounts:forgot_password_verify'))
        otp_rec = PasswordResetOTP.objects.filter(user=self.admin_user, channel='SMS').first()
        self.assertIsNotNone(otp_rec)
        self.assertEqual(otp_rec.destination, '+919876543210')

    # 3. Unknown contact receives no OTP
    def test_03_unknown_contact_receives_no_otp(self):
        mail.outbox = []
        initial_otp_count = PasswordResetOTP.objects.count()

        response = self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'unknown_person@nowhere.com',
        }, follow=True)

        # Anti-enumeration redirects to verify with dummy challenge, but sends NO email
        self.assertRedirects(response, reverse('accounts:forgot_password_verify'))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PasswordResetOTP.objects.count(), initial_otp_count)

    # 4. Incorrect OTP is rejected
    def test_04_incorrect_otp_rejected(self):
        self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'chef@grandbistro.com',
        })
        otp_rec = PasswordResetOTP.objects.filter(user=self.chef_user).first()
        self.assertIsNotNone(otp_rec)

        # Submit deliberately wrong OTP
        response = self.client.post(reverse('accounts:forgot_password_verify'), {
            'otp': '9999',
        }, follow=True)

        otp_rec.refresh_from_db()
        self.assertEqual(otp_rec.attempts, 1)
        self.assertFalse(otp_rec.is_verified)
        self.assertContains(response, "Incorrect verification code")

    # 5. Expired OTP is rejected
    def test_05_expired_otp_rejected(self):
        self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'chef@grandbistro.com',
        })
        otp_rec = PasswordResetOTP.objects.filter(user=self.chef_user).first()

        # Artificially expire the OTP
        otp_rec.expires_at = timezone.now() - timedelta(minutes=10)
        otp_rec.save()

        response = self.client.post(reverse('accounts:forgot_password_verify'), {
            'otp': '1234',
        }, follow=True)

        self.assertContains(response, "expired")

    # 6. Used OTP cannot be reused
    def test_06_used_otp_cannot_be_reused(self):
        self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'chef@grandbistro.com',
        })
        otp_rec = PasswordResetOTP.objects.filter(user=self.chef_user).first()
        otp_rec.is_used = True
        otp_rec.save()

        # Session attempt should fail or redirect
        response = self.client.get(reverse('accounts:forgot_password_verify'), follow=True)
        self.assertRedirects(response, reverse('accounts:forgot_password'))

    # 7. Resend cooldown works
    def test_07_resend_cooldown_works(self):
        self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'cashier@grandbistro.com',
        })
        # Attempt immediate resend (cooldown is 60s)
        response = self.client.post(reverse('accounts:forgot_password_resend_otp'))
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn("wait", data['message'])

        # Now simulate 61 seconds passing
        otp_rec = PasswordResetOTP.objects.filter(user=self.cashier_user).first()
        otp_rec.last_resend_at = timezone.now() - timedelta(seconds=65)
        otp_rec.save()

        response2 = self.client.post(reverse('accounts:forgot_password_resend_otp'))
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])

    # 8. Maximum incorrect attempts are enforced
    def test_08_max_incorrect_attempts_enforced(self):
        self.client.post(reverse('accounts:forgot_password'), {
            'channel': 'EMAIL',
            'email': 'admin@grandbistro.com',
        })
        otp_rec = PasswordResetOTP.objects.filter(user=self.admin_user).first()

        for attempt in range(5):
            self.client.post(reverse('accounts:forgot_password_verify'), {'otp': '0000'})

        otp_rec.refresh_from_db()
        self.assertEqual(otp_rec.attempts, 5)

        # 6th attempt is blocked
        response = self.client.post(reverse('accounts:forgot_password_verify'), {'otp': '0000'}, follow=True)
        self.assertContains(response, "Maximum verification attempts exceeded")

    # 9. New and confirm passwords must match
    def test_09_passwords_must_match(self):
        otp_rec = PasswordResetOTP.objects.create(
            user=self.admin_user,
            channel='EMAIL',
            destination=self.admin_user.email,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        token = otp_rec.generate_reset_token()
        session = self.client.session
        session['reset_token'] = token
        session.save()

        response = self.client.post(reverse('accounts:forgot_password_reset'), {
            'new_password': 'BrandNewPassword123!',
            'confirm_password': 'DifferentPassword456!',
        })
        self.assertContains(response, "Passwords do not match")

    # 10. Weak passwords are rejected
    def test_10_weak_passwords_rejected(self):
        otp_rec = PasswordResetOTP.objects.create(
            user=self.admin_user,
            channel='EMAIL',
            destination=self.admin_user.email,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        token = otp_rec.generate_reset_token()
        session = self.client.session
        session['reset_token'] = token
        session.save()

        # Weak: no number, no uppercase, no special char
        response = self.client.post(reverse('accounts:forgot_password_reset'), {
            'new_password': 'weak',
            'confirm_password': 'weak',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password must contain")

    # 11, 12, 13. Correct password updates user account, old password fails, new password logs in
    def test_11_12_13_password_update_and_login_flow(self):
        otp_rec = PasswordResetOTP.objects.create(
            user=self.admin_user,
            channel='EMAIL',
            destination=self.admin_user.email,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        token = otp_rec.generate_reset_token()
        session = self.client.session
        session['reset_token'] = token
        session.save()

        new_pass = "UltraSecure2026!#"
        response = self.client.post(reverse('accounts:forgot_password_reset'), {
            'new_password': new_pass,
            'confirm_password': new_pass,
        }, follow=True)

        self.assertRedirects(response, reverse('accounts:forgot_password_success'))
        self.assertContains(response, "Your password has been successfully changed")

        # 11: User password updated
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password(new_pass))

        # 12: Old password no longer works
        self.assertFalse(self.admin_user.check_password("OldPassword123!"))

        # 13: New password works on login page
        login_resp = self.client.post(reverse('accounts:login'), {
            'username': self.admin_user.username,
            'password': new_pass,
        }, follow=True)
        self.assertEqual(login_resp.status_code, 200)
        self.assertTrue(login_resp.context['user'].is_authenticated)

    # 14. Recovery works for all applicable user roles
    def test_14_recovery_works_for_all_roles(self):
        roles_to_test = [
            (self.admin_user, "admin@grandbistro.com"),
            (self.chef_user, "chef@grandbistro.com"),
            (self.cashier_user, "cashier@grandbistro.com"),
            (self.customer_user, "customer@example.com"),
        ]
        for user, email in roles_to_test:
            res = self.client.post(reverse('accounts:forgot_password'), {
                'channel': 'EMAIL',
                'email': email,
            })
            self.assertEqual(res.status_code, 302)
            self.assertTrue(PasswordResetOTP.objects.filter(user=user).exists())

    # 15. Reset tokens cannot be reused
    def test_15_reset_token_cannot_be_reused(self):
        otp_rec = PasswordResetOTP.objects.create(
            user=self.admin_user,
            channel='EMAIL',
            destination=self.admin_user.email,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        token = otp_rec.generate_reset_token()
        session = self.client.session
        session['reset_token'] = token
        session.save()

        new_pass = "StrongPassOne!99"
        self.client.post(reverse('accounts:forgot_password_reset'), {
            'new_password': new_pass,
            'confirm_password': new_pass,
        })

        # Try using same token a second time
        response2 = self.client.get(f"{reverse('accounts:forgot_password_reset')}?token={token}", follow=True)
        self.assertRedirects(response2, reverse('accounts:forgot_password'))
        self.assertContains(response2, "expired or is invalid")

    # 16. Cross-user OTP and token access are blocked
    def test_16_cross_user_token_access_blocked(self):
        # Admin gets a token
        otp_admin = PasswordResetOTP.objects.create(
            user=self.admin_user,
            channel='EMAIL',
            destination=self.admin_user.email,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        token = otp_admin.generate_reset_token()

        # Chef gets another token
        otp_chef = PasswordResetOTP.objects.create(
            user=self.chef_user,
            channel='EMAIL',
            destination=self.chef_user.email,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        token_chef = otp_chef.generate_reset_token()

        # Setting chef's password cannot be done using admin's token
        self.assertFalse(otp_admin.is_reset_token_valid(token_chef))
        self.assertFalse(otp_chef.is_reset_token_valid(token))

    # 17. Existing login, staff management and role redirects continue working
    def test_17_existing_login_and_staff_management_continue_working(self):
        # Login page renders and has the Forgot Password link
        login_page = self.client.get(reverse('accounts:login'))
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, 'href="/forgot-password/"')
        self.assertContains(login_page, 'Forgot Password?')

        # Login works for staff
        login_res = self.client.post(reverse('accounts:login'), {
            'username': self.admin_user.username,
            'password': 'OldPassword123!',
        }, follow=True)
        self.assertEqual(login_res.status_code, 200)
        self.assertTrue(login_res.context['user'].is_authenticated)


from unittest.mock import patch, MagicMock
from accounts.services.delivery import send_via_msg91_v5, send_otp_sms, map_msg91_error


class SMSProviderIntegrationTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Grand Test Bistro")
        self.user = User.objects.create_user(
            username="sms_tester",
            email="smstester@example.com",
            phone_number="+919345843238",
            password="TestPassword123!",
            restaurant=self.restaurant
        )

    @override_settings(
        MSG91_AUTH_KEY="test_auth_key_12345",
        MSG91_TEMPLATE_ID="test_template_id_67890",
        MSG91_SENDER_ID="RESTRO"
    )
    @patch('urllib.request.urlopen')
    def test_msg91_v5_api_request_structure_and_success(self, mock_urlopen):
        # Mock successful MSG91 response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "message": "OTP sent successfully",
            "type": "success"
        }).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        success, err = send_via_msg91_v5("+919345843238", "5821", expires_in_minutes=5, user=self.user)

        self.assertTrue(success)
        self.assertIsNone(err)

        # Verify request parameters
        self.assertTrue(mock_urlopen.called)
        req = mock_urlopen.call_args[0][0]
        self.assertIn("https://control.msg91.com/api/v5/otp", req.full_url)
        self.assertIn("template_id=test_template_id_67890", req.full_url)
        self.assertIn("mobile=919345843238", req.full_url)
        self.assertIn("otp=5821", req.full_url)
        self.assertIn("otp_length=4", req.full_url)
        self.assertIn("otp_expiry=5", req.full_url)
        self.assertEqual(req.headers['Authkey'], "test_auth_key_12345")
        self.assertEqual(req.headers['Content-type'], "application/json")

    @override_settings(
        MSG91_AUTH_KEY="bad_auth_key",
        MSG91_TEMPLATE_ID="valid_template"
    )
    @patch('urllib.request.urlopen')
    def test_msg91_authkey_rejection(self, mock_urlopen):
        import urllib.error
        # Simulate 401 Unauthorized from MSG91
        err_response = urllib.error.HTTPError(
            url="https://control.msg91.com/api/v5/otp",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=MagicMock(read=lambda: json.dumps({"message": "Invalid AuthKey", "type": "error"}).encode('utf-8'))
        )
        mock_urlopen.side_effect = err_response

        success, err = send_via_msg91_v5("+919345843238", "1234", 5, self.user)
        self.assertFalse(success)
        self.assertIn("Invalid SMS provider credentials", err)

    @override_settings(
        MSG91_AUTH_KEY="valid_key",
        MSG91_TEMPLATE_ID="missing_template"
    )
    @patch('urllib.request.urlopen')
    def test_msg91_template_rejection(self, mock_urlopen):
        # Simulate 200 OK with type error from MSG91
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "message": "Template Id not found",
            "type": "error"
        }).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        success, err = send_via_msg91_v5("+919345843238", "1234", 5, self.user)
        self.assertFalse(success)
        self.assertIn("template configuration error", err.lower())

    @override_settings(
        FAST2SMS_API_KEY="test_fast2sms_api_key_xyz",
        FAST2SMS_ROUTE="q"
    )
    @patch('urllib.request.urlopen')
    def test_fast2sms_success_dispatch(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "return": True,
            "request_id": "req_123456",
            "message": ["SMS sent successfully."]
        }).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        from accounts.services.delivery import send_via_fast2sms
        success, err = send_via_fast2sms("+919345843238", "5821", expires_in_minutes=5, user=self.user)

        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertTrue(mock_urlopen.called)
        req = mock_urlopen.call_args[0][0]
        self.assertIn("https://www.fast2sms.com/dev/bulkV2", req.full_url)
        self.assertEqual(req.headers['Authorization'], "test_fast2sms_api_key_xyz")

    @override_settings(
        MSG91_AUTH_KEY="",
        MSG91_TEMPLATE_ID="",
        TWILIO_ACCOUNT_SID="",
        SMS_PROVIDER_MOCK=False
    )
    def test_unconfigured_sms_provider_fails_cleanly_without_silent_simulation(self):
        # When unconfigured in live mode (mock disabled), send_otp_sms must return False with guidance
        with patch('accounts.services.delivery.is_test_environment', return_value=False):
            success, err = send_otp_sms(self.user, "+919345843238", "1234", 5)
            self.assertFalse(success)
            self.assertIn("SMS provider is not configured", err)
            self.assertIn("MSG91_AUTH_KEY", err)


class UsernameVerificationBeforeChannelTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name="Grand Test Bistro")
        self.user = User.objects.create_user(
            username="dhanushaa",
            employee_id="EMP-2002",
            email="dhanushaa@example.com",
            phone_number="+919345843238",
            password="StrongPassword123!",
            role=User.Role.KITCHEN,
            restaurant=self.restaurant
        )

    def test_forgot_password_initial_page_asks_username(self):
        response = self.client.get(reverse('accounts:forgot_password'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/forgot_password_username.html')
        self.assertContains(response, 'Registered Username')

    def test_nonexistent_username_rejected_and_stays_on_page(self):
        response = self.client.post(reverse('accounts:forgot_password'), {
            'username': 'non_existent_user_999'
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/forgot_password_username.html')
        self.assertContains(response, 'is not registered in our database')
        self.assertNotIn('recovery_user_id', self.client.session)

    def test_direct_access_to_channel_page_without_username_redirects_to_username_page(self):
        response = self.client.get(reverse('accounts:forgot_password_channel'), follow=True)
        self.assertRedirects(response, reverse('accounts:forgot_password'))
        self.assertContains(response, 'Please enter your registered username first')

    def test_valid_username_stores_session_and_allows_access_to_channel_page(self):
        response = self.client.post(reverse('accounts:forgot_password'), {
            'username': 'dhanushaa'
        }, follow=True)
        self.assertRedirects(response, reverse('accounts:forgot_password_channel'))
        self.assertTemplateUsed(response, 'accounts/forgot_password_request.html')
        self.assertContains(response, '@dhanushaa')
        self.assertContains(response, 'Registered Email Address')

    def test_valid_employee_id_stores_session_and_allows_access_to_channel_page(self):
        response = self.client.post(reverse('accounts:forgot_password'), {
            'username': 'EMP-2002'
        }, follow=True)
        self.assertRedirects(response, reverse('accounts:forgot_password_channel'))
        self.assertTemplateUsed(response, 'accounts/forgot_password_request.html')
        self.assertContains(response, '@dhanushaa')

    def test_mismatched_email_on_channel_page_rejected(self):
        # Step 1: verify username
        self.client.post(reverse('accounts:forgot_password'), {'username': 'dhanushaa'})
        # Step 2: attempt with wrong email
        response = self.client.post(reverse('accounts:forgot_password_channel'), {
            'channel': 'EMAIL',
            'email': 'wrong_email@example.com'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'does not match the registered email')

    def test_matched_email_on_channel_page_dispatches_otp(self):
        mail.outbox = []
        # Step 1: verify username
        self.client.post(reverse('accounts:forgot_password'), {'username': 'dhanushaa'})
        # Step 2: enter correct email
        response = self.client.post(reverse('accounts:forgot_password_channel'), {
            'channel': 'EMAIL',
            'email': 'dhanushaa@example.com'
        }, follow=True)
        self.assertRedirects(response, reverse('accounts:forgot_password_verify'))
        self.assertEqual(len(mail.outbox), 1)

    def test_change_username_link_clears_session_and_returns_to_username_page(self):
        self.client.post(reverse('accounts:forgot_password'), {'username': 'dhanushaa'})
        self.assertIn('recovery_user_id', self.client.session)
        # Click (Change) username
        response = self.client.get(reverse('accounts:forgot_password') + '?change=1')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/forgot_password_username.html')
        self.assertNotIn('recovery_user_id', self.client.session)

