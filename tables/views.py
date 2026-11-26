import json
import datetime
from decimal import Decimal
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Sum
from django.core.paginator import Paginator
from accounts.models import User, Restaurant
from accounts.decorators import role_required
from menu.models import Category, MenuItem, FoodCombo, FoodComboItem
from orders.models import Order, OrderItem, OrderStatusHistory, OrderCancellationLog
from billing.models import Invoice, Payment, BillRequest
from .models import (
    DiningTable, Reservation, CustomerSession, SessionCart, SessionCartItem,
    RestaurantSeat, SessionSeatAllocation, SeatAuditLog, GroupMember, get_lan_ip, build_table_qr_url
)
from .consumers import broadcast_group_cart_update
from superadmin.models import RestaurantConfig
from .forms import DiningTableForm, ReservationForm
from accounts.tenant_utils import (
    get_current_restaurant,
    is_impersonating_restaurant,
    get_tenant_object_or_404,
    filter_by_tenant,
)
from .services import get_dining_table_or_404


# ============================================================================
# TABLE MANAGEMENT & FLOOR PLAN (ADMIN / MANAGER / STAFF)
# ============================================================================

@login_required
def floor_plan(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    location_filter = request.GET.get('location')
    tables = DiningTable.objects.filter(is_active=True).prefetch_related(
        'sessions__orders__items',
        'sessions__invoices__payments'
    )
    if restaurant:
        tables = tables.filter(restaurant=restaurant)
    elif not request.user.is_superuser or is_impersonating:
        tables = tables.none()

    if location_filter:
        tables = tables.filter(location=location_filter)

    for t in tables:
        t.sync_status()

    total_tables = tables.count()
    available_tables = tables.filter(status=DiningTable.Status.AVAILABLE).count()
    occupied_tables = tables.filter(status=DiningTable.Status.OCCUPIED).count()
    partially_occupied = tables.filter(status=DiningTable.Status.PARTIALLY_OCCUPIED).count()
    bill_requested_tables = tables.filter(status=DiningTable.Status.BILL_REQUESTED).count()
    reserved_tables = tables.filter(status=DiningTable.Status.RESERVED).count()
    cleaning_tables = tables.filter(status=DiningTable.Status.CLEANING).count()

    # Total active sessions across this restaurant
    if restaurant:
        active_sessions_count = CustomerSession.objects.filter(
            restaurant=restaurant,
            status=CustomerSession.Status.ACTIVE
        ).count()
    elif request.user.is_superuser and not is_impersonating:
        active_sessions_count = CustomerSession.objects.filter(
            status=CustomerSession.Status.ACTIVE
        ).count()
    else:
        active_sessions_count = 0

    context = {
        'tables': tables,
        'locations': DiningTable.Location.choices,
        'selected_location': location_filter,
        'total_tables': total_tables,
        'available_tables': available_tables,
        'occupied_tables': occupied_tables,
        'partially_occupied': partially_occupied,
        'bill_requested_tables': bill_requested_tables,
        'reserved_tables': reserved_tables,
        'cleaning_tables': cleaning_tables,
        'active_sessions_count': active_sessions_count,
    }
    return render(request, 'tables/floor_plan.html', context)


@login_required
def table_update_status(request, table_id):
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(DiningTable.Status.choices):
            if new_status == DiningTable.Status.AVAILABLE:
                table.release_and_set_available(performed_by=request.user)
            elif new_status == DiningTable.Status.OCCUPIED:
                table.set_occupied(performed_by=request.user)
            elif new_status == DiningTable.Status.CLEANING:
                table.release_and_set_available(performed_by=request.user)
                table.status = DiningTable.Status.CLEANING
                table.save(update_fields=['status'])
            else:
                table.status = new_status
                table.save(update_fields=['status'])
            messages.success(request, f"Table {table.table_number} status updated to {table.get_status_display()}. All seats set to {table.get_status_display()}.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:floor_plan'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def table_create(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    if request.method == 'POST':
        form = DiningTableForm(request.POST, restaurant=restaurant)
        if form.is_valid():
            table = form.save()
            messages.success(request, f"Table {table.table_number} created successfully.")
            return redirect('tables:floor_plan')
    else:
        form = DiningTableForm(restaurant=restaurant)

    return render(request, 'tables/table_form.html', {'form': form, 'title': 'Add Dining Table'})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def table_edit(request, table_id):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    if request.method == 'POST':
        form = DiningTableForm(request.POST, instance=table, restaurant=restaurant)
        if form.is_valid():
            form.save()
            messages.success(request, f"Table {table.table_number} updated successfully.")
            return redirect('tables:floor_plan')
    else:
        form = DiningTableForm(instance=table, restaurant=restaurant)

    return render(request, 'tables/table_form.html', {'form': form, 'table': table, 'title': f'Edit Table {table.table_number}'})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def table_delete(request, table_id):
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    if request.method == 'POST':
        table_num = table.table_number
        has_active_sessions = table.sessions.filter(status=CustomerSession.Status.ACTIVE).exists()
        has_active_orders = table.orders.filter(
            status__in=['PENDING', 'ACCEPTED', 'PREPARING', 'READY', 'SERVED']
        ).exists()

        if has_active_sessions or has_active_orders or table.status == DiningTable.Status.OCCUPIED:
            messages.error(
                request,
                f"Cannot delete Table {table_num} because it currently has active guests or orders in progress. Please complete or release the table first."
            )
            return redirect('tables:floor_plan')

        table.delete()
        messages.success(request, f"Table {table_num} has been deleted successfully.")
    return redirect('tables:floor_plan')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def table_regenerate_qr(request, table_id):
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    table.regenerate_qr_token()
    messages.success(request, f"New QR code and security token generated for Table {table.table_number}.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:floor_plan'))


@login_required
def table_release(request, table_id):
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    table.release_and_set_available(performed_by=request.user)
    messages.success(request, f"Table {table.table_number} and all its seats are now released and Available.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:floor_plan'))


# ============================================================================
# SEAT-BASED OCCUPANCY MANAGEMENT & AUDIT DASHBOARD
# ============================================================================

@login_required
def seat_management(request):
    location_filter = request.GET.get('location')
    status_filter = request.GET.get('status')
    table_filter = request.GET.get('table')

    tables_qs = DiningTable.objects.filter(is_active=True).prefetch_related(
        'seats',
        'seats__allocations__session__invoices',
        'seats__allocations__session__orders',
        'sessions'
    ).order_by('table_number')
    tables_qs = filter_by_tenant(tables_qs, request)

    if location_filter:
        tables_qs = tables_qs.filter(location=location_filter)
    if table_filter:
        tables_qs = tables_qs.filter(id=table_filter)

    # Ensure all tables have physical seats initialized
    for t in tables_qs:
        t.sync_seats()

    all_seats = filter_by_tenant(
        RestaurantSeat.objects.filter(is_active=True, table__is_active=True),
        request,
        restaurant_field='table__restaurant'
    )
    total_tables_count = tables_qs.count()
    total_capacity = all_seats.count()
    occupied_seats_count = all_seats.filter(status=RestaurantSeat.Status.OCCUPIED).count()
    available_seats_count = all_seats.filter(status=RestaurantSeat.Status.AVAILABLE).count()
    cleaning_seats_count = all_seats.filter(status=RestaurantSeat.Status.CLEANING).count()
    reserved_seats_count = all_seats.filter(status=RestaurantSeat.Status.RESERVED).count()
    disabled_seats_count = all_seats.filter(status=RestaurantSeat.Status.DISABLED).count()

    tables_data = []
    for table in tables_qs:
        table_seats = table.seats.filter(is_active=True).order_by('seat_number')
        if status_filter:
            matching_seats = [s for s in table_seats if s.status == status_filter]
        else:
            matching_seats = list(table_seats)

        tables_data.append({
            'table': table,
            'seats': table_seats,
            'matching_seats': matching_seats,
            'occupied_count': table.occupied_seats_count,
            'available_count': table.available_seats_count,
            'active_sessions': table.active_sessions(),
        })

    recent_logs = filter_by_tenant(
        SeatAuditLog.objects.select_related('seat__table', 'performed_by', 'session'),
        request,
        restaurant_field='seat__table__restaurant'
    ).order_by('-timestamp')[:15]
    all_active_tables = filter_by_tenant(DiningTable.objects.filter(is_active=True), request).order_by('table_number')

    context = {
        'tables_data': tables_data,
        'all_active_tables': all_active_tables,
        'total_tables_count': total_tables_count,
        'total_capacity': total_capacity,
        'occupied_seats_count': occupied_seats_count,
        'available_seats_count': available_seats_count,
        'cleaning_seats_count': cleaning_seats_count,
        'reserved_seats_count': reserved_seats_count,
        'disabled_seats_count': disabled_seats_count,
        'location_filter': location_filter,
        'status_filter': status_filter,
        'table_filter': table_filter,
        'locations': DiningTable.Location.choices,
        'seat_statuses': RestaurantSeat.Status.choices,
        'recent_logs': recent_logs,
    }
    return render(request, 'tables/seat_management.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
def seat_manual_allocate(request, seat_id):
    seat = get_tenant_object_or_404(RestaurantSeat, request, id=seat_id, is_active=True)
    if request.method == 'POST':
        session_id = request.POST.get('session_id')
        guest_name = request.POST.get('guest_name', '').strip()
        reason = request.POST.get('reason', '').strip() or "Manual staff seat assignment"

        with transaction.atomic():
            locked_seat = RestaurantSeat.objects.select_for_update().get(id=seat.id)
            if locked_seat.status == RestaurantSeat.Status.OCCUPIED:
                messages.error(request, f"Table {locked_seat.table.table_number} Seat {locked_seat.seat_number} is already occupied.")
                return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))

            target_session = None
            if session_id:
                target_session = get_object_or_404(CustomerSession, id=session_id, table=locked_seat.table)
            else:
                active_count = locked_seat.table.sessions.filter(
                    status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]
                ).count()
                grp_name = f"Group {chr(65 + (active_count % 26))}"
                target_session = CustomerSession.objects.create(
                    restaurant=locked_seat.table.restaurant,
                    table=locked_seat.table,
                    session_name=grp_name,
                    customer_name=guest_name or f"{grp_name} Walk-in",
                    guest_count=1,
                    status=CustomerSession.Status.ACTIVE
                )
                SessionCart.objects.get_or_create(session=target_session)

            locked_seat.status = RestaurantSeat.Status.OCCUPIED
            locked_seat.save(update_fields=['status', 'updated_at'])

            SessionSeatAllocation.objects.create(
                session=target_session,
                seat=locked_seat,
                status=SessionSeatAllocation.AllocationStatus.ACTIVE
            )

            SeatAuditLog.objects.create(
                seat=locked_seat,
                action="MANUAL_ALLOCATE",
                performed_by=request.user,
                session=target_session,
                previous_status=RestaurantSeat.Status.AVAILABLE,
                new_status=RestaurantSeat.Status.OCCUPIED,
                reason=reason
            )

            locked_seat.table.sync_status()

            messages.success(
                request,
                f"Seat {locked_seat.seat_number} at Table {locked_seat.table.table_number} allocated to {target_session.session_name}."
            )

    return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
def seat_manual_release(request, seat_id):
    seat = get_tenant_object_or_404(RestaurantSeat, request, id=seat_id, is_active=True)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "A recorded reason is required to manually release a seat.")
            return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))

        with transaction.atomic():
            locked_seat = RestaurantSeat.objects.select_for_update().get(id=seat.id)
            prev_status = locked_seat.status

            alloc = locked_seat.allocations.filter(status='ACTIVE').first()
            session = alloc.session if alloc else None
            if alloc:
                alloc.release(reason=f"Manual staff release: {reason}")

            locked_seat.status = RestaurantSeat.Status.AVAILABLE
            locked_seat.save(update_fields=['status', 'updated_at'])

            SeatAuditLog.objects.create(
                seat=locked_seat,
                action="MANUAL_RELEASE",
                performed_by=request.user,
                session=session,
                previous_status=prev_status,
                new_status=RestaurantSeat.Status.AVAILABLE,
                reason=reason
            )

            locked_seat.table.sync_status()

            messages.success(
                request,
                f"Seat {locked_seat.seat_number} at Table {locked_seat.table.table_number} released to Available."
            )

    return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
def seat_update_status(request, seat_id):
    seat = get_tenant_object_or_404(RestaurantSeat, request, id=seat_id, is_active=True)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        reason = request.POST.get('reason', '').strip() or f"Status changed to {new_status}"

        if new_status in dict(RestaurantSeat.Status.choices):
            prev_status = seat.status
            if prev_status == new_status:
                return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))

            with transaction.atomic():
                locked_seat = RestaurantSeat.objects.select_for_update().get(id=seat.id)
                if prev_status == RestaurantSeat.Status.OCCUPIED and new_status != RestaurantSeat.Status.OCCUPIED:
                    alloc = locked_seat.allocations.filter(status='ACTIVE').first()
                    if alloc:
                        alloc.release(reason=f"Seat status updated to {new_status}: {reason}")

                locked_seat.status = new_status
                locked_seat.save(update_fields=['status', 'updated_at'])

                SeatAuditLog.objects.create(
                    seat=locked_seat,
                    action="STATUS_CHANGE",
                    performed_by=request.user,
                    session=locked_seat.current_session(),
                    previous_status=prev_status,
                    new_status=new_status,
                    reason=reason
                )

                locked_seat.table.sync_status()

                messages.success(
                    request,
                    f"Table {locked_seat.table.table_number} Seat {locked_seat.seat_number} marked as {locked_seat.get_status_display()}."
                )

    return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def table_update_capacity(request, table_id):
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    if request.method == 'POST':
        try:
            new_capacity = int(request.POST.get('capacity', 0))
            reason = request.POST.get('reason', '').strip() or "Capacity configuration update"
            if new_capacity < 1:
                messages.error(request, "Table capacity must be at least 1 seat.")
                return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))

            occ = table.occupied_seats_count
            if new_capacity < occ:
                messages.error(
                    request,
                    f"Cannot reduce capacity to {new_capacity}. Table {table.table_number} currently has {occ} occupied seats. Please release seats first."
                )
                return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))

            old_cap = table.capacity
            table.capacity = new_capacity
            table.save(update_fields=['capacity'])
            table.sync_seats()
            table.sync_status()

            first_seat = table.seats.first()
            if first_seat:
                SeatAuditLog.objects.create(
                    seat=first_seat,
                    action="CAPACITY_CHANGE",
                    performed_by=request.user,
                    previous_status=f"Capacity {old_cap}",
                    new_status=f"Capacity {new_capacity}",
                    reason=reason
                )

            messages.success(request, f"Table {table.table_number} capacity updated from {old_cap} to {new_capacity} seats.")
        except ValueError:
            messages.error(request, "Invalid capacity value.")

    return redirect(request.META.get('HTTP_REFERER', 'tables:seat_management'))


