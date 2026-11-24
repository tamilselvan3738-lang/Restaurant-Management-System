import os
import shutil
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.http import FileResponse, Http404
from django.db.models import Sum, Count, Q
from accounts.models import User, Restaurant
from accounts.forms import UserProfileForm
from tables.models import DiningTable, CustomerSession
from orders.models import Order
from billing.models import Payment, Invoice
from .models import RestaurantConfig, AdminReport, SystemSetting
from .forms import (
    AdminCreationForm, AdminEditForm,
    RestaurantDetailsForm, RestaurantStatusForm,
    AdminReportForm, AdminReportReviewForm,
    GeneralSettingsForm, SecuritySettingsForm, BackupSettingsForm
)

def super_admin_check(user):
    return user.is_authenticated and user.is_superuser

super_admin_required = user_passes_test(super_admin_check, login_url='accounts:login')


# ==========================================
# 1. ADMIN MANAGEMENT & GLOBAL SAAS OVERVIEW
# ==========================================

@super_admin_required
def admin_list(request):
    status_filter = request.GET.get('status', 'all')
    # Exclude Super Admin accounts so only restaurant admins/managers are managed here
    admins = User.objects.filter(
        role__in=[User.Role.ADMIN, User.Role.MANAGER],
        is_superuser=False
    ).select_related('restaurant').order_by('-date_joined')

    total_admins = admins.count()
    active_count = admins.filter(is_active=True, is_suspended=False).count()
    inactive_count = admins.filter(is_active=False).count()
    suspended_count = admins.filter(is_suspended=True).count()

    if status_filter == 'active':
        admins = admins.filter(is_active=True, is_suspended=False)
    elif status_filter == 'inactive':
        admins = admins.filter(is_active=False)
    elif status_filter == 'suspended':
        admins = admins.filter(is_suspended=True)

    # Global SaaS metrics computed from real database records across all restaurants
    total_restaurants = Restaurant.objects.count()
    active_restaurants = Restaurant.objects.filter(status=Restaurant.RestaurantStatus.ACTIVE).count()
    inactive_restaurants = Restaurant.objects.filter(status=Restaurant.RestaurantStatus.INACTIVE).count()
    suspended_restaurants = Restaurant.objects.filter(status=Restaurant.RestaurantStatus.SUSPENDED).count()

    total_employees = User.objects.exclude(
        role__in=[User.Role.ADMIN, User.Role.CUSTOMER]
    ).exclude(is_superuser=True).count()

    total_customers = CustomerSession.objects.count()
    total_orders = Order.objects.count()
    total_revenue = Payment.objects.filter(status='COMPLETED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    plan_counts = {
        'free': Restaurant.objects.filter(plan=Restaurant.SubscriptionPlan.FREE).count(),
        'starter': Restaurant.objects.filter(plan=Restaurant.SubscriptionPlan.STARTER).count(),
        'premium': Restaurant.objects.filter(plan=Restaurant.SubscriptionPlan.PREMIUM).count(),
        'enterprise': Restaurant.objects.filter(plan=Restaurant.SubscriptionPlan.ENTERPRISE).count(),
    }

    context = {
        'admins': admins,
        'status_filter': status_filter,
        'total_admins': total_admins,
        'active_count': active_count,
        'inactive_count': inactive_count,
        'suspended_count': suspended_count,
        'total_restaurants': total_restaurants,
        'active_restaurants': active_restaurants,
        'inactive_restaurants': inactive_restaurants,
        'suspended_restaurants': suspended_restaurants,
        'total_employees': total_employees,
        'total_customers': total_customers,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'plan_counts': plan_counts,
    }
    return render(request, 'superadmin/admin_list.html', context)


@super_admin_required
def admin_create(request):
    if request.method == 'POST':
        form = AdminCreationForm(request.POST, request.FILES)
        if form.is_valid():
            new_admin = form.save()
            rest_code = new_admin.restaurant.code if new_admin.restaurant else 'N/A'
            rest_name = new_admin.restaurant.name if new_admin.restaurant else 'N/A'
            messages.success(
                request,
                f"Restaurant Admin '{new_admin.username}' created successfully for {rest_name} (ID: {rest_code})."
            )
            return redirect('superadmin:admin_list')
    else:
        form = AdminCreationForm()

    return render(request, 'superadmin/admin_form.html', {'form': form, 'title': 'Add New Administrator'})


