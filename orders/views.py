import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.db import transaction
from django.utils import timezone
from django.utils.timesince import timesince
from django.db.models import Sum, Q
from django.core.paginator import Paginator

from accounts.models import User
from accounts.decorators import role_required
from accounts.tenant_utils import (
    get_current_restaurant,
    is_impersonating_restaurant,
    get_tenant_object_or_404,
    filter_by_tenant,
)
from menu.models import Category, MenuItem
from tables.models import DiningTable
from .models import Order, OrderItem, OrderCancellationLog, OrderStatusHistory, ExtraFoodOrderLog
from .forms import OrderCreateForm, OrderItemAddForm


@login_required
def order_list(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    status_filter = request.GET.get('status', 'active')
    type_filter = request.GET.get('type')
    chef_filter = request.GET.get('chef', '').strip()
    delayed_filter = request.GET.get('delayed', '').strip()

    orders = Order.objects.select_related('table', 'server', 'accepted_by').prefetch_related('items__menu_item')

    if restaurant:
        orders = orders.filter(restaurant=restaurant)
    elif not request.user.is_superuser or is_impersonating:
        orders = orders.none()

    if status_filter == 'active':
        orders = orders.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED])
    elif status_filter == 'completed':
        orders = orders.filter(status__in=[Order.Status.COMPLETED, Order.Status.SERVED])
    elif status_filter == 'cancelled':
        orders = orders.filter(status=Order.Status.CANCELLED)
    elif status_filter != 'all':
        orders = orders.filter(status=status_filter)

    if type_filter:
        orders = orders.filter(order_type=type_filter)

    if chef_filter == 'unassigned':
        orders = orders.filter(accepted_by__isnull=True)
    elif chef_filter.isdigit():
        orders = orders.filter(accepted_by_id=int(chef_filter))

    if delayed_filter == '1':
        delayed_ids = [o.id for o in orders if o.is_delayed or o.is_serving_delayed]
        orders = orders.filter(id__in=delayed_ids)

    # Kitchen staff list for filtering
    kitchen_staff = User.objects.filter(
        role__in=[User.Role.CHEF, User.Role.KITCHEN],
        is_active=True
    ).order_by('first_name', 'username')
    if restaurant:
        kitchen_staff = kitchen_staff.filter(restaurant=restaurant)

    paginator = Paginator(orders, 24)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'orders/order_list.html', {
        'orders': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'chef_filter': chef_filter,
        'delayed_filter': delayed_filter,
        'kitchen_staff': kitchen_staff,
        'order_types': Order.OrderType.choices,
        'order_statuses': Order.Status.choices,
    })


