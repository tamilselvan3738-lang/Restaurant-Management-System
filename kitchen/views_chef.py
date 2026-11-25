import json
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import chef_required
from accounts.forms import UserProfileForm
from accounts.models import Restaurant, User
from kitchen.models import ItemAvailabilityReport, KitchenNotification, KitchenStaffProfile, KitchenStation
from menu.models import MenuItem
from orders.models import Order, OrderCancellationLog, OrderItem, OrderStatusHistory


def get_user_restaurant(user):
    """Resolve the associated restaurant for the given user."""
    if hasattr(user, 'restaurant') and user.restaurant:
        return user.restaurant
    profile = getattr(user, 'kitchen_profile', None)
    if profile and profile.restaurant:
        return profile.restaurant
    return Restaurant.objects.first()


def get_chef_profile(user):
    """Retrieve or automatically create default KitchenStaffProfile for the user."""
    profile, _ = KitchenStaffProfile.objects.get_or_create(
        user=user,
        defaults={
            'restaurant': get_user_restaurant(user),
            'designation': KitchenStaffProfile.Designation.CHEF if user.role == User.Role.CHEF else KitchenStaffProfile.Designation.KITCHEN,
            'can_view_all_orders': True,
            'can_accept_orders': True,
            'can_update_status': True,
            'can_mark_ready': True,
            'can_cancel_items': True,
            'can_request_order_cancel': True,
            'can_report_unavailable': True,
            'can_view_kitchen_reports': True,
        }
    )
    return profile


def get_chef_orders_queryset(request):
    """Return orders queryset scoped to the chef's restaurant and assigned station."""
    restaurant = get_user_restaurant(request.user)
    profile = get_chef_profile(request.user)

    qs = Order.objects.all().select_related('table', 'session', 'server').prefetch_related('items__menu_item')

    # Multi-tenant scoping if restaurant is defined on tables or servers
    if restaurant:
        # If orders are tied to tables with restaurant, or servers with restaurant
        qs = qs.filter(Q(server__restaurant=restaurant) | Q(server__isnull=True))

    # Station scoping if chef is restricted to a specific kitchen station
    if not profile.can_view_all_orders and profile.assigned_station:
        qs = qs.filter(
            Q(items__kitchen_station=profile.assigned_station) |
            Q(items__menu_item__category__name__icontains=profile.assigned_station.name)
        ).distinct()

    return qs


# ==========================================
# 1. CHEF DASHBOARD OVERVIEW
# ==========================================
@chef_required
def chef_dashboard(request):
    profile = get_chef_profile(request.user)
    restaurant = get_user_restaurant(request.user)
    today = timezone.now().date()

    orders_qs = get_chef_orders_queryset(request)

    # Summary Cards (Live Counts from database)
    total_orders_today = orders_qs.filter(created_at__date=today).count()
    my_orders_count = orders_qs.filter(accepted_by=request.user).exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).count()
    new_orders_count = orders_qs.filter(status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED'], accepted_by__isnull=True).count()
    pending_orders_count = orders_qs.filter(status=Order.Status.PENDING).count()
    preparing_orders_count = orders_qs.filter(status=Order.Status.PREPARING).count()
    ready_orders_count = orders_qs.filter(status=Order.Status.READY).count()
    cancelled_today_count = orders_qs.filter(status=Order.Status.CANCELLED, created_at__date=today).count()

    # Delayed orders check
    preparing_orders_list = list(orders_qs.filter(status=Order.Status.PREPARING).order_by('created_at'))
    delayed_orders_count = sum(1 for o in preparing_orders_list if o.is_delayed)

    # Overview sections
    recent_incoming = orders_qs.filter(
        status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED']
    ).order_by('-created_at')[:8]

    orders_in_preparation = preparing_orders_list[:8]

    orders_ready = orders_qs.filter(
        status=Order.Status.READY
    ).order_by('-updated_at')[:8]

    recent_cancelled = orders_qs.filter(
        status=Order.Status.CANCELLED,
        created_at__date=today
    ).order_by('-updated_at')[:6]

    unread_notifications_count = KitchenNotification.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True),
        is_read=False
    ).count()

    context = {
        'profile': profile,
        'restaurant': restaurant,
        'today': today,
        # Summary metrics
        'total_orders_today': total_orders_today,
        'my_orders_count': my_orders_count,
        'new_orders_count': new_orders_count,
        'pending_orders_count': pending_orders_count,
        'preparing_orders_count': preparing_orders_count,
        'ready_orders_count': ready_orders_count,
        'cancelled_today_count': cancelled_today_count,
        'delayed_orders_count': delayed_orders_count,
        # Sections
        'recent_incoming': recent_incoming,
        'orders_in_preparation': orders_in_preparation,
        'orders_ready': orders_ready,
        'recent_cancelled': recent_cancelled,
        'unread_notifications_count': unread_notifications_count,
    }
    return render(request, 'chef/dashboard.html', context)