@login_required
def api_seats_live(request):
    tables = DiningTable.objects.filter(is_active=True).prefetch_related(
        'seats',
        'seats__allocations__session'
    ).order_by('table_number')
    tables = filter_by_tenant(tables, request)

    data = {
        'total_capacity': 0,
        'occupied_count': 0,
        'available_count': 0,
        'cleaning_count': 0,
        'reserved_count': 0,
        'disabled_count': 0,
        'tables': []
    }

    for t in tables:
        seats_list = []
        for s in t.seats.filter(is_active=True).order_by('seat_number'):
            data['total_capacity'] += 1
            if s.status == RestaurantSeat.Status.OCCUPIED:
                data['occupied_count'] += 1
            elif s.status == RestaurantSeat.Status.AVAILABLE:
                data['available_count'] += 1
            elif s.status == RestaurantSeat.Status.CLEANING:
                data['cleaning_count'] += 1
            elif s.status == RestaurantSeat.Status.RESERVED:
                data['reserved_count'] += 1
            elif s.status == RestaurantSeat.Status.DISABLED:
                data['disabled_count'] += 1

            sess = s.current_session()
            seats_list.append({
                'id': s.id,
                'seat_number': s.seat_number,
                'status': s.status,
                'status_display': s.get_status_display(),
                'badge_class': s.badge_class,
                'session_name': sess.session_name if sess else None,
                'customer_name': sess.customer_name if sess else None,
            })

        data['tables'].append({
            'id': t.id,
            'table_number': t.table_number,
            'capacity': t.capacity,
            'status': t.status,
            'status_display': t.get_status_display(),
            'badge_class': t.badge_class,
            'occupied_seats': t.occupied_seats_count,
            'available_seats': t.available_seats_count,
            'seats': seats_list
        })

    return JsonResponse(data)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def seat_audit_logs(request):
    table_id = request.GET.get('table')
    action_filter = request.GET.get('action')

    logs_qs = filter_by_tenant(
        SeatAuditLog.objects.select_related('seat__table', 'performed_by', 'session'),
        request,
        restaurant_field='seat__table__restaurant'
    ).order_by('-timestamp')

    if table_id:
        logs_qs = logs_qs.filter(seat__table_id=table_id)
    if action_filter:
        logs_qs = logs_qs.filter(action=action_filter)

    tables = filter_by_tenant(DiningTable.objects.filter(is_active=True), request).order_by('table_number')

    return render(request, 'tables/seat_audit_logs.html', {
        'logs': logs_qs[:100],
        'tables': tables,
        'table_id': table_id,
        'action_filter': action_filter,
    })


# ============================================================================
# CUSTOMER SESSIONS LIST & SEPARATE BILLING DASHBOARD
# ============================================================================

@login_required
def session_list(request):
    status_filter = request.GET.get('status')
    table_id = request.GET.get('table')

    sessions_qs = filter_by_tenant(
        CustomerSession.objects.select_related('table').prefetch_related('orders__items', 'invoices__payments'),
        request
    )

    if status_filter:
        sessions_qs = sessions_qs.filter(status=status_filter)
    if table_id:
        sessions_qs = sessions_qs.filter(table_id=table_id)

    total_active = filter_by_tenant(
        CustomerSession.objects.filter(status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]),
        request
    ).count()
    tables = filter_by_tenant(DiningTable.objects.filter(is_active=True), request)

    paginator = Paginator(sessions_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'sessions': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'table_id': table_id,
        'tables': tables,
        'total_active': total_active,
        'statuses': CustomerSession.Status.choices,
    }
    return render(request, 'tables/session_list.html', context)


@login_required
def session_detail(request, session_id):
    session = get_tenant_object_or_404(
        CustomerSession.objects.select_related('table').prefetch_related('orders__items__menu_item', 'invoices__payments'),
        request,
        id=session_id
    )
    invoice = session.invoices.first()
    if not invoice and session.orders.exists():
        invoice = Invoice.objects.create(
            restaurant=session.restaurant,
            session=session,
            table=session.table,
            invoice_type=Invoice.InvoiceType.SESSION_BILL
        )
        invoice.recalculate(save=True)

    context = {
        'session': session,
        'invoice': invoice,
        'orders': session.orders.all(),
    }
    return render(request, 'tables/session_detail.html', context)


