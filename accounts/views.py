import os
import base64
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.core.paginator import Paginator
from .models import Restaurant
from .tenant_utils import get_current_restaurant
from .forms import CustomLoginForm, StaffCreationForm, StaffUpdateForm, UserProfileForm
from .decorators import role_required

User = get_user_model()


def user_login(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('superadmin:admin_list')
        elif request.user.role in [User.Role.CHEF, User.Role.KITCHEN]:
            return redirect('chef:dashboard')
        elif request.user.role == User.Role.CASHIER:
            return redirect('dashboard:cashier')
        elif request.user.role == User.Role.WAITER:
            return redirect('tables:waiter_requests')
        return redirect('dashboard:index')

    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if not user.is_active or user.is_suspended:
                messages.error(request, "This account is inactive or suspended. Please contact your restaurant administrator.")
                return redirect('accounts:login')

            if not user.is_superuser and getattr(user, 'restaurant', None) and user.restaurant.status == 'SUSPENDED':
                messages.error(request, f"Access blocked: '{user.restaurant.name}' has been suspended. Please contact platform administration.")
                return redirect('accounts:login')

            login(request, user)
            emp_str = f" [{user.employee_id}]" if user.employee_id else ""
            messages.success(request, f"Welcome back, {user.first_name or user.username}{emp_str}!")
            next_url = request.GET.get('next') or request.POST.get('next')
            if not next_url:
                if user.is_superuser:
                    return redirect('superadmin:admin_list')
                elif user.role in [User.Role.CHEF, User.Role.KITCHEN]:
                    return redirect('chef:dashboard')
                elif user.role == User.Role.CASHIER:
                    return redirect('dashboard:cashier')
                elif user.role == User.Role.WAITER:
                    return redirect('tables:waiter_requests')
                return redirect('dashboard:index')
            return redirect(next_url)
        else:
            messages.error(
                request,
                "Invalid credentials. Please enter your valid Username or Email Address and password."
            )
    else:
        form = CustomLoginForm()

    return render(request, 'accounts/login.html', {
        'form': form,
    })


def user_logout(request):
    if request.user.is_authenticated:
        if getattr(request.user, 'role', None) == User.Role.WAITER:
            try:
                from tables.services import handle_waiter_shift_end_or_logout
                handle_waiter_shift_end_or_logout(request.user)
            except Exception:
                pass
        logout(request)
    messages.info(request, "You have been logged out successfully. Thank you for dining with us!")
    return redirect('login')


@login_required
def user_profile(request):
    user = request.user
    form = UserProfileForm(instance=user)
    password_form = PasswordChangeForm(user=user)

    if request.method == 'POST':
        if 'change_password' in request.POST:
            password_form = PasswordChangeForm(user=user, data=request.POST)
            if password_form.is_valid():
                saved_user = password_form.save()
                update_session_auth_hash(request, saved_user)
                messages.success(request, "Your password has been changed successfully.")
                return redirect('accounts:profile')
            else:
                messages.error(request, "Please correct the password errors below.")
        else:
            form = UserProfileForm(request.POST, request.FILES, instance=user)
            if form.is_valid():
                instance = form.save(commit=False)
                if request.POST.get('remove_profile_picture') == '1':
                    if instance.profile_picture:
                        try:
                            if instance.profile_picture.storage.exists(instance.profile_picture.name):
                                instance.profile_picture.delete(save=False)
                        except Exception:
                            pass
                        instance.profile_picture = None
                instance.save()
                form.save_m2m()

                # If admin user, handle restaurant logo & brand emblem icon updates
                is_admin_user = user.is_superuser or user.role == User.Role.ADMIN or getattr(user, 'is_super_or_admin', False)
                if is_admin_user:
                    restaurant = getattr(user, 'restaurant', None) or get_current_restaurant(request)
                    if not restaurant and user.pk:
                        restaurant = Restaurant.objects.filter(users=user).first()

                    if restaurant:
                        rest_logo = form.cleaned_data.get('restaurant_logo')
                        rest_logo_icon = form.cleaned_data.get('restaurant_logo_icon')
                        remove_rest_logo = request.POST.get('remove_restaurant_logo') == '1'
                        rest_changed = False

                        if remove_rest_logo:
                            if restaurant.logo:
                                try:
                                    if restaurant.logo.storage.exists(restaurant.logo.name):
                                        restaurant.logo.delete(save=False)
                                except Exception:
                                    pass
                                restaurant.logo = None
                            restaurant.logo_base64 = ''
                            rest_changed = True
                        elif rest_logo:
                            restaurant.logo = rest_logo
                            try:
                                rest_logo.seek(0)
                                logo_bytes = rest_logo.read()
                                rest_logo.seek(0)
                                ext = os.path.splitext(rest_logo.name)[1].lower()
                                mime = 'image/svg+xml' if ext == '.svg' else ('image/png' if ext == '.png' else ('image/webp' if ext == '.webp' else 'image/jpeg'))
                                restaurant.logo_base64 = f"data:{mime};base64,{base64.b64encode(logo_bytes).decode('ascii')}"
                            except Exception:
                                pass
                            rest_changed = True

                        if rest_logo_icon and rest_logo_icon != restaurant.logo_icon:
                            restaurant.logo_icon = rest_logo_icon
                            rest_changed = True

                        if rest_changed:
                            restaurant.save()
                            if not user.restaurant:
                                user.restaurant = restaurant
                                user.save(update_fields=['restaurant'])

                messages.success(request, "Your personal information and restaurant branding have been updated successfully.")
                return redirect('accounts:profile')
            else:
                messages.error(request, "Please correct the errors in the form below.")

    return render(request, 'accounts/profile.html', {
        'form': form,
        'password_form': password_form,
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def staff_list(request):
    staff_members = User.objects.exclude(
        role__in=[User.Role.ADMIN, User.Role.CUSTOMER]
    ).exclude(is_superuser=True).order_by('role', 'employee_id')

    if not request.user.is_superuser:
        if request.user.restaurant:
            staff_members = staff_members.filter(restaurant=request.user.restaurant)
        else:
            staff_members = staff_members.filter(restaurant__isnull=True)

    # Search query
    q = request.GET.get('q', '').strip()
    if q:
        staff_members = staff_members.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(employee_id__icontains=q) |
            Q(email__icontains=q) |
            Q(phone_number__icontains=q)
        )

    # Role filter
    role_filter = request.GET.get('role', '').strip()
    if role_filter:
        staff_members = staff_members.filter(role=role_filter)

    # Duty status filter
    duty_filter = request.GET.get('duty', '').strip()
    if duty_filter == 'on':
        staff_members = staff_members.filter(is_on_duty=True)
    elif duty_filter == 'off':
        staff_members = staff_members.filter(is_on_duty=False)

    total_staff = staff_members.count()
    on_duty_count = staff_members.filter(is_on_duty=True).count()

    paginator = Paginator(staff_members, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/staff_list.html', {
        'staff_members': page_obj,
        'page_obj': page_obj,
        'search_q': q,
        'role_filter': role_filter,
        'duty_filter': duty_filter,
        'total_staff': total_staff,
        'on_duty_count': on_duty_count,
        'roles': User.Role,
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def staff_create(request):
    if request.method == 'POST':
        form = StaffCreationForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            try:
                from django.db import IntegrityError, transaction
                with transaction.atomic():
                    user = form.save()
                messages.success(
                    request,
                    f"Staff member {user.get_full_name() or user.username} [{user.employee_id}] created successfully."
                )
                return redirect('accounts:staff_list')
            except IntegrityError:
                form.add_error(
                    'username',
                    "This username is already registered in your organization. Please choose another username."
                )
    else:
        form = StaffCreationForm(user=request.user)

    return render(request, 'accounts/staff_form.html', {
        'form': form,
        'title': 'Add New Staff Member'
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def staff_edit(request, user_id):
    staff_base = User.objects.exclude(role__in=[User.Role.ADMIN, User.Role.CUSTOMER]).exclude(is_superuser=True)
    if request.user.is_superuser:
        staff_member = get_object_or_404(staff_base, id=user_id)
    elif request.user.restaurant:
        staff_member = get_object_or_404(staff_base, id=user_id, restaurant=request.user.restaurant)
    else:
        staff_member = get_object_or_404(staff_base, id=user_id, restaurant__isnull=True)

    if request.method == 'POST':
        form = StaffUpdateForm(request.POST, request.FILES, instance=staff_member, user=request.user)
        if form.is_valid():
            try:
                from django.db import IntegrityError, transaction
                with transaction.atomic():
                    updated_user = form.save()
                messages.success(
                    request,
                    f"Staff account for {staff_member.username} [{staff_member.employee_id}] updated."
                )
                return redirect('accounts:staff_list')
            except IntegrityError:
                form.add_error(
                    'username',
                    "This username is already registered in your organization. Please choose another username."
                )
    else:
        form = StaffUpdateForm(instance=staff_member, user=request.user)

    return render(request, 'accounts/staff_form.html', {
        'form': form,
        'title': f'Edit Staff: {staff_member.get_full_name() or staff_member.username} ({staff_member.employee_id})',
        'is_edit': True,
        'staff_member': staff_member,
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def staff_toggle_duty(request, user_id):
    staff_base = User.objects.exclude(role__in=[User.Role.ADMIN, User.Role.CUSTOMER]).exclude(is_superuser=True)
    if not request.user.is_superuser:
        if request.user.restaurant:
            staff_base = staff_base.filter(restaurant=request.user.restaurant)
        else:
            staff_base = staff_base.filter(restaurant__isnull=True)
    staff_member = get_object_or_404(staff_base, id=user_id)
    staff_member.is_on_duty = not staff_member.is_on_duty
    staff_member.save(update_fields=['is_on_duty'])
    status = "On Duty" if staff_member.is_on_duty else "Off Duty"
    messages.info(request, f"{staff_member.username} [{staff_member.employee_id}] is now marked {status}.")
    return redirect('accounts:staff_list')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def staff_delete(request, user_id):
    staff_base = User.objects.exclude(is_superuser=True)
    if not request.user.is_superuser:
        if request.user.restaurant:
            staff_base = staff_base.filter(restaurant=request.user.restaurant)
        else:
            staff_base = staff_base.filter(restaurant__isnull=True)
    staff_member = get_object_or_404(staff_base, id=user_id)
    if staff_member == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('accounts:staff_list')
    username = staff_member.username
    staff_member.delete()
    messages.success(request, f"Staff member '{username}' has been deleted.")
    return redirect('accounts:staff_list')