# ==========================================
# 2. ORDER MANAGEMENT & STATUS LISTS
# ==========================================
@chef_required
def chef_order_list(request, status_filter=None):
    profile = get_chef_profile(request.user)
    restaurant = get_user_restaurant(request.user)
    today = timezone.now().date()

    # Base queryset
    qs = get_chef_orders_queryset(request)

    # Determine filter from URL param or GET query
    filter_val = (status_filter or request.GET.get('status', 'active')).lower()
    search_query = request.GET.get('q', '').strip()
    date_filter = request.GET.get('date', '').strip()

    # Search filter (order ID, table, customer)
    if search_query:
        qs = qs.filter(
            Q(order_number__icontains=search_query) |
            Q(table__table_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(items__menu_item__name__icontains=search_query)
        ).distinct()

    # Date filter
    if date_filter:
        try:
            qs = qs.filter(created_at__date=date_filter)
        except Exception:
            pass

    # Status filter
    if filter_val == 'my_orders':
        qs = qs.filter(accepted_by=request.user).exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED])
    elif filter_val == 'active':
        qs = qs.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED])
    elif filter_val == 'completed':
        qs = qs.filter(status__in=[Order.Status.COMPLETED, Order.Status.SERVED])
    elif filter_val == 'cancelled':
        qs = qs.filter(status=Order.Status.CANCELLED)
    elif filter_val == 'new':
        qs = qs.filter(status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED'])
    elif filter_val == 'pending':
        qs = qs.filter(status=Order.Status.PENDING)
    elif filter_val == 'preparing':
        qs = qs.filter(status=Order.Status.PREPARING)
    elif filter_val == 'ready':
        qs = qs.filter(status=Order.Status.READY)
    elif filter_val == 'today':
        qs = qs.filter(created_at__date=today)
    elif filter_val == 'delayed':
        # Filter preparing orders that are delayed or serving delayed
        delayed_ids = [o.id for o in qs if o.is_delayed or o.is_serving_delayed]
        qs = qs.filter(id__in=delayed_ids)

    # Ordering: pending/active first, newest first
    qs = qs.order_by('-created_at')

    # Tab counts for quick badge counters
    base_qs = get_chef_orders_queryset(request)
    if date_filter:
        base_qs = base_qs.filter(created_at__date=date_filter)

    counts = {
        'all': base_qs.count(),
        'my_orders': base_qs.filter(accepted_by=request.user).exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).count(),
        'active': base_qs.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).count(),
        'new': base_qs.filter(status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED']).count(),
        'preparing': base_qs.filter(status=Order.Status.PREPARING).count(),
        'ready': base_qs.filter(status=Order.Status.READY).count(),
        'completed': base_qs.filter(status__in=[Order.Status.COMPLETED, Order.Status.SERVED]).count(),
        'cancelled': base_qs.filter(status=Order.Status.CANCELLED).count(),
        'today': base_qs.filter(created_at__date=today).count(),
        'delayed': sum(1 for o in base_qs if o.is_delayed or o.is_serving_delayed),
    }

    context = {
        'profile': profile,
        'restaurant': restaurant,
        'orders': qs,
        'status_filter': filter_val,
        'current_filter': filter_val,
        'counts': counts,
        'search_query': search_query,
        'date_filter': date_filter,
    }
    return render(request, 'chef/order_list.html', context)


