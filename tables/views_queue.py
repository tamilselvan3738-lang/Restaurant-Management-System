import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.db.models import Q, Count
from django.utils import timezone
from accounts.models import User
from accounts.decorators import role_required
from accounts.tenant_utils import get_current_restaurant, get_tenant_object_or_404, filter_by_tenant
from .models import (
    DiningTable,
    RestaurantSeat,
    CustomerSession,
    TableWaitingQueue,
)
from .services import (
    calculate_live_waiting_time,
    update_all_waiting_queue_estimates,
    assign_table_to_queue_entry,
    seat_queue_customer,
    broadcast_queue_event,
)
from .forms import WaitingQueueForm


# ============================================================================
# 1. CUSTOMER-FACING DIGITAL WAITING LIST & LIVE QUEUE
# ============================================================================

def public_queue_view(request):
    """
    Public Digital Waiting List Page.
    Accessible before dining guests are seated and before scanning a table QR code.
    Shows current restaurant availability, queue length, estimated wait times,
    and a registration form to join the digital queue.
    """
    restaurant = getattr(request.user, 'restaurant', None) if request.user.is_authenticated else None

    # Check if this browser already has an active waiting queue token
    saved_token = request.session.get('rms_queue_token') or request.COOKIES.get('rms_queue_token')
    existing_entry = None
    if saved_token:
        existing_entry = TableWaitingQueue.objects.filter(
            queue_token=saved_token,
            status__in=[
                TableWaitingQueue.Status.WAITING,
                TableWaitingQueue.Status.CALLED,
                TableWaitingQueue.Status.TABLE_ASSIGNED
            ]
        ).first()

    # Tables overview
    tables_qs = DiningTable.objects.filter(is_active=True)
    if restaurant:
        tables_qs = tables_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    total_tables = tables_qs.count()
    available_tables = tables_qs.filter(status=DiningTable.Status.AVAILABLE, sessions__status=CustomerSession.Status.ACTIVE).distinct()
    available_count = tables_qs.filter(status=DiningTable.Status.AVAILABLE).count()
    occupied_count = tables_qs.filter(status__in=[DiningTable.Status.OCCUPIED, DiningTable.Status.PARTIALLY_OCCUPIED]).count()

    # Active waiting queue count
    queue_qs = TableWaitingQueue.objects.filter(status=TableWaitingQueue.Status.WAITING)
    if restaurant:
        queue_qs = queue_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
    waiting_groups_count = queue_qs.count()

    # Live estimated wait times for standard party sizes
    estimates = {
        'couple': calculate_live_waiting_time(2, restaurant=restaurant),
        'family': calculate_live_waiting_time(4, restaurant=restaurant),
        'large': calculate_live_waiting_time(6, restaurant=restaurant),
    }

    form = WaitingQueueForm()

    context = {
        'total_tables': total_tables,
        'available_count': available_count,
        'occupied_count': occupied_count,
        'waiting_groups_count': waiting_groups_count,
        'estimates': estimates,
        'form': form,
        'existing_entry': existing_entry,
        'is_busy': available_count == 0 or waiting_groups_count > 0,
    }
    return render(request, 'tables/public_queue.html', context)