@super_admin_required
def admin_edit(request, user_id):
    admin_user = get_object_or_404(User, id=user_id, is_superuser=False)
    if request.method == 'POST':
        form = AdminEditForm(request.POST, request.FILES, instance=admin_user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Administrator account for '{admin_user.username}' updated.")
            return redirect('superadmin:admin_list')
    else:
        form = AdminEditForm(instance=admin_user)

    return render(request, 'superadmin/admin_form.html', {
        'form': form,
        'title': f'Edit Admin: {admin_user.username}',
        'admin_user': admin_user
    })


@super_admin_required
def admin_reset_password(request, user_id):
    admin_user = get_object_or_404(User, id=user_id, is_superuser=False)
    if request.method == 'POST':
        form = SetPasswordForm(admin_user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Password for '{admin_user.username}' reset successfully.")
            return redirect('superadmin:admin_list')
    else:
        form = SetPasswordForm(admin_user)

    return render(request, 'superadmin/admin_reset_password.html', {
        'form': form,
        'admin_user': admin_user,
    })


@super_admin_required
def admin_toggle_status(request, user_id):
    admin_user = get_object_or_404(User, id=user_id, is_superuser=False)
    if admin_user.is_superuser and admin_user == request.user:
        messages.error(request, "You cannot suspend or deactivate your own Super Admin account.")
        return redirect('superadmin:admin_list')

    action = request.POST.get('action')
    if action == 'suspend':
        admin_user.is_suspended = True
        admin_user.suspension_reason = request.POST.get('reason', 'Suspended by Super Admin')
        admin_user.save(update_fields=['is_suspended', 'suspension_reason'])
        messages.warning(request, f"Admin '{admin_user.username}' has been suspended.")
    elif action == 'unsuspend':
        admin_user.is_suspended = False
        admin_user.suspension_reason = ''
        admin_user.save(update_fields=['is_suspended', 'suspension_reason'])
        messages.success(request, f"Admin '{admin_user.username}' suspension removed.")
    elif action == 'deactivate':
        admin_user.is_active = False
        admin_user.save(update_fields=['is_active'])
        messages.info(request, f"Admin '{admin_user.username}' deactivated.")
    elif action == 'activate':
        admin_user.is_active = True
        admin_user.save(update_fields=['is_active'])
        messages.success(request, f"Admin '{admin_user.username}' activated.")

    return redirect('superadmin:admin_list')


# ==========================================
# 2. RESTAURANT TENANT MANAGEMENT
# ==========================================

@super_admin_required
def restaurant_list(request):
    """
    Dedicated Super Admin view for managing all restaurant tenants in the SaaS platform.
    Displays code, name, primary admin, status, subscription plan, and quick actions.
    """
    status_filter = request.GET.get('status', 'all')
    restaurants = Restaurant.objects.all().order_by('-created_at')

    if status_filter == 'active':
        restaurants = restaurants.filter(status=Restaurant.RestaurantStatus.ACTIVE)
    elif status_filter == 'inactive':
        restaurants = restaurants.filter(status=Restaurant.RestaurantStatus.INACTIVE)
    elif status_filter == 'suspended':
        restaurants = restaurants.filter(status=Restaurant.RestaurantStatus.SUSPENDED)

    # Enrich each restaurant with related metrics
    rest_data = []
    for r in restaurants:
        admin = User.objects.filter(restaurant=r, role=User.Role.ADMIN).first()
        emp_count = User.objects.filter(restaurant=r).exclude(role=User.Role.ADMIN).count()
        table_count = DiningTable.objects.filter(restaurant=r).count()
        order_count = Order.objects.filter(restaurant=r).count()
        revenue = Payment.objects.filter(invoice__restaurant=r, status='COMPLETED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        rest_data.append({
            'restaurant': r,
            'admin': admin,
            'emp_count': emp_count,
            'table_count': table_count,
            'order_count': order_count,
            'revenue': revenue,
        })

    context = {
        'restaurants_data': rest_data,
        'status_filter': status_filter,
        'total_count': Restaurant.objects.count(),
        'active_count': Restaurant.objects.filter(status=Restaurant.RestaurantStatus.ACTIVE).count(),
        'inactive_count': Restaurant.objects.filter(status=Restaurant.RestaurantStatus.INACTIVE).count(),
        'suspended_count': Restaurant.objects.filter(status=Restaurant.RestaurantStatus.SUSPENDED).count(),
    }
    return render(request, 'superadmin/restaurant_list.html', context)


@super_admin_required
def restaurant_edit(request, restaurant_id):
    restaurant = get_object_or_404(Restaurant, id=restaurant_id)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        status = request.POST.get('status', restaurant.status)
        plan = request.POST.get('plan', restaurant.plan)
        suspension_reason = request.POST.get('suspension_reason', '')

        if name:
            restaurant.name = name
        restaurant.address = address
        restaurant.phone = phone
        restaurant.email = email
        restaurant.status = status
        restaurant.plan = plan
        if status == Restaurant.RestaurantStatus.SUSPENDED:
            restaurant.suspension_reason = suspension_reason
        else:
            restaurant.suspension_reason = ''

        if 'logo' in request.FILES:
            restaurant.logo = request.FILES['logo']

        restaurant.save()
        messages.success(request, f"Restaurant '{restaurant.name}' details updated.")
        return redirect('superadmin:restaurant_list')

    return render(request, 'superadmin/restaurant_edit.html', {
        'restaurant': restaurant,
        'status_choices': Restaurant.RestaurantStatus.choices,
        'plan_choices': Restaurant.SubscriptionPlan.choices,
    })


@super_admin_required
def restaurant_toggle_status(request, restaurant_id):
    restaurant = get_object_or_404(Restaurant, id=restaurant_id)
    action = request.POST.get('action')

    if action == 'suspend':
        reason = request.POST.get('reason', 'Suspended by Super Admin')
        restaurant.status = Restaurant.RestaurantStatus.SUSPENDED
        restaurant.suspension_reason = reason
        restaurant.save(update_fields=['status', 'suspension_reason'])
        messages.warning(request, f"Restaurant '{restaurant.name}' ({restaurant.code}) has been SUSPENDED.")
    elif action == 'reactivate' or action == 'activate':
        restaurant.status = Restaurant.RestaurantStatus.ACTIVE
        restaurant.suspension_reason = ''
        restaurant.save(update_fields=['status', 'suspension_reason'])
        messages.success(request, f"Restaurant '{restaurant.name}' ({restaurant.code}) is now ACTIVE.")
    elif action == 'deactivate':
        restaurant.status = Restaurant.RestaurantStatus.INACTIVE
        restaurant.save(update_fields=['status'])
        messages.info(request, f"Restaurant '{restaurant.name}' ({restaurant.code}) is now INACTIVE.")

    return redirect('superadmin:restaurant_list')


@super_admin_required
def view_as_restaurant(request, restaurant_id):
    """
    Enters Super Admin "View as Restaurant" mode.
    Sets the target restaurant in session, audits the action, and redirects to dashboard.
    """
    restaurant = get_object_or_404(Restaurant, id=restaurant_id)
    request.session['impersonate_restaurant_id'] = restaurant.id
    messages.success(
        request,
        f"You are now viewing the system as '{restaurant.name}' ({restaurant.code}). All pages will reflect this tenant."
    )
    return redirect('dashboard:index')


@super_admin_required
def exit_view_as_restaurant(request):
    """
    Exits Super Admin "View as Restaurant" mode and returns to global Super Admin view.
    """
    request.session.pop('impersonate_restaurant_id', None)
    messages.info(request, "Exited View as Restaurant mode. You are back in Super Admin global management.")
    return redirect('superadmin:restaurant_list')


# ==========================================
# 2. RESTAURANT MANAGEMENT
# ==========================================

@super_admin_required
def restaurant_details(request):
    config = RestaurantConfig.get_config()
    if request.method == 'POST':
        form = RestaurantDetailsForm(request.POST, instance=config)
        if form.is_valid():
            config = form.save()
            curr_name = 'Indian Rupee (₹)' if config.currency == '₹' else ('US Dollar ($)' if config.currency == '$' else config.currency)
            messages.success(request, f"Restaurant profile saved. Currency is set to {curr_name}.")
            return redirect('superadmin:restaurant_details')
    else:
        form = RestaurantDetailsForm(instance=config)

    return render(request, 'superadmin/restaurant_details.html', {'form': form, 'config': config})


@super_admin_required
def restaurant_status(request):
    config = RestaurantConfig.get_config()
    if request.method == 'POST':
        form = RestaurantStatusForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            status_text = "Open" if config.is_open else "Closed"
            messages.success(request, f"Restaurant status is now set to: {status_text}.")
            return redirect('superadmin:restaurant_status')
    else:
        form = RestaurantStatusForm(instance=config)

    # Live Floor Capacity
    total_tables = DiningTable.objects.filter(is_active=True).count()
    occupied_tables = DiningTable.objects.filter(is_active=True, status=DiningTable.Status.OCCUPIED).count()
    occupancy_pct = round((occupied_tables / total_tables * 100), 1) if total_tables > 0 else 0

    return render(request, 'superadmin/restaurant_status.html', {
        'form': form,
        'config': config,
        'total_tables': total_tables,
        'occupied_tables': occupied_tables,
        'occupancy_pct': occupancy_pct,
    })


# ==========================================
# 3. REPORTS
# ==========================================

@super_admin_required
def report_list(request):
    status_filter = request.GET.get('status')
    priority_filter = request.GET.get('priority')

    reports = AdminReport.objects.select_related('submitted_by', 'reviewed_by').all()

    if status_filter:
        reports = reports.filter(status=status_filter)
    if priority_filter:
        reports = reports.filter(priority=priority_filter)

    pending_count = AdminReport.objects.filter(status=AdminReport.Status.PENDING).count()

    context = {
        'reports': reports,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'pending_count': pending_count,
        'statuses': AdminReport.Status.choices,
        'priorities': AdminReport.Priority.choices,
    }
    return render(request, 'superadmin/report_list.html', context)


@super_admin_required
def report_detail(request, report_id):
    report = get_object_or_404(AdminReport.objects.select_related('submitted_by', 'reviewed_by'), id=report_id)

    if request.method == 'POST':
        form = AdminReportReviewForm(request.POST, instance=report)
        if form.is_valid():
            review = form.save(commit=False)
            review.reviewed_by = request.user
            if review.status == AdminReport.Status.RESOLVED and not review.resolved_at:
                review.resolved_at = timezone.now()
            review.save()
            messages.success(request, f"Report {report.report_id} updated.")
            return redirect('superadmin:report_detail', report_id=report.id)
    else:
        form = AdminReportReviewForm(instance=report)

    return render(request, 'superadmin/report_detail.html', {'report': report, 'form': form})


@login_required
def report_create(request):
    """
    Allows Admins and Managers to submit an operational or incident report to Super Admin.
    """
    if request.method == 'POST':
        form = AdminReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.submitted_by = request.user
            report.save()
            messages.success(request, f"Report {report.report_id} submitted to Super Admin successfully.")
            if request.user.is_superuser or request.user.role == User.Role.ADMIN:
                return redirect('superadmin:report_list')
            return redirect('dashboard:index')
    else:
        form = AdminReportForm()

    return render(request, 'superadmin/report_form.html', {'form': form})


# ==========================================
# 4. SYSTEM SETTINGS
# ==========================================

@super_admin_required
def general_settings(request):
    settings_obj = SystemSetting.get_settings()
    config = RestaurantConfig.get_config()
    if request.method == 'POST':
        form = GeneralSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            selected_currency = form.cleaned_data.get('currency')
            if selected_currency and selected_currency != config.currency:
                config.currency = selected_currency
                config.save()
            curr_name = 'Indian Rupee (₹)' if config.currency == '₹' else ('US Dollar ($)' if config.currency == '$' else config.currency)
            messages.success(request, f"General settings saved. Active currency is now {curr_name}.")
            return redirect('superadmin:general_settings')
    else:
        form = GeneralSettingsForm(instance=settings_obj, initial={'currency': config.currency})

    return render(request, 'superadmin/settings_general.html', {
        'form': form,
        'settings': settings_obj,
        'config': config
    })


@super_admin_required
def switch_currency(request):
    config = RestaurantConfig.get_config()
    target_currency = request.POST.get('currency') or request.GET.get('currency')
    if target_currency in ['₹', '$', '€', '£']:
        config.currency = target_currency
        config.save()
        curr_label = 'Rupees (₹ INR)' if target_currency == '₹' else ('Dollars ($ USD)' if target_currency == '$' else target_currency)
        messages.success(request, f"Currency switched to {curr_label}. All menu prices, bills, POS, and dashboards now display in {target_currency}.")
    return redirect(request.META.get('HTTP_REFERER') or 'superadmin:general_settings')


@super_admin_required
def security_settings(request):
    settings_obj = SystemSetting.get_settings()
    if request.method == 'POST':
        form = SecuritySettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Security settings updated.")
            return redirect('superadmin:security_settings')
    else:
        form = SecuritySettingsForm(instance=settings_obj)

    return render(request, 'superadmin/settings_security.html', {'form': form, 'settings': settings_obj})


@super_admin_required
def backup_settings(request):
    settings_obj = SystemSetting.get_settings()
    if request.method == 'POST':
        if 'trigger_backup' in request.POST:
            # Create timestamped snapshot in media/backups
            backup_dir = settings.BASE_DIR / 'media' / 'backups'
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            backup_file = backup_dir / f"db_backup_{timestamp}.sqlite3"
            db_source = settings.DATABASES['default']['NAME']

            if os.path.exists(db_source):
                shutil.copy2(db_source, backup_file)
                settings_obj.last_backup_date = timezone.now()
                settings_obj.save(update_fields=['last_backup_date'])
                messages.success(request, f"New database backup created: db_backup_{timestamp}.sqlite3")
            else:
                messages.error(request, "Database source file not found.")

            return redirect('superadmin:backup_settings')

        form = BackupSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Backup preferences updated.")
            return redirect('superadmin:backup_settings')
    else:
        form = BackupSettingsForm(instance=settings_obj)

    # List recent backup snapshots
    backup_dir = settings.BASE_DIR / 'media' / 'backups'
    recent_backups = []
    if backup_dir.exists():
        for f in backup_dir.glob('*.sqlite3'):
            recent_backups.append({
                'name': f.name,
                'size_kb': round(f.stat().st_size / 1024, 1),
                'created': timezone.datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.get_current_timezone()),
            })
        recent_backups.sort(key=lambda x: x['created'], reverse=True)

    return render(request, 'superadmin/backup_settings.html', {
        'form': form,
        'settings': settings_obj,
        'recent_backups': recent_backups[:5],
    })


@super_admin_required
def download_backup(request, filename):
    backup_path = settings.BASE_DIR / 'media' / 'backups' / filename
    if not backup_path.exists() or not filename.endswith('.sqlite3'):
        raise Http404("Backup snapshot not found.")
    return FileResponse(open(backup_path, 'rb'), as_attachment=True, filename=filename)


# ==========================================
# 5. MY PROFILE (Profile & Password Change)
# ==========================================

@login_required
def profile_view(request):
    user = request.user
    profile_form = UserProfileForm(instance=user)
    password_form = PasswordChangeForm(user=user)

    if request.method == 'POST':
        if 'update_profile' in request.POST:
            profile_form = UserProfileForm(request.POST, request.FILES, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your profile information has been saved.")
                return redirect('superadmin:profile')
        elif 'change_password' in request.POST:
            password_form = PasswordChangeForm(user=user, data=request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)
                messages.success(request, "Password changed successfully!")
                return redirect('superadmin:profile')
            else:
                messages.error(request, "Please correct the password errors below.")

    for field in password_form.fields.values():
        field.widget.attrs.update({'class': 'form-control'})

    return render(request, 'superadmin/profile.html', {
        'profile_form': profile_form,
        'password_form': password_form,
    })