@login_required
def session_close(request, session_id):
    session = get_tenant_object_or_404(CustomerSession, request, id=session_id)
    session.close_session()
    messages.success(request, f"Session '{session.session_name}' for Table {session.table.table_number} closed.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:session_list'))


# ============================================================================
# RESERVATIONS
# ============================================================================

@login_required
def reservation_list(request):
    filter_type = request.GET.get('filter', 'today')
    today = timezone.localdate()

    reservations = filter_by_tenant(Reservation.objects.select_related('table'), request)

    if filter_type == 'today':
        reservations = reservations.filter(reservation_date=today)
    elif filter_type == 'upcoming':
        reservations = reservations.filter(reservation_date__gt=today)

    paginator = Paginator(reservations, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'reservations': page_obj,
        'page_obj': page_obj,
        'filter_type': filter_type,
        'today': today,
    }
    return render(request, 'tables/reservation_list.html', context)


@login_required
def reservation_create(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    if request.method == 'POST':
        form = ReservationForm(request.POST, restaurant=restaurant)
        if form.is_valid():
            res = form.save()
            if res.reservation_date == timezone.localdate() and res.status == Reservation.Status.CONFIRMED:
                res.table.status = DiningTable.Status.RESERVED
                res.table.save(update_fields=['status'])
            messages.success(request, f"Reservation for {res.customer_name} confirmed on Table {res.table.table_number}.")
            return redirect('tables:reservation_list')
    else:
        table_id = request.GET.get('table')
        initial = {'reservation_date': timezone.localdate()}
        if table_id:
            initial['table'] = table_id
        form = ReservationForm(restaurant=restaurant, initial=initial)

    return render(request, 'tables/reservation_form.html', {'form': form, 'title': 'Create Reservation'})


@login_required
def reservation_update_status(request, reservation_id):
    res = get_tenant_object_or_404(Reservation, request, id=reservation_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Reservation.Status.choices):
            res.status = new_status
            res.save(update_fields=['status'])
            if new_status == Reservation.Status.SEATED:
                res.table.status = DiningTable.Status.OCCUPIED
                res.table.save(update_fields=['status'])
            elif new_status == Reservation.Status.CANCELLED:
                if res.table.status == DiningTable.Status.RESERVED:
                    res.table.status = DiningTable.Status.AVAILABLE
                    res.table.save(update_fields=['status'])
            messages.success(request, f"Reservation for {res.customer_name} is now {res.get_status_display()}.")
    return redirect('tables:reservation_list')


# ============================================================================
# CUSTOMER QR DIGITAL MENU & MULTI-SESSION ORDERING SYSTEM (NO LOGIN REQUIRED)
# ============================================================================

def _get_active_session_and_member(request, table):
    """
    Resolves the current active customer session and specific group member for a table.
    Uses request cookies and session keys.
    """
    session_cookie_key = f'rms_session_{table.id}'
    member_cookie_key = f'rms_member_{table.id}'

    session_token = (
        request.GET.get('session') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )

    member_token = (
        request.GET.get('member') or
        request.session.get(f'table_{table.id}_member_token') or
        request.COOKIES.get(member_cookie_key)
    )

    current_session = None
    if session_token:
        current_session = CustomerSession.objects.filter(
            table=table,
            session_token=session_token,
            status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]
        ).first()

    current_member = None
    if current_session:
        if member_token:
            matched_member = current_session.members.filter(member_token=member_token).first()
            if matched_member:
                if not matched_member.is_active:
                    # Member was deactivated / removed by leader or left the group
                    if f'table_{table.id}_session_token' in request.session:
                        del request.session[f'table_{table.id}_session_token']
                    if f'table_{table.id}_member_token' in request.session:
                        del request.session[f'table_{table.id}_member_token']
                    return None, None
                current_member = matched_member

        # If session exists but no specific member cookie, check if a leader exists or create one
        if not current_member:
            leader = current_session.leader
            if not leader:
                leader = current_session.get_or_create_leader()
            current_member = leader

        # CRITICAL SAFETY: Ensure current_session has an active leader and permissions are properly granted
        if current_member:
            is_creator = bool(
                current_session.customer_name and
                current_member.name and
                current_member.name.strip().lower() == current_session.customer_name.strip().lower()
            )
            is_solo_group = (current_session.active_members.count() <= 1)
            has_no_other_leader = not current_session.members.filter(
                role=GroupMember.Role.LEADER,
                is_active=True
            ).exclude(id=current_member.id).exists()

            if is_creator or is_solo_group or has_no_other_leader or current_member.is_leader:
                if current_member.role != GroupMember.Role.LEADER or not current_member.can_submit_order:
                    current_member.role = GroupMember.Role.LEADER
                    current_member.can_submit_order = True
                    current_member.save(update_fields=['role', 'can_submit_order'])

    return current_session, current_member


def _clear_customer_table_session(request, response, table_id):
    """Completely clears all session cookies and Django session keys for a customer table."""
    for c in [
        f'rms_session_{table_id}',
        f'rms_member_{table_id}',
        f'customer_session_{table_id}',
        f'group_member_{table_id}',
    ]:
        response.delete_cookie(c)
    if hasattr(request, 'session'):
        request.session.pop(f'table_{table_id}_session_token', None)
        request.session.pop(f'table_{table_id}_member_token', None)
    return response


def customer_table_order(request, table_number):
    """
    Public customer-facing digital menu loaded when dining guests scan the table QR code.
    Supports Group Ordering and Shared Cart:
      - If no active session or guest requests switch/join, displays the Group Selection screen.
      - If active session, loads the shared digital menu with real-time cart.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)

    if table.restaurant and table.restaurant.status == Restaurant.RestaurantStatus.SUSPENDED:
        return render(request, 'accounts/tenant_suspended.html', {
            'restaurant': table.restaurant,
            'reason': table.restaurant.suspension_reason or "Account suspended by Super Administrator."
        }, status=403)

    table.sync_seats()

    current_session, current_member = _get_active_session_and_member(request, table)
    session_cookie_key = f'rms_session_{table.id}'
    member_cookie_key = f'rms_member_{table.id}'

    # 1. Handle POST Actions (Create Group, Join Group, Place Order)
    if request.method == 'POST':
        action = request.POST.get('action')

        # Action A: CREATE A NEW SHARED GROUP OR 1-PERSON SESSION
        if action in ['start_session', 'create_group']:
            try:
                guest_count = max(1, int(request.POST.get('guest_count') or request.POST.get('party_size', 1)))
            except (ValueError, TypeError):
                guest_count = 1

            if guest_count > table.capacity:
                err_msg = f"Party size ({guest_count}) exceeds table capacity ({table.capacity} seats)."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('tables:customer_order', table_number=table.table_number)

            existing_count = table.sessions.filter(
                status=CustomerSession.Status.ACTIVE
            ).count()

            if guest_count == 1:
                default_group_name = f"Table {table.table_number}"
                custom_name = request.POST.get('session_name', '').strip()
                group_name = custom_name if custom_name else default_group_name
                customer_name = request.POST.get('customer_name', '').strip() or "Guest"
            else:
                default_group_name = f"Group {chr(65 + (existing_count % 26))}"
                custom_name = request.POST.get('session_name', '').strip()
                group_name = custom_name if custom_name else default_group_name
                customer_name = request.POST.get('customer_name', '').strip() or f"{group_name} Leader"

            customer_phone = request.POST.get('customer_phone', '').strip()
            special_requests = request.POST.get('special_requests', '').strip()

            new_session = CustomerSession.objects.create(
                table=table,
                session_name=group_name,
                customer_name=customer_name,
                customer_phone=customer_phone,
                guest_count=guest_count,
                special_requests=special_requests,
                status=CustomerSession.Status.ACTIVE
            )
            # Create Group Leader
            leader = GroupMember.objects.create(
                session=new_session,
                name=customer_name,
                role=GroupMember.Role.LEADER,
                can_submit_order=True,
                is_active=True
            )
            # Create Shared Cart
            SessionCart.objects.get_or_create(session=new_session)

            request.session[f'table_{table.id}_session_token'] = new_session.session_token
            request.session[f'table_{table.id}_member_token'] = leader.member_token

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                resp = JsonResponse({
                    'status': 'success',
                    'message': f"Created {new_session.session_name}!",
                    'group': {
                        'id': new_session.id,
                        'session_name': new_session.session_name,
                        'customer_name': new_session.customer_name,
                        'guest_count': new_session.guest_count,
                        'join_code': new_session.join_code,
                        'session_token': new_session.session_token,
                    },
                    'redirect_url': f"/tables/{table.table_number}/order/"
                })
                resp.set_cookie(session_cookie_key, new_session.session_token, max_age=86400)
                resp.set_cookie(member_cookie_key, leader.member_token, max_age=86400)
                return resp

            if guest_count == 1:
                messages.success(request, f"Welcome to Table {table.table_number}! You can now start ordering.")
            else:
                messages.success(
                    request,
                    f"Your shared dining group is ready! Share invitation code #{new_session.join_code} with your friends."
                )
            resp = redirect('tables:customer_order', table_number=table.table_number)
            resp.set_cookie(session_cookie_key, new_session.session_token, max_age=86400)
            resp.set_cookie(member_cookie_key, leader.member_token, max_age=86400)
            return resp

        # Action A2: CREATE MULTIPLE SEPARATE GROUPS (SPLIT DINING)
        elif action == 'create_split_groups':
            try:
                party_size = max(1, int(request.POST.get('party_size', 1)))
            except (ValueError, TypeError):
                party_size = 1

            groups_raw = request.POST.get('groups_data') or request.POST.get('groups')
            groups_list = []
            if groups_raw:
                if isinstance(groups_raw, str):
                    try:
                        groups_list = json.loads(groups_raw)
                    except Exception:
                        groups_list = []
                elif isinstance(groups_raw, list):
                    groups_list = groups_raw

            if not groups_list:
                half = max(1, party_size // 2)
                groups_list = [
                    {'name': 'Group A', 'guest_count': half, 'leader_name': request.POST.get('customer_name', '').strip() or 'Leader A'},
                    {'name': 'Group B', 'guest_count': party_size - half, 'leader_name': 'Leader B'},
                ]

            total_allocated = sum(int(g.get('guest_count', 1)) for g in groups_list)
            if total_allocated != party_size:
                err_msg = f"Allocated members ({total_allocated}) must match confirmed party size ({party_size})."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('tables:customer_order', table_number=table.table_number)

            if party_size > table.capacity:
                err_msg = f"Total party size ({party_size}) exceeds table capacity ({table.capacity})."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('tables:customer_order', table_number=table.table_number)

            created_groups = []
            with transaction.atomic():
                for idx, g_info in enumerate(groups_list):
                    g_name = g_info.get('name', '').strip() or f"Group {chr(65 + idx)}"
                    g_count = max(1, int(g_info.get('guest_count', 1)))
                    l_name = g_info.get('leader_name', '').strip() or f"{g_name} Leader"

                    sess = CustomerSession.objects.create(
                        table=table,
                        session_name=g_name,
                        customer_name=l_name,
                        guest_count=g_count,
                        status=CustomerSession.Status.ACTIVE
                    )
                    ldr = GroupMember.objects.create(
                        session=sess,
                        name=l_name,
                        role=GroupMember.Role.LEADER,
                        can_submit_order=True,
                        is_active=True
                    )
                    SessionCart.objects.get_or_create(session=sess)

                    created_groups.append({
                        'id': sess.id,
                        'session_name': sess.session_name,
                        'customer_name': sess.customer_name,
                        'guest_count': sess.guest_count,
                        'join_code': sess.join_code,
                        'session_token': sess.session_token,
                        'is_mine': (idx == 0)
                    })

            # Bind current client to Group A (index 0)
            primary_group = created_groups[0]
            primary_session = CustomerSession.objects.get(id=primary_group['id'])
            primary_leader = primary_session.leader

            request.session[f'table_{table.id}_session_token'] = primary_session.session_token
            request.session[f'table_{table.id}_member_token'] = primary_leader.member_token

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                resp = JsonResponse({
                    'status': 'success',
                    'message': f"Created {len(created_groups)} separate groups at Table {table.table_number}.",
                    'groups': created_groups,
                    'redirect_url': f"/tables/{table.table_number}/order/"
                })
                resp.set_cookie(session_cookie_key, primary_session.session_token, max_age=86400)
                resp.set_cookie(member_cookie_key, primary_leader.member_token, max_age=86400)
                return resp

            messages.success(request, f"Created {len(created_groups)} groups. You are leading {primary_session.session_name} (Code: {primary_session.join_code}).")
            resp = redirect('tables:customer_order', table_number=table.table_number)
            resp.set_cookie(session_cookie_key, primary_session.session_token, max_age=86400)
            resp.set_cookie(member_cookie_key, primary_leader.member_token, max_age=86400)
            return resp

        # Action B: JOIN AN EXISTING GROUP (SPLIT TABLE JOINING)
        elif action in ['join_session', 'join_group']:
            join_code = request.POST.get('join_code', '').strip().upper()
            session_id = request.POST.get('session_id')
            display_name = request.POST.get('customer_name', '').strip()

            # Smart check: if user entered a 6-digit invitation code in the name box
            candidate_code = join_code
            if not candidate_code and display_name.isdigit() and len(display_name) == 6:
                candidate_code = display_name
                display_name = ''

            target_session = None
            if candidate_code:
                # 1. Look for matching code at this table
                target_session = CustomerSession.objects.filter(
                    table=table,
                    join_code=candidate_code
                ).order_by('-created_at').first()

                if not target_session:
                    # 2. Check if code exists globally at another restaurant or table
                    other_sess = CustomerSession.objects.filter(join_code=candidate_code).select_related('table').order_by('-created_at').first()
                    if other_sess:
                        if other_sess.table.restaurant_id != table.restaurant_id:
                            err_msg = "Invalid invitation code. Please check the code and try again."
                        else:
                            err_msg = f"This invitation code belongs to Table {other_sess.table.table_number}, not Table {table.table_number}."
                    else:
                        err_msg = "Invalid invitation code. Please check the code and try again."

                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                    messages.error(request, err_msg)
                    return redirect('tables:customer_order', table_number=table.table_number)

            elif session_id:
                target_session = CustomerSession.objects.filter(
                    table=table,
                    id=session_id
                ).first()

            if not target_session:
                err_msg = "Invalid invitation code. Please check the code and try again."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('tables:customer_order', table_number=table.table_number)

            # Check if group is completed / cancelled
            if target_session.status in [CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED]:
                err_msg = "This group is no longer accepting members."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('tables:customer_order', table_number=table.table_number)

            active_cnt = target_session.members.filter(is_active=True).count()

            # Check if this user/device already has an active member token for this session
            current_member_token = request.session.get(f'table_{table.id}_member_token') or request.COOKIES.get(member_cookie_key)
            is_already_in_group = False
            existing_member = None
            if current_member_token:
                existing_member = target_session.members.filter(
                    member_token=current_member_token,
                    is_active=True
                ).first()
                if existing_member:
                    is_already_in_group = True

            # If display_name is not provided, default to Member N
            if not display_name:
                display_name = f"Member {active_cnt + 1}"

            # Strict group member capacity check:
            # If a leader creates a group for 3 members, only 3 members can join (including the leader)
            if not is_already_in_group and target_session.guest_count and active_cnt >= target_session.guest_count:
                err_msg = f"{target_session.session_name} is full (maximum {target_session.guest_count} members allowed)."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('tables:customer_order', table_number=table.table_number)

            is_creator = bool(
                target_session.customer_name and
                display_name.strip().lower() == target_session.customer_name.strip().lower()
            )
            has_active_leader = target_session.members.filter(
                role=GroupMember.Role.LEADER,
                is_active=True
            ).exists()
            is_first_member = (active_cnt == 0)

            should_be_leader = is_creator or (not has_active_leader) or is_first_member

            if existing_member:
                new_member = existing_member
                if should_be_leader and (new_member.role != GroupMember.Role.LEADER or not new_member.can_submit_order):
                    new_member.role = GroupMember.Role.LEADER
                    new_member.can_submit_order = True
                    new_member.save(update_fields=['role', 'can_submit_order'])
            else:
                new_member = GroupMember.objects.create(
                    session=target_session,
                    name=display_name,
                    role=GroupMember.Role.LEADER if should_be_leader else GroupMember.Role.MEMBER,
                    can_submit_order=should_be_leader,
                    is_active=True
                )

            # Migrate active cart items from previous session if customer is switching/joining
            prev_session_token = request.session.get(f'table_{table.id}_session_token') or request.COOKIES.get(session_cookie_key)
            if prev_session_token and prev_session_token != target_session.session_token:
                prev_sess = CustomerSession.objects.filter(session_token=prev_session_token).first()
                if prev_sess and hasattr(prev_sess, 'cart'):
                    target_cart, _ = SessionCart.objects.get_or_create(session=target_session)
                    prev_sess.cart.items.filter(status=SessionCartItem.Status.ACTIVE).update(
                        cart=target_cart,
                        added_by=new_member,
                        added_by_name=new_member.name
                    )

            request.session[f'table_{table.id}_session_token'] = target_session.session_token
            request.session[f'table_{table.id}_member_token'] = new_member.member_token

            broadcast_group_cart_update(
                target_session.session_token,
                event_type='cart_updated',
                data={'member_name': display_name, 'action': 'joined'}
            )

            success_msg = "Successfully joined your group! You can now order food with your group members."

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                resp = JsonResponse({
                    'status': 'success',
                    'message': success_msg,
                    'group': {
                        'id': target_session.id,
                        'session_name': target_session.session_name,
                        'customer_name': target_session.customer_name,
                        'join_code': target_session.join_code,
                    },
                    'redirect_url': f"/tables/{table.table_number}/order/"
                })
                resp.set_cookie(session_cookie_key, target_session.session_token, max_age=86400)
                resp.set_cookie(member_cookie_key, new_member.member_token, max_age=86400)
                return resp

            messages.success(request, success_msg)
            resp = redirect('tables:customer_order', table_number=table.table_number)
            resp.set_cookie(session_cookie_key, target_session.session_token, max_age=86400)
            resp.set_cookie(member_cookie_key, new_member.member_token, max_age=86400)
            return resp

        # Action B2: REMOVE MEMBER FROM GROUP (LEADER ONLY)
        elif action == 'remove_member':
            if not current_session or not current_member:
                return JsonResponse({'status': 'error', 'message': 'No active session or member found.'}, status=403)
            if not current_member.is_leader:
                return JsonResponse({'status': 'error', 'message': 'Only the Group Leader can remove members.'}, status=403)

            target_member_id = request.POST.get('member_id')
            target_member = current_session.members.filter(id=target_member_id, is_active=True).first()
            if not target_member:
                return JsonResponse({'status': 'error', 'message': 'Member not found or already inactive.'}, status=404)
            if target_member.id == current_member.id or target_member.is_leader:
                return JsonResponse({'status': 'error', 'message': 'The Group Leader cannot be removed.'}, status=400)

            target_member.is_active = False
            target_member.save(update_fields=['is_active'])

            # Remove unsubmitted cart items added by this member from the shared cart
            SessionCartItem.objects.filter(
                cart__session=current_session,
                added_by=target_member,
                status=SessionCartItem.Status.ACTIVE
            ).delete()

            broadcast_group_cart_update(
                current_session.session_token,
                event_type='member_removed',
                data={
                    'member_id': target_member.id,
                    'member_token': target_member.member_token,
                    'member_name': target_member.name,
                    'removed_by': current_member.name,
                    'active_count': current_session.active_members.count(),
                }
            )
            broadcast_group_cart_update(
                current_session.session_token,
                event_type='cart_updated',
                data={'member_name': target_member.name, 'action': 'removed'}
            )

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'status': 'success',
                    'message': f"{target_member.name} has been removed from {current_session.session_name}.",
                    'member_id': target_member.id,
                    'member_name': target_member.name,
                    'member_count': current_session.active_members.count(),
                    'guest_count': current_session.guest_count
                })

            messages.success(request, f"{target_member.name} has been removed from {current_session.session_name}.")
            return redirect('tables:customer_order', table_number=table.table_number)

        # Action C: DIRECT ORDER SUBMISSION (Form POST Fallback)
        elif request.POST.get('cart_data') or action == 'submit_order':
            if not current_session:
                try:
                    g_cnt = max(1, int(request.POST.get('guest_count', 1)))
                except (ValueError, TypeError):
                    g_cnt = 1
                c_name = request.POST.get('customer_name', '').strip() or f"Guest"
                c_phone = request.POST.get('customer_phone', '').strip()
                s_notes = request.POST.get('special_instructions', '').strip()

                current_session = CustomerSession.objects.create(
                    table=table,
                    session_name="Group A",
                    customer_name=c_name,
                    customer_phone=c_phone,
                    guest_count=g_cnt,
                    special_requests=s_notes,
                    status=CustomerSession.Status.ACTIVE
                )
                current_member = GroupMember.objects.create(
                    session=current_session,
                    name=c_name,
                    role=GroupMember.Role.LEADER,
                    can_submit_order=True,
                    is_active=True
                )
                request.session[f'table_{table.id}_session_token'] = current_session.session_token
                request.session[f'table_{table.id}_member_token'] = current_member.member_token

            has_prior_orders = current_session.orders.exclude(status=Order.Status.CANCELLED).exists()
            if not current_member.can_submit and not has_prior_orders:
                messages.error(request, f"Only the Group Leader ({current_session.leader.name if current_session.leader else 'Leader'}) can send the initial order to the kitchen.")
                return redirect('tables:customer_order', table_number=table.table_number)

            idempotency_key = request.POST.get('idempotency_key', '').strip() or None
            if idempotency_key:
                existing_order = Order.objects.filter(idempotency_key=idempotency_key).first()
                if existing_order:
                    messages.info(request, f"Order #{existing_order.order_number} already placed.")
                    return redirect('tables:customer_shop', table_number=table.table_number)

            cart, _ = SessionCart.objects.get_or_create(session=current_session)

            # Support legacy or direct cart_data JSON payload in form submission
            cart_data_raw = request.POST.get('cart_data')
            if cart_data_raw:
                try:
                    parsed_items = json.loads(cart_data_raw)
                    if isinstance(parsed_items, list):
                        for p_item in parsed_items:
                            is_combo = bool(p_item.get('is_combo') or p_item.get('combo_id'))
                            qty = max(1, int(p_item.get('quantity', 1)))
                            notes = p_item.get('notes') or p_item.get('special_notes', '')
                            if is_combo:
                                c_id = p_item.get('combo_id')
                                combo_obj = FoodCombo.objects.filter(id=c_id, is_available=True).first()
                                if combo_obj:
                                    SessionCartItem.objects.create(
                                        cart=cart,
                                        combo=combo_obj,
                                        combo_name=combo_obj.name,
                                        is_combo=True,
                                        quantity=qty,
                                        unit_price_snapshot=combo_obj.price,
                                        special_notes=notes,
                                        added_by=current_member,
                                        added_by_name=current_member.name if current_member else 'Guest',
                                        status=SessionCartItem.Status.ACTIVE
                                    )
                            else:
                                m_id = p_item.get('item_id') or p_item.get('menu_item_id')
                                m_obj = MenuItem.objects.filter(id=m_id, is_available=True).first()
                                if m_obj:
                                    SessionCartItem.objects.create(
                                        cart=cart,
                                        menu_item=m_obj,
                                        quantity=qty,
                                        unit_price_snapshot=m_obj.price,
                                        special_notes=notes,
                                        added_by=current_member,
                                        added_by_name=current_member.name if current_member else 'Guest',
                                        status=SessionCartItem.Status.ACTIVE
                                    )
                except Exception:
                    pass

            active_cart_items = list(cart.items.filter(status=SessionCartItem.Status.ACTIVE).select_related('menu_item', 'added_by', 'combo'))

            if not active_cart_items:
                messages.error(request, "Your cart is empty. Please add items to place your order.")
                return redirect('tables:customer_order', table_number=table.table_number)

            special_instructions = request.POST.get('special_instructions', '').strip()

            with transaction.atomic():
                locked_table = DiningTable.objects.select_for_update().get(id=table.id)
                locked_table.sync_seats()

                # Allocate seats if not already allocated for this session
                if not current_session.seat_allocations.filter(status='ACTIVE').exists():
                    avail_seats = list(locked_table.seats.select_for_update().filter(
                        is_active=True,
                        status=RestaurantSeat.Status.AVAILABLE
                    ).order_by('seat_number'))
                    req_seats = max(current_session.guest_count, current_session.active_members.count(), 1)
                    if len(avail_seats) == 0:
                        messages.error(request, f"Table {table.table_number} is currently full (0 seats available). Please wait for seats to free up or join the waiting list.")
                        return redirect('tables:customer_order', table_number=table.table_number)
                    elif len(avail_seats) < req_seats:
                        messages.error(request, f"Table {table.table_number} only has {len(avail_seats)} seat(s) available, but you requested {req_seats}.")
                        return redirect('tables:customer_order', table_number=table.table_number)

                    to_alloc = avail_seats[:req_seats]
                    for s in to_alloc:
                        s.status = RestaurantSeat.Status.OCCUPIED
                        s.save(update_fields=['status', 'updated_at'])
                        SessionSeatAllocation.objects.create(
                            session=current_session,
                            seat=s,
                            status=SessionSeatAllocation.AllocationStatus.ACTIVE
                        )
                        SeatAuditLog.objects.create(
                            session=current_session,
                            seat=s,
                            action='ALLOCATE',
                            reason='Order placed'
                        )
                    locked_table.sync_status()

                # Check if existing active order can be updated in-place (legacy form support)
                existing_pending_order = current_session.orders.exclude(
                    status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]
                ).order_by('created_at').first()

                if existing_pending_order and request.POST.get('cart_data'):
                    order = existing_pending_order
                    has_prior = True
                    order.has_unseen_extra = True
                    if special_instructions:
                        order.special_instructions = f"{order.special_instructions}\nExtra: {special_instructions}".strip()
                    order.save()

                    for ci in active_cart_items:
                        owner_name = ci.added_by_name or (ci.added_by.name if ci.added_by else 'Guest')
                        oi = None
                        if ci.is_combo and ci.combo:
                            first_dish = ci.combo.items.first()
                            fallback_menu_item = first_dish.menu_item if first_dish else MenuItem.objects.first()
                            oi = OrderItem.objects.create(
                                order=order,
                                menu_item=fallback_menu_item,
                                combo=ci.combo,
                                combo_name=ci.combo_name or ci.combo.name,
                                is_combo_header=True,
                                quantity=ci.quantity,
                                initial_quantity=0,
                                extra_quantity=ci.quantity,
                                is_extra=True,
                                unit_price=ci.unit_price,
                                special_notes=ci.special_notes,
                                added_by_member=ci.added_by,
                                added_by_name=owner_name,
                                status=OrderItem.ItemStatus.PENDING
                            )
                            for c_item in ci.combo.items.all():
                                OrderItem.objects.create(
                                    order=order,
                                    menu_item=c_item.menu_item,
                                    combo=ci.combo,
                                    combo_name=ci.combo_name or ci.combo.name,
                                    is_combo_component=True,
                                    quantity=c_item.quantity * ci.quantity,
                                    initial_quantity=0,
                                    extra_quantity=c_item.quantity * ci.quantity,
                                    is_extra=True,
                                    unit_price=Decimal('0.00'),
                                    special_notes=ci.special_notes,
                                    added_by_member=ci.added_by,
                                    added_by_name=owner_name,
                                    status=OrderItem.ItemStatus.PENDING
                                )
                        else:
                            existing_oi = order.items.filter(menu_item=ci.menu_item, status=OrderItem.ItemStatus.PENDING, is_combo_component=False, is_combo_header=False).first()
                            if existing_oi:
                                if ci.quantity > existing_oi.quantity:
                                    delta = ci.quantity - existing_oi.quantity
                                    existing_oi.extra_quantity += delta
                                    existing_oi.quantity = ci.quantity
                                    existing_oi.is_extra = True
                                    existing_oi.save()
                                    oi = existing_oi
                            else:
                                oi = OrderItem.objects.create(
                                    order=order,
                                    menu_item=ci.menu_item,
                                    quantity=ci.quantity,
                                    initial_quantity=0,
                                    extra_quantity=ci.quantity,
                                    is_extra=True,
                                    unit_price=ci.menu_item.price,
                                    special_notes=ci.special_notes,
                                    added_by_member=ci.added_by,
                                    added_by_name=owner_name,
                                    status=OrderItem.ItemStatus.PENDING
                                )

                        from orders.models import ExtraFoodOrderLog
                        item_title = ci.combo_name or ci.combo.name if (ci.is_combo and ci.combo) else ci.menu_item.name
                        unit_p = ci.unit_price if (ci.is_combo and ci.combo) else ci.menu_item.price
                        qty_added = (ci.quantity - (existing_oi.quantity - (ci.quantity - existing_oi.quantity))) if (not ci.is_combo and 'existing_oi' in locals() and existing_oi) else ci.quantity
                        if qty_added <= 0:
                            qty_added = ci.quantity
                        ExtraFoodOrderLog.objects.create(
                            order=order,
                            order_item=oi,
                            table=locked_table,
                            table_name=locked_table.table_number,
                            session_name=current_session.session_name if current_session else '',
                            item_name=item_title,
                            quantity=qty_added,
                            unit_price=unit_p,
                            total_price=unit_p * qty_added,
                            added_by_name=owner_name,
                            special_notes=ci.special_notes,
                            is_seen=False
                        )
                        ci.status = SessionCartItem.Status.SUBMITTED
                        ci.save(update_fields=['status', 'updated_at'])

                    order.calculate_totals(save=True)
                else:
                    has_prior = current_session.orders.exclude(status=Order.Status.CANCELLED).exists()
                    order = Order.objects.create(
                        order_type=Order.OrderType.DINE_IN,
                        table=locked_table,
                        session=current_session,
                        customer_name=current_session.customer_name,
                        customer_phone=current_session.customer_phone,
                        status=Order.Status.PENDING,
                        idempotency_key=idempotency_key,
                        special_instructions=special_instructions,
                        has_unseen_extra=has_prior
                    )

                    for ci in active_cart_items:
                        owner_name = ci.added_by_name or (ci.added_by.name if ci.added_by else 'Guest')
                        if ci.is_combo and ci.combo:
                            first_dish = ci.combo.items.first()
                            fallback_menu_item = first_dish.menu_item if first_dish else MenuItem.objects.first()
                            OrderItem.objects.create(
                                order=order,
                                menu_item=fallback_menu_item,
                                combo=ci.combo,
                                combo_name=ci.combo_name or ci.combo.name,
                                is_combo_header=True,
                                quantity=ci.quantity,
                                initial_quantity=0 if has_prior else ci.quantity,
                                extra_quantity=ci.quantity if has_prior else 0,
                                is_extra=has_prior,
                                unit_price=ci.unit_price,
                                special_notes=ci.special_notes,
                                added_by_member=ci.added_by,
                                added_by_name=owner_name,
                                status=OrderItem.ItemStatus.PENDING
                            )
                            for c_item in ci.combo.items.all():
                                OrderItem.objects.create(
                                    order=order,
                                    menu_item=c_item.menu_item,
                                    combo=ci.combo,
                                    combo_name=ci.combo_name or ci.combo.name,
                                    is_combo_component=True,
                                    quantity=c_item.quantity * ci.quantity,
                                    initial_quantity=0 if has_prior else (c_item.quantity * ci.quantity),
                                    extra_quantity=(c_item.quantity * ci.quantity) if has_prior else 0,
                                    is_extra=has_prior,
                                    unit_price=Decimal('0.00'),
                                    special_notes=ci.special_notes,
                                    added_by_member=ci.added_by,
                                    added_by_name=owner_name,
                                    status=OrderItem.ItemStatus.PENDING
                                )
                        else:
                            OrderItem.objects.create(
                                order=order,
                                menu_item=ci.menu_item,
                                quantity=ci.quantity,
                                initial_quantity=0 if has_prior else ci.quantity,
                                extra_quantity=ci.quantity if has_prior else 0,
                                is_extra=has_prior,
                                unit_price=ci.menu_item.price,
                                special_notes=ci.special_notes,
                                added_by_member=ci.added_by,
                                added_by_name=owner_name,
                                status=OrderItem.ItemStatus.PENDING
                            )
                        ci.status = SessionCartItem.Status.SUBMITTED
                        ci.save(update_fields=['status', 'updated_at'])

                    order.calculate_totals(save=True)

                invoice, _ = Invoice.objects.get_or_create(
                    session=current_session,
                    defaults={'table': locked_table, 'invoice_type': Invoice.InvoiceType.SESSION_BILL}
                )
                invoice.recalculate(save=True)

            broadcast_group_cart_update(
                current_session.session_token,
                event_type='order_submitted',
                data={'order_number': order.order_number}
            )

            messages.success(request, f"Group Order #{order.order_number} sent to the kitchen!")
            return redirect('tables:customer_shop', table_number=table.table_number)

    # 2. Check if customer needs to select or create a group
    want_switch = request.GET.get('switch') == '1' or request.GET.get('join') == '1'
    if current_session and current_session.status in [CustomerSession.Status.BILL_REQUESTED, CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED] and not want_switch:
        resp = redirect(f"/tables/{table.table_number}/thank-you/?bill_requested=1")
        return _clear_customer_table_session(request, resp, table.id)

    if not current_session or want_switch:
        # Auto-complete stale sessions older than 6 hours
        stale_cutoff = timezone.now() - datetime.timedelta(hours=6)
        table.sessions.filter(
            status=CustomerSession.Status.ACTIVE,
            created_at__lt=stale_cutoff
        ).update(status=CustomerSession.Status.COMPLETED)

        active_sessions = table.sessions.filter(
            status=CustomerSession.Status.ACTIVE
        ).prefetch_related('members').order_by('-created_at')

        target_restaurant = table.restaurant
        if target_restaurant:
            categories = Category.objects.filter(restaurant=target_restaurant, is_active=True).order_by('display_order', 'name')
            items = MenuItem.objects.filter(restaurant=target_restaurant, is_available=True, category__is_active=True).select_related('category')
        else:
            categories = Category.objects.filter(is_active=True).order_by('display_order', 'name')
            items = MenuItem.objects.filter(is_available=True, category__is_active=True).select_related('category')
        restaurant_logo_url = target_restaurant.logo_url if target_restaurant else (settings.STATIC_URL + 'images/default_restaurant_logo.svg')
        if not restaurant_logo_url:
            restaurant_logo_url = settings.STATIC_URL + 'images/default_restaurant_logo.svg'

        restaurant_name = target_restaurant.name if target_restaurant else (getattr(settings, 'RESTAURANT_NAME', 'The Grand Bistro'))

        context = {
            'table': table,
            'active_sessions': active_sessions,
            'active_sessions_count': active_sessions.count(),
            'available_seats_count': table.available_seats_count,
            'occupied_seats_count': table.occupied_seats_count,
            'seats': table.seats.filter(is_active=True).order_by('seat_number'),
            'categories': categories,
            'items': items,
            'restaurant_name': restaurant_name,
            'restaurant_logo_url': restaurant_logo_url,
        }
        if request.GET.get('removed') == '1':
            messages.warning(request, "You have been removed from the dining group by the group leader.")

        resp = render(request, 'tables/session_select.html', context)
        if not current_session:
            resp.delete_cookie(session_cookie_key)
            resp.delete_cookie(member_cookie_key)
        return resp

    # 3. GET Request: Customer has active group session -> Render Menu with Shared Cart
    selected_category = request.GET.get('category', '')
    target_restaurant = table.restaurant
    if target_restaurant:
        categories = Category.objects.filter(restaurant=target_restaurant, is_active=True).order_by('display_order', 'name')
        items = MenuItem.objects.filter(restaurant=target_restaurant, is_available=True, category__is_active=True).select_related('category').prefetch_related('dietary_tags')
        combos = FoodCombo.objects.filter(restaurant=target_restaurant, is_available=True).prefetch_related('items__menu_item')
    else:
        categories = Category.objects.filter(is_active=True).order_by('display_order', 'name')
        items = MenuItem.objects.filter(is_available=True, category__is_active=True).select_related('category').prefetch_related('dietary_tags')
        combos = FoodCombo.objects.filter(is_available=True).prefetch_related('items__menu_item')

    cart, _ = SessionCart.objects.get_or_create(session=current_session)
    active_cart_items = cart.items.filter(status=SessionCartItem.Status.ACTIVE).select_related('menu_item', 'added_by', 'combo').order_by('added_at')

    cart_items_list = []
    cart_subtotal = Decimal('0.00')
    for ci in active_cart_items:
        it_total = ci.total_price
        cart_subtotal += it_total
        cart_items_list.append({
            'id': ci.id,
            'menu_item_id': ci.menu_item_id,
            'is_combo': ci.is_combo,
            'combo_id': ci.combo_id,
            'name': (ci.combo_name or (ci.combo.name if ci.combo else 'Combo')) if ci.is_combo else (ci.menu_item.name if ci.menu_item else 'Item'),
            'image_url': (ci.combo.image_url if ci.combo else '') if ci.is_combo else (ci.menu_item.image_url if ci.menu_item else ''),
            'quantity': ci.quantity,
            'unit_price': str(ci.unit_price),
            'total_price': str(it_total),
            'special_notes': ci.special_notes,
            'added_by_id': ci.added_by_id,
            'added_by_name': ci.added_by_name or (ci.added_by.name if ci.added_by else 'Guest'),
            'is_my_item': (current_member and ci.added_by_id == current_member.id),
            'can_edit': (current_member and (ci.added_by_id == current_member.id or current_member.is_leader)),
        })

    tax_rate = Decimal('0.0825')
    service_rate = Decimal('0.05')
    cart_tax = round(cart_subtotal * tax_rate, 2)
    cart_service = round(cart_subtotal * service_rate, 2)
    cart_grand_total = cart_subtotal + cart_tax + cart_service

    session_orders = current_session.orders.exclude(status=Order.Status.CANCELLED).order_by('-created_at')
    active_order = session_orders.exclude(status=Order.Status.COMPLETED).first()
    has_prior_orders = session_orders.exists()

    page_title = f"Menu - Table {table.table_number} • {current_session.session_name} | {getattr(settings, 'RESTAURANT_NAME', 'Restaurant POS')}"

    context = {
        'table': table,
        'current_session': current_session,
        'current_member': current_member,
        'active_members': current_session.active_members,
        'member_count': current_session.active_members.count(),
        'is_leader': current_member.is_leader if current_member else False,
        'can_submit_order': (current_member.can_submit or has_prior_orders) if current_member else False,
        'categories': categories,
        'combos': combos,
        'items': items,
        'selected_category': selected_category,
        'session_orders': session_orders,
        'active_order': active_order,
        'has_prior_orders': has_prior_orders,
        'cart_items_json': json.dumps(cart_items_list),
        'cart_subtotal': cart_subtotal,
        'cart_grand_total': cart_grand_total,
        'cart_items_count': sum(it['quantity'] for it in cart_items_list),
        'session_invoice': current_session.invoices.first(),
        'page_title': page_title,
    }

    resp = render(request, 'tables/customer_order.html', context)
    resp.set_cookie(session_cookie_key, current_session.session_token, max_age=86400)
    if current_member:
        resp.set_cookie(member_cookie_key, current_member.member_token, max_age=86400)
    return resp


# ============================================================================
# REAL-TIME SHARED CART & GROUP MANAGEMENT JSON API ENDPOINTS
# ============================================================================

def api_group_cart_get(request, table_number):
    """Returns current active items in the shared cart and member roster."""
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group session found.'}, status=403)

    cart, _ = SessionCart.objects.get_or_create(session=current_session)
    active_items = cart.items.filter(status=SessionCartItem.Status.ACTIVE).select_related('menu_item', 'added_by', 'combo').order_by('added_at')

    items_data = []
    subtotal = Decimal('0.00')

    for item in active_items:
        it_total = item.total_price
        subtotal += it_total
        is_my_item = (item.added_by_id == current_member.id)
        can_edit = is_my_item or current_member.is_leader
        added_name = item.added_by_name or (item.added_by.name if item.added_by else 'Guest')

        items_data.append({
            'id': item.id,
            'menu_item_id': item.menu_item_id,
            'is_combo': item.is_combo,
            'combo_id': item.combo_id,
            'name': (item.combo_name or (item.combo.name if item.combo else 'Combo')) if item.is_combo else (item.menu_item.name if item.menu_item else 'Item'),
            'image_url': (item.combo.image_url if item.combo else '') if item.is_combo else (item.menu_item.image_url if item.menu_item else ''),
            'quantity': item.quantity,
            'unit_price': str(item.unit_price),
            'total_price': str(it_total),
            'special_notes': item.special_notes,
            'added_by_id': item.added_by_id,
            'added_by_name': added_name,
            'is_my_item': is_my_item,
            'can_edit': can_edit,
        })

    tax_rate = Decimal('0.0825')
    service_rate = Decimal('0.05')
    tax_amount = round(subtotal * tax_rate, 2)
    service_amount = round(subtotal * service_rate, 2)
    grand_total = subtotal + tax_amount + service_amount

    has_prior_orders = current_session.orders.exclude(status=Order.Status.CANCELLED).exists()
    can_submit_for_member = current_member.can_submit or has_prior_orders

    members_data = [
        {
            'id': m.id,
            'name': m.name,
            'role': m.role,
            'role_display': m.get_role_display(),
            'is_leader': m.is_leader,
            'is_me': (m.id == current_member.id),
            'can_submit': m.can_submit or has_prior_orders,
            'joined_at': m.joined_at.strftime('%I:%M %p'),
        }
        for m in current_session.active_members
    ]

    return JsonResponse({
        'status': 'success',
        'group_name': current_session.session_name,
        'join_code': current_session.join_code,
        'table_number': table.table_number,
        'has_prior_orders': has_prior_orders,
        'items': items_data,
        'total_items': sum(it['quantity'] for it in items_data),
        'subtotal': str(subtotal),
        'tax_amount': str(tax_amount),
        'service_charge_amount': str(service_amount),
        'grand_total': str(grand_total),
        'members': members_data,
        'member_count': len(members_data),
        'current_member': {
            'id': current_member.id,
            'name': current_member.name,
            'role': current_member.role,
            'is_leader': current_member.is_leader,
            'can_submit': can_submit_for_member,
        }
    })


def api_group_cart_add(request, table_number):
    """Adds or increments an item in the shared cart. Attributed to current_member."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group session found.'}, status=403)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    try:
        quantity = max(1, int(data.get('quantity', 1)))
    except (ValueError, TypeError):
        quantity = 1
    special_notes = (data.get('special_notes') or data.get('notes') or '').strip()

    is_combo = bool(data.get('is_combo') or data.get('combo_id'))
    combo_id = data.get('combo_id')
    menu_item_id = data.get('menu_item_id') or data.get('item_id')

    cart, _ = SessionCart.objects.get_or_create(session=current_session)

    if is_combo or combo_id:
        combo_qs = FoodCombo.objects.filter(id=combo_id, is_available=True)
        if table.restaurant:
            combo_qs = combo_qs.filter(Q(restaurant=table.restaurant) | Q(restaurant__isnull=True))
        combo = get_object_or_404(combo_qs)
        with transaction.atomic():
            cart_item = cart.items.select_for_update().filter(
                combo=combo,
                is_combo=True,
                added_by=current_member,
                special_notes=special_notes,
                status=SessionCartItem.Status.ACTIVE
            ).first()

            if cart_item:
                cart_item.quantity += quantity
                cart_item.unit_price_snapshot = combo.price
                cart_item.save(update_fields=['quantity', 'unit_price_snapshot', 'updated_at'])
            else:
                cart_item = cart.items.create(
                    combo=combo,
                    combo_name=combo.name,
                    is_combo=True,
                    added_by=current_member,
                    added_by_name=current_member.name,
                    quantity=quantity,
                    unit_price_snapshot=combo.price,
                    special_notes=special_notes,
                    status=SessionCartItem.Status.ACTIVE
                )

        broadcast_group_cart_update(
            current_session.session_token,
            event_type='cart_updated',
            data={'item_name': combo.name, 'added_by': current_member.name, 'action': 'add'}
        )

        return JsonResponse({
            'status': 'success',
            'message': f"Added {quantity}x {combo.name} combo to shared cart.",
            'item_id': cart_item.id
        })

    item_qs = MenuItem.objects.filter(id=menu_item_id, is_available=True)
    if table.restaurant:
        item_qs = item_qs.filter(Q(restaurant=table.restaurant) | Q(restaurant__isnull=True))
    menu_item = get_object_or_404(item_qs)

    with transaction.atomic():
        cart_item = cart.items.select_for_update().filter(
            menu_item=menu_item,
            is_combo=False,
            added_by=current_member,
            special_notes=special_notes,
            status=SessionCartItem.Status.ACTIVE
        ).first()

        if cart_item:
            cart_item.quantity += quantity
            cart_item.unit_price_snapshot = menu_item.price
            cart_item.save(update_fields=['quantity', 'unit_price_snapshot', 'updated_at'])
        else:
            cart_item = cart.items.create(
                menu_item=menu_item,
                is_combo=False,
                added_by=current_member,
                added_by_name=current_member.name,
                quantity=quantity,
                unit_price_snapshot=menu_item.price,
                special_notes=special_notes,
                status=SessionCartItem.Status.ACTIVE
            )

    broadcast_group_cart_update(
        current_session.session_token,
        event_type='cart_updated',
        data={'item_name': menu_item.name, 'added_by': current_member.name, 'action': 'add'}
    )

    return JsonResponse({
        'status': 'success',
        'message': f"Added {quantity}x {menu_item.name} to shared cart.",
        'item_id': cart_item.id
    })


def api_group_cart_reduce(request, table_number):
    """Decrements or removes an item (menu dish or food combo) in the shared cart. Attributed to current_member."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group session found.'}, status=403)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    combo_id = data.get('combo_id')
    menu_item_id = data.get('menu_item_id') or data.get('item_id')

    cart = get_object_or_404(SessionCart, session=current_session)

    with transaction.atomic():
        if combo_id:
            cart_item = cart.items.select_for_update().filter(
                combo_id=combo_id,
                is_combo=True,
                added_by=current_member,
                status=SessionCartItem.Status.ACTIVE
            ).last()
        else:
            cart_item = cart.items.select_for_update().filter(
                menu_item_id=menu_item_id,
                is_combo=False,
                added_by=current_member,
                status=SessionCartItem.Status.ACTIVE
            ).last()

        if not cart_item:
            return JsonResponse({'status': 'error', 'message': 'Item not found in your cart.'}, status=404)

        item_name = cart_item.combo_name if cart_item.is_combo else (cart_item.menu_item.name if cart_item.menu_item else 'Item')

        if cart_item.quantity > 1:
            cart_item.quantity -= 1
            cart_item.save(update_fields=['quantity', 'updated_at'])
            action_desc = 'reduced'
        else:
            cart_item.delete()
            action_desc = 'removed'

    broadcast_group_cart_update(
        current_session.session_token,
        event_type='cart_updated',
        data={'item_name': item_name, 'action': action_desc}
    )

    return JsonResponse({
        'status': 'success',
        'message': f"{item_name} {action_desc} in shared cart."
    })


def api_group_cart_update(request, table_number, item_id):
    """Updates quantity or notes of a shared cart item. Enforces ownership permissions."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group session found.'}, status=403)

    cart = get_object_or_404(SessionCart, session=current_session)
    cart_item = get_object_or_404(SessionCartItem, id=item_id, cart=cart, status=SessionCartItem.Status.ACTIVE)

    # Permission check: A regular member cannot edit another member's item
    if cart_item.added_by_id and cart_item.added_by_id != current_member.id and not current_member.is_leader:
        return JsonResponse({
            'status': 'error',
            'message': f"Permission denied: This item was added by {cart_item.added_by_name}. You can only edit your own dishes."
        }, status=403)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    try:
        new_qty = int(data.get('quantity', cart_item.quantity))
    except (ValueError, TypeError):
        new_qty = cart_item.quantity

    if new_qty <= 0:
        cart_item.delete()
        action_desc = 'deleted'
    else:
        cart_item.quantity = new_qty
        if 'special_notes' in data:
            cart_item.special_notes = data.get('special_notes', '').strip()
        cart_item.save(update_fields=['quantity', 'special_notes', 'updated_at'])
        action_desc = 'updated'

    broadcast_group_cart_update(
        current_session.session_token,
        event_type='cart_updated',
        data={'item_id': item_id, 'action': action_desc}
    )

    return JsonResponse({'status': 'success', 'message': f'Item {action_desc} successfully.'})


def api_group_cart_remove(request, table_number, item_id):
    """Removes an item from the shared cart. Enforces ownership permissions."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group session found.'}, status=403)

    cart = get_object_or_404(SessionCart, session=current_session)
    cart_item = get_object_or_404(SessionCartItem, id=item_id, cart=cart, status=SessionCartItem.Status.ACTIVE)

    # Permission check: A regular member cannot remove another member's item
    if cart_item.added_by_id and cart_item.added_by_id != current_member.id and not current_member.is_leader:
        return JsonResponse({
            'status': 'error',
            'message': f"Permission denied: This item was added by {cart_item.added_by_name}. You can only remove your own dishes."
        }, status=403)

    cart_item.delete()

    broadcast_group_cart_update(
        current_session.session_token,
        event_type='cart_updated',
        data={'item_id': item_id, 'action': 'removed'}
    )

    return JsonResponse({'status': 'success', 'message': 'Item removed from shared cart.'})


def api_group_submit_order(request, table_number):
    """Submits the shared cart to the kitchen. Exclusive to Group Leader / authorized member."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)

    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group session found.'}, status=403)

    has_prior_orders = current_session.orders.exclude(status=Order.Status.CANCELLED).exists()
    # Permission check: Leader can submit anytime. When adding extra food (has_prior_orders=True), members don't need leader permission - just add it!
    if not current_member.can_submit and not has_prior_orders:
        leader_name = current_session.leader.name if current_session.leader else "Leader"
        return JsonResponse({
            'status': 'error',
            'message': f"Only the Group Leader ({leader_name}) has permission to send the initial group order to the kitchen."
        }, status=403)

    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST

    idempotency_key = data.get('idempotency_key', '').strip() or None
    special_instructions = data.get('special_instructions', '').strip()

    if idempotency_key:
        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return JsonResponse({
                'status': 'success',
                'order_number': existing.order_number,
                'redirect_url': f"/tables/{table.table_number}/shop/",
                'message': f"Order #{existing.order_number} already submitted."
            })

    cart = get_object_or_404(SessionCart, session=current_session)
    active_cart_items = list(cart.items.filter(status=SessionCartItem.Status.ACTIVE).select_related('menu_item', 'added_by', 'combo'))

    if not active_cart_items:
        return JsonResponse({'status': 'error', 'message': 'Cannot send empty order. Please add dishes to cart.'}, status=400)

    with transaction.atomic():
        locked_table = DiningTable.objects.select_for_update().get(id=table.id)
        locked_table.sync_seats()

        # Seat allocation on order submission if not already allocated
        has_seats = current_session.seat_allocations.filter(status='ACTIVE').exists()
        if not has_seats:
            available_seats = list(locked_table.seats.select_for_update().filter(
                is_active=True,
                status=RestaurantSeat.Status.AVAILABLE
            ).order_by('seat_number'))

            alloc_count = max(current_session.guest_count, current_session.active_members.count(), 1)
            to_allocate = available_seats[:alloc_count]

            for seat in to_allocate:
                seat.status = RestaurantSeat.Status.OCCUPIED
                seat.save(update_fields=['status', 'updated_at'])

                SessionSeatAllocation.objects.create(
                    session=current_session,
                    seat=seat,
                    status=SessionSeatAllocation.AllocationStatus.ACTIVE
                )

                SeatAuditLog.objects.create(
                    seat=seat,
                    action="ALLOCATE",
                    session=current_session,
                    previous_status=RestaurantSeat.Status.AVAILABLE,
                    new_status=RestaurantSeat.Status.OCCUPIED,
                    reason=f"Group order submitted for {current_session.session_name} ({current_member.name})"
                )

            locked_table.sync_status()

        existing_order = current_session.orders.exclude(
            status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]
        ).order_by('created_at').first()

        if existing_order:
            order = existing_order
            has_prior = True
            order.has_unseen_extra = True
            if special_instructions:
                if order.special_instructions:
                    order.special_instructions = f"{order.special_instructions}\nExtra: {special_instructions}".strip()
                else:
                    order.special_instructions = special_instructions
            if order.status in [Order.Status.READY, Order.Status.SERVED]:
                order.status = Order.Status.PREPARING
            order.save()
        else:
            has_prior = False
            order = Order.objects.create(
                order_type=Order.OrderType.DINE_IN,
                table=locked_table,
                session=current_session,
                customer_name=current_session.customer_name,
                customer_phone=current_session.customer_phone,
                status=Order.Status.PENDING,
                idempotency_key=idempotency_key,
                special_instructions=special_instructions,
                has_unseen_extra=False
            )

        for ci in active_cart_items:
            owner_name = ci.added_by_name or (ci.added_by.name if ci.added_by else 'Guest')
            oi = None
            if ci.is_combo and ci.combo:
                first_dish = ci.combo.items.first()
                fallback_menu_item = first_dish.menu_item if first_dish else MenuItem.objects.first()
                oi = OrderItem.objects.create(
                    order=order,
                    menu_item=fallback_menu_item,
                    combo=ci.combo,
                    combo_name=ci.combo_name or ci.combo.name,
                    is_combo_header=True,
                    quantity=ci.quantity,
                    initial_quantity=0 if has_prior else ci.quantity,
                    extra_quantity=ci.quantity if has_prior else 0,
                    is_extra=has_prior,
                    unit_price=ci.unit_price,
                    special_notes=ci.special_notes,
                    added_by_member=ci.added_by,
                    added_by_name=owner_name,
                    status=OrderItem.ItemStatus.PENDING
                )
                for c_item in ci.combo.items.all():
                    OrderItem.objects.create(
                        order=order,
                        menu_item=c_item.menu_item,
                        combo=ci.combo,
                        combo_name=ci.combo_name or ci.combo.name,
                        is_combo_component=True,
                        quantity=c_item.quantity * ci.quantity,
                        initial_quantity=0 if has_prior else (c_item.quantity * ci.quantity),
                        extra_quantity=(c_item.quantity * ci.quantity) if has_prior else 0,
                        is_extra=has_prior,
                        unit_price=Decimal('0.00'),
                        special_notes=ci.special_notes,
                        added_by_member=ci.added_by,
                        added_by_name=owner_name,
                        status=OrderItem.ItemStatus.PENDING
                    )
            else:
                oi = OrderItem.objects.create(
                    order=order,
                    menu_item=ci.menu_item,
                    quantity=ci.quantity,
                    initial_quantity=0 if has_prior else ci.quantity,
                    extra_quantity=ci.quantity if has_prior else 0,
                    is_extra=has_prior,
                    unit_price=ci.menu_item.price,
                    special_notes=ci.special_notes,
                    added_by_member=ci.added_by,
                    added_by_name=owner_name,
                    status=OrderItem.ItemStatus.PENDING
                )

            if has_prior:
                from orders.models import ExtraFoodOrderLog
                item_title = ci.combo_name or ci.combo.name if (ci.is_combo and ci.combo) else ci.menu_item.name
                unit_p = ci.unit_price if (ci.is_combo and ci.combo) else ci.menu_item.price
                ExtraFoodOrderLog.objects.create(
                    order=order,
                    order_item=oi,
                    table=locked_table,
                    table_name=locked_table.table_number,
                    session_name=current_session.session_name,
                    item_name=item_title,
                    quantity=ci.quantity,
                    unit_price=unit_p,
                    total_price=unit_p * ci.quantity,
                    added_by_name=owner_name,
                    special_notes=ci.special_notes,
                    is_seen=False
                )

            ci.status = SessionCartItem.Status.SUBMITTED
            ci.save(update_fields=['status', 'updated_at'])

        order.calculate_totals(save=True)

        history_note = (
            f"Extra food added by {current_member.name} ({current_session.session_name}) - Order #{order.order_number}"
            if has_prior else
            f"Group order #{order.order_number} submitted by {current_member.name} ({current_session.session_name})"
        )
        OrderStatusHistory.objects.create(
            order=order,
            from_status=order.status,
            to_status=order.status,
            notes=history_note
        )

        invoice, _ = Invoice.objects.get_or_create(
            session=current_session,
            defaults={
                'table': locked_table,
                'invoice_type': Invoice.InvoiceType.SESSION_BILL,
                'customer_name': current_session.customer_name,
                'customer_phone': current_session.customer_phone,
            }
        )
        invoice.recalculate(save=True)

    broadcast_group_cart_update(
        current_session.session_token,
        event_type='order_submitted',
        data={'order_number': order.order_number, 'redirect_url': f"/tables/{table.table_number}/shop/"}
    )

    return JsonResponse({
        'status': 'success',
        'order_number': order.order_number,
        'redirect_url': f"/tables/{table.table_number}/shop/",
        'message': f"Order #{order.order_number} sent to kitchen!"
    })