@require_POST
def customer_join_queue(request):
    """
    Registers a customer into the Digital Waiting List:
    - Validates inputs (name, phone, guest count, special requests).
    - Prevents accidental duplicates for active entries with the same phone number.
    - Generates unique queue token (e.g. Q-024).
    - Computes data-driven initial waiting time range.
    - Saves cookie & session token and redirects to the Live Tracking screen.
    """
    form = WaitingQueueForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the errors in the form below.")
        return render(request, 'tables/public_queue.html', {'form': form})

    customer_name = form.cleaned_data['customer_name'].strip()
    customer_phone = form.cleaned_data['customer_phone'].strip()
    guest_count = form.cleaned_data['guest_count']
    special_requirements = (form.cleaned_data.get('special_requirements') or '').strip()

    restaurant = getattr(request.user, 'restaurant', None) if request.user.is_authenticated else None

    # Check for active duplicate entry for the same phone number on the same day
    today = timezone.now().date()
    existing_active = TableWaitingQueue.objects.filter(
        customer_phone=customer_phone,
        created_at__date=today,
        status__in=[
            TableWaitingQueue.Status.WAITING,
            TableWaitingQueue.Status.CALLED,
            TableWaitingQueue.Status.TABLE_ASSIGNED
        ]
    ).first()

    if existing_active:
        messages.info(request, f"You already have an active queue ticket ({existing_active.queue_token}). Welcome back!")
        response = redirect('tables:queue_track', queue_token=existing_active.queue_token)
        response.set_cookie('rms_queue_token', existing_active.queue_token, max_age=86400)
        request.session['rms_queue_token'] = existing_active.queue_token
        return response

    # Calculate real-time estimated wait time
    est = calculate_live_waiting_time(guest_count, restaurant=restaurant)

    queue_entry = TableWaitingQueue.objects.create(
        restaurant=restaurant,
        customer_name=customer_name,
        customer_phone=customer_phone,
        guest_count=guest_count,
        special_requirements=special_requirements,
        status=TableWaitingQueue.Status.WAITING,
        estimated_wait_minutes_min=est['min_minutes'],
        estimated_wait_minutes_max=est['max_minutes']
    )

    update_all_waiting_queue_estimates(restaurant=restaurant)
    broadcast_queue_event(queue_entry, event_type='new_entry')

    messages.success(request, f"Welcome {customer_name}! You have been added to the waiting list with Token #{queue_entry.queue_token}.")
    response = redirect('tables:queue_track', queue_token=queue_entry.queue_token)
    response.set_cookie('rms_queue_token', queue_entry.queue_token, max_age=86400)
    request.session['rms_queue_token'] = queue_entry.queue_token
    return response


def customer_track_queue_view(request, queue_token):
    """
    Live Queue Position & Status Tracking Screen for Customers.
    Displays:
    - Queue Token (e.g. Q-024)
    - Current Position in Line (#1, #2...)
    - Live Estimated Wait Time Range
    - Table-Ready / Calling notification when assigned
    - Option to cancel entry
    """
    queue_entry = get_object_or_404(TableWaitingQueue, queue_token=queue_token)

    context = {
        'entry': queue_entry,
        'position': queue_entry.queue_position,
        'wait_display': queue_entry.wait_time_display,
        'is_called': queue_entry.status == TableWaitingQueue.Status.CALLED,
        'is_assigned': queue_entry.status == TableWaitingQueue.Status.TABLE_ASSIGNED,
        'is_seated': queue_entry.status == TableWaitingQueue.Status.SEATED,
        'is_cancelled': queue_entry.status in [TableWaitingQueue.Status.CANCELLED, TableWaitingQueue.Status.NO_SHOW],
    }
    return render(request, 'tables/customer_track_queue.html', context)


def api_queue_status_live(request, queue_token):
    """
    Live polling endpoint for customer queue tracking screen.
    Returns JSON with current position, status, assigned table, and wait time.
    """
    try:
        entry = TableWaitingQueue.objects.select_related('assigned_table').get(queue_token=queue_token)
    except TableWaitingQueue.DoesNotExist:
        return JsonResponse({'error': 'Queue token not found'}, status=404)

    return JsonResponse({
        'token': entry.queue_token,
        'customer_name': entry.customer_name,
        'guest_count': entry.guest_count,
        'position': entry.queue_position,
        'status': entry.status,
        'status_display': entry.get_status_display(),
        'badge_class': entry.badge_class,
        'assigned_table': entry.assigned_table.table_number if entry.assigned_table else None,
        'wait_display': entry.wait_time_display,
        'is_called': entry.status == TableWaitingQueue.Status.CALLED,
        'is_table_ready': entry.status == TableWaitingQueue.Status.TABLE_ASSIGNED,
        'is_seated': entry.status == TableWaitingQueue.Status.SEATED,
        'is_cancelled': entry.status in [TableWaitingQueue.Status.CANCELLED, TableWaitingQueue.Status.NO_SHOW],
    })