# ==========================================
# 3. ORDER DETAILS & ITEM PROGRESSION
# ==========================================
@chef_required
def chef_order_detail(request, order_id):
    profile = get_chef_profile(request.user)
    restaurant = get_user_restaurant(request.user)

    order = get_object_or_404(
        Order.objects.select_related('table', 'session', 'server').prefetch_related('items__menu_item'),
        id=order_id
    )

    items = order.items.all().order_by('created_at')
    status_history = order.status_history.all().select_related('changed_by')[:15]
    cancellation_logs = order.cancellation_logs.all().select_related('reviewed_by')

    # Mark unseen extras or cancellations as seen by kitchen
    if order.has_unseen_extra:
        order.has_unseen_extra = False
        order.save(update_fields=['has_unseen_extra'])

    context = {
        'profile': profile,
        'restaurant': restaurant,
        'order': order,
        'items': items,
        'status_history': status_history,
        'cancellation_logs': cancellation_logs,
        # Perm flags
        'can_accept_orders': profile.can_accept_orders,
        'can_update_status': profile.can_update_status,
        'can_mark_ready': profile.can_mark_ready,
        'can_cancel_items': profile.can_cancel_items,
        'can_request_order_cancel': profile.can_request_order_cancel,
    }
    return render(request, 'chef/order_detail.html', context)


# ==========================================
# 4. ORDER STATUS ACTIONS (ACCEPT, UPDATE)
# ==========================================
@chef_required
@require_POST
def chef_accept_order(request, order_id):
    profile = get_chef_profile(request.user)
    if not profile.can_accept_orders and not request.user.is_superuser:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Permission denied: Cannot accept orders.'}, status=403)
        messages.error(request, "You do not have permission to accept orders.")
        return redirect('chef:order_detail', order_id=order_id)

    with transaction.atomic():
        try:
            # Row-level locking to guarantee atomic single-staff acceptance without outer join lock failure
            order = Order.objects.select_for_update().get(id=order_id)
        except Order.DoesNotExist:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'Order not found.'}, status=404)
            messages.error(request, "Order not found.")
            return redirect('chef:orders')

        # 1. Multi-tenant security check
        chef_restaurant = get_user_restaurant(request.user)
        if chef_restaurant:
            if order.server and order.server.restaurant and order.server.restaurant != chef_restaurant:
                err = "Permission denied: This order belongs to another restaurant."
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': err}, status=403)
                messages.error(request, err)
                return redirect('chef:orders')

        # 2. Check if order was already cancelled
        if order.status == Order.Status.CANCELLED:
            err = "Cancelled orders cannot be accepted or prepared."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('chef:order_detail', order_id=order_id)

        # 3. Check if already accepted (Exclusive acceptance rule)
        if order.accepted_by is not None:
            if order.accepted_by == request.user:
                msg = "You have already accepted this order."
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': msg,
                        'status': order.status,
                        'status_display': order.get_status_display(),
                        'accepted_by': order.accepted_by_name
                    })
                messages.info(request, msg)
                return redirect('chef:order_detail', order_id=order_id)
            else:
                other_staff = order.accepted_by.get_full_name() or order.accepted_by.username
                err = f"This order has already been accepted by another kitchen staff member ({other_staff})."
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': False,
                        'error': err,
                        'accepted_by': other_staff
                    }, status=409)
                messages.warning(request, err)
                return redirect('chef:order_detail', order_id=order_id)

        # 4. Atomically accept order & advance to Start Cooking (PREPARING)
        now = timezone.now()
        prev_status = order.status
        order.accepted_by = request.user
        order.accepted_at = now
        order.cooking_started_at = now
        order.status = Order.Status.PREPARING
        order.save(update_fields=['accepted_by', 'accepted_at', 'cooking_started_at', 'status', 'updated_at'])

        # Cascade all pending items to COOKING
        order.items.filter(status=OrderItem.ItemStatus.PENDING).update(
            status=OrderItem.ItemStatus.COOKING,
            cooking_started_at=now,
            assigned_cook=request.user
        )

        # Record audit status history
        staff_name = request.user.get_full_name() or request.user.username
        staff_id = order.accepted_by_employee_id or 'N/A'
        OrderStatusHistory.objects.create(
            order=order,
            from_status=prev_status,
            to_status=Order.Status.PREPARING,
            changed_by=request.user,
            notes=f"Order accepted by {staff_name} (ID: {staff_id}, Designation: {order.accepted_by_designation}). Cooking started."
        )

        # Notify Admin, Manager, and Cashier
        KitchenNotification.objects.create(
            restaurant=chef_restaurant,
            notification_type=KitchenNotification.NotificationType.GENERAL,
            title=f"Order #{order.order_number} Accepted",
            message=f"Order #{order.order_number} (Table {order.table.table_number if order.table else 'Takeaway'}) accepted by {staff_name} ({order.accepted_by_designation}). Cooking in progress.",
            related_order=order,
        )

        success_msg = "You have successfully accepted this order."
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': success_msg,
                'status': order.status,
                'status_display': order.get_status_display(),
                'accepted_by': staff_name,
                'accepted_at': order.accepted_at.strftime('%I:%M %p')
            })
        messages.success(request, success_msg)
        return redirect('chef:order_detail', order_id=order_id)