@login_required
def order_detail(request, order_id):
    order = get_tenant_object_or_404(
        Order.objects.select_related('table', 'server', 'accepted_by').prefetch_related('items__menu_item'),
        request,
        id=order_id
    )

    # Once opened by staff/admin, clear the unseen notifications
    changed = False
    if order.has_unseen_extra:
        order.has_unseen_extra = False
        changed = True
    if order.has_unseen_cancellation:
        order.has_unseen_cancellation = False
        changed = True
    if changed:
        order.save(update_fields=['has_unseen_extra', 'has_unseen_cancellation'])
    order.cancellation_logs.filter(is_seen=False).update(is_seen=True)
    order.extra_food_logs.filter(is_seen=False).update(is_seen=True, seen_at=timezone.now(), seen_by=request.user)

    status_history = order.status_history.all().select_related('changed_by')[:20]
    add_item_form = OrderItemAddForm()

    return render(request, 'orders/order_detail.html', {
        'order': order,
        'status_history': status_history,
        'add_item_form': add_item_form,
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
def cancelled_orders(request):
    """
    Dedicated view for cancelled orders and items strictly scoped to active tenant.
    Marks all unseen cancellations as seen so the notification count resets to 0.
    """
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    filter_type = request.GET.get('type', 'all')
    search_q = request.GET.get('q', '').strip()

    logs_qs = OrderCancellationLog.objects.select_related(
        'order', 'order__table', 'order__session', 'order__server',
        'order_item', 'order_item__menu_item', 'reviewed_by'
    ).prefetch_related(
        'order__items', 'order__items__menu_item'
    ).order_by('-created_at')

    if restaurant:
        logs_qs = logs_qs.filter(order__restaurant=restaurant)
        OrderCancellationLog.objects.filter(order__restaurant=restaurant, is_seen=False).update(is_seen=True)
        Order.objects.filter(restaurant=restaurant, has_unseen_cancellation=True).update(has_unseen_cancellation=False)
    elif request.user.is_superuser and not is_impersonating:
        OrderCancellationLog.objects.filter(is_seen=False).update(is_seen=True)
        Order.objects.filter(has_unseen_cancellation=True).update(has_unseen_cancellation=False)
    else:
        logs_qs = logs_qs.none()

    if filter_type == 'order':
        logs_qs = logs_qs.filter(cancellation_type=OrderCancellationLog.CancellationType.ORDER)
    elif filter_type == 'item':
        logs_qs = logs_qs.filter(cancellation_type=OrderCancellationLog.CancellationType.ITEM)
    elif filter_type == 'requested':
        logs_qs = logs_qs.filter(status=OrderCancellationLog.LogStatus.REQUESTED)

    if search_q:
        logs_qs = logs_qs.filter(
            Q(order__order_number__icontains=search_q) |
            Q(order__table__table_number__icontains=search_q) |
            Q(order__customer_name__icontains=search_q) |
            Q(item_name__icontains=search_q) |
            Q(reason__icontains=search_q)
        )

    # Key Metrics (strictly for this restaurant)
    if restaurant:
        total_logs = OrderCancellationLog.objects.filter(order__restaurant=restaurant).count()
        total_cancelled_orders = Order.objects.filter(restaurant=restaurant, status=Order.Status.CANCELLED).count()
        total_amount_deducted = OrderCancellationLog.objects.filter(order__restaurant=restaurant).aggregate(total=Sum('amount_deducted'))['total'] or Decimal('0.00')
        pending_review_count = OrderCancellationLog.objects.filter(order__restaurant=restaurant, status=OrderCancellationLog.LogStatus.REQUESTED).count()
    else:
        total_logs = logs_qs.count()
        total_cancelled_orders = Order.objects.filter(status=Order.Status.CANCELLED).count()
        total_amount_deducted = logs_qs.aggregate(total=Sum('amount_deducted'))['total'] or Decimal('0.00')
        pending_review_count = logs_qs.filter(status=OrderCancellationLog.LogStatus.REQUESTED).count()

    paginator = Paginator(logs_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'cancellations': page_obj,
        'page_obj': page_obj,
        'filter_type': filter_type,
        'search_q': search_q,
        'total_logs': total_logs,
        'total_cancelled_orders': total_cancelled_orders,
        'total_amount_deducted': total_amount_deducted,
        'pending_review_count': pending_review_count,
    }
    return render(request, 'orders/cancelled_orders.html', context)


@login_required
def api_mark_cancellations_seen(request):
    """AJAX endpoint to mark all cancellation notifications as seen for active tenant."""
    if request.method == 'POST':
        restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
        if restaurant:
            OrderCancellationLog.objects.filter(order__restaurant=restaurant, is_seen=False).update(is_seen=True)
            Order.objects.filter(restaurant=restaurant, has_unseen_cancellation=True).update(has_unseen_cancellation=False)
        else:
            OrderCancellationLog.objects.filter(is_seen=False).update(is_seen=True)
            Order.objects.filter(has_unseen_cancellation=True).update(has_unseen_cancellation=False)
        return JsonResponse({'success': True, 'count': 0})
    return JsonResponse({'success': False}, status=400)


@login_required
def order_dismiss_extra_notification(request, order_id):
    """Dismiss extra items notification banner directly from card without navigating."""
    if request.method == 'POST':
        order = get_tenant_object_or_404(Order, request, id=order_id)
        if order.has_unseen_extra:
            order.has_unseen_extra = False
            order.save(update_fields=['has_unseen_extra'])
        order.extra_food_logs.filter(is_seen=False).update(is_seen=True, seen_at=timezone.now(), seen_by=request.user)
        remaining = ExtraFoodOrderLog.objects.filter(order__restaurant=order.restaurant, is_seen=False).count() if order.restaurant else 0
        return JsonResponse({'success': True, 'remaining_count': remaining})
    return JsonResponse({'error': 'Invalid request method'}, status=400)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER, User.Role.CASHIER])