def api_group_cart_sync(request, table_number):
    """Lightweight real-time polling fallback endpoint."""
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({
            'status': 'removed',
            'message': 'You are no longer in an active dining group.',
            'redirect_url': f"/tables/{table.table_number}/order/?switch=1&removed=1"
        }, status=200)

    if current_session.status in [CustomerSession.Status.BILL_REQUESTED, CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED]:
        return JsonResponse({
            'status': 'bill_requested',
            'bill_requested': True,
            'message': 'Welcome back! Your bill will arrive at your table within 5 mins. After collecting your bill, go and pay your amount to cashiers.',
            'redirect_url': f"/tables/{table.table_number}/thank-you/?bill_requested=1"
        }, status=200)

    cart, _ = SessionCart.objects.get_or_create(session=current_session)
    active_items = cart.items.filter(status=SessionCartItem.Status.ACTIVE).select_related('menu_item', 'added_by').order_by('added_at')

    items_data = []
    subtotal = Decimal('0.00')

    for item in active_items:
        subtotal += item.total_price
        items_data.append({
            'id': item.id,
            'menu_item_id': item.menu_item_id,
            'name': item.menu_item.name,
            'quantity': item.quantity,
            'unit_price': str(item.unit_price),
            'total_price': str(item.total_price),
            'special_notes': item.special_notes,
            'added_by_name': item.added_by_name or (item.added_by.name if item.added_by else 'Guest'),
            'is_my_item': (item.added_by_id == current_member.id),
            'can_edit': (item.added_by_id == current_member.id) or current_member.is_leader,
        })

    tax_rate = Decimal('0.0825')
    service_rate = Decimal('0.05')
    tax_amount = round(subtotal * tax_rate, 2)
    service_amount = round(subtotal * service_rate, 2)
    grand_total = subtotal + tax_amount + service_amount

    has_active_orders = current_session.orders.exclude(status=Order.Status.CANCELLED).exists()
    return JsonResponse({
        'status': 'success',
        'session_token': current_session.session_token,
        'group_name': current_session.session_name,
        'join_code': current_session.join_code,
        'items': items_data,
        'total_items': sum(it['quantity'] for it in items_data),
        'subtotal': str(subtotal),
        'grand_total': str(grand_total),
        'member_count': current_session.active_members.count(),
        'is_leader': current_member.is_leader,
        'can_submit': current_member.can_submit or has_active_orders,
        'has_active_orders': has_active_orders,
    })