@chef_required
@require_POST
def chef_update_order_status(request, order_id):
    profile = get_chef_profile(request.user)
    if not profile.can_update_status and not request.user.is_superuser:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Permission denied: Cannot update order status.'}, status=403)
        messages.error(request, "Permission denied to update order status.")
        return redirect('chef:order_detail', order_id=order_id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id)
        new_status = request.POST.get('status')
        now = timezone.now()

        # Authorization: Only assigned kitchen employee or Admin/Manager can update status
        if order.accepted_by and order.accepted_by != request.user and not request.user.is_admin_or_manager and not request.user.is_superuser:
            err = f"Permission denied: Only the assigned kitchen staff member ({order.accepted_by_name}) or an administrator can update this order."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': err}, status=403)
            messages.error(request, err)
            return redirect('chef:order_detail', order_id=order_id)

        # Cannot modify cancelled orders
        if order.status == Order.Status.CANCELLED:
            err = "Cancelled orders cannot be modified or marked ready."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('chef:order_detail', order_id=order_id)

        # Valid status flow: PENDING -> ACCEPTED / PREPARING -> READY -> SERVED -> COMPLETED
        allowed_transitions = {
            Order.Status.PENDING: [Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.CANCELLED],
            'PLACED': [Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.CANCELLED],
            Order.Status.ACCEPTED: [Order.Status.PREPARING, Order.Status.CANCELLED],
            Order.Status.PREPARING: [Order.Status.READY, Order.Status.CANCELLED],
            Order.Status.READY: [Order.Status.SERVED, Order.Status.COMPLETED],
            Order.Status.SERVED: [Order.Status.COMPLETED],
        }

        if new_status not in allowed_transitions.get(order.status, []):
            err = f"Invalid status transition from {order.get_status_display()} to {new_status}."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('chef:order_detail', order_id=order_id)

        old_status = order.status
        order.status = new_status

        # If transitioning to PREPARING:
        if new_status == Order.Status.PREPARING:
            if not order.cooking_started_at:
                order.cooking_started_at = now
            if not order.accepted_by:
                order.accepted_by = request.user
                order.accepted_at = now
            order.items.filter(status=OrderItem.ItemStatus.PENDING).update(
                status=OrderItem.ItemStatus.COOKING,
                cooking_started_at=now,
                assigned_cook=request.user
            )

        # If transitioning to READY (COOK COMPLETE – MARK AS READY):
        elif new_status == Order.Status.READY:
            order.ready_at = now
            order.items.exclude(status__in=[OrderItem.ItemStatus.CANCELLED, OrderItem.ItemStatus.SERVED]).update(
                status=OrderItem.ItemStatus.READY,
                ready_at=now
            )
            # Create high-priority alert for Admin, Cashier, and service staff
            staff_name = order.accepted_by_name or request.user.username
            KitchenNotification.objects.create(
                restaurant=get_user_restaurant(request.user),
                notification_type=KitchenNotification.NotificationType.PRIORITY_ORDER,
                title=f"Order #{order.order_number} Ready for Serving!",
                message=f"Order #{order.order_number} (Table {order.table.table_number if order.table else 'Takeaway'}) prepared by {staff_name} is COOK COMPLETE. Ready for pickup and serving within 5 minutes.",
                related_order=order,
            )

        # If transitioning to SERVED:
        elif new_status == Order.Status.SERVED:
            order.served_at = now
            order.items.exclude(status=OrderItem.ItemStatus.CANCELLED).update(
                status=OrderItem.ItemStatus.SERVED
            )

        order.save()

        # Log status history
        actor_name = request.user.get_full_name() or request.user.username
        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=new_status,
            changed_by=request.user,
            notes=request.POST.get('notes', f"Order status updated to {new_status} by {actor_name}.")
        )

    if new_status == Order.Status.READY:
        msg = f"Order #{order.order_number} marked as COOK COMPLETE – READY. Customer and service staff notified (expected serving time: 5 minutes)."
    elif new_status == Order.Status.SERVED:
        msg = f"Order #{order.order_number} marked as SERVED."
    else:
        msg = f"Order #{order.order_number} status updated to {order.get_status_display()}."

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': msg,
            'status': order.status,
            'status_display': order.get_status_display(),
            'ready_at': order.ready_at.strftime('%I:%M %p') if order.ready_at else None,
            'serving_countdown': order.serving_countdown_remaining_seconds
        })
    messages.success(request, msg)
    return redirect('chef:order_detail', order_id=order_id)