def extra_food_orders(request):
    """Dedicated view for Extra Food Orders strictly filtered by tenant."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    filter_type = request.GET.get('filter', 'all')
    search_q = request.GET.get('q', '').strip()

    logs_qs = ExtraFoodOrderLog.objects.select_related(
        'order', 'order__table', 'order__session', 'order_item', 'seen_by'
    ).order_by('-created_at')

    if restaurant:
        logs_qs = logs_qs.filter(order__restaurant=restaurant)
    elif not request.user.is_superuser or is_impersonating:
        logs_qs = logs_qs.none()

    if filter_type == 'unseen':
        logs_qs = logs_qs.filter(is_seen=False)
    elif filter_type == 'seen':
        logs_qs = logs_qs.filter(is_seen=True)

    if search_q:
        logs_qs = logs_qs.filter(
            Q(table_name__icontains=search_q) |
            Q(order__order_number__icontains=search_q) |
            Q(item_name__icontains=search_q) |
            Q(added_by_name__icontains=search_q) |
            Q(session_name__icontains=search_q)
        )

    if restaurant:
        unseen_count = ExtraFoodOrderLog.objects.filter(order__restaurant=restaurant, is_seen=False).count()
        total_count = ExtraFoodOrderLog.objects.filter(order__restaurant=restaurant).count()
        total_amount = ExtraFoodOrderLog.objects.filter(order__restaurant=restaurant).aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
        active_tables_count = ExtraFoodOrderLog.objects.filter(
            order__restaurant=restaurant,
            order__status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.READY]
        ).values('table_name').distinct().count()
    else:
        unseen_count = logs_qs.filter(is_seen=False).count()
        total_count = logs_qs.count()
        total_amount = logs_qs.aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
        active_tables_count = logs_qs.filter(
            order__status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, Order.Status.PREPARING, Order.Status.READY]
        ).values('table_name').distinct().count()

    paginator = Paginator(logs_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'extra_food_logs': page_obj,
        'page_obj': page_obj,
        'filter_type': filter_type,
        'search_q': search_q,
        'unseen_count': unseen_count,
        'total_count': total_count,
        'total_amount': total_amount,
        'active_tables_count': active_tables_count,
    }
    return render(request, 'orders/extra_food_orders.html', context)


@login_required
def api_mark_single_extra_food_seen(request, log_id):
    """Marks a single extra food notification as seen (or toggles), scoped to tenant."""
    if request.method == 'POST':
        log = get_tenant_object_or_404(ExtraFoodOrderLog, request, id=log_id)
        action = request.POST.get('action') or request.GET.get('action')

        if action == 'unseen':
            log.is_seen = False
            log.seen_at = None
            log.seen_by = None
            log.save(update_fields=['is_seen', 'seen_at', 'seen_by'])
            log.order.has_unseen_extra = True
            log.order.save(update_fields=['has_unseen_extra'])
        elif action == 'toggle':
            log.is_seen = not log.is_seen
            if log.is_seen:
                log.seen_at = timezone.now()
                log.seen_by = request.user
            else:
                log.seen_at = None
                log.seen_by = None
            log.save(update_fields=['is_seen', 'seen_at', 'seen_by'])
            has_unseen = log.order.extra_food_logs.filter(is_seen=False).exists()
            log.order.has_unseen_extra = has_unseen
            log.order.save(update_fields=['has_unseen_extra'])
        else:
            if not log.is_seen:
                log.is_seen = True
                log.seen_at = timezone.now()
                log.seen_by = request.user
                log.save(update_fields=['is_seen', 'seen_at', 'seen_by'])
                if not log.order.extra_food_logs.filter(is_seen=False).exists():
                    log.order.has_unseen_extra = False
                    log.order.save(update_fields=['has_unseen_extra'])

        restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
        if restaurant:
            remaining = ExtraFoodOrderLog.objects.filter(order__restaurant=restaurant, is_seen=False).count()
        else:
            remaining = ExtraFoodOrderLog.objects.filter(is_seen=False).count()

        return JsonResponse({
            'success': True,
            'is_seen': log.is_seen,
            'remaining_count': remaining
        })
    return JsonResponse({'error': 'POST required'}, status=400)


@login_required
def api_mark_extra_food_seen(request):
    """Marks all unseen extra food notifications as seen for active tenant."""
    if request.method == 'POST':
        restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
        if restaurant:
            ExtraFoodOrderLog.objects.filter(order__restaurant=restaurant, is_seen=False).update(
                is_seen=True,
                seen_at=timezone.now(),
                seen_by=request.user
            )
            Order.objects.filter(restaurant=restaurant, has_unseen_extra=True).update(has_unseen_extra=False)
        else:
            ExtraFoodOrderLog.objects.filter(is_seen=False).update(
                is_seen=True,
                seen_at=timezone.now(),
                seen_by=request.user
            )
            Order.objects.filter(has_unseen_extra=True).update(has_unseen_extra=False)
        return JsonResponse({'success': True, 'remaining_count': 0})
    return JsonResponse({'error': 'POST required'}, status=400)


@login_required
def api_poll_extra_food(request):
    """Real-time polling endpoint returning unseen extra food count for active tenant."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    unseen_qs = ExtraFoodOrderLog.objects.filter(is_seen=False).select_related('order', 'table').order_by('-created_at')
    if restaurant:
        unseen_qs = unseen_qs.filter(order__restaurant=restaurant)

    count = unseen_qs.count()
    latest_data = None
    if count > 0:
        latest = unseen_qs.first()
        latest_data = {
            'id': latest.id,
            'table_name': latest.table_name or (latest.table.table_number if latest.table else 'N/A'),
            'item_name': latest.item_name,
            'quantity': latest.quantity,
            'added_by_name': latest.added_by_name,
            'order_number': latest.order.order_number,
            'order_id': latest.order.id,
            'time_ago': timesince(latest.created_at) + ' ago',
        }
    return JsonResponse({
        'unseen_count': count,
        'latest': latest_data
    })


