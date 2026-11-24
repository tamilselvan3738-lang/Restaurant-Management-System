import base64
import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger('accounts.delivery')


def is_test_environment():
    """Detects whether Django is running under automated test runner."""
    return (
        'test' in sys.argv
        or getattr(settings, 'IS_TESTING', False)
        or getattr(settings, 'SMS_PROVIDER_MOCK', False)
    )


def normalize_email(email_raw):
    """
    Normalizes email address by trimming whitespace and converting to lowercase.
    """
    if not email_raw:
        return ''
    return str(email_raw).strip().lower()


def normalize_phone_number(phone_raw, default_country_code='+91'):
    """
    Normalizes phone numbers to standard E.164 format (+919876543210).
    """
    if not phone_raw:
        return ''
    cleaned = re.sub(r'[\s\-\(\)\.]', '', str(phone_raw).strip())
    if not cleaned:
        return ''
    if cleaned.startswith('+'):
        return cleaned

    # Strip leading zero if present
    cleaned = cleaned.lstrip('0')
    country_prefix = default_country_code.strip()
    if not country_prefix.startswith('+'):
        country_prefix = '+' + country_prefix

    return f"{country_prefix}{cleaned}"


def is_valid_e164(phone_number):
    """
    Validates E.164 phone number format (+ followed by 8 to 15 digits).
    """
    if not phone_number:
        return False
    return bool(re.match(r'^\+[1-9]\d{7,14}$', phone_number))


def mask_phone_for_logs(phone_number):
    """Masks phone number for secure backend diagnostic logging."""
    digits = re.sub(r'\D', '', phone_number or '')
    if len(digits) > 4:
        return f"+{digits[:2]} {'*' * (len(digits) - 6)} {digits[-4:]}"
    return phone_number or ''


def map_msg91_error(raw_message, http_status=None):
    """Maps MSG91 API error responses to user-friendly status explanations."""
    msg_str = str(raw_message or '').lower()

    if http_status == 401 or 'authkey' in msg_str or 'unauthorized' in msg_str or 'authentication' in msg_str:
        return "Invalid SMS provider credentials: MSG91_AUTH_KEY is rejected or unauthorized."
    elif 'template' in msg_str or 'template_id' in msg_str or 'template not found' in msg_str:
        return "SMS template configuration error: MSG91_TEMPLATE_ID is invalid, not found, or not approved."
    elif 'mobile' in msg_str or 'invalid mobile' in msg_str or 'number' in msg_str:
        return "Invalid mobile number format. Please ensure your registered mobile number includes a valid country code."
    elif 'dlt' in msg_str or 'entity' in msg_str or 'sender' in msg_str or 'header' in msg_str:
        return "Carrier DLT registration error: Indian DLT Entity ID, Sender ID, or template approval is pending."
    elif 'balance' in msg_str or 'credit' in msg_str or 'quota' in msg_str or 'insufficient' in msg_str:
        return "SMS provider account balance exhausted. Please contact system administration."
    elif http_status == 429 or 'limit' in msg_str or 'retry' in msg_str or 'too many' in msg_str:
        return "SMS request limit reached. Please wait a few minutes before requesting another code."
    return f"SMS delivery failed: {raw_message or 'Carrier rejected request'}"


def send_otp_email(user, email, otp, expires_in_minutes=5):
    """
    Sends an OTP recovery email to the registered email address.
    Returns (success: bool, error: str | None).
    """
    restaurant_name = getattr(settings, 'RESTAURANT_NAME', 'Restaurant POS')
    subject = f"{otp} is your {restaurant_name} verification code"
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@restaurantpos.com')

    context = {
        'user': user,
        'email': email,
        'otp': otp,
        'expires_in_minutes': expires_in_minutes,
        'restaurant_name': restaurant_name,
    }

    try:
        html_content = render_to_string('accounts/emails/otp_email.html', context)
        text_content = (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"Your one-time verification code for {restaurant_name} is: {otp}\n\n"
            f"This code will expire in {expires_in_minutes} minutes.\n"
            f"Do not share this code with anyone.\n"
        )

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[email]
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info(f"OTP email sent successfully to registered email for user {user.username}")
        return True, None
    except Exception as e:
        logger.error(f"Failed to send OTP email: {str(e)}", exc_info=True)
        return False, str(e)


