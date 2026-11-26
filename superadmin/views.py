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
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.contrib.sessions.models import Session
import logging

from accounts.models import User, Restaurant
from accounts.forms import UserProfileForm
from tables.models import DiningTable, CustomerSession, ServiceRequest
from orders.models import Order
from billing.models import Payment, Invoice, BillRequest
from .models import RestaurantConfig, AdminReport, SystemSetting, AccountStatusAuditLog
from .forms import (
    AdminCreationForm, AdminEditForm,
    RestaurantDetailsForm, RestaurantStatusForm,
    AdminReportForm, AdminReportReviewForm,
    GeneralSettingsForm, SecuritySettingsForm, BackupSettingsForm
)

logger = logging.getLogger(__name__)

def invalidate_user_sessions(user_ids):
    """Purges active sessions for given user IDs."""
    if not user_ids:
        return
    user_id_strs = {str(uid) for uid in user_ids}
    try:
        active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
        for session in active_sessions:
            data = session.get_decoded()
            if str(data.get('_auth_user_id')) in user_id_strs:
                session.delete()
    except Exception as e:
        logger.warning("Session invalidation failed: %s", e)

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

    paginator = Paginator(admins, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Convert to list and compute per-admin staff statistics & cascade impact
    admins_list = list(page_obj)
    for a in admins_list:
        emp_q = Q(created_by=a)
        if a.restaurant:
            emp_q |= Q(restaurant=a.restaurant)
        emp_qs = User.objects.filter(emp_q).exclude(pk=a.pk).exclude(is_superuser=True).distinct()
        a.total_employees_count = emp_qs.count()
        a.active_employees_count = emp_qs.filter(is_active=True).count()
        a.inactive_employees_count = emp_qs.filter(is_active=False).count()

        latest_cascade = AccountStatusAuditLog.objects.filter(
            target_user=a,
            action__in=[AccountStatusAuditLog.Action.CASCADE_DEACTIVATE, AccountStatusAuditLog.Action.ADMIN_DEACTIVATE]
        ).order_by('-created_at').first()
        a.affected_employees_count = latest_cascade.affected_count if latest_cascade else a.inactive_employees_count

    # Recent Audit Logs for quick reference modal
    recent_audit_logs = AccountStatusAuditLog.objects.select_related(
        'target_user', 'restaurant', 'performed_by'
    ).all().order_by('-created_at')[:20]

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
        'admins': admins_list,
        'page_obj': page_obj,
        'recent_audit_logs': recent_audit_logs,
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
            try:
                with transaction.atomic():
                    new_admin = form.save()
                messages.success(
                    request,
                    f"Restaurant Admin created successfully. Administrator '{new_admin.get_full_name() or new_admin.username}' is ready."
                )
                return redirect('superadmin:admin_list')
            except IntegrityError as exc:
                logger.exception("IntegrityError creating admin: %s", exc)
                err_text = str(exc).lower()
                if 'username' in err_text or 'normalized_username' in err_text:
                    form.add_error('username', "An administrator or employee with this username already exists in this restaurant.")
                elif 'email' in err_text:
                    form.add_error('email', "This email address is already registered.")
                elif 'phone' in err_text:
                    form.add_error('phone_number', "This phone number is already registered.")
                else:
                    form.add_error(None, f"Database constraint violation: {exc}")
                messages.error(request, "Failed to save administrator. Please review the highlighted errors below.")
            except Exception as e:
                logger.exception("Failed to create restaurant admin: %s", e)
                form.add_error(None, f"Failed to save administrator: {e}")
                messages.error(request, f"Failed to create restaurant admin: {e}")
        else:
            messages.error(request, "Please correct the errors indicated below.")
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
    reason = request.POST.get('reason', '').strip()

    if action == 'suspend':
        admin_user.is_suspended = True
        admin_user.suspension_reason = reason or 'Suspended by Super Admin'
        admin_user.save(update_fields=['is_suspended', 'suspension_reason'])

        restaurant = admin_user.restaurant
        affected_staff_ids = []
        if restaurant:
            restaurant.status = Restaurant.RestaurantStatus.SUSPENDED
            restaurant.save(update_fields=['status'])
            affected_staff_ids = list(User.objects.filter(restaurant=restaurant).values_list('id', flat=True))

        all_suspended_ids = list(set([admin_user.id] + affected_staff_ids))
        invalidate_user_sessions(all_suspended_ids)

        AccountStatusAuditLog.objects.create(
            target_user=admin_user,
            restaurant=restaurant,
            action=AccountStatusAuditLog.Action.ADMIN_SUSPEND,
            performed_by=request.user,
            affected_count=len(affected_staff_ids),
            affected_employee_ids=affected_staff_ids,
            reason=admin_user.suspension_reason
        )
        if restaurant:
            messages.warning(request, f"Admin '{admin_user.username}' and restaurant '{restaurant.name}' have been suspended.")
        else:
            messages.warning(request, f"Admin '{admin_user.username}' has been suspended.")

    elif action == 'unsuspend':
        admin_user.is_suspended = False
        admin_user.suspension_reason = ''
        admin_user.save(update_fields=['is_suspended', 'suspension_reason'])

        restaurant = admin_user.restaurant
        if restaurant:
            restaurant.status = Restaurant.RestaurantStatus.ACTIVE
            restaurant.save(update_fields=['status'])

        AccountStatusAuditLog.objects.create(
            target_user=admin_user,
            restaurant=restaurant,
            action=AccountStatusAuditLog.Action.ADMIN_UNSUSPEND,
            performed_by=request.user,
            reason="Suspension lifted by Super Admin"
        )
        if restaurant:
            messages.success(request, f"Admin '{admin_user.username}' and restaurant '{restaurant.name}' suspension removed. Status restored to Active.")
        else:
            messages.success(request, f"Admin '{admin_user.username}' suspension removed.")

    elif action == 'deactivate':
        with transaction.atomic():
            admin_user.is_active = False
            admin_user.save(update_fields=['is_active'])

            restaurant = admin_user.restaurant
            affected_count = 0
            affected_ids = []

            if restaurant:
                # Deactivate restaurant status to Inactive
                restaurant.status = Restaurant.RestaurantStatus.INACTIVE
                restaurant.save(update_fields=['status'])

                # Cascade deactivate all employees of this restaurant across all roles
                employees = User.objects.filter(
                    restaurant=restaurant
                ).exclude(pk=admin_user.pk).exclude(is_superuser=True)

                active_employees = employees.filter(is_active=True)
                affected_ids = list(active_employees.values_list('id', flat=True))
                affected_count = len(affected_ids)

                active_employees.update(is_active=False)

            # Record audit log
            AccountStatusAuditLog.objects.create(
                target_user=admin_user,
                restaurant=restaurant,
                action=AccountStatusAuditLog.Action.CASCADE_DEACTIVATE,
                performed_by=request.user,
                affected_count=affected_count,
                affected_employee_ids=affected_ids,
                reason=reason or f"Cascade deactivation: Admin deactivated and {affected_count} employee accounts deactivated."
            )

            # Invalidate sessions for Admin and all affected employees
            invalidate_user_sessions([admin_user.id] + affected_ids)

        if affected_count > 0:
            messages.success(
                request,
                f"Admin {admin_user.username} and {affected_count} associated employee accounts have been deactivated successfully."
            )
        else:
            messages.success(
                request,
                f"Admin {admin_user.username} has been deactivated successfully."
            )

    elif action == 'activate':
        with transaction.atomic():
            admin_user.is_active = True
            admin_user.save(update_fields=['is_active'])

            if admin_user.restaurant:
                admin_user.restaurant.status = Restaurant.RestaurantStatus.ACTIVE
                admin_user.restaurant.save(update_fields=['status'])

            AccountStatusAuditLog.objects.create(
                target_user=admin_user,
                restaurant=admin_user.restaurant,
                action=AccountStatusAuditLog.Action.ADMIN_REACTIVATE,
                performed_by=request.user,
                reason="Admin reactivated. Associated employee accounts preserved inactive for security and review."
            )

        messages.success(
            request,
            f"Admin {admin_user.username} has been reactivated successfully. Employee accounts remain inactive for security and require individual review/reactivation."
        )

    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('superadmin:admin_list')


@super_admin_required
def admin_delete(request, user_id):
    """
    Permanently delete an Administrator account and all employees created by
    or associated with this Administrator / restaurant.
    """
    admin_user = get_object_or_404(User, id=user_id, is_superuser=False)

    if admin_user.is_superuser or admin_user == request.user:
        messages.error(request, "Super Administrator accounts cannot be deleted.")
        return redirect('superadmin:admin_list')

    if request.method == 'POST':
        admin_name = admin_user.get_full_name() or admin_user.username
        admin_username = admin_user.username
        restaurant = admin_user.restaurant

        # Identify all employee accounts created by this admin or belonging to their restaurant
        q_filter = Q(created_by=admin_user)
        if restaurant:
            q_filter |= Q(restaurant=restaurant)

        employees_to_delete = User.objects.filter(q_filter).exclude(pk=admin_user.pk).exclude(is_superuser=True).distinct()

        emp_count = employees_to_delete.count()
        emp_ids = list(employees_to_delete.values_list('id', flat=True))
        all_user_ids = emp_ids + [admin_user.id]

        try:
            with transaction.atomic():
                # 1. Invalidate active sessions for both the admin and all deleted employees
                invalidate_user_sessions(all_user_ids)

                # 2. Permanently delete all associated employee accounts
                employees_to_delete.delete()

                # 3. Permanently delete the admin user
                admin_user.delete()

                # 4. If this restaurant has no other administrators, clean up and delete the restaurant
                restaurant_deleted = False
                rest_name = ""
                if restaurant:
                    has_other_admins = User.objects.filter(
                        restaurant=restaurant,
                        role__in=[User.Role.ADMIN, User.Role.MANAGER]
                    ).exclude(is_superuser=True).exists()

                    if not has_other_admins:
                        # Clean up associated tables and requests to prevent orphan rows
                        DiningTable.objects.filter(restaurant=restaurant).delete()
                        BillRequest.objects.filter(restaurant=restaurant).delete()
                        ServiceRequest.objects.filter(restaurant=restaurant).delete()
                        rest_name = restaurant.name
                        restaurant.delete()
                        restaurant_deleted = True

            if restaurant_deleted:
                messages.success(
                    request,
                    f"Administrator '{admin_name}' (@{admin_username}), restaurant '{rest_name}', and {emp_count} associated employee account(s) have been permanently deleted."
                )
            else:
                messages.success(
                    request,
                    f"Administrator '{admin_name}' (@{admin_username}) and {emp_count} associated employee account(s) have been permanently deleted."
                )
            logger.info(
                "Superadmin %s permanently deleted admin %s, restaurant %s (deleted=%s), and %d associated employees.",
                request.user.username,
                admin_username,
                restaurant.name if restaurant else "N/A",
                restaurant_deleted,
                emp_count
            )
        except Exception as e:
            logger.exception("Failed to permanently delete admin %s: %s", admin_username, e)
            messages.error(request, f"Error permanently deleting administrator: {str(e)}")

        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect('superadmin:admin_list')

    return redirect('superadmin:admin_list')


@super_admin_required
def admin_employees(request, user_id):
    """View and manage employees belonging to a specific restaurant admin."""
    admin_user = get_object_or_404(User, id=user_id, is_superuser=False)
    restaurant = admin_user.restaurant
    if not restaurant:
        messages.warning(request, f"Admin '{admin_user.username}' has no restaurant assigned.")
        return redirect('superadmin:admin_list')

    employees = User.objects.filter(
        restaurant=restaurant
    ).exclude(pk=admin_user.pk).exclude(is_superuser=True).order_by('role', 'employee_id', 'username')

    return render(request, 'superadmin/admin_employees.html', {
        'admin_user': admin_user,
        'restaurant': restaurant,
        'employees': employees,
        'total_count': employees.count(),
        'active_count': employees.filter(is_active=True).count(),
        'inactive_count': employees.filter(is_active=False).count(),
    })


@super_admin_required
def employee_toggle_status(request, user_id):
    """Allows Super Admin to individually reactivate or deactivate an employee."""
    employee = get_object_or_404(User, id=user_id, is_superuser=False)
    action = request.POST.get('action')
    return_admin_id = request.POST.get('admin_id')

    if action == 'activate':
        employee.is_active = True
        employee.save(update_fields=['is_active'])
        AccountStatusAuditLog.objects.create(
            target_user=employee,
            restaurant=employee.restaurant,
            action=AccountStatusAuditLog.Action.EMPLOYEE_REACTIVATE,
            performed_by=request.user,
            reason="Individual employee reactivation"
        )
        messages.success(request, f"Employee '{employee.username}' [{employee.get_role_display()}] activated successfully.")
    elif action == 'deactivate':
        employee.is_active = False
        employee.save(update_fields=['is_active'])
        AccountStatusAuditLog.objects.create(
            target_user=employee,
            restaurant=employee.restaurant,
            action=AccountStatusAuditLog.Action.EMPLOYEE_DEACTIVATE,
            performed_by=request.user,
            reason="Individual employee deactivation"
        )
        invalidate_user_sessions([employee.id])
        messages.warning(request, f"Employee '{employee.username}' [{employee.get_role_display()}] deactivated.")

    if return_admin_id:
        return redirect('superadmin:admin_employees', user_id=return_admin_id)
    return redirect('superadmin:admin_list')


@super_admin_required
def audit_history(request):
    """View full system audit log of account status and cascade operations."""
    logs = AccountStatusAuditLog.objects.select_related(
        'target_user', 'restaurant', 'performed_by'
    ).all().order_by('-created_at')

    return render(request, 'superadmin/audit_history.html', {
        'logs': logs,
        'total_logs': logs.count(),
    })


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

    paginator = Paginator(restaurants, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Enrich each restaurant with related metrics
    rest_data = []
    for r in page_obj:
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
        'page_obj': page_obj,
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
            logo_file = request.FILES['logo']
            restaurant.logo = logo_file
            try:
                import base64
                import mimetypes
                logo_file.seek(0)
                content = logo_file.read()
                logo_file.seek(0)
                mime = getattr(logo_file, 'content_type', None) or mimetypes.guess_type(logo_file.name)[0] or 'image/png'
                b64 = base64.b64encode(content).decode('utf-8')
                restaurant.logo_base64 = f"data:{mime};base64,{b64}"
            except Exception:
                pass

        logo_icon = request.POST.get('logo_icon', '').strip()
        if logo_icon:
            restaurant.logo_icon = logo_icon

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

    paginator = Paginator(reports, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    pending_count = AdminReport.objects.filter(status=AdminReport.Status.PENDING).count()

    context = {
        'reports': page_obj,
        'page_obj': page_obj,
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
    backup_dir = getattr(settings, 'BACKUP_DIR', settings.BASE_DIR / 'backups')
    backup_dir.mkdir(parents=True, exist_ok=True)

    if request.method == 'POST':
        if 'trigger_backup' in request.POST:
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            db_engine = settings.DATABASES['default'].get('ENGINE', '')
            db_source = settings.DATABASES['default'].get('NAME', '')

            try:
                if 'sqlite' in db_engine and os.path.exists(db_source):
                    backup_file = backup_dir / f"db_backup_{timestamp}.sqlite3"
                    shutil.copy2(db_source, backup_file)
                    created_name = backup_file.name
                else:
                    # Generic / PostgreSQL export using Django dumpdata
                    from django.core.management import call_command
                    backup_file = backup_dir / f"db_backup_{timestamp}.json"
                    with open(backup_file, 'w', encoding='utf-8') as f:
                        call_command('dumpdata', exclude=['contenttypes', 'auth.permission'], stdout=f)
                    created_name = backup_file.name

                settings_obj.last_backup_date = timezone.now()
                settings_obj.save(update_fields=['last_backup_date'])
                messages.success(request, f"New database snapshot created successfully: {created_name}")
            except Exception as e:
                logger.error("Database backup creation failed: %s", e)
                messages.error(request, f"Backup creation failed: {e}")

            return redirect('superadmin:backup_settings')

        form = BackupSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Backup preferences updated.")
            return redirect('superadmin:backup_settings')
    else:
        form = BackupSettingsForm(instance=settings_obj)

    # List recent backup snapshots (supporting both .sqlite3 and .json)
    recent_backups = []
    if backup_dir.exists():
        for f in list(backup_dir.glob('*.sqlite3')) + list(backup_dir.glob('*.json')):
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
    clean_filename = os.path.basename(filename)
    if clean_filename != filename:
        raise Http404("Invalid backup filename.")

    allowed_exts = ('.sqlite3', '.json', '.sql')
    if not any(clean_filename.endswith(ext) for ext in allowed_exts) or not clean_filename.startswith('db_backup_'):
        raise Http404("Invalid backup snapshot.")

    backup_dir = getattr(settings, 'BACKUP_DIR', settings.BASE_DIR / 'backups')
    backup_path = backup_dir / clean_filename

    # Fallback to legacy path if existing snapshot was saved under media/backups
    if not backup_path.exists():
        legacy_path = settings.BASE_DIR / 'media' / 'backups' / clean_filename
        if legacy_path.exists():
            backup_path = legacy_path

    if not backup_path.exists() or not backup_path.is_file():
        raise Http404("Backup snapshot not found.")

    return FileResponse(open(backup_path, 'rb'), as_attachment=True, filename=clean_filename)


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