# ==========================================
# 5. INDIVIDUAL ITEM STATUS PROGRESSION
# ==========================================
@chef_required
@require_POST
def chef_update_item_status(request, item_id):
    profile = get_chef_profile(request.user)
    item = get_object_or_404(OrderItem.objects.select_related('order', 'menu_item'), id=item_id)
    new_status = request.POST.get('status')
    now = timezone.now()

    if new_status == OrderItem.ItemStatus.COOKING:
        if not profile.can_update_status and not request.user.is_superuser:
            return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
        item.status = OrderItem.ItemStatus.COOKING
        item.cooking_started_at = now
        item.assigned_cook = request.user
        item.save(update_fields=['status', 'cooking_started_at', 'assigned_cook'])

        # If overall order is still pending/accepted, promote to preparing
        if item.order.status in [Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED']:
            item.order.status = Order.Status.PREPARING
            item.order.save(update_fields=['status', 'updated_at'])
            OrderStatusHistory.objects.create(
                order=item.order,
                from_status=Order.Status.ACCEPTED,
                to_status=Order.Status.PREPARING,
                changed_by=request.user,
                notes=f"Preparation started for {item.menu_item.name}."
            )

    elif new_status == OrderItem.ItemStatus.READY:
        if not profile.can_mark_ready and not request.user.is_superuser:
            return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
        item.status = OrderItem.ItemStatus.READY
        item.ready_at = now
        item.save(update_fields=['status', 'ready_at'])

        # Notify that item is ready
        KitchenNotification.objects.create(
            restaurant=get_user_restaurant(request.user),
            notification_type=KitchenNotification.NotificationType.GENERAL,
            title=f"Item Ready: {item.menu_item.name}",
            message=f"{item.quantity}x {item.menu_item.name} for {item.order.order_number} is ready at Table {item.order.table.table_number if item.order.table else '-'}.",
            related_order=item.order,
        )

        # Check if ALL items in order are now ready
        active_items = item.order.items.exclude(status=OrderItem.ItemStatus.CANCELLED)
        if active_items.exists() and all(it.status in [OrderItem.ItemStatus.READY, OrderItem.ItemStatus.SERVED] for it in active_items):
            item.order.status = Order.Status.READY
            item.order.save(update_fields=['status', 'updated_at'])
            OrderStatusHistory.objects.create(
                order=item.order,
                from_status=Order.Status.PREPARING,
                to_status=Order.Status.READY,
                changed_by=request.user,
                notes="All individual order items marked as ready."
            )

    msg = f"{item.menu_item.name} updated to {item.get_status_display()}."
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': msg,
            'item_id': item.id,
            'item_status': item.status,
            'item_status_display': item.get_status_display(),
            'order_status': item.order.status,
            'order_status_display': item.order.get_status_display(),
        })
    messages.success(request, msg)
    return redirect('chef:order_detail', order_id=item.order.id)