def api_group_members(request, table_number):
    """Returns active group member roster."""
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active group.'}, status=403)

    has_active_orders = current_session.orders.exclude(status=Order.Status.CANCELLED).exists()
    members_data = [
        {
            'id': m.id,
            'name': m.name,
            'role': m.role,
            'role_display': m.get_role_display(),
            'is_leader': m.is_leader,
            'is_me': (m.id == current_member.id),
            'can_submit': m.can_submit or has_active_orders,
            'joined_at': m.joined_at.strftime('%I:%M %p'),
        }
        for m in current_session.active_members
    ]
    return JsonResponse({
        'status': 'success',
        'members': members_data,
        'member_count': len(members_data),
        'guest_count': current_session.guest_count,
        'join_code': current_session.join_code,
        'group_name': current_session.session_name,
        'is_leader': current_member.is_leader
    })


@require_POST
def api_group_member_remove(request, table_number, member_id):
    """Allows the group leader to remove a non-leader member from the active dining group."""
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({'status': 'error', 'message': 'No active session or member found.'}, status=403)

    if not current_member.is_leader:
        return JsonResponse({'status': 'error', 'message': 'Only the Group Leader can remove members.'}, status=403)

    target_member = current_session.members.filter(id=member_id, is_active=True).first()
    if not target_member:
        return JsonResponse({'status': 'error', 'message': 'Member not found or already inactive.'}, status=404)

    if target_member.id == current_member.id or target_member.is_leader:
        return JsonResponse({'status': 'error', 'message': 'The Group Leader cannot be removed.'}, status=400)

    target_member.is_active = False
    target_member.save(update_fields=['is_active'])

    # Remove unsubmitted cart items from shared cart
    SessionCartItem.objects.filter(
        cart__session=current_session,
        added_by=target_member,
        status=SessionCartItem.Status.ACTIVE
    ).delete()

    broadcast_group_cart_update(
        current_session.session_token,
        event_type='member_removed',
        data={
            'member_id': target_member.id,
            'member_token': target_member.member_token,
            'member_name': target_member.name,
            'removed_by': current_member.name,
            'active_count': current_session.active_members.count(),
        }
    )
    broadcast_group_cart_update(
        current_session.session_token,
        event_type='cart_updated',
        data={'member_name': target_member.name, 'action': 'removed'}
    )

    return JsonResponse({
        'status': 'success',
        'message': f"{target_member.name} has been removed from {current_session.session_name}.",
        'member_id': target_member.id,
        'member_name': target_member.name,
        'member_count': current_session.active_members.count(),
        'guest_count': current_session.guest_count
    })