@require_POST
def customer_queue_cancel(request, queue_token):
    """
    Allows a customer to cancel their waiting list entry.
    """
    entry = get_object_or_404(TableWaitingQueue, queue_token=queue_token)

    if entry.status in [TableWaitingQueue.Status.SEATED, TableWaitingQueue.Status.CANCELLED, TableWaitingQueue.Status.NO_SHOW]:
        messages.info(request, "This queue ticket has already been concluded.")
        return redirect('tables:queue_public')

    entry.status = TableWaitingQueue.Status.CANCELLED
    entry.cancelled_at = timezone.now()
    entry.cancellation_reason = "Cancelled by customer"
    entry.save()

    update_all_waiting_queue_estimates(restaurant=entry.restaurant)
    broadcast_queue_event(entry, event_type='queue_cancelled')

    messages.info(request, f"Your waiting list ticket {queue_token} has been cancelled.")
    response = redirect('tables:queue_public')
    response.delete_cookie('rms_queue_token')
    if 'rms_queue_token' in request.session:
        del request.session['rms_queue_token']
    return response


# ============================================================================
# 2. ADMIN & STAFF DIGITAL QUEUE MANAGEMENT
# ============================================================================

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
def admin_queue_management_view(request):
    """
    Admin & Staff Digital Waiting List Management Dashboard.
    Add under sidebar: Table Management -> Waiting List.
    Features:
    - Overview metrics (Total Waiting, Available Tables, Occupied Tables, Called, Seated, No-Shows).
    - Status filtering tabs.
    - Quick actions per entry: Call Customer, Assign Table, Mark Seated, Mark No-Show, Cancel.
    - Modal integration with available tables matching party capacity.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    status_filter = request.GET.get('status', 'WAITING').upper()
    query = request.GET.get('q', '').strip()

    today = timezone.now().date()
    base_qs = TableWaitingQueue.objects.select_related(
        'assigned_table', 'called_by', 'seated_by', 'restaurant'
    ).filter(created_at__date=today).order_by('created_at')
    base_qs = filter_by_tenant(base_qs, request)

    # Metrics
    total_waiting = base_qs.filter(status__in=[TableWaitingQueue.Status.WAITING, TableWaitingQueue.Status.CALLED]).count()
    total_assigned = base_qs.filter(status=TableWaitingQueue.Status.TABLE_ASSIGNED).count()
    total_seated = base_qs.filter(status=TableWaitingQueue.Status.SEATED).count()
    total_noshow = base_qs.filter(status=TableWaitingQueue.Status.NO_SHOW).count()
    total_cancelled = base_qs.filter(status=TableWaitingQueue.Status.CANCELLED).count()

    # Table availability
    all_tables = DiningTable.objects.filter(is_active=True).order_by('table_number')
    all_tables = filter_by_tenant(all_tables, request)

    available_tables = all_tables.filter(status=DiningTable.Status.AVAILABLE)

    # Filter base query
    if status_filter == 'ALL':
        entries = base_qs
    elif status_filter == 'WAITING':
        entries = base_qs.filter(status__in=[TableWaitingQueue.Status.WAITING, TableWaitingQueue.Status.CALLED])
    elif status_filter == 'ASSIGNED':
        entries = base_qs.filter(status=TableWaitingQueue.Status.TABLE_ASSIGNED)
    elif status_filter == 'SEATED':
        entries = base_qs.filter(status=TableWaitingQueue.Status.SEATED)
    elif status_filter == 'NO_SHOW':
        entries = base_qs.filter(status=TableWaitingQueue.Status.NO_SHOW)
    elif status_filter == 'CANCELLED':
        entries = base_qs.filter(status=TableWaitingQueue.Status.CANCELLED)
    else:
        entries = base_qs.filter(status=status_filter)

    if query:
        entries = entries.filter(
            Q(queue_token__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(customer_phone__icontains=query) |
            Q(assigned_table__table_number__icontains=query)
        )

    context = {
        'entries': entries,
        'total_waiting': total_waiting,
        'total_assigned': total_assigned,
        'total_seated': total_seated,
        'total_noshow': total_noshow,
        'total_cancelled': total_cancelled,
        'status_filter': status_filter,
        'query': query,
        'all_tables': all_tables,
        'available_tables': available_tables,
        'available_tables_count': available_tables.count(),
        'occupied_tables_count': all_tables.exclude(status=DiningTable.Status.AVAILABLE).count(),
    }
    return render(request, 'tables/admin_queue_manage.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
@require_POST
def admin_queue_call_customer(request, queue_id):
    """
    Staff calls the waiting customer (approaching the front of the queue).
    """
    entry = get_tenant_object_or_404(TableWaitingQueue, request, id=queue_id)
    if entry.status != TableWaitingQueue.Status.WAITING:
        messages.warning(request, f"Customer is already {entry.get_status_display().lower()}.")
        return redirect('tables:admin_queue_manage')

    entry.status = TableWaitingQueue.Status.CALLED
    entry.called_at = timezone.now()
    entry.called_by = request.user
    entry.save(update_fields=['status', 'called_at', 'called_by'])

    broadcast_queue_event(entry, event_type='customer_called')
    messages.info(request, f"Called {entry.customer_name} ({entry.queue_token}). Notification dispatched!")
    return redirect('tables:admin_queue_manage')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
@require_POST
def admin_queue_assign_table(request, queue_id):
    """
    Staff assigns a compatible, available table to the waiting customer.
    """
    table_id = request.POST.get('table_id')
    if not table_id:
        messages.error(request, "Please select an available dining table.")
        return redirect('tables:admin_queue_manage')

    # Security check: ensure both table and queue entry belong to current tenant
    entry = get_tenant_object_or_404(TableWaitingQueue, request, id=queue_id)
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)

    entry, success, msg = assign_table_to_queue_entry(entry.id, table.id, staff_user=request.user)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect('tables:admin_queue_manage')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
@require_POST
def admin_queue_mark_seated(request, queue_id):
    """
    Staff confirms customer arrival and seats them at the assigned table.
    Creates CustomerSession, allocates seats atomically, and updates table to OCCUPIED.
    """
    entry = get_tenant_object_or_404(TableWaitingQueue, request, id=queue_id)
    entry, success, msg = seat_queue_customer(entry.id, staff_user=request.user)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect('tables:admin_queue_manage')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
@require_POST
def admin_queue_mark_noshow(request, queue_id):
    """
    Staff marks a customer as No Show if they fail to arrive. Releases any held table.
    """
    entry = get_tenant_object_or_404(TableWaitingQueue, request, id=queue_id)
    entry.status = TableWaitingQueue.Status.NO_SHOW
    entry.assigned_table = None
    entry.cancelled_at = timezone.now()
    entry.cancellation_reason = "Customer did not arrive (No Show)"
    entry.save()

    update_all_waiting_queue_estimates(restaurant=entry.restaurant)
    broadcast_queue_event(entry, event_type='no_show')

    messages.warning(request, f"Marked {entry.customer_name} ({entry.queue_token}) as No Show. Table released.")
    return redirect('tables:admin_queue_manage')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
@require_POST
def admin_queue_cancel(request, queue_id):
    """
    Staff cancels a queue entry with reason.
    """
    entry = get_tenant_object_or_404(TableWaitingQueue, request, id=queue_id)
    reason = request.POST.get('reason', '').strip() or "Cancelled by restaurant staff"

    entry.status = TableWaitingQueue.Status.CANCELLED
    entry.assigned_table = None
    entry.cancelled_at = timezone.now()
    entry.cancellation_reason = reason
    entry.save()

    update_all_waiting_queue_estimates(restaurant=entry.restaurant)
    broadcast_queue_event(entry, event_type='queue_cancelled')

    messages.info(request, f"Cancelled waiting list entry {entry.queue_token}.")
    return redirect('tables:admin_queue_manage')


@login_required
def admin_queue_poll_api(request):
    """
    Live polling endpoint for Admin waiting list management dashboard.
    """
    today = timezone.now().date()
    qs = filter_by_tenant(TableWaitingQueue.objects.filter(created_at__date=today), request)

    total_waiting = qs.filter(status__in=[TableWaitingQueue.Status.WAITING, TableWaitingQueue.Status.CALLED]).count()
    total_assigned = qs.filter(status=TableWaitingQueue.Status.TABLE_ASSIGNED).count()

    return JsonResponse({
        'total_waiting': total_waiting,
        'total_assigned': total_assigned,
        'timestamp': timezone.now().strftime('%H:%M:%S'),
    })
