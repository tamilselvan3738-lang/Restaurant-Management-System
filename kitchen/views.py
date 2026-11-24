import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from accounts.models import User
from accounts.decorators import role_required
from accounts.tenant_utils import get_current_restaurant, get_tenant_object_or_404, filter_by_tenant
from orders.models import Order, OrderItem, OrderStatusHistory, OrderCancellationLog
from .models import KitchenStation

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.WAITER, User.Role.CASHIER])
def kitchen_dashboard(request):
    station_id = request.GET.get('station')
    stations = filter_by_tenant(KitchenStation.objects.filter(is_active=True), request)

    orders_qs = Order.objects.exclude(
        status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.SERVED]
    ).select_related('table', 'session', 'server').prefetch_related('items__menu_item')
    orders_qs = filter_by_tenant(orders_qs, request)

    pending_orders = orders_qs.filter(status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED'])
    preparing_orders = orders_qs.filter(status__in=[Order.Status.PREPARING, 'IN_KITCHEN', 'ACTIVE'])
    ready_orders = orders_qs.filter(status__in=[Order.Status.READY])

    total_active = orders_qs.count()

    context = {
        'pending_orders': pending_orders,
        'preparing_orders': preparing_orders,
        'ready_orders': ready_orders,
        'total_active': total_active,
        'stations': stations,
        'selected_station': station_id,
        'now': timezone.now(),
    }
    return render(request, 'kitchen/kitchen_dashboard.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.WAITER])
def kitchen_update_order_status(request, order_id):
    order = get_tenant_object_or_404(Order, request, id=order_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        valid_statuses = dict(Order.Status.choices)
        if new_status in valid_statuses:
            old_status = order.status
            order.status = new_status
            update_fields = ['status', 'updated_at']

            now = timezone.now()
            if new_status == Order.Status.PREPARING:
                if not order.cooking_started_at:
                    order.cooking_started_at = now
                    update_fields.append('cooking_started_at')
                if not order.accepted_by:
                    order.accepted_by = request.user
                    order.accepted_at = now
                    update_fields.extend(['accepted_by', 'accepted_at'])
            elif new_status == Order.Status.READY:
                if not order.ready_at:
                    order.ready_at = now
                    update_fields.append('ready_at')
            elif new_status == Order.Status.SERVED:
                if not order.served_at:
                    order.served_at = now
                    update_fields.append('served_at')

            order.save(update_fields=update_fields)

            # If marking ready, update order items to ready (excluding cancelled or requested items)
            if new_status == Order.Status.READY:
                order.items.filter(status__in=[OrderItem.ItemStatus.PENDING, OrderItem.ItemStatus.COOKING]).update(status=OrderItem.ItemStatus.READY)
            elif new_status == Order.Status.PREPARING:
                order.items.filter(status=OrderItem.ItemStatus.PENDING).update(status=OrderItem.ItemStatus.COOKING)

            # Record history
            OrderStatusHistory.objects.create(
                order=order,
                from_status=old_status,
                to_status=new_status,
                changed_by=request.user,
                notes=request.POST.get('notes', f"Status updated via Kitchen Display System")
            )
            messages.success(request, f"Order {order.order_number} marked as {order.get_status_display()}.")

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'new_status': order.status, 'display': order.get_status_display()})
    return redirect(request.META.get('HTTP_REFERER', 'kitchen:dashboard'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.WAITER])
def kitchen_update_item_status(request, item_id):
    item = get_tenant_object_or_404(OrderItem.objects.select_related('order'), request, id=item_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(OrderItem.ItemStatus.choices):
            item.status = new_status
            item.save(update_fields=['status'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'new_status': item.status, 'display': item.get_status_display()})
    return redirect(request.META.get('HTTP_REFERER', 'kitchen:dashboard'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.WAITER])
def kitchen_approve_cancellation(request, item_id):
    """
    Staff / Kitchen approves cancellation of an item (e.g. while cooking).
    Marks item as CANCELLED, recalculates order and invoice totals.
    """
    item = get_tenant_object_or_404(OrderItem.objects.select_related('order'), request, id=item_id)
    order = item.order

    with transaction.atomic():
        item.status = OrderItem.ItemStatus.CANCELLED
        item.cancelled_at = timezone.now()
        item.cancelled_by = f"Staff ({request.user.first_name or request.user.username})"
        item.save()

        # Recalculate order totals
        order.calculate_totals(save=True)

        order.has_unseen_cancellation = True
        # If all items are cancelled, mark order as cancelled
        if not order.items.exclude(status=OrderItem.ItemStatus.CANCELLED).exists():
            order.status = Order.Status.CANCELLED
            order.cancelled_at = timezone.now()
            order.cancellation_reason = f"All items cancelled: approved by {request.user.username}"
            order.cancellation_requested_by = request.user.username
            order.save()
        else:
            order.save(update_fields=['has_unseen_cancellation'])

        # Recalculate invoices
        if order.session:
            for inv in order.session.invoices.all():
                inv.recalculate(save=True)
        elif order.invoices.exists():
            for inv in order.invoices.all():
                inv.recalculate(save=True)

        # Update log
        log = OrderCancellationLog.objects.filter(order_item=item, status=OrderCancellationLog.LogStatus.REQUESTED).first()
        if log:
            log.status = OrderCancellationLog.LogStatus.APPROVED
            log.reviewed_by = request.user
            log.resolved_at = timezone.now()
            log.save()
        else:
            OrderCancellationLog.objects.create(
                restaurant=order.restaurant,
                order=order,
                order_item=item,
                item_name=item.menu_item.name,
                quantity=item.quantity,
                amount_deducted=item.original_total_price,
                reason="Approved by kitchen staff",
                requested_by="Kitchen Staff",
                status=OrderCancellationLog.LogStatus.APPROVED,
                reviewed_by=request.user,
                resolved_at=timezone.now()
            )

        OrderStatusHistory.objects.create(
            order=order,
            from_status=order.status,
            to_status=order.status,
            changed_by=request.user,
            notes=f"Kitchen approved cancellation of {item.quantity}x {item.menu_item.name}."
        )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': f"Cancelled {item.menu_item.name}."})
    messages.success(request, f"Cancellation approved for {item.quantity}x {item.menu_item.name}.")
    return redirect('kitchen:dashboard')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.WAITER])
