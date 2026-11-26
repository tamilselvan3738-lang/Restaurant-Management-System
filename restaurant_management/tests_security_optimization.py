import io
import logging
from django.test import TestCase, RequestFactory
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from PIL import Image

from accounts.models import Restaurant, User
from orders.models import Order
from restaurant_management.file_security import validate_image_file, sanitize_filename
from restaurant_management.logging_utils import SensitiveDataMaskingFilter
from restaurant_management.views_errors import (
    custom_bad_request_view,
    custom_permission_denied_view,
    custom_page_not_found_view,
    custom_server_error_view,
)


class FileUploadSecurityTests(TestCase):
    """Test suite for secure file upload validation, magic bytes, and sanitization."""

    def create_dummy_png(self):
        """Creates a minimal valid PNG image in memory."""
        file_obj = io.BytesIO()
        image = Image.new("RGBA", size=(50, 50), color=(256, 0, 0))
        image.save(file_obj, 'png')
        file_obj.seek(0)
        return SimpleUploadedFile("test_valid.png", file_obj.read(), content_type="image/png")

    def test_valid_png_accepted(self):
        png = self.create_dummy_png()
        validated = validate_image_file(png, max_size_mb=5)
        self.assertIsNotNone(validated)

    def test_disallowed_extension_rejected(self):
        fake_file = SimpleUploadedFile("malware.exe", b"MZDummyContent", content_type="application/x-msdownload")
        with self.assertRaises(ValidationError):
            validate_image_file(fake_file, max_size_mb=5)

    def test_disguised_executable_rejected(self):
        # A file named .jpg but starting with Windows PE signature MZ
        fake_jpg = SimpleUploadedFile("fake_photo.jpg", b"MZ\x90\x00\x03\x00\x00\x00", content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_image_file(fake_jpg, max_size_mb=5)

    def test_disguised_php_script_rejected(self):
        # A file named .png but containing PHP script
        fake_png = SimpleUploadedFile("shell.png", b"<?php system($_GET['cmd']); ?>", content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_image_file(fake_png, max_size_mb=5)

    def test_svg_with_xss_rejected(self):
        malicious_svg = SimpleUploadedFile(
            "xss.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert("XSS")</script></svg>',
            content_type="image/svg+xml"
        )
        with self.assertRaises(ValidationError):
            validate_image_file(malicious_svg, max_size_mb=5)

    def test_oversized_file_rejected(self):
        large_content = b"X" * (6 * 1024 * 1024)  # 6 MB
        large_file = SimpleUploadedFile("large.png", large_content, content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_image_file(large_file, max_size_mb=5)

    def test_filename_sanitization(self):
        unsafe_name = "../../exploit shell #1?.png"
        clean = sanitize_filename(unsafe_name, prefix="dish")
        self.assertNotIn("..", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn("?", clean)
        self.assertTrue(clean.startswith("dish_"))
        self.assertTrue(clean.endswith(".png"))


class SensitiveDataMaskingFilterTests(TestCase):
    """Test suite for log masking of sensitive credentials."""

    def setUp(self):
        self.filter = SensitiveDataMaskingFilter()

    def test_masks_password(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="User login attempt with password='SuperSecretPassword123' failed.",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn("SuperSecretPassword123", record.msg)
        self.assertIn("[MASKED]", record.msg)

    def test_masks_otp(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="OTP verification failed for otp='948215'",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn("948215", record.msg)
        self.assertIn("[MASKED]", record.msg)

    def test_masks_credit_card(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="Processing transaction for card 4111 2222 3333 4444 via gateway",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn("4111 2222 3333 4444", record.msg)
        self.assertIn("[MASKED_CARD]", record.msg)


class CentralizedErrorHandlingTests(TestCase):
    """Test suite for HTML and JSON error handlers."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_404_html_for_browser(self):
        request = self.factory.get('/nonexistent-page/')
        response = custom_page_not_found_view(request)
        self.assertEqual(response.status_code, 404)
        self.assertIn("Page or Dish Not Found", response.content.decode('utf-8'))

    def test_404_json_for_ajax(self):
        import json
        request = self.factory.get('/api/orders/99999/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = custom_page_not_found_view(request)
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.content.decode('utf-8'))
        self.assertTrue(data['error'])
        self.assertEqual(data['status_code'], 404)

    def test_403_html_for_browser(self):
        request = self.factory.get('/billing/restricted/')
        response = custom_permission_denied_view(request)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Access Denied", response.content.decode('utf-8'))

    def test_403_json_for_ajax(self):
        import json
        request = self.factory.get('/billing/restricted/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = custom_permission_denied_view(request)
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content.decode('utf-8'))
        self.assertTrue(data['error'])
        self.assertEqual(data['status_code'], 403)

    def test_500_server_error_includes_reference_id(self):
        request = self.factory.get('/')
        response = custom_server_error_view(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn("REF-", response.content.decode('utf-8'))


class PaginationTagTests(TestCase):
    """Test suite for query parameter preservation in pagination."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_url_replace_preserves_existing_filters(self):
        request = self.factory.get('/orders/?status=active&type=dine_in&page=1')
        template_to_render = Template("{% load pagination_tags %}{% url_replace page=3 %}")
        context = Context({'request': request})
        rendered = template_to_render.render(context)
        self.assertIn("status=active", rendered)
        self.assertIn("type=dine_in", rendered)
        self.assertIn("page=3", rendered)