# ==========================================
# 6. INDIVIDUAL FOOD ITEM CANCELLATION
# ==========================================
@chef_required
@require_POST
def chef_cancel_item(request, item_id):
    profile = get_chef_profile(request.user)
    if not profile.can_cancel_items and not request.user.is_superuser:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Permission denied: Cannot cancel food items.'}, status=403)
        messages.error(request, "Permission denied to cancel food items.")
        return redirect('chef:dashboard')

    item = get_object_or_404(OrderItem.objects.select_related('order', 'menu_item'), id=item_id)
    order = item.order
    reason_code = request.POST.get('reason_code', 'Other')
    custom_notes = request.POST.get('reason_notes', '').strip()
    full_reason = f"{reason_code}: {custom_notes}" if custom_notes else reason_code

    if item.status == OrderItem.ItemStatus.CANCELLED:
        err = "This item has already been cancelled."
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': err}, status=400)
        messages.warning(request, err)
        return redirect('chef:order_detail', order_id=order.id)

    # If item is already READY or SERVED, require manager authorization
    if item.status in [OrderItem.ItemStatus.READY, OrderItem.ItemStatus.SERVED] and not profile.can_approve_order_cancel:
        err = "Cannot cancel an already completed item without manager authorization."
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': err}, status=403)
        messages.error(request, err)
        return redirect('chef:order_detail', order_id=order.id)

    now = timezone.now()
    deducted_amount = item.total_price

    with transaction.atomic():
        # Update item
        item.status = OrderItem.ItemStatus.CANCELLED
        item.cancelled_at = now
        item.cancellation_reason = full_reason
        item.cancelled_by = request.user.get_full_name() or request.user.username
        item.save()

        # Create cancellation log
        OrderCancellationLog.objects.create(
            order=order,
            order_item=item,
            cancellation_type=OrderCancellationLog.CancellationType.ITEM,
            item_name=item.menu_item.name,
            quantity=item.quantity,
            amount_deducted=deducted_amount,
            reason=full_reason,
            requested_by=f"Chef {request.user.get_full_name() or request.user.username}",
            status=OrderCancellationLog.LogStatus.CANCELLED,
            reviewed_by=request.user,
            resolved_at=now,
        )

        # If this item was part of a combo, adjust the combo header price proportionally
        if item.is_combo_component and item.combo:
            header = order.items.filter(combo=item.combo, is_combo_header=True).first()
            if header and item.combo.individual_total_price > Decimal('0.00'):
                item_val = item.quantity * item.menu_item.price
                proportional_deduction = round((item_val / item.combo.individual_total_price) * item.combo.price, 2)
                header.unit_price = max(Decimal('0.00'), header.unit_price - proportional_deduction)
                header.save(update_fields=['unit_price'])
                deducted_amount = proportional_deduction

        # Recalculate bill totals according to billing policy
        order.calculate_totals()

        # Update session invoice if attached
        if order.session:
            for inv in order.session.invoices.all():
                inv.recalculate(save=True)

        # Check if all items in the order are now cancelled
        if not order.items.exclude(status=OrderItem.ItemStatus.CANCELLED).exists():
            order.status = Order.Status.CANCELLED
            order.cancelled_at = now
            order.cancellation_reason = f"All items cancelled in kitchen: {full_reason}"
            order.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])

        # Notify Admin / Floor Staff
        KitchenNotification.objects.create(
            restaurant=get_user_restaurant(request.user),
            notification_type=KitchenNotification.NotificationType.ORDER_CANCELLED,
            title=f"Item Cancelled: {item.menu_item.name}",
            message=f"Kitchen cancelled {item.quantity}x {item.menu_item.name} for {order.order_number}. Reason: {full_reason}",
            related_order=order,
        )

    msg = f"Successfully cancelled {item.quantity}x {item.menu_item.name}. Bill recalculated."
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': msg,
            'item_id': item.id,
            'deducted_amount': str(deducted_amount),
            'new_order_total': str(order.total_amount),
            'order_status': order.status,
            'order_status_display': order.get_status_display(),
        })
    messages.success(request, msg)
    return redirect('chef:order_detail', order_id=order.id)