def customer_leave_group(request, table_number):
    """Allows a member to leave their current group."""
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if current_member:
        current_member.is_active = False
        current_member.save(update_fields=['is_active'])

        if current_session:
            broadcast_group_cart_update(
                current_session.session_token,
                event_type='member_updated',
                data={'member_name': current_member.name, 'action': 'left'}
            )

    resp = redirect(f"/tables/{table.table_number}/order/?switch=1")
    resp.delete_cookie(f'rms_session_{table.id}')
    resp.delete_cookie(f'rms_member_{table.id}')
    if f'table_{table.id}_session_token' in request.session:
        del request.session[f'table_{table.id}_session_token']
    if f'table_{table.id}_member_token' in request.session:
        del request.session[f'table_{table.id}_member_token']
    return resp


def customer_leave_session(request, table_number):
    """Allows guest to exit their current session to start fresh or join another group."""
    table = get_dining_table_or_404(request, table_number, is_active=None)
    session_cookie_key = f'rms_session_{table.id}'
    member_cookie_key = f'rms_member_{table.id}'

    if f'table_{table.id}_session_token' in request.session:
        del request.session[f'table_{table.id}_session_token']
    if f'table_{table.id}_member_token' in request.session:
        del request.session[f'table_{table.id}_member_token']

    messages.info(request, "You have left the session. You can now start a new bill or join another.")
    resp = redirect(f"/tables/{table.table_number}/order/?switch=1")
    resp.delete_cookie(session_cookie_key)
    resp.delete_cookie(member_cookie_key)
    return resp