@login_required
def order_pos(request):
    return redirect('tables:floor_plan')


@login_required
def order_pos_submit(request):
    return redirect('tables:floor_plan')


@login_required
def order_update_status(request, order_id):
    order = get_tenant_object_or_404(Order, request, id=order_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Order.Status.choices):
            with transaction.atomic():
                old_status = order.status
                order.status = new_status
                now = timezone.now()

                if new_status == Order.Status.READY:
                    order.ready_at = now
                    order.items.exclude(status__in=[OrderItem.ItemStatus.CANCELLED, OrderItem.ItemStatus.SERVED]).update(
                        status=OrderItem.ItemStatus.READY,
                        ready_at=now
                    )
                elif new_status == Order.Status.SERVED:
                    order.served_at = now
                    order.items.exclude(status=OrderItem.ItemStatus.CANCELLED).update(
                        status=OrderItem.ItemStatus.SERVED
                    )
                elif new_status == Order.Status.PREPARING:
                    if not order.cooking_started_at:
                        order.cooking_started_at = now
                    order.items.filter(status=OrderItem.ItemStatus.PENDING).update(
                        status=OrderItem.ItemStatus.COOKING,
                        cooking_started_at=now
                    )

                if new_status in [Order.Status.COMPLETED, Order.Status.CANCELLED]:
                    if order.table:
                        order.table.status = DiningTable.Status.CLEANING
                        order.table.save(update_fields=['status'])

                order.save()

                staff_name = request.user.get_full_name() or request.user.username
                OrderStatusHistory.objects.create(
                    order=order,
                    from_status=old_status,
                    to_status=new_status,
                    changed_by=request.user,
                    notes=request.POST.get('notes', f"Order status changed to {new_status} by {staff_name}.")
                )

            msg = f"Order #{order.order_number} marked as {order.get_status_display()}."
            if new_status == Order.Status.SERVED:
                msg = f"Order #{order.order_number} marked as SERVED to customer."
            messages.success(request, msg)

            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': msg,
                    'status': order.status,
                    'status_display': order.get_status_display()
                })

    return redirect(request.META.get('HTTP_REFERER', 'orders:list'))


@login_required
def order_item_update_status(request, item_id):
    item = get_tenant_object_or_404(OrderItem, request, id=item_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(OrderItem.ItemStatus.choices):
            prev_status = item.status
            item.status = new_status
            item.save(update_fields=['status'])

            if new_status == OrderItem.ItemStatus.CANCELLED and prev_status != OrderItem.ItemStatus.CANCELLED:
                if item.is_combo_component and item.combo:
                    header = item.order.items.filter(combo=item.combo, is_combo_header=True).first()
                    if header and item.combo.individual_total_price > Decimal('0.00'):
                        item_val = item.quantity * item.menu_item.price
                        proportional_deduction = round((item_val / item.combo.individual_total_price) * item.combo.price, 2)
                        header.unit_price = max(Decimal('0.00'), header.unit_price - proportional_deduction)
                        header.save(update_fields=['unit_price'])

                item.order.calculate_totals(save=True)
                if item.order.session:
                    for inv in item.order.session.invoices.all():
                        inv.recalculate(save=True)

            messages.success(request, f"{item.menu_item.name} status updated to {item.get_status_display()}.")

    return redirect(request.META.get('HTTP_REFERER', 'orders:kitchen_display'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.CASHIER])
def kitchen_display(request):
    """Live Kitchen Display System (KDS) showing active orders strictly for active tenant."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    active_orders = Order.objects.filter(
        status__in=[Order.Status.PLACED, Order.Status.IN_KITCHEN]
    ).prefetch_related('items__menu_item').order_by('created_at')

    if restaurant:
        active_orders = active_orders.filter(restaurant=restaurant)
    elif not request.user.is_superuser:
        active_orders = active_orders.none()

    return render(request, 'orders/kitchen_display.html', {
        'orders': active_orders,
    })


@login_required
def order_add_item(request, order_id):
    order = get_tenant_object_or_404(Order, request, id=order_id)
    if request.method == 'POST':
        form = OrderItemAddForm(request.POST)
        if form.is_valid():
            order_item = form.save(commit=False)
            order_item.order = order
            order_item.unit_price = order_item.menu_item.price
            order_item.save()
            messages.success(request, f"Added {order_item.quantity}x {order_item.menu_item.name} to order.")
    return redirect('orders:detail', order_id=order.id)
