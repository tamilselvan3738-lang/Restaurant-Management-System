import logging
import re
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.db import models
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from .models import User, PasswordResetOTP
from .forms import ForgotPasswordRequestForm, OTPVerifyForm, SetNewPasswordForm, UsernameVerificationForm
from .services.delivery import (
    send_otp_email,
    send_otp_sms,
    send_password_change_notice,
    normalize_email,
    normalize_phone_number,
)

logger = logging.getLogger('accounts.forgot_password')


def get_client_ip(request):
    """Safely extracts client IP address from request headers."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def generate_otp_code(digits=4):
    """Generates a secure numeric OTP of specified length."""
    limit = 10 ** digits
    num = secrets.randbelow(limit)
    return f"{num:0{digits}d}"


def mask_contact(destination, channel):
    """Masks email or phone number for safe display."""
    if not destination:
        return ""
    if channel == 'EMAIL' and '@' in destination:
        name, domain = destination.split('@', 1)
        if len(name) <= 2:
            masked_name = name[0] + '*'
        else:
            masked_name = name[0] + ('*' * (len(name) - 2)) + name[-1]
        return f"{masked_name}@{domain}"
    elif channel == 'SMS':
        cleaned = re.sub(r'\D', '', destination)
        if len(cleaned) > 4:
            return f"+{cleaned[:2]} {'*' * (len(cleaned) - 6)} {cleaned[-4:]}"
        return destination
    return destination


def forgot_password_username(request):
    """
    Step 1: Asks for registered Username or Employee ID.
    Checks whether the username is stored on database or not.
    If stored: saves recovery_user_id in session and allows the user to proceed to
    Step 2 (the Password Recovery Channel selection page).
    If not stored: renders an error message and keeps the user on the username page.
    """
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    if request.GET.get('change'):
        request.session.pop('recovery_user_id', None)

    # Backward compatibility: if POST submitted directly with 'channel'
    if request.method == 'POST' and 'channel' in request.POST:
        return forgot_password_channel(request)

    if request.method == 'POST':
        form = UsernameVerificationForm(request.POST)
        if form.is_valid():
            user = getattr(form, 'user', None)
            if user:
                request.session['recovery_user_id'] = user.id
                return redirect('accounts:forgot_password_channel')
            else:
                form.add_error('username', "Username was not found in our database. Please check and try again.")
    else:
        form = UsernameVerificationForm()

    return render(request, 'accounts/forgot_password_username.html', {
        'form': form,
        'page_title': 'Forgot Password - Enter Username',
    })


def forgot_password_channel(request):
    """
    Step 2: Password Recovery Channel selection page (Email or Mobile SMS).
    Allows user to select recovery method and dispatches real OTP.
    Only accessible if username has been verified in Step 1.
    """
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    recovery_user_id = request.session.get('recovery_user_id')
    recovery_user = None
    if recovery_user_id:
        recovery_user = User.objects.filter(id=recovery_user_id, is_active=True).first()

    # If GET request and no valid recovery_user, require username verification first
    if request.method == 'GET' and not recovery_user:
        messages.info(request, "Please enter your registered username first to begin password recovery.")
        return redirect('accounts:forgot_password')

    if request.method == 'POST':
        form = ForgotPasswordRequestForm(request.POST)
        if form.is_valid():
            channel = form.cleaned_data['channel']
            destination = ""
            user = recovery_user

            # Fallback for direct API/test submissions without prior session state
            if not user:
                if channel == 'EMAIL':
                    email = form.cleaned_data['email']
                    user = User.objects.filter(email__iexact=email, is_active=True).first()
                elif channel == 'SMS':
                    phone = form.cleaned_data['phone_number']
                    cleaned_digits = re.sub(r'\D', '', phone)
                    cleaned_10 = cleaned_digits[-10:] if len(cleaned_digits) >= 10 else cleaned_digits
                    user = User.objects.filter(is_active=True).filter(
                        models.Q(phone_number__iexact=phone) |
                        models.Q(phone_number__endswith=cleaned_10)
                    ).first()

            if channel == 'EMAIL':
                email = form.cleaned_data['email']
                destination = email
                if user and user.email:
                    if user.email.strip().lower() != email.strip().lower():
                        messages.error(request, f"The entered email does not match the registered email for account @{user.username}.")
                        return render(request, 'accounts/forgot_password_request.html', {
                            'form': form,
                            'recovery_user': user,
                            'page_title': 'Reset Your Password'
                        })
                elif user and not user.email:
                    # Account has no registered email yet; bind the entered email to this user
                    user.email = email.strip().lower()
                    user.save(update_fields=['email'])

            elif channel == 'SMS':
                phone = form.cleaned_data['phone_number']
                destination = phone
                if user and user.phone_number:
                    user_digits = re.sub(r'\D', '', user.phone_number)
                    input_digits = re.sub(r'\D', '', phone)
                    user_10 = user_digits[-10:] if len(user_digits) >= 10 else user_digits
                    input_10 = input_digits[-10:] if len(input_digits) >= 10 else input_digits
                    if user_10 != input_10:
                        messages.error(request, f"The entered mobile number does not match the registered phone for account @{user.username}.")
                        return render(request, 'accounts/forgot_password_request.html', {
                            'form': form,
                            'recovery_user': user,
                            'page_title': 'Reset Your Password'
                        })
                elif user and not user.phone_number:
                    messages.error(request, f"Account @{user.username} has no registered phone number. Please recover via Email or contact your administrator.")
                    return render(request, 'accounts/forgot_password_request.html', {
                        'form': form,
                        'recovery_user': user,
                        'page_title': 'Reset Your Password'
                    })

            ip_addr = get_client_ip(request)
            digits_count = getattr(settings, 'OTP_DIGITS_COUNT', 4)
            expiry_secs = getattr(settings, 'OTP_EXPIRY_SECONDS', 300)

            if user:
                raw_otp = generate_otp_code(digits_count)
                expires_at = timezone.now() + timedelta(seconds=expiry_secs)

                # Attempt dispatch FIRST before creating challenge
                if channel == 'EMAIL':
                    sent, err = send_otp_email(
                        user=user,
                        email=destination,
                        otp=raw_otp,
                        expires_in_minutes=expiry_secs // 60
                    )
                else:
                    sent, err = send_otp_sms(
                        user=user,
                        phone_number=destination,
                        otp=raw_otp,
                        expires_in_minutes=expiry_secs // 60
                    )

                if not sent:
                    logger.error(f"OTP dispatch failed via {channel} to {mask_contact(destination, channel)}: {err}")
                    messages.error(request, err or "Failed to deliver verification code. Please check SMS provider configuration.")
                    return render(request, 'accounts/forgot_password_request.html', {
                        'form': form,
                        'recovery_user': user,
                        'page_title': 'Reset Your Password'
                    })

                # Invalidate any prior unverified challenges
                PasswordResetOTP.objects.filter(user=user, is_used=False).update(is_used=True)

                otp_challenge = PasswordResetOTP.objects.create(
                    user=user,
                    channel=channel,
                    destination=destination,
                    expires_at=expires_at,
                    ip_address=ip_addr,
                )
                otp_challenge.set_otp(raw_otp)
                otp_challenge.save(update_fields=['otp_hash'])

                # Bind challenge ID into session
                request.session['otp_challenge_id'] = otp_challenge.id
                request.session.pop('dummy_otp_challenge', None)

                request.session.pop('dev_otp_code', None)
                messages.success(
                    request,
                    f"A 4-digit verification code has been dispatched to {mask_contact(destination, channel)}. Please enter the code received on your mobile below."
                )
                return redirect('accounts:forgot_password_verify')
            else:
                # Anti-enumeration for unknown contact
                request.session.pop('otp_challenge_id', None)
                request.session['dummy_otp_challenge'] = {
                    'channel': channel,
                    'destination': destination,
                    'expires_at': (timezone.now() + timedelta(seconds=expiry_secs)).isoformat(),
                    'attempts': 0,
                    'last_resend_at': timezone.now().isoformat(),
                    'resend_count': 0,
                }
                messages.success(
                    request,
                    f"OTP request accepted. If the registered contact exists, a verification code has been dispatched to {mask_contact(destination, channel)}."
                )
                return redirect('accounts:forgot_password_verify')
    else:
        initial_data = {'channel': 'EMAIL'}
        if recovery_user and recovery_user.email:
            initial_data['email'] = recovery_user.email
        form = ForgotPasswordRequestForm(initial=initial_data)

    context = {
        'form': form,
        'recovery_user': recovery_user,
        'page_title': 'Reset Your Password',
    }
    return render(request, 'accounts/forgot_password_request.html', context)


# Alias for backward compatibility
forgot_password_request = forgot_password_channel



def forgot_password_verify(request):
    """
    Step 4: OTP Verification page with 4 separate input boxes, auto-focus, paste,
    countdown expiry timer, and resend cooldown.
    """
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    challenge_id = request.session.get('otp_challenge_id')
    dummy_challenge = request.session.get('dummy_otp_challenge')

    if not challenge_id and not dummy_challenge:
        messages.warning(request, "No active password recovery session found. Please enter your contact details.")
        return redirect('accounts:forgot_password')

    challenge = None
    if challenge_id:
        challenge = PasswordResetOTP.objects.filter(id=challenge_id, is_used=False).first()
        if not challenge:
            request.session.pop('otp_challenge_id', None)
            messages.error(request, "This verification session is invalid or has expired. Please initiate recovery again.")
            return redirect('accounts:forgot_password')

    # Prepare display details
    if challenge:
        channel = challenge.channel
        masked_destination = challenge.masked_destination
        remaining_seconds = challenge.remaining_seconds
        resend_cooldown = challenge.resend_cooldown_remaining
        is_expired = challenge.is_expired
        attempts_left = max(0, 5 - challenge.attempts)
        is_locked = challenge.attempts >= 5
    else:
        channel = dummy_challenge.get('channel', 'EMAIL')
        destination = dummy_challenge.get('destination', '')
        masked_destination = mask_contact(destination, channel)
        remaining_seconds = 300
        resend_cooldown = 60
        is_expired = False
        dummy_attempts = dummy_challenge.get('attempts', 0)
        attempts_left = max(0, 5 - dummy_attempts)
        is_locked = dummy_attempts >= 5

    if request.method == 'POST':
        form = OTPVerifyForm(request.POST)
        if form.is_valid():
            otp_code = form.cleaned_data['otp']

            if challenge:
                if challenge.is_expired:
                    messages.error(request, "This verification code has expired. Please click 'Resend Code' to receive a new one.")
                elif challenge.attempts >= 5:
                    messages.error(request, "Maximum verification attempts exceeded. For security, this challenge is locked. Please request a new code.")
                elif challenge.check_otp(otp_code):
                    # OTP is valid!
                    reset_token = challenge.generate_reset_token()
                    request.session['reset_token'] = reset_token
                    request.session.pop('otp_challenge_id', None)
                    request.session.pop('dev_otp_code', None)
                    messages.success(request, "Verification successful! You can now choose a new secure password.")
                    return redirect('accounts:forgot_password_reset')
                else:
                    challenge.attempts += 1
                    challenge.save(update_fields=['attempts'])
                    attempts_left = max(0, 5 - challenge.attempts)
                    if attempts_left > 0:
                        messages.error(request, f"Incorrect verification code. You have {attempts_left} attempt{'s' if attempts_left > 1 else ''} remaining.")
                    else:
                        messages.error(request, "Maximum verification attempts exceeded. This session is locked. Please request a new code.")
            else:
                # Dummy challenge failure
                dummy_challenge['attempts'] = dummy_challenge.get('attempts', 0) + 1
                request.session['dummy_otp_challenge'] = dummy_challenge
                attempts_left = max(0, 5 - dummy_challenge['attempts'])
                if attempts_left > 0:
                    messages.error(request, f"Incorrect verification code. You have {attempts_left} attempt{'s' if attempts_left > 1 else ''} remaining.")
                else:
                    messages.error(request, "Maximum verification attempts exceeded. Please request a new code.")
        else:
            messages.error(request, "Please enter all 4 numeric digits of your verification code.")

    context = {
        'channel': channel,
        'masked_destination': masked_destination,
        'remaining_seconds': remaining_seconds,
        'resend_cooldown': resend_cooldown,
        'is_expired': is_expired,
        'attempts_left': attempts_left,
        'is_locked': is_locked,
        'page_title': 'Verify Recovery Code',
    }
    return render(request, 'accounts/forgot_password_verify.html', context)


@require_POST
def forgot_password_resend_otp(request):
    """
    Resends OTP code respecting 60s cooldown and max 3 resends per session.
    """
    challenge_id = request.session.get('otp_challenge_id')
    dummy_challenge = request.session.get('dummy_otp_challenge')

    if not challenge_id and not dummy_challenge:
        return JsonResponse({'success': False, 'message': 'No active recovery session found.'}, status=400)

    if challenge_id:
        challenge = PasswordResetOTP.objects.filter(id=challenge_id, is_used=False).first()
        if not challenge:
            return JsonResponse({'success': False, 'message': 'Invalid session.'}, status=400)

        if not challenge.can_resend:
            cooldown = challenge.resend_cooldown_remaining
            if cooldown > 0:
                return JsonResponse({
                    'success': False,
                    'message': f"Please wait {cooldown} seconds before requesting a new code.",
                    'cooldown': cooldown
                }, status=429)
            elif challenge.resend_count >= 3:
                return JsonResponse({
                    'success': False,
                    'message': "Maximum resend limit reached for this session. Please initiate recovery again."
                }, status=429)

        # Issue new OTP
        digits_count = getattr(settings, 'OTP_DIGITS_COUNT', 4)
        expiry_secs = getattr(settings, 'OTP_EXPIRY_SECONDS', 300)
        new_otp = generate_otp_code(digits_count)

        challenge.set_otp(new_otp)
        challenge.resend_count += 1
        challenge.last_resend_at = timezone.now()
        challenge.expires_at = timezone.now() + timedelta(seconds=expiry_secs)
        challenge.attempts = 0  # Reset attempt counter for new OTP
        challenge.save()

        if challenge.channel == 'EMAIL':
            sent, err = send_otp_email(
                user=challenge.user,
                email=challenge.destination,
                otp=new_otp,
                expires_in_minutes=expiry_secs // 60
            )
        else:
            sent, err = send_otp_sms(
                user=challenge.user,
                phone_number=challenge.destination,
                otp=new_otp,
                expires_in_minutes=expiry_secs // 60
            )

        if not sent:
            return JsonResponse({
                'success': False,
                'message': err or "Failed to resend verification code. Please check SMS provider settings."
            }, status=400)

        return JsonResponse({
            'success': True,
            'message': f"A new verification code has been dispatched to {challenge.masked_destination}.",
            'cooldown': 60,
            'remaining_seconds': expiry_secs
        })
    else:
        # Dummy resend: simulate success
        return JsonResponse({
            'success': True,
            'message': "A new verification code has been dispatched.",
            'cooldown': 60,
            'remaining_seconds': 300
        })


def forgot_password_reset(request):
    """
    Step 5: Password Reset form guarded by the single-use reset authorization token.
    Enforces password complexity, mismatch checking, and current password reuse prevention.
    """
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    token = request.session.get('reset_token') or request.GET.get('token')
    if not token:
        messages.error(request, "Authorization token is missing. Please complete OTP verification first.")
        return redirect('accounts:forgot_password')

    challenge = PasswordResetOTP.objects.filter(
        reset_token=token,
        is_verified=True,
        is_used=False
    ).first()

    if not challenge or not challenge.is_reset_token_valid(token):
        messages.error(request, "Your password reset authorization token has expired or is invalid. Please start again.")
        return redirect('accounts:forgot_password')

    user = challenge.user

    if request.method == 'POST':
        form = SetNewPasswordForm(user, request.POST)
        if form.is_valid():
            new_password = form.cleaned_data['new_password']

            # Step 6: Save password securely
            user.set_password(new_password)
            user.save()

            # Mark challenge used and invalidate outstanding tokens
            challenge.mark_used()
            PasswordResetOTP.objects.filter(user=user, is_used=False).update(
                is_used=True,
                reset_token=None
            )

            # Clear session authorization
            request.session.pop('reset_token', None)
            request.session.pop('otp_challenge_id', None)

            # Send confirmation notification
            send_password_change_notice(user, challenge.channel, challenge.destination)

            messages.success(request, "Your password has been successfully changed. Please log in using your new password.")
            return redirect('accounts:forgot_password_success')
    else:
        form = SetNewPasswordForm(user)

    context = {
        'form': form,
        'user_account': user,
        'page_title': 'Set New Password',
    }
    return render(request, 'accounts/forgot_password_reset.html', context)


def forgot_password_success(request):
    """
    Step 6 Success: Informs user of successful reset and provides clean Back to Login button.
    """
    return render(request, 'accounts/forgot_password_success.html', {
        'page_title': 'Password Changed Successfully',
    })