def kitchen_reject_cancellation(request, item_id):
    """
    Staff / Kitchen rejects cancellation request (food is already prepared or cannot be cancelled).
    Restores item to COOKING status.
    """
    item = get_tenant_object_or_404(OrderItem.objects.select_related('order'), request, id=item_id)
    notes = request.POST.get('review_notes', 'Food is already plated and ready to serve.').strip()

    with transaction.atomic():
        item.status = OrderItem.ItemStatus.COOKING
        item.save(update_fields=['status'])

        log = OrderCancellationLog.objects.filter(order_item=item, status=OrderCancellationLog.LogStatus.REQUESTED).first()
        if log:
            log.status = OrderCancellationLog.LogStatus.REJECTED
            log.reviewed_by = request.user
            log.review_notes = notes
            log.resolved_at = timezone.now()
            log.save()

        OrderStatusHistory.objects.create(
            order=item.order,
            from_status=item.order.status,
            to_status=item.order.status,
            changed_by=request.user,
            notes=f"Kitchen rejected cancellation of {item.menu_item.name}: {notes}"
        )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': f"Rejected cancellation for {item.menu_item.name}."})
    messages.info(request, f"Cancellation rejected for {item.menu_item.name}: {notes}")
    return redirect('kitchen:dashboard')


@login_required
def api_kitchen_live(request):
    """Real-time live JSON feed for Kitchen KDS polling, cancellation tracking, and audio alert."""
    active_orders = Order.objects.exclude(
        status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.SERVED]
    ).select_related('table', 'session', 'accepted_by').prefetch_related('items__menu_item')
    active_orders = filter_by_tenant(active_orders, request)

    data = []
    for o in active_orders:
        data.append({
            'id': o.id,
            'order_number': o.order_number,
            'table': o.table.table_number if o.table else 'Takeaway',
            'session_name': o.session.session_name if o.session else 'General',
            'session_join_code': o.session.join_code if o.session else '',
            'customer_name': o.customer_name,
            'status': o.status,
            'status_display': o.get_status_display(),
            'accepted_by_name': o.accepted_by_name,
            'accepted_by_id': o.accepted_by_employee_id,
            'accepted_at': o.accepted_at.strftime('%H:%M:%S') if o.accepted_at else '',
            'is_delayed': o.is_serving_delayed,
            'delayed_minutes': o.serving_delay_minutes,
            'special_instructions': o.special_instructions,
            'has_unseen_extra': o.has_unseen_extra,
            'has_cancellations': o.has_cancellations,
            'has_cancellation_requests': o.has_cancellation_requests,
            'created_at': o.created_at.strftime('%H:%M:%S'),
            'elapsed_minutes': int((timezone.now() - o.created_at).total_seconds() / 60),
            'items': [
                {
                    'id': it.id,
                    'name': it.menu_item.name,
                    'quantity': it.quantity,
                    'notes': it.special_notes,
                    'status': it.status,
                    'status_display': it.get_status_display(),
                    'is_extra': it.is_extra or it.extra_quantity > 0,
                    'extra_quantity': it.extra_quantity,
                    'is_cancelled': it.is_cancelled,
                    'is_cancellation_requested': it.is_cancellation_requested,
                    'cancellation_reason': it.cancellation_reason,
                    'cancelled_at': it.cancelled_at.strftime('%H:%M') if it.cancelled_at else '',
                }
                for it in o.items.all()
            ]
        })

    return JsonResponse({
        'total': len(data),
        'timestamp': timezone.now().isoformat(),
        'orders': data,
    })