def customer_request_bill(request, table_number, session_token):
    """
    Step 1 & 2: Customer clicks Request for Bill on mobile ordering page.
    1. Identify active session, table, and customer group.
    2. Recalculate invoice / outstanding bill amount.
    3. Check for existing active bill request (prevent duplicate requests).
    4. Create a BillRequest record in the database.
    5. Show confirmation message to customer:
       'Your bill request has been sent successfully. Our staff will assist you shortly.'
    """
    table = get_dining_table_or_404(request, table_number, is_active=None)
    session = get_object_or_404(CustomerSession, table=table, session_token=session_token)

    if session.status in [CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED]:
        messages.info(request, "This customer session is already completed or closed.")
        return redirect('tables:customer_order', table_number=table.table_number)

    # Determine restaurant tenant from table, session, or server
    from accounts.models import Restaurant
    first_ord = session.orders.exclude(status=Order.Status.CANCELLED).first()
    restaurant = (
        getattr(table, 'restaurant', None)
        or getattr(session, 'restaurant', None)
        or (first_ord.server.restaurant if (first_ord and first_ord.server and first_ord.server.restaurant) else None)
        or Restaurant.objects.filter(status=Restaurant.RestaurantStatus.ACTIVE).first()
    )
    if restaurant:
        if getattr(table, 'restaurant', None) is None:
            table.restaurant = restaurant
            table.save(update_fields=['restaurant'])
        if getattr(session, 'restaurant', None) is None:
            session.restaurant = restaurant
            session.save(update_fields=['restaurant'])

    # Ensure invoice is up to date and linked to restaurant
    invoice, _ = Invoice.objects.get_or_create(
        session=session,
        defaults={
            'table': table,
            'restaurant': restaurant,
            'invoice_type': Invoice.InvoiceType.SESSION_BILL
        }
    )
    if not invoice.restaurant and restaurant:
        invoice.restaurant = restaurant
    invoice.recalculate(save=True)

    from billing.models import BillRequest

    # Step 2: Prevent duplicate bill requests
    active_req = BillRequest.objects.filter(
        session=session,
        status__in=[
            BillRequest.Status.PENDING,
            BillRequest.Status.ACCEPTED,
            BillRequest.Status.BILL_GENERATED,
            BillRequest.Status.PAYMENT_PENDING,
            BillRequest.Status.PARTIALLY_PAID,
        ]
    ).first()

    if active_req:
        session.status = CustomerSession.Status.BILL_REQUESTED
        session.save(update_fields=['status'])
        session.release_seats(reason="Customer requested bill")
        table.sync_status()
        if active_req.status == BillRequest.Status.ACCEPTED:
            staff_name = active_req.accepted_by.get_full_name() or active_req.accepted_by.username if active_req.accepted_by else "Staff"
            messages.info(request, f"Your bill request has already been accepted by {staff_name}. Staff will assist you shortly.")
        elif active_req.status == BillRequest.Status.PENDING:
            messages.info(request, f"Your bill request ({active_req.request_number}) has already been sent. Our staff will assist you shortly.")
        else:
            messages.info(request, f"Your bill request ({active_req.request_number}) status is {active_req.get_status_display()}.")
    else:
        # Create unique BillRequest
        bill_req = BillRequest.objects.create(
            restaurant=restaurant,
            table=table,
            session=session,
            invoice=invoice,
            total_amount=invoice.total_amount,
            paid_amount=invoice.total_paid,
            balance_due=invoice.balance_due,
            status=BillRequest.Status.PENDING,
        )
        session.status = CustomerSession.Status.BILL_REQUESTED
        session.save(update_fields=['status'])
        session.release_seats(reason="Customer requested bill")
        table.sync_status()

        messages.success(request, "Welcome back! Your bill will arrive at your table within 5 mins. After collecting your bill, go and pay your amount to cashiers.")

    redirect_dest = f'/tables/{table.table_number}/thank-you/?bill_requested=1'

    broadcast_group_cart_update(
        session.session_token,
        event_type='bill_requested',
        data={
            'redirect_url': redirect_dest,
            'message': 'Welcome back! Your bill will arrive at your table within 5 mins. After collecting your bill, go and pay your amount to cashiers.'
        }
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        resp = JsonResponse({
            'status': 'success',
            'message': 'Welcome back! Your bill will arrive at your table within 5 mins. After collecting your bill, go and pay your amount to cashiers.',
            'redirect_url': redirect_dest
        })
    else:
        resp = redirect(redirect_dest)

    return _clear_customer_table_session(request, resp, table.id)


def customer_pay_bill(request, table_number, session_token):
    """Customer pays their group bill or split share online (UPI / Card / Cash at counter)."""
    table = get_dining_table_or_404(request, table_number, is_active=None)
    session = get_object_or_404(CustomerSession, table=table, session_token=session_token)
    current_session, current_member = _get_active_session_and_member(request, table)

    invoice, _ = Invoice.objects.get_or_create(
        session=session,
        defaults={'table': table, 'invoice_type': Invoice.InvoiceType.SESSION_BILL}
    )
    invoice.recalculate(save=True)

    member_name = current_member.name if current_member else (request.GET.get('member_name') or session.customer_name or 'Guest')
    my_items = []
    my_subtotal = Decimal('0.00')
    session_orders = session.orders.exclude(status=Order.Status.CANCELLED).prefetch_related('items__menu_item')
    for ord_obj in session_orders:
        for it in ord_obj.items.exclude(status=OrderItem.ItemStatus.CANCELLED):
            if (current_member and it.added_by_member_id == current_member.id) or it.added_by_name == member_name:
                my_items.append(it)
                my_subtotal += it.total_price

    ratio = (my_subtotal / session.subtotal) if session.subtotal > Decimal('0.00') else Decimal('0.00')
    my_tax = round(invoice.tax_amount * ratio, 2)
    my_service = round(invoice.service_charge_amount * ratio, 2)
    my_share_total = my_subtotal + my_tax + my_service
    my_paid = invoice.payments.filter(
        status=Payment.Status.COMPLETED
    ).filter(
        Q(allocated_member=current_member) | Q(paid_by_name=member_name)
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    my_balance_due = max(Decimal('0.00'), my_share_total - my_paid)

    active_member_count = max(1, session.active_members.count() if session.active_members.exists() else session.guest_count)
    equal_share = round(invoice.total_amount / Decimal(active_member_count), 2)
    equal_balance_due = max(Decimal('0.00'), equal_share - my_paid)

    if request.method == 'POST':
        payment_method = request.POST.get('payment_method', Payment.PaymentMethod.UPI)
        split_type = request.POST.get('split_type', 'FULL')
        try:
            amount = Decimal(request.POST.get('amount', str(invoice.balance_due)))
        except (ValueError, TypeError):
            amount = invoice.balance_due

        amount = max(Decimal('1.00'), min(amount, invoice.balance_due))
        tx_ref = request.POST.get('tx_ref', f"ONLINE-{uuid_str()[:8].upper()}")

        Payment.objects.create(
            invoice=invoice,
            payment_method=payment_method,
            amount=amount,
            tendered_amount=amount,
            change_returned=Decimal('0.00'),
            transaction_reference=tx_ref,
            status=Payment.Status.COMPLETED,
            paid_by_name=member_name,
            split_type=split_type,
            allocated_member=current_member
        )

        invoice.recalculate(save=True)

        broadcast_group_cart_update(
            session.session_token,
            event_type='payment_received',
            data={
                'paid_by': member_name,
                'amount': str(amount),
                'split_type': split_type,
                'balance_due': str(invoice.balance_due),
                'is_paid': (invoice.status == Invoice.Status.PAID)
            }
        )

        # Payment does NOT release the table automatically (per requirement 18)
        if invoice.status == Invoice.Status.PAID:
            messages.success(request, f"Payment of ₹{amount} received from {member_name}! Group bill is paid in full.")
        else:
            messages.success(request, f"Payment of ₹{amount} received from {member_name} ({split_type.replace('_', ' ').title()}). Remaining balance due: ₹{invoice.balance_due}")

        return redirect('tables:customer_shop', table_number=table.table_number)

    return render(request, 'tables/customer_payment.html', {
        'table': table,
        'session': session,
        'invoice': invoice,
        'current_member': current_member,
        'member_name': member_name,
        'my_items': my_items,
        'my_subtotal': my_subtotal,
        'my_share_total': my_share_total,
        'my_paid': my_paid,
        'my_balance_due': my_balance_due,
        'active_member_count': active_member_count,
        'equal_share': equal_share,
        'equal_balance_due': equal_balance_due,
    })


def customer_shop(request, table_number):
    """
    Dedicated Customer Shop Page for mobile QR ordering.
    Displays submitted items attributed to group members, kitchen status, bill, and split options.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)
    session_cookie_key = f'rms_session_{table.id}'
    member_cookie_key = f'rms_member_{table.id}'

    if not current_session or not current_member:
        messages.warning(request, "You have been removed from the dining group by the group leader.")
        token_param = f"&token={table.qr_token}" if getattr(table, 'qr_token', None) else ""
        resp = redirect(f"/tables/{table.table_number}/order/?switch=1&removed=1{token_param}")
        return _clear_customer_table_session(request, resp, table.id)

    if current_session.status in [CustomerSession.Status.BILL_REQUESTED, CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED]:
        resp = redirect(f"/tables/{table.table_number}/thank-you/?bill_requested=1")
        return _clear_customer_table_session(request, resp, table.id)

    # 1. Fetch orders strictly belonging to this customer session
    session_orders = current_session.orders.select_related('accepted_by', 'table').prefetch_related('items__menu_item', 'items__added_by_member').order_by('created_at')

    initial_order = session_orders.first()
    additional_orders = list(session_orders)[1:] if session_orders.count() > 1 else []

    # 2. Consolidated active items summary
    consolidated_items = []
    item_map = {}
    for ord_obj in session_orders:
        for it in ord_obj.items.all():
            if it.status != OrderItem.ItemStatus.CANCELLED:
                key = (it.menu_item_id, it.added_by_name or '')
                if key not in item_map:
                    item_map[key] = {
                        'menu_item': it.menu_item,
                        'total_quantity': 0,
                        'unit_price': it.unit_price,
                        'total_price': Decimal('0.00'),
                        'order_numbers': set(),
                        'statuses': [],
                        'added_by_name': it.added_by_name or 'Group Member',
                    }
                item_map[key]['total_quantity'] += it.quantity
                item_map[key]['total_price'] += it.total_price
                item_map[key]['order_numbers'].add(ord_obj.order_number)
                item_map[key]['statuses'].append(it.get_status_display())

    for key, data in item_map.items():
        data['orders_ref'] = ", ".join(sorted(data['order_numbers']))
        data['status_summary'] = ", ".join(sorted(set(data['statuses'])))
        consolidated_items.append(data)

    # 3. Overall tracking progress
    active_order = session_orders.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).last()
    if not active_order and session_orders.exists():
        active_order = session_orders.last()

    overall_step = 1
    chef_name = None
    is_ready = False
    serving_countdown = 0

    if active_order:
        if active_order.status in [Order.Status.SERVED, Order.Status.COMPLETED]:
            overall_step = 5
        elif active_order.status == Order.Status.READY:
            overall_step = 4
            is_ready = True
            serving_countdown = active_order.serving_countdown_remaining_seconds
        elif active_order.status in [Order.Status.PREPARING, 'IN_KITCHEN'] or active_order.cooking_started_at is not None:
            overall_step = 3
        elif active_order.status == Order.Status.ACCEPTED or active_order.accepted_by is not None:
            overall_step = 2
            chef_name = active_order.accepted_by_name

    # 4. Invoice & Billing
    invoice, _ = Invoice.objects.get_or_create(
        session=current_session,
        defaults={
            'table': table,
            'invoice_type': Invoice.InvoiceType.SESSION_BILL,
            'customer_name': current_session.customer_name,
            'customer_phone': current_session.customer_phone,
        }
    )
    invoice.recalculate(save=True)

    # Calculate personal share for this member
    member_name = current_member.name if current_member else (current_session.customer_name or 'Guest')
    my_subtotal = Decimal('0.00')
    for ord_obj in session_orders:
        for it in ord_obj.items.exclude(status=OrderItem.ItemStatus.CANCELLED):
            if (current_member and it.added_by_member_id == current_member.id) or it.added_by_name == member_name:
                my_subtotal += it.total_price

    ratio = (my_subtotal / current_session.subtotal) if current_session.subtotal > Decimal('0.00') else Decimal('0.00')
    my_tax = round(invoice.tax_amount * ratio, 2)
    my_service = round(invoice.service_charge_amount * ratio, 2)
    my_share_total = my_subtotal + my_tax + my_service
    my_paid = invoice.payments.filter(
        status=Payment.Status.COMPLETED
    ).filter(
        Q(allocated_member=current_member) | Q(paid_by_name=member_name)
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    my_balance_due = max(Decimal('0.00'), my_share_total - my_paid)

    active_member_count = max(1, current_session.active_members.count() if current_session.active_members.exists() else current_session.guest_count)
    equal_share_total = round(invoice.total_amount / Decimal(active_member_count), 2)
    equal_balance_due = max(Decimal('0.00'), equal_share_total - my_paid)

    restaurant_name = getattr(settings, 'RESTAURANT_NAME', 'Restaurant POS')
    page_title = f"Shop • Table {table.table_number} ({current_session.session_name}) | {restaurant_name}"

    context = {
        'table': table,
        'current_session': current_session,
        'current_member': current_member,
        'active_members': current_session.active_members,
        'member_count': active_member_count,
        'session_orders': session_orders,
        'initial_order': initial_order,
        'additional_orders': additional_orders,
        'consolidated_items': consolidated_items,
        'active_order': active_order,
        'overall_step': overall_step,
        'chef_name': chef_name,
        'is_ready': is_ready,
        'serving_countdown': serving_countdown,
        'invoice': invoice,
        'my_subtotal': my_subtotal,
        'my_share_total': my_share_total,
        'my_paid': my_paid,
        'my_balance_due': my_balance_due,
        'equal_share_total': equal_share_total,
        'equal_balance_due': equal_balance_due,
        'bill_request': BillRequest.objects.filter(session=current_session).order_by('-requested_at').first(),
        'page_title': page_title,
        'RESTAURANT_NAME': restaurant_name,
    }

    resp = render(request, 'tables/customer_shop.html', context)
    resp.set_cookie(session_cookie_key, current_session.session_token, max_age=86400)
    return resp


def api_customer_shop_live(request, table_number):
    """
    Real-time polling endpoint for the customer Shop page.
    Returns JSON updates on order preparation status, countdowns, and items.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    current_session, current_member = _get_active_session_and_member(request, table)

    if not current_session or not current_member:
        return JsonResponse({
            'success': False,
            'removed': True,
            'redirect_url': f"/tables/{table.table_number}/order/?switch=1&removed=1"
        }, status=200)

    if current_session.status in [CustomerSession.Status.BILL_REQUESTED, CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED]:
        return JsonResponse({
            'success': True,
            'bill_requested': True,
            'message': 'Welcome back! Your bill will arrive at your table within 5 mins. After collecting your bill, go and pay your amount to cashiers.',
            'redirect_url': f"/tables/{table.table_number}/thank-you/?bill_requested=1"
        }, status=200)

    session_orders = current_session.orders.select_related('accepted_by').prefetch_related('items__menu_item').order_by('created_at')

    active_order = session_orders.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).last()
    if not active_order and session_orders.exists():
        active_order = session_orders.last()

    overall_step = 1
    chef_name = None
    is_ready = False
    serving_countdown = 0
    status_message = "Your order has been sent to the kitchen."

    if active_order:
        if active_order.status in [Order.Status.SERVED, Order.Status.COMPLETED]:
            overall_step = 5
            status_message = "Your food has been served. Enjoy your meal!"
        elif active_order.status == Order.Status.READY:
            overall_step = 4
            is_ready = True
            serving_countdown = active_order.serving_countdown_remaining_seconds
            status_message = "Your food is ready! We will serve it within 5 minutes."
        elif active_order.status in [Order.Status.PREPARING, 'IN_KITCHEN'] or active_order.cooking_started_at is not None:
            overall_step = 3
            status_message = "Your food is currently being cooked in the kitchen."
        elif active_order.status == Order.Status.ACCEPTED or active_order.accepted_by is not None:
            overall_step = 2
            chef_name = active_order.accepted_by_name
            status_message = f"Accepted by kitchen staff ({chef_name or 'Kitchen Staff'})."

    orders_data = []
    total_active_items = 0

    for ord_obj in session_orders:
        items_data = []
        for it in ord_obj.items.select_related('menu_item').all():
            if it.status != OrderItem.ItemStatus.CANCELLED:
                total_active_items += it.quantity
            items_data.append({
                'id': it.id,
                'name': (it.combo_name or 'Food Combo') if it.is_combo_header else it.menu_item.name,
                'is_combo_header': it.is_combo_header,
                'is_combo_component': it.is_combo_component,
                'quantity': it.quantity,
                'unit_price': str(it.unit_price),
                'total_price': str(it.total_price),
                'status': it.status,
                'status_display': it.get_status_display(),
                'is_cancelled': it.status == OrderItem.ItemStatus.CANCELLED,
                'can_cancel': it.status in [OrderItem.ItemStatus.PENDING, OrderItem.ItemStatus.COOKING],
                'special_notes': it.special_notes,
            })
        orders_data.append({
            'id': ord_obj.id,
            'order_number': ord_obj.order_number,
            'status': ord_obj.status,
            'status_display': ord_obj.get_status_display(),
            'total_amount': str(ord_obj.total_amount),
            'items_count': ord_obj.total_items,
            'created_at': ord_obj.created_at.strftime('%I:%M %p'),
            'can_cancel': ord_obj.status not in [Order.Status.COMPLETED, Order.Status.CANCELLED],
            'items': items_data,
        })

    invoice = current_session.invoices.first()
    subtotal = current_session.subtotal
    total = invoice.total_amount if invoice else current_session.total_amount
    balance_due = invoice.balance_due if invoice else current_session.balance_due

    from billing.models import BillRequest
    active_bill_req = BillRequest.objects.filter(session=current_session).order_by('-requested_at').first()
    bill_req_data = None
    if active_bill_req:
        bill_req_data = {
            'id': active_bill_req.id,
            'request_number': active_bill_req.request_number,
            'status': active_bill_req.status,
            'status_display': active_bill_req.get_status_display(),
            'accepted_by': active_bill_req.accepted_by.get_full_name() or active_bill_req.accepted_by.username if active_bill_req.accepted_by else None,
            'total_amount': str(active_bill_req.total_amount),
            'badge_class': active_bill_req.badge_class,
        }

    return JsonResponse({
        'success': True,
        'overall_step': overall_step,
        'status_message': status_message,
        'chef_name': chef_name,
        'is_ready': is_ready,
        'serving_countdown': serving_countdown,
        'total_active_items': total_active_items,
        'orders_count': session_orders.count(),
        'subtotal': str(subtotal),
        'total_amount': str(total),
        'balance_due': str(balance_due),
        'bill_request': bill_req_data,
        'orders': orders_data,
    })


def _get_cancel_redirect(request, table, order):
    """Helper to redirect customer back to Shop or Status page based on origin."""
    if request.POST.get('from') == 'shop' or 'shop' in request.META.get('HTTP_REFERER', ''):
        return redirect('tables:customer_shop', table_number=table.table_number)
    return redirect('tables:customer_order_status', table_number=table.table_number, order_id=order.id)


def uuid_str():
    import uuid
    return uuid.uuid4().hex


def customer_order_status(request, table_number, order_id):
    """Live order tracking page for customer watching their meal progress."""
    table = get_dining_table_or_404(request, table_number, is_active=None)
    order = get_object_or_404(Order, id=order_id, table=table)
    session = order.session

    session_cookie_key = f'rms_session_{table.id}'
    client_token = (
        request.GET.get('session') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )
    if session and not client_token:
        request.session[f'table_{table.id}_session_token'] = session.session_token

    invoice = session.invoices.first() if session else order.invoices.first()
    all_items = order.items.select_related('menu_item').all()
    active_items = [it for it in all_items if it.status != OrderItem.ItemStatus.CANCELLED]
    cancelled_items = [it for it in all_items if it.status == OrderItem.ItemStatus.CANCELLED]

    can_cancel_order = order.status not in [Order.Status.COMPLETED, Order.Status.CANCELLED] and len(active_items) > 0

    can_close_session = False
    if session:
        if session.is_paid or not session.has_active_orders or (order.status == Order.Status.CANCELLED and not session.has_active_orders):
            can_close_session = True

    context = {
        'table': table,
        'order': order,
        'session': session,
        'invoice': invoice,
        'items': all_items,
        'active_items': active_items,
        'cancelled_items': cancelled_items,
        'can_cancel_order': can_cancel_order,
        'can_close_session': can_close_session,
    }
    resp = render(request, 'tables/customer_order_status.html', context)
    if session and not request.COOKIES.get(session_cookie_key):
        resp.set_cookie(session_cookie_key, session.session_token, max_age=86400)
    return resp


def api_customer_order_live(request, table_number, order_id):
    """
    Real-time lightweight JSON endpoint polled by customer mobile tracking screen.
    Returns the live 5-step status progression, kitchen staff details, and 5-minute countdown.
    """
    table = get_dining_table_or_404(request, table_number, is_active=None)
    order = get_object_or_404(Order.objects.select_related('accepted_by', 'table', 'session'), id=order_id, table=table)

    session_cookie_key = f'rms_session_{table.id}'
    client_token = (
        request.GET.get('session') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )
    if order.session and client_token and order.session.session_token != client_token:
        return JsonResponse({'error': 'Unauthorized session access'}, status=403)

    if order.session and order.session.status in [CustomerSession.Status.BILL_REQUESTED, CustomerSession.Status.COMPLETED, CustomerSession.Status.CANCELLED]:
        return JsonResponse({
            'order_id': order.id,
            'status': order.status,
            'bill_requested': True,
            'redirect_url': f"/tables/{table.table_number}/thank-you/?bill_requested=1"
        })

    # 5-step progress sequence:
    # 1: Order Placed
    # 2: Accepted by Kitchen
    # 3: Start Cooking (Preparing)
    # 4: Food Ready
    # 5: Served
    if order.status == Order.Status.CANCELLED:
        step_stage = -1
    elif order.status in [Order.Status.SERVED, Order.Status.COMPLETED]:
        step_stage = 5
    elif order.status == Order.Status.READY:
        step_stage = 4
    elif order.status in [Order.Status.PREPARING, 'IN_KITCHEN'] or order.cooking_started_at is not None:
        step_stage = 3
    elif order.status == Order.Status.ACCEPTED or order.accepted_by is not None:
        step_stage = 2
    else:
        step_stage = 1

    status_message = "Your order has been placed and sent to the kitchen."
    if step_stage == 3:
        status_message = "Your food is being prepared in the kitchen."
    elif step_stage == 4:
        status_message = "Your order is ready! Our staff will serve your food within 5 minutes."
    elif step_stage == 5:
        status_message = "Your food has been served. Enjoy your meal!"
    elif step_stage == 2:
        status_message = f"Accepted by kitchen staff ({order.accepted_by_name or 'Kitchen Staff'})."

    items_data = []
    for it in order.items.select_related('menu_item').all():
        items_data.append({
            'id': it.id,
            'name': it.menu_item.name,
            'quantity': it.quantity,
            'status': it.status,
            'status_display': it.get_status_display(),
            'is_cancelled': it.is_cancelled,
        })

    stage_title = "Start Cooking" if step_stage == 3 else order.get_status_display()
    return JsonResponse({
        'order_id': order.id,
        'order_number': order.order_number,
        'status': order.status,
        'status_display': stage_title,
        'stage_title': stage_title,
        'step_stage': step_stage,
        'stage': step_stage,
        'status_message': status_message,
        'is_accepted': order.is_accepted,
        'accepted_by_name': order.accepted_by_name,
        'chef_name': order.accepted_by_name,
        'accepted_at': order.accepted_at.strftime('%I:%M %p') if order.accepted_at else None,
        'cooking_started_at': order.cooking_started_at.strftime('%I:%M %p') if order.cooking_started_at else None,
        'ready_at': order.ready_at.strftime('%I:%M %p') if order.ready_at else None,
        'served_at': order.served_at.strftime('%I:%M %p') if order.served_at else None,
        'is_ready': order.status == Order.Status.READY,
        'is_serving_delayed': order.is_serving_delayed,
        'serving_countdown_seconds': order.serving_countdown_remaining_seconds,
        'countdown_seconds': order.serving_countdown_remaining_seconds,
        'items': items_data,
    })


def customer_cancel_item(request, table_number, order_id, item_id):
    """
    Cancel an individual food item from the customer mobile interface.
    - If preparation has not started (PENDING): cancel immediately & remove charge from bill.
    - If preparation started (COOKING): request staff confirmation.
    - Preserves audit trail & logs in OrderCancellationLog.
    - Does NOT release table seats.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    order = get_object_or_404(Order, id=order_id, table=table)

    session_cookie_key = f'rms_session_{table.id}'
    client_token = (
        request.POST.get('session_token') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )
    if order.session and client_token and order.session.session_token != client_token:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': 'Unauthorized session access.'}, status=403)
        messages.error(request, "Unauthorized session access.")
        return redirect('tables:customer_order_status', table_number=table.table_number, order_id=order.id)

    item = get_object_or_404(OrderItem, id=item_id, order=order)
    reason = request.POST.get('reason', 'Cancelled by customer').strip() or 'Cancelled by customer'

    if item.status == OrderItem.ItemStatus.CANCELLED:
        msg = f"{item.menu_item.name} is already cancelled."
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': msg})
        messages.warning(request, msg)
        return redirect('tables:customer_order_status', table_number=table.table_number, order_id=order.id)

    with transaction.atomic():
        if item.status == OrderItem.ItemStatus.PENDING:
            item.status = OrderItem.ItemStatus.CANCELLED
            item.cancelled_at = timezone.now()
            item.cancellation_reason = reason
            item.cancelled_by = f"Customer ({order.session.session_name if order.session else 'Guest'})"
            item.save()

            order.calculate_totals(save=True)
            order.has_unseen_cancellation = True

            if not order.items.exclude(status=OrderItem.ItemStatus.CANCELLED).exists():
                order.status = Order.Status.CANCELLED
                order.cancelled_at = timezone.now()
                order.cancellation_reason = f"All items cancelled: {reason}"
                order.cancellation_requested_by = 'Customer'
                order.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'cancellation_requested_by', 'has_unseen_cancellation'])
            else:
                order.save(update_fields=['has_unseen_cancellation'])

            # Automatically update the bill
            if order.session:
                for inv in order.session.invoices.all():
                    inv.recalculate(save=True)
            elif order.invoices.exists():
                for inv in order.invoices.all():
                    inv.recalculate(save=True)

            OrderCancellationLog.objects.create(
                order=order,
                order_item=item,
                cancellation_type=OrderCancellationLog.CancellationType.ITEM,
                item_name=item.menu_item.name,
                quantity=item.quantity,
                amount_deducted=item.original_total_price,
                reason=reason,
                requested_by=f"Customer ({order.session.session_name if order.session else 'Guest'})",
                status=OrderCancellationLog.LogStatus.CANCELLED
            )

            OrderStatusHistory.objects.create(
                order=order,
                from_status=order.status,
                to_status=order.status,
                notes=f"Customer cancelled {item.quantity}x {item.menu_item.name}. Reason: {reason}"
            )

            success_msg = f"{item.quantity}x {item.menu_item.name} has been cancelled and removed from your bill."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': success_msg,
                    'item_id': item.id,
                    'item_status': item.status,
                    'order_subtotal': str(order.subtotal),
                    'order_total': str(order.total_amount),
                    'session_total': str(order.session.total_amount) if order.session else str(order.total_amount),
                    'order_status': order.status
                })
            messages.success(request, success_msg)

        elif item.status == OrderItem.ItemStatus.COOKING:
            item.status = OrderItem.ItemStatus.CANCELLATION_REQUESTED
            item.cancellation_reason = reason
            item.cancelled_by = f"Customer ({order.session.session_name if order.session else 'Guest'})"
            item.save(update_fields=['status', 'cancellation_reason', 'cancelled_by'])

            order.has_unseen_cancellation = True
            order.save(update_fields=['has_unseen_cancellation'])

            OrderCancellationLog.objects.create(
                order=order,
                order_item=item,
                cancellation_type=OrderCancellationLog.CancellationType.ITEM,
                item_name=item.menu_item.name,
                quantity=item.quantity,
                amount_deducted=item.original_total_price,
                reason=reason,
                requested_by=f"Customer ({order.session.session_name if order.session else 'Guest'})",
                status=OrderCancellationLog.LogStatus.REQUESTED
            )

            req_msg = f"Preparation has already started for {item.menu_item.name}. A cancellation request has been sent to kitchen staff for confirmation."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'requested': True,
                    'message': req_msg,
                    'item_id': item.id,
                    'item_status': item.status
                })
            messages.info(request, req_msg)

        else:
            err_msg = f"{item.menu_item.name} is {item.get_status_display()} and cannot be cancelled online. Please speak to your server."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': err_msg})
            messages.error(request, err_msg)

    return _get_cancel_redirect(request, table, order)


def customer_cancel_order(request, table_number, order_id):
    """
    Cancel entire order for this customer group.
    - Cancels all eligible pending items immediately.
    - If items are cooking, marks cancellation requested for kitchen confirmation.
    - Does NOT cancel other groups' orders or bills at the same table.
    - Does NOT release seats automatically; customer must choose Leave Table when ready.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    order = get_object_or_404(Order, id=order_id, table=table)

    session_cookie_key = f'rms_session_{table.id}'
    client_token = (
        request.POST.get('session_token') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )
    if order.session and client_token and order.session.session_token != client_token:
        messages.error(request, "Unauthorized session access.")
        return redirect('tables:customer_order_status', table_number=table.table_number, order_id=order.id)

    if order.status in [Order.Status.COMPLETED, Order.Status.CANCELLED]:
        messages.warning(request, f"Order #{order.order_number} is already {order.get_status_display()}.")
        return redirect('tables:customer_order_status', table_number=table.table_number, order_id=order.id)

    reason = request.POST.get('reason', 'Cancelled entire order by customer').strip() or 'Cancelled entire order by customer'

    with transaction.atomic():
        has_cooking = False
        cancelled_count = 0
        total_deducted = Decimal('0.00')

        for item in order.items.exclude(status=OrderItem.ItemStatus.CANCELLED):
            if item.status == OrderItem.ItemStatus.PENDING:
                item.status = OrderItem.ItemStatus.CANCELLED
                item.cancelled_at = timezone.now()
                item.cancellation_reason = reason
                item.cancelled_by = f"Customer ({order.session.session_name if order.session else 'Guest'})"
                item.save()
                total_deducted += item.original_total_price
                cancelled_count += 1
            elif item.status == OrderItem.ItemStatus.COOKING:
                item.status = OrderItem.ItemStatus.CANCELLATION_REQUESTED
                item.cancellation_reason = reason
                item.cancelled_by = f"Customer ({order.session.session_name if order.session else 'Guest'})"
                item.save(update_fields=['status', 'cancellation_reason', 'cancelled_by'])
                has_cooking = True

        if not has_cooking:
            order.status = Order.Status.CANCELLED
            order.cancelled_at = timezone.now()
            order.cancellation_reason = reason
            order.cancellation_requested_by = f"Customer ({order.session.session_name if order.session else 'Guest'})"
            order.has_unseen_cancellation = True
            order.calculate_totals(save=False)
            order.save()

            if order.session:
                for inv in order.session.invoices.all():
                    inv.recalculate(save=True)
            elif order.invoices.exists():
                for inv in order.invoices.all():
                    inv.recalculate(save=True)

            OrderCancellationLog.objects.create(
                order=order,
                cancellation_type=OrderCancellationLog.CancellationType.ORDER,
                quantity=cancelled_count,
                amount_deducted=total_deducted,
                reason=reason,
                requested_by=f"Customer ({order.session.session_name if order.session else 'Guest'})",
                status=OrderCancellationLog.LogStatus.CANCELLED
            )

            OrderStatusHistory.objects.create(
                order=order,
                from_status=order.status,
                to_status=Order.Status.CANCELLED,
                notes=f"Customer cancelled entire order. Reason: {reason}"
            )

            messages.success(request, f"Order #{order.order_number} has been cancelled. Your bill has been updated.")
        else:
            order.cancellation_reason = reason
            order.has_unseen_cancellation = True
            order.save(update_fields=['cancellation_reason', 'has_unseen_cancellation'])

            OrderCancellationLog.objects.create(
                order=order,
                cancellation_type=OrderCancellationLog.CancellationType.ORDER,
                quantity=cancelled_count,
                amount_deducted=total_deducted,
                reason=reason,
                requested_by=f"Customer ({order.session.session_name if order.session else 'Guest'})",
                status=OrderCancellationLog.LogStatus.REQUESTED
            )

            messages.info(request, f"Cancellation requested for Order #{order.order_number}. Items currently cooking require kitchen staff confirmation.")

    return _get_cancel_redirect(request, table, order)


def customer_close_session(request, table_number):
    """
    Leave Table & Close Session option:
    - Atomically releases the customer group's occupied seats.
    - Leaves other groups' seats and sessions at the table intact.
    - Clears customer browser session cookie.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session_cookie_key = f'rms_session_{table.id}'
    session_token = (
        request.POST.get('session_token') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )

    if not session_token:
        messages.info(request, "No active table session found.")
        return redirect('tables:customer_order', table_number=table.table_number)

    session = CustomerSession.objects.filter(
        table=table,
        session_token=session_token,
        status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]
    ).first()

    if not session:
        messages.info(request, "Your table session is already closed.")
        return redirect('tables:customer_thank_you', table_number=table.table_number)

    # Check if there is unpaid balance on active orders
    if session.balance_due > Decimal('0.00') and session.has_active_orders:
        messages.warning(request, f"Please settle your remaining bill of ₹{session.balance_due:.2f} before closing your session, or request your server for assistance.")
        return redirect('tables:customer_pay_bill', table_number=table.table_number, session_token=session.session_token)

    with transaction.atomic():
        seats = session.allocated_seats()
        seat_names = ", ".join(f"Seat {s.seat_number}" for s in seats) or "Seats"
        session.close_session(reason=f"Customer departure from Table {table.table_number}")

        if f'table_{table.id}_session_token' in request.session:
            del request.session[f'table_{table.id}_session_token']

    resp = redirect('tables:customer_thank_you', table_number=table.table_number)
    resp.delete_cookie(session_cookie_key)
    messages.success(request, f"Your session has ended and {seat_names} at Table {table.table_number} have been released. Thank you!")
    return resp


def customer_thank_you(request, table_number):
    """Customer departure confirmation page."""
    table = get_dining_table_or_404(request, table_number, is_active=None)
    is_bill_requested = (request.GET.get('bill_requested') == '1')

    target_restaurant = table.restaurant or Restaurant.objects.first()
    restaurant_logo_url = target_restaurant.logo_url if target_restaurant else (settings.STATIC_URL + 'images/default_restaurant_logo.svg')
    if not restaurant_logo_url:
        restaurant_logo_url = settings.STATIC_URL + 'images/default_restaurant_logo.svg'

    restaurant_name = target_restaurant.name if target_restaurant else (getattr(settings, 'RESTAURANT_NAME', 'The Grand Bistro'))

    active_bill_req = None
    if is_bill_requested:
        from billing.models import BillRequest
        active_bill_req = BillRequest.objects.filter(table=table).order_by('-requested_at').first()

    context = {
        'table': table,
        'bill_requested': is_bill_requested,
        'active_bill_request': active_bill_req,
        'restaurant_name': restaurant_name,
        'restaurant_logo_url': restaurant_logo_url,
    }
    resp = render(request, 'tables/customer_thank_you.html', context)
    return _clear_customer_table_session(request, resp, table.id)


# ============================================================================
# ADMIN QR CODE VIEW & PRINT MANAGEMENT
# ============================================================================

@login_required
def table_qr_card(request, table_id):
    table = get_tenant_object_or_404(DiningTable, request, id=table_id)
    config = RestaurantConfig.get_config()
    persistent_domain = (config.qr_domain or '').strip()
    raw_host = request.get_host()
    lan_ip = get_lan_ip()

    # Handle POST actions (Saving permanent domain or regenerating QR)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_permanent_domain':
            new_domain = request.POST.get('qr_domain', '').strip()
            config.qr_domain = new_domain
            config.save(update_fields=['qr_domain'])

            # Automatically re-generate and save server QR images for all active tables
            for t in DiningTable.objects.filter(is_active=True):
                t.generate_and_save_qr()

            if new_domain:
                messages.success(
                    request,
                    f"Permanent QR Domain successfully saved: '{new_domain}'. All table QR stands now encode this URL permanently!"
                )
            else:
                messages.info(
                    request,
                    f"Permanent QR domain cleared. Tables now automatically use local Wi-Fi IP ({lan_ip}:8000)."
                )
            return redirect('tables:table_qr_card', table_id=table.id)

        elif action == 'regenerate_qr':
            table.regenerate_qr_token()
            messages.success(request, f"Table {table.table_number} QR code regenerated with a new secure token.")
            return redirect('tables:table_qr_card', table_id=table.id)

    # Check for temporary preview host via GET param ?host=...
    custom_preview_host = request.GET.get('host', '').strip()

    if custom_preview_host:
        active_host = custom_preview_host
    elif persistent_domain:
        active_host = persistent_domain
    elif raw_host.startswith('127.0.0.1') or raw_host.startswith('localhost'):
        port = raw_host.split(':')[-1] if ':' in raw_host else str(request.get_port())
        active_host = f"{lan_ip}:{port}" if port and port not in ('80', '443') else f"{lan_ip}:8000"
    else:
        active_host = raw_host

    order_url = build_table_qr_url(table, host=active_host)
    qr_base64 = table.get_qr_base64(host=active_host)

    is_persistent = bool(persistent_domain)
    is_loopback = raw_host.startswith('127.0.0.1') or raw_host.startswith('localhost')

    context = {
        'table': table,
        'qr_base64': qr_base64,
        'order_url': order_url,
        'active_host': active_host,
        'persistent_domain': persistent_domain,
        'is_persistent': is_persistent,
        'lan_ip': lan_ip,
        'raw_host': raw_host,
        'is_loopback': is_loopback,
    }
    return render(request, 'tables/table_qr_card.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def table_qr_print_all(request):
    tables = filter_by_tenant(DiningTable.objects.filter(is_active=True), request).order_by('table_number')
    config = RestaurantConfig.get_config()
    persistent_domain = (config.qr_domain or '').strip()
    raw_host = request.get_host()
    lan_ip = get_lan_ip()
    custom_host = request.GET.get('host', '').strip()

    if custom_host:
        active_host = custom_host
    elif persistent_domain:
        active_host = persistent_domain
    elif raw_host.startswith('127.0.0.1') or raw_host.startswith('localhost'):
        port = raw_host.split(':')[-1] if ':' in raw_host else str(request.get_port())
        active_host = f"{lan_ip}:{port}" if port and port not in ('80', '443') else f"{lan_ip}:8000"
    else:
        active_host = raw_host

    table_data = []
    for t in tables:
        order_url = build_table_qr_url(t, host=active_host)
        table_data.append({
            'table': t,
            'qr_base64': t.get_qr_base64(host=active_host),
            'order_url': order_url,
        })

    return render(request, 'tables/table_qr_print_all.html', {
        'tables_data': table_data,
        'active_host': active_host,
        'persistent_domain': persistent_domain,
        'is_persistent': bool(persistent_domain),
    })