def send_via_msg91_v5(phone_number, otp, expires_in_minutes=5, user=None):
    """
    Dispatches OTP via official MSG91 V5 SendOTP API:
    POST https://control.msg91.com/api/v5/otp
    """
    auth_key = (getattr(settings, 'MSG91_AUTH_KEY', '') or '').strip()
    template_id = (getattr(settings, 'MSG91_TEMPLATE_ID', '') or '').strip()
    sender_id = (getattr(settings, 'MSG91_SENDER_ID', '') or '').strip()
    dlt_te_id = (getattr(settings, 'MSG91_DLT_TE_ID', '') or '').strip()
    restaurant_name = getattr(settings, 'RESTAURANT_NAME', 'Restaurant POS')

    if not auth_key:
        logger.error("MSG91 dispatch aborted: MSG91_AUTH_KEY is not configured in .env.")
        return False, "SMS provider authentication key (MSG91_AUTH_KEY) is not configured in .env."

    if not template_id:
        logger.error("MSG91 dispatch aborted: MSG91_TEMPLATE_ID is not configured in .env.")
        return False, "SMS OTP Template ID (MSG91_TEMPLATE_ID) is not configured in .env."

    # Format destination: international digits without '+' (e.g. 919345843238)
    cleaned_digits = re.sub(r'\D', '', phone_number)
    if len(cleaned_digits) == 10:
        cleaned_digits = '91' + cleaned_digits

    if len(cleaned_digits) < 10:
        return False, "Invalid mobile number. Must contain at least 10 digits."

    # Query parameters for V5 SendOTP API
    params = {
        'template_id': template_id,
        'mobile': cleaned_digits,
        'otp': str(otp),
        'otp_length': 4,
        'otp_expiry': int(expires_in_minutes),
    }
    if sender_id:
        params['sender'] = sender_id
    if dlt_te_id:
        params['dlt_te_id'] = dlt_te_id

    query_string = urllib.parse.urlencode(params)
    endpoint_url = f"https://control.msg91.com/api/v5/otp?{query_string}"

    headers = {
        'authkey': auth_key,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    body_data = json.dumps({
        'OTP': str(otp),
        'otp': str(otp),
        'company_name': restaurant_name,
    }).encode('utf-8')

    masked_mobile = mask_phone_for_logs(phone_number)
    masked_key = f"{auth_key[:4]}...{auth_key[-4:]}" if len(auth_key) > 8 else "***"
    logger.info(f"Calling MSG91 V5 SendOTP API for destination {masked_mobile} with template {template_id} (AuthKey: {masked_key})")

    try:
        req = urllib.request.Request(endpoint_url, data=body_data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=12) as response:
            status_code = response.status
            raw_body = response.read().decode('utf-8')
            logger.info(f"MSG91 Response [{status_code}]: {raw_body}")

            try:
                resp_json = json.loads(raw_body)
            except Exception:
                resp_json = {'message': raw_body, 'type': 'unknown'}

            resp_type = str(resp_json.get('type', '')).lower()
            resp_msg = str(resp_json.get('message', ''))

            # Check if MSG91 accepted the request
            if resp_type == 'success' or 'success' in resp_msg.lower():
                logger.info(f"MSG91 OTP request accepted for delivery to {masked_mobile}")
                return True, None
            else:
                user_msg = map_msg91_error(resp_msg, status_code)
                logger.error(f"MSG91 rejected OTP request: {resp_msg}")
                return False, user_msg

    except urllib.error.HTTPError as e:
        raw_err = e.read().decode('utf-8', errors='ignore')
        logger.error(f"MSG91 HTTP Error {e.code}: {raw_err}")
        try:
            err_json = json.loads(raw_err)
            err_msg = err_json.get('message', raw_err)
        except Exception:
            err_msg = raw_err
        user_msg = map_msg91_error(err_msg, e.code)
        return False, user_msg

    except urllib.error.URLError as e:
        logger.error(f"MSG91 Gateway Connection Error: {str(e)}")
        return False, "SMS delivery gateway is temporarily unreachable. Please retry in a few moments."
    except Exception as e:
        logger.error(f"Unexpected error calling MSG91: {str(e)}")
        return False, f"SMS dispatch error: {str(e)}"


def send_via_twilio(phone_number, otp, expires_in_minutes=5, user=None):
    """
    Dispatches OTP via Twilio REST API.
    """
    account_sid = (getattr(settings, 'TWILIO_ACCOUNT_SID', '') or '').strip()
    auth_token = (getattr(settings, 'TWILIO_AUTH_TOKEN', '') or '').strip()
    from_number = (getattr(settings, 'TWILIO_FROM_NUMBER', '') or '').strip()
    restaurant_name = getattr(settings, 'RESTAURANT_NAME', 'Restaurant POS')

    if not (account_sid and auth_token and from_number):
        return False, "Twilio configuration incomplete."

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    message_body = f"Your {restaurant_name} verification code is: {otp}. Valid for {expires_in_minutes} minutes. Do not share this code."

    post_data = urllib.parse.urlencode({
        'From': from_number,
        'To': phone_number,
        'Body': message_body,
    }).encode('utf-8')

    auth_header = base64.b64encode(f"{account_sid}:{auth_token}".encode('utf-8')).decode('utf-8')
    headers = {
        'Authorization': f'Basic {auth_header}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    }

    try:
        req = urllib.request.Request(url, data=post_data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=12) as response:
            raw_body = response.read().decode('utf-8')
            resp_json = json.loads(raw_body)
            status = str(resp_json.get('status', '')).lower()
            if status in ['queued', 'sending', 'sent', 'delivered']:
                logger.info(f"Twilio accepted message: status={status}")
                return True, None
            else:
                return False, f"Twilio rejected delivery: {resp_json.get('error_message', status)}"
    except urllib.error.HTTPError as e:
        raw_err = e.read().decode('utf-8', errors='ignore')
        logger.error(f"Twilio HTTP Error {e.code}: {raw_err}")
        try:
            err_json = json.loads(raw_err)
            err_msg = err_json.get('message', raw_err)
        except Exception:
            err_msg = raw_err
        if e.code == 401:
            return False, "Invalid Twilio credentials (TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN)."
        return False, f"Twilio SMS delivery failure: {err_msg}"
    except Exception as e:
        logger.error(f"Twilio connection error: {str(e)}")
        return False, "Twilio SMS gateway is temporarily unreachable."


def send_via_fast2sms(phone_number, otp, expires_in_minutes=5, user=None):
    """
    Dispatches real SMS to Indian mobile numbers (+91) using Fast2SMS API.
    Supports Quick SMS route ('q') which delivers real SMS directly to mobile phones
    without requiring lengthy DLT registration approval.
    """
    api_key = (getattr(settings, 'FAST2SMS_API_KEY', '') or '').strip()
    if not api_key:
        return False, "Fast2SMS API Key (FAST2SMS_API_KEY) is not configured in .env."

    cleaned_digits = re.sub(r'\D', '', phone_number or '')
    if len(cleaned_digits) > 10:
        cleaned_digits = cleaned_digits[-10:]

    if len(cleaned_digits) != 10:
        return False, "Invalid mobile number. Fast2SMS requires a 10-digit Indian mobile number."

    restaurant_name = getattr(settings, 'RESTAURANT_NAME', 'The Grand Bistro')
    message_text = f"Your {restaurant_name} verification code is: {otp}. Valid for {expires_in_minutes} minutes. Do not share this OTP."

    endpoint_url = "https://www.fast2sms.com/dev/bulkV2"
    fast2sms_route = getattr(settings, 'FAST2SMS_ROUTE', 'q')
    fast2sms_message_id = getattr(settings, 'FAST2SMS_MESSAGE_ID', '')

    if fast2sms_route == 'dlt' and fast2sms_message_id:
        payload = {
            "route": "dlt",
            "sender_id": getattr(settings, 'FAST2SMS_SENDER_ID', 'FSTSMS'),
            "message": str(fast2sms_message_id),
            "variables_values": str(otp),
            "numbers": cleaned_digits,
        }
    else:
        # Quick SMS route ('q') - sends instant real SMS without DLT registration
        payload = {
            "route": "q",
            "message": message_text,
            "language": "english",
            "flash": 0,
            "numbers": cleaned_digits,
        }

    headers = {
        "authorization": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    masked_mobile = mask_phone_for_logs(phone_number)
    logger.info(f"Dispatching SMS via Fast2SMS to {masked_mobile}")

    try:
        req = urllib.request.Request(
            endpoint_url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            raw_body = response.read().decode('utf-8')
            logger.info(f"Fast2SMS Response: {raw_body}")
            resp_json = json.loads(raw_body)
            if resp_json.get('return') is True:
                logger.info(f"Fast2SMS accepted message for delivery to {masked_mobile}")
                return True, None
            else:
                msg = resp_json.get('message', ['Delivery rejected'])
                if isinstance(msg, list):
                    msg = msg[0] if msg else 'Delivery rejected'
                return False, f"Fast2SMS delivery error: {msg}"
    except urllib.error.HTTPError as e:
        raw_err = e.read().decode('utf-8', errors='ignore')
        logger.error(f"Fast2SMS HTTP Error {e.code}: {raw_err}")
        try:
            err_json = json.loads(raw_err)
            err_msg = err_json.get('message', raw_err)
            if isinstance(err_msg, list):
                err_msg = err_msg[0] if err_msg else raw_err
        except Exception:
            err_msg = raw_err
        if e.code == 401 or 'authorization' in str(err_msg).lower():
            return False, "Invalid Fast2SMS API Key: Authorization rejected. Please verify FAST2SMS_API_KEY in .env."
        return False, f"Fast2SMS delivery failure: {err_msg}"
    except urllib.error.URLError as e:
        logger.error(f"Fast2SMS Gateway Connection Error: {str(e)}")
        return False, "Fast2SMS gateway is temporarily unreachable. Please check internet connection."
    except Exception as e:
        logger.error(f"Fast2SMS unexpected error: {str(e)}")
        return False, f"Fast2SMS dispatch error: {str(e)}"


def send_otp_sms(user, phone_number, otp, expires_in_minutes=5):
    """
    Sends a real OTP code via SMS using configured Fast2SMS, MSG91, or Twilio API.
    Validates provider acceptance before reporting success.
    Returns (success: bool, error: str | None).
    """
    fast2sms_api_key = (getattr(settings, 'FAST2SMS_API_KEY', '') or '').strip()
    msg91_auth_key = (getattr(settings, 'MSG91_AUTH_KEY', '') or '').strip()
    msg91_template_id = (getattr(settings, 'MSG91_TEMPLATE_ID', '') or '').strip()
    twilio_sid = (getattr(settings, 'TWILIO_ACCOUNT_SID', '') or '').strip()
    twilio_token = (getattr(settings, 'TWILIO_AUTH_TOKEN', '') or '').strip()

    # 1. Primary for India: Fast2SMS (Instant real SMS delivery to +91 numbers)
    if fast2sms_api_key:
        return send_via_fast2sms(phone_number, otp, expires_in_minutes, user=user)

    # 2. Indian DLT: MSG91 V5 SendOTP API
    if msg91_auth_key and msg91_template_id:
        return send_via_msg91_v5(phone_number, otp, expires_in_minutes, user=user)

    # 3. International: Twilio REST API
    if twilio_sid and twilio_token:
        return send_via_twilio(phone_number, otp, expires_in_minutes, user=user)

    # 4. Automated Test Runner / Mock Mode only
    if is_test_environment():
        masked_mobile = mask_phone_for_logs(phone_number)
        uname = user.username if user else 'Unknown'
        logger.info(f"[SMS CONSOLE/MOCK DISPATCH] Destination: {masked_mobile} | User: @{uname} | OTP: {otp}")
        print(f"\n{'='*55}\n[SMS CONSOLE BACKEND] To: {phone_number} (User: @{uname})\nVerification OTP Code: {otp}\nValid for: {expires_in_minutes} minutes\n{'='*55}\n")
        return True, None

    # 5. Production / Live server with unconfigured SMS provider
    logger.error("SMS dispatch failed: No SMS carrier credentials configured in environment.")
    return False, "SMS provider is not configured. MSG91_AUTH_KEY, FAST2SMS_API_KEY, or TWILIO_ACCOUNT_SID must be set in .env to deliver real SMS to mobile numbers."


def send_password_change_notice(user, channel, destination):
    """
    Sends confirmation notice that password was changed.
    """
    restaurant_name = getattr(settings, 'RESTAURANT_NAME', 'Restaurant POS')
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@restaurantpos.com')

    if channel == 'EMAIL' and destination:
        try:
            context = {
                'user': user,
                'restaurant_name': restaurant_name,
                'timestamp': timezone.now(),
            }
            html_content = render_to_string('accounts/emails/password_changed_email.html', context)
            text_content = (
                f"Hello {user.get_full_name() or user.username},\n\n"
                f"Your {restaurant_name} password was successfully changed.\n"
                f"If you did not make this change, please contact your administrator immediately.\n"
            )
            msg = EmailMultiAlternatives(
                subject=f"Password Changed - {restaurant_name}",
                body=text_content,
                from_email=from_email,
                to=[destination]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Failed to send password changed email: {str(e)}")