# ==========================================
# 7. ENTIRE ORDER CANCELLATION REQUEST
# ==========================================
@chef_required
@require_POST
def chef_request_order_cancel(request, order_id):
    profile = get_chef_profile(request.user)
    if not profile.can_request_order_cancel and not request.user.is_superuser:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Permission denied: Cannot request order cancellation.'}, status=403)
        messages.error(request, "Permission denied to request order cancellation.")
        return redirect('chef:order_detail', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    reason = request.POST.get('reason', '').strip()
    if not reason:
        reason = "Kitchen unable to fulfill order."

    now = timezone.now()

    # If Chef has direct manager approval authority
    if profile.can_approve_order_cancel or request.user.is_superuser:
        with transaction.atomic():
            # Cancel all active items
            for item in order.items.exclude(status=OrderItem.ItemStatus.CANCELLED):
                item.status = OrderItem.ItemStatus.CANCELLED
                item.cancelled_at = now
                item.cancellation_reason = reason
                item.cancelled_by = request.user.get_full_name() or request.user.username
                item.save()

            order.status = Order.Status.CANCELLED
            order.cancelled_at = now
            order.cancellation_reason = reason
            order.cancellation_requested_by = f"Chef {request.user.get_full_name() or request.user.username}"
            order.save()
            order.calculate_totals()

            # Log
            OrderCancellationLog.objects.create(
                order=order,
                cancellation_type=OrderCancellationLog.CancellationType.ORDER,
                item_name="Entire Order",
                quantity=order.total_items,
                amount_deducted=order.total_amount,
                reason=reason,
                requested_by=f"Chef {request.user.get_full_name() or request.user.username}",
                status=OrderCancellationLog.LogStatus.APPROVED,
                reviewed_by=request.user,
                resolved_at=now,
            )

            # Notification
            KitchenNotification.objects.create(
                restaurant=get_user_restaurant(request.user),
                notification_type=KitchenNotification.NotificationType.ORDER_CANCELLED,
                title=f"Order Cancelled: {order.order_number}",
                message=f"Entire order {order.order_number} cancelled by Chef {request.user.get_full_name()}. Reason: {reason}",
                related_order=order,
            )

        msg = f"Entire order {order.order_number} has been cancelled directly."
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': msg, 'status': 'CANCELLED'})
        messages.success(request, msg)
        return redirect('chef:order_detail', order_id=order.id)

    # Standard flow: submit request to Restaurant Admin for review
    with transaction.atomic():
        order.has_unseen_cancellation = True
        order.save(update_fields=['has_unseen_cancellation'])

        OrderCancellationLog.objects.create(
            order=order,
            cancellation_type=OrderCancellationLog.CancellationType.ORDER,
            item_name="Entire Order",
            quantity=order.total_items,
            amount_deducted=order.total_amount,
            reason=reason,
            requested_by=f"Chef {request.user.get_full_name() or request.user.username}",
            status=OrderCancellationLog.LogStatus.REQUESTED,
        )

        KitchenNotification.objects.create(
            restaurant=get_user_restaurant(request.user),
            notification_type=KitchenNotification.NotificationType.CANCELLATION_REQUEST,
            title=f"Order Cancellation Requested: {order.order_number}",
            message=f"Kitchen requested cancellation of order {order.order_number} (Table {order.table.table_number if order.table else '-'}). Reason: {reason}",
            related_order=order,
        )

    msg = f"Cancellation request submitted to Restaurant Admin for order {order.order_number}."
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': msg, 'status': 'REQUESTED'})
    messages.info(request, msg)
    return redirect('chef:order_detail', order_id=order.id)


# ==========================================
# 8. CANCELLATION REQUESTS & LOGS (REDIRECTED)
# ==========================================
@chef_required
def chef_cancellations(request):
    return redirect('chef:orders_cancelled')


# ==========================================
# 9. KITCHEN NOTIFICATIONS
# ==========================================
@chef_required
def chef_notifications(request):
    profile = get_chef_profile(request.user)
    restaurant = get_user_restaurant(request.user)

    notifications_qs = KitchenNotification.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True)
    ).select_related('related_order').order_by('-created_at')

    unread_count = notifications_qs.filter(is_read=False).count()

    paginator = Paginator(notifications_qs, 30)
    notifications_page = paginator.get_page(request.GET.get('page'))

    context = {
        'profile': profile,
        'restaurant': restaurant,
        'notifications': notifications_page,
        'unread_count': unread_count,
    }
    return render(request, 'chef/notifications.html', context)


@chef_required
@require_POST
def chef_mark_notification_read(request, notif_id):
    notif = get_object_or_404(KitchenNotification, id=notif_id)
    notif.is_read = True
    notif.save(update_fields=['is_read'])
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    return redirect('chef:notifications')


@chef_required
@require_POST
def chef_mark_all_read(request):
    restaurant = get_user_restaurant(request.user)
    KitchenNotification.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True),
        is_read=False
    ).update(is_read=True)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    messages.success(request, "All notifications marked as read.")
    return redirect('chef:notifications')


# ==========================================
# 10. REPORT UNAVAILABLE FOOD ITEMS (86 ITEM)
# ==========================================
@chef_required
def chef_report_unavailable(request):
    profile = get_chef_profile(request.user)
    restaurant = get_user_restaurant(request.user)

    if request.method == 'POST':
        if not profile.can_report_unavailable and not request.user.is_superuser:
            messages.error(request, "Permission denied to report unavailable items.")
            return redirect('chef:dashboard')

        item_id = request.POST.get('menu_item_id')
        reason = request.POST.get('reason', '').strip()

        if not item_id or not reason:
            messages.error(request, "Please select an item and provide a reason.")
        else:
            menu_item = get_object_or_404(MenuItem, id=item_id)
            report = ItemAvailabilityReport.objects.create(
                restaurant=restaurant,
                menu_item=menu_item,
                reported_by=request.user,
                reason=reason,
                status=ItemAvailabilityReport.ReportStatus.PENDING
            )

            # Notify Admin
            KitchenNotification.objects.create(
                restaurant=restaurant,
                notification_type=KitchenNotification.NotificationType.ITEM_UNAVAILABLE,
                title=f"Item Shortage Report: {menu_item.name}",
                message=f"Kitchen reported {menu_item.name} as unavailable: '{reason}'. Awaiting admin approval to take off digital menu.",
            )

            messages.success(request, f"Report for '{menu_item.name}' submitted to Restaurant Admin.")
            return redirect('chef:report_unavailable')

    menu_items = MenuItem.objects.filter(is_available=True).order_by('category__name', 'name')
    past_reports = ItemAvailabilityReport.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True)
    ).select_related('menu_item', 'reported_by', 'reviewed_by').order_by('-created_at')[:20]

    context = {
        'profile': profile,
        'restaurant': restaurant,
        'menu_items': menu_items,
        'past_reports': past_reports,
    }
    return render(request, 'chef/report_unavailable.html', context)


# ==========================================
# 11. MY PROFILE & PASSWORD CHANGE
# ==========================================
@chef_required
def chef_profile(request):
    profile = get_chef_profile(request.user)
    restaurant = get_user_restaurant(request.user)
    profile_form = UserProfileForm(instance=request.user)
    password_form = PasswordChangeForm(request.user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            profile_form = UserProfileForm(request.POST, request.FILES, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your personal information has been updated successfully.")
                return redirect('chef:profile')
            else:
                for error_list in profile_form.errors.values():
                    for err in error_list:
                        messages.error(request, err)

        elif action == 'change_password':
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed successfully.")
                return redirect('chef:profile')
            else:
                for error in password_form.errors.values():
                    messages.error(request, error)

    context = {
        'profile': profile,
        'restaurant': restaurant,
        'profile_form': profile_form,
        'password_form': password_form,
    }
    return render(request, 'chef/profile.html', context)


# ==========================================
# 12. LOGOUT
# ==========================================
@chef_required
def chef_logout(request):
    logout(request)
    messages.info(request, "You have been logged out of the Kitchen portal.")
    return redirect('accounts:login')


# ==========================================
# 13. LIVE POLLING API FOR CHEF SCREENS
# ==========================================
@chef_required
def chef_api_live(request):
    """
    Lightweight JSON endpoint polled every few seconds by the kitchen dashboard.
    Returns counts, audio alerts for new orders, and recent active order cards.
    """
    orders_qs = get_chef_orders_queryset(request)
    restaurant = get_user_restaurant(request.user)
    today = timezone.now().date()
    one_minute_ago = timezone.now() - timezone.timedelta(seconds=45)

    has_brand_new_incoming = orders_qs.filter(
        status__in=[Order.Status.PENDING, 'PLACED'],
        accepted_by__isnull=True,
        created_at__gte=one_minute_ago
    ).exists()

    my_orders_count = orders_qs.filter(accepted_by=request.user).exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED]).count()
    new_count = orders_qs.filter(status__in=[Order.Status.PENDING, 'PLACED'], accepted_by__isnull=True).count()
    preparing_count = orders_qs.filter(status=Order.Status.PREPARING).count()
    ready_count = orders_qs.filter(status=Order.Status.READY).count()
    delayed_serving_count = sum(1 for o in orders_qs if o.is_serving_delayed)

    unread_notifications = KitchenNotification.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True),
        is_read=False
    ).count()

    data = {
        'status': 'ok',
        'has_new_incoming': has_brand_new_incoming,
        'counts': {
            'my_orders': my_orders_count,
            'new': new_count,
            'preparing': preparing_count,
            'ready': ready_count,
            'delayed_serving': delayed_serving_count,
            'unread_notifications': unread_notifications,
        },
        'timestamp': timezone.now().strftime('%H:%M:%S'),
    }
    return JsonResponse(data)
