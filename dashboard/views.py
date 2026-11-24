from decimal import Decimal
from datetime import datetime, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Avg, Count, Max, Q, F
from accounts.models import User, Restaurant
from orders.models import Order, OrderItem, ExtraFoodOrderLog
from tables.models import DiningTable, Reservation, CustomerSession
from menu.models import MenuItem, Ingredient, Category
from billing.models import Invoice, Payment, BillRequest
from .models import CustomerReview
from accounts.decorators import role_required
from accounts.tenant_utils import get_current_restaurant, is_impersonating_restaurant, get_tenant_object_or_404, filter_by_tenant


@login_required
def index(request):
    user = request.user

    # Role-based redirection
    if user.role in [User.Role.CHEF, User.Role.KITCHEN] and not user.is_superuser:
        return redirect('chef:dashboard')
    if user.role == User.Role.CASHIER and not user.is_superuser:
        return redirect('dashboard:cashier')
    if user.role == User.Role.WAITER and not user.is_superuser:
        return redirect('tables:waiter_requests')

    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    # Scoped base querysets
    if restaurant:
        orders_base = Order.objects.filter(restaurant=restaurant)
        invoices_base = Invoice.objects.filter(restaurant=restaurant)
        tables_base = DiningTable.objects.filter(restaurant=restaurant, is_active=True)
        sessions_base = CustomerSession.objects.filter(restaurant=restaurant)
        users_base = User.objects.filter(restaurant=restaurant)
        ingredients_base = Ingredient.objects.filter(restaurant=restaurant)
        menu_items_base = MenuItem.objects.filter(restaurant=restaurant)
        reservations_base = Reservation.objects.filter(restaurant=restaurant)
    elif user.is_superuser and not is_impersonating:
        orders_base = Order.objects.all()
        invoices_base = Invoice.objects.all()
        tables_base = DiningTable.objects.filter(is_active=True)
        sessions_base = CustomerSession.objects.all()
        users_base = User.objects.all()
        ingredients_base = Ingredient.objects.all()
        menu_items_base = MenuItem.objects.all()
        reservations_base = Reservation.objects.all()
    else:
        orders_base = Order.objects.none()
        invoices_base = Invoice.objects.none()
        tables_base = DiningTable.objects.none()
        sessions_base = CustomerSession.objects.none()
        users_base = User.objects.none()
        ingredients_base = Ingredient.objects.none()
        menu_items_base = MenuItem.objects.none()
        reservations_base = Reservation.objects.none()

    today = timezone.localdate()

    # 1. Total orders
    total_orders = orders_base.count()

    # 2. Today's orders
    today_orders = orders_base.filter(created_at__date=today).count()

    # 3. Active orders
    active_orders = orders_base.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.SERVED]).count()
    pending_orders = orders_base.filter(status__in=[Order.Status.PENDING, 'PLACED']).count()
    preparing_orders = orders_base.filter(status__in=[Order.Status.PREPARING, 'IN_KITCHEN']).count()
    completed_orders = orders_base.filter(status=Order.Status.COMPLETED).count()
    cancelled_orders = orders_base.filter(status=Order.Status.CANCELLED).count()

    # 4. Revenue
    today_invoices = invoices_base.filter(paid_at__date=today, status=Invoice.Status.PAID)
    today_revenue = today_invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    monthly_invoices = invoices_base.filter(paid_at__year=today.year, paid_at__month=today.month, status=Invoice.Status.PAID)
    monthly_revenue = monthly_invoices.aggregate(total=Sum('total_amount'))['total'] or today_revenue

    # 5. Customers
    unique_cust_names = set(orders_base.exclude(customer_name__in=['', 'Guest']).values_list('customer_name', flat=True))
    registered_customers = users_base.filter(role=User.Role.CUSTOMER).count()
    total_customers = max(len(unique_cust_names) + registered_customers, 1 if total_orders > 0 else 0)

    # 6. Tables & Sessions
    active_tables = tables_base.filter(status__in=[DiningTable.Status.OCCUPIED, DiningTable.Status.PARTIALLY_OCCUPIED, DiningTable.Status.BILL_REQUESTED]).count()
    available_tables = tables_base.filter(status=DiningTable.Status.AVAILABLE).count()
    active_sessions_count = sessions_base.filter(status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]).count()

    unpaid_bills_count = invoices_base.exclude(status=Invoice.Status.PAID).count()
    paid_bills_count = invoices_base.filter(status=Invoice.Status.PAID).count()

    # 7. Employees (strictly for this restaurant)
    total_employees = users_base.exclude(role__in=[User.Role.ADMIN, User.Role.CUSTOMER]).exclude(is_superuser=True).count()

    # 8. Low-stock products
    low_stock_ingredients = [item for item in ingredients_base if item.is_low_stock]
    unavailable_menu_items = menu_items_base.filter(is_available=False).count()
    low_stock_products = len(low_stock_ingredients) + unavailable_menu_items

    # 9. Average order value (AOV)
    paid_invoices = invoices_base.filter(status=Invoice.Status.PAID)
    avg_from_invoices = paid_invoices.aggregate(avg=Avg('total_amount'))['avg']
    if avg_from_invoices:
        average_order_value = round(avg_from_invoices, 2)
    elif completed_orders > 0 and today_revenue > 0:
        average_order_value = round(today_revenue / completed_orders, 2)
    elif total_orders > 0 and today_revenue > 0:
        average_order_value = round(today_revenue / total_orders, 2)
    else:
        average_order_value = Decimal('0.00')

    # Supplemental contextual data
    total_tables = tables_base.count()
    table_occupancy_rate = round((active_tables / total_tables * 100), 1) if total_tables > 0 else 0
    today_reservations = reservations_base.filter(reservation_date=today)
    recent_orders = orders_base.select_related('table', 'session', 'server').order_by('-created_at')[:8]
    tables_summary = tables_base.order_by('table_number')

    context = {
        'today': today,
        'total_orders': total_orders,
        'today_orders': today_orders,
        'active_orders': active_orders,
        'pending_orders': pending_orders,
        'preparing_orders': preparing_orders,
        'completed_orders': completed_orders,
        'cancelled_orders': cancelled_orders,
        'today_revenue': today_revenue,
        'monthly_revenue': monthly_revenue,
        'total_customers': total_customers,
        'active_tables': active_tables,
        'available_tables': available_tables,
        'active_sessions_count': active_sessions_count,
        'unpaid_bills_count': unpaid_bills_count,
        'paid_bills_count': paid_bills_count,
        'total_employees': total_employees,
        'low_stock_products': low_stock_products,
        'average_order_value': average_order_value,
        'total_tables': total_tables,
        'table_occupancy_rate': table_occupancy_rate,
        'today_reservations': today_reservations,
        'recent_orders': recent_orders,
        'tables_summary': tables_summary,
    }

    if user.is_superuser:
        # Multi-Tenant & Super Admin metrics: Total & status of created admins
        all_admins = User.objects.filter(
            role__in=[User.Role.ADMIN, User.Role.MANAGER],
            is_superuser=False
        ).select_related('restaurant').order_by('-date_joined')

        total_admins = all_admins.count()
        active_admins = all_admins.filter(is_active=True, is_suspended=False).count()
        inactive_admins = all_admins.filter(is_active=False).count()
        suspended_admins = all_admins.filter(is_suspended=True).count()
        admins_created_today = all_admins.filter(date_joined__date=today).count()
        admins_created_this_month = all_admins.filter(date_joined__year=today.year, date_joined__month=today.month).count()

        total_restaurants = Restaurant.objects.count()
        active_restaurants = Restaurant.objects.filter(status=Restaurant.RestaurantStatus.ACTIVE).count()
        suspended_restaurants = Restaurant.objects.filter(status=Restaurant.RestaurantStatus.SUSPENDED).count()

        recent_admins = all_admins[:10]
        recent_restaurants = Restaurant.objects.order_by('-created_at')[:6]

        total_employees = User.objects.exclude(
            role__in=[User.Role.ADMIN, User.Role.CUSTOMER]
        ).exclude(is_superuser=True).count()

        total_platform_orders = Order.objects.count()
        total_platform_revenue = Payment.objects.filter(status='COMPLETED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        context.update({
            'is_superuser_dashboard': True,
            'total_admins': total_admins,
            'active_admins': active_admins,
            'inactive_admins': inactive_admins,
            'suspended_admins': suspended_admins,
            'admins_created_today': admins_created_today,
            'admins_created_this_month': admins_created_this_month,
            'total_restaurants': total_restaurants,
            'active_restaurants': active_restaurants,
            'suspended_restaurants': suspended_restaurants,
            'recent_admins': recent_admins,
            'recent_restaurants': recent_restaurants,
            'total_employees': total_employees,
            'total_platform_orders': total_platform_orders,
            'total_platform_revenue': total_platform_revenue,
        })

    return render(request, 'dashboard/index.html', context)


@login_required
def customers_view(request):
    """Customer Directory & Management View with Calendar & Payment Method Filtering strictly scoped by tenant."""
    if request.user.role == User.Role.CASHIER and not request.user.is_superuser:
        messages.warning(request, "Access restricted: Cashier accounts do not have permission to access Guests & Hospitality.")
        return redirect('dashboard:cashier')

    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    search_query = request.GET.get('q', '').strip()
    date_str = request.GET.get('date', '').strip()
    payment_method = request.GET.get('payment_method', 'ALL').strip().upper()

    if restaurant:
        orders_query = Order.objects.filter(restaurant=restaurant).exclude(customer_name__in=['', 'Guest']).prefetch_related('items__menu_item')
    elif request.user.is_superuser and not is_impersonating:
        orders_query = Order.objects.exclude(customer_name__in=['', 'Guest']).prefetch_related('items__menu_item')
    else:
        orders_query = Order.objects.none()

    if search_query:
        orders_query = orders_query.filter(
            Q(customer_name__icontains=search_query) | Q(customer_phone__icontains=search_query)
        )

    selected_date = None
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            orders_query = orders_query.filter(created_at__date=selected_date)
        except ValueError:
            date_str = ''

    if payment_method in ['CASH', 'UPI', 'ONLINE']:
        paid_inv_filter = Q(payments__payment_method=payment_method, status=Invoice.Status.PAID)
        if restaurant:
            paid_inv_filter &= Q(restaurant=restaurant)
        paid_invoices = Invoice.objects.filter(paid_inv_filter)
        orders_query = orders_query.filter(
            Q(invoices__in=paid_invoices) | Q(session__invoices__in=paid_invoices)
        ).distinct()

    customer_data = []
    seen_names = set()
    total_filtered_orders_count = orders_query.count()
    total_filtered_revenue = Decimal('0.00')

    today_date = timezone.localdate().strftime('%Y-%m-%d')
    yesterday_date = (timezone.localdate() - timedelta(days=1)).strftime('%Y-%m-%d')

    for ord in orders_query.order_by('-created_at'):
        c_name = ord.customer_name.strip()
        if c_name in seen_names:
            continue
        seen_names.add(c_name)

        if restaurant:
            all_c_orders = Order.objects.filter(restaurant=restaurant, customer_name=c_name)
            c_invoices = Invoice.objects.filter(restaurant=restaurant, status=Invoice.Status.PAID).filter(
                Q(order__in=all_c_orders) | Q(session__orders__in=all_c_orders)
            )
        else:
            all_c_orders = Order.objects.filter(customer_name=c_name)
            c_invoices = Invoice.objects.filter(
                Q(order__in=all_c_orders) | Q(session__orders__in=all_c_orders), status=Invoice.Status.PAID
            )

        c_order_count = all_c_orders.count()
        c_phone = ord.customer_phone or "Not provided"
        c_last_order = ord.created_at
        c_total_spent = c_invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        total_filtered_revenue += ord.total_amount

        # Loyalty Tier
        if c_order_count >= 10 or c_total_spent > Decimal('500.00'):
            tier = 'VIP Platinum'
            badge_class = 'primary'
        elif c_order_count >= 5 or c_total_spent > Decimal('200.00'):
            tier = 'Regular Gold'
            badge_class = 'success'
        else:
            tier = 'New Guest'
            badge_class = 'secondary'

        fav_items = OrderItem.objects.filter(order__in=all_c_orders)\
            .values('menu_item__name')\
            .annotate(qty=Sum('quantity'))\
            .order_by('-qty')[:2]
        fav_dishes_list = [f"{item['menu_item__name']} ({item['qty']})" for item in fav_items if item['menu_item__name']]
        fav_dish_str = ", ".join(fav_dishes_list) if fav_dishes_list else "None recorded"

        customer_data.append({
            'name': c_name,
            'phone': c_phone,
            'orders_count': c_order_count,
            'total_spent': c_total_spent,
            'last_visit': c_last_order,
            'tier': tier,
            'badge_class': badge_class,
            'favorite_dish': fav_dish_str,
            'latest_order_number': ord.order_number,
        })

    avg_spend = round(total_filtered_revenue / total_filtered_orders_count, 2) if total_filtered_orders_count > 0 else Decimal('0.00')

    context = {
        'customers': customer_data,
        'total_customers_count': len(customer_data),
        'total_filtered_orders_count': total_filtered_orders_count,
        'total_filtered_revenue': total_filtered_revenue,
        'avg_spend': avg_spend,
        'search_query': search_query,
        'date_str': date_str,
        'payment_method': payment_method,
        'today_date': today_date,
        'yesterday_date': yesterday_date,
    }
    return render(request, 'dashboard/customers.html', context)


@login_required
def analytics_view(request):
    """Sales & Analytics Executive Report strictly scoped by tenant."""
    if request.user.role == User.Role.CASHIER and not request.user.is_superuser:
        messages.warning(request, "Access restricted: Cashier accounts do not have permission to access Analytics & System Reports.")
        return redirect('dashboard:cashier')

    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    today = timezone.localdate()

    if restaurant:
        inv_base = Invoice.objects.filter(restaurant=restaurant)
        orders_base = Order.objects.filter(restaurant=restaurant)
        cat_base = Category.objects.filter(restaurant=restaurant)
        items_base = OrderItem.objects.filter(order__restaurant=restaurant)
    elif request.user.is_superuser and not is_impersonating:
        inv_base = Invoice.objects.all()
        orders_base = Order.objects.all()
        cat_base = Category.objects.all()
        items_base = OrderItem.objects.all()
    else:
        inv_base = Invoice.objects.none()
        orders_base = Order.objects.none()
        cat_base = Category.objects.none()
        items_base = OrderItem.objects.none()

    today_invoices = inv_base.filter(paid_at__date=today, status=Invoice.Status.PAID)
    today_revenue = today_invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    monthly_invoices = inv_base.filter(paid_at__year=today.year, paid_at__month=today.month, status=Invoice.Status.PAID)
    monthly_revenue = monthly_invoices.aggregate(total=Sum('total_amount'))['total'] or today_revenue

    all_paid_invoices = inv_base.filter(status=Invoice.Status.PAID)
    total_lifetime_revenue = all_paid_invoices.aggregate(total=Sum('total_amount'))['total'] or monthly_revenue
    avg_order_value = all_paid_invoices.aggregate(avg=Avg('total_amount'))['avg'] or Decimal('0.00')

    # Top selling dishes
    top_dishes = items_base.values('menu_item__name', 'menu_item__price')\
        .annotate(total_qty=Sum('quantity'), total_sales=Sum(F('quantity') * F('unit_price')))\
        .order_by('-total_qty')[:6]

    # Category breakdown
    categories_stats = cat_base.annotate(items_count=Count('items'))

    # Order type breakdown
    dine_in_count = orders_base.filter(order_type=Order.OrderType.DINE_IN).count()
    takeaway_count = orders_base.filter(order_type=Order.OrderType.TAKEAWAY).count()
    delivery_count = orders_base.filter(order_type=Order.OrderType.DELIVERY).count()

    context = {
        'today': today,
        'today_revenue': today_revenue,
        'monthly_revenue': monthly_revenue,
        'total_lifetime_revenue': total_lifetime_revenue,
        'avg_order_value': round(avg_order_value, 2),
        'top_dishes': top_dishes,
        'categories_stats': categories_stats,
        'dine_in_count': dine_in_count,
        'takeaway_count': takeaway_count,
        'delivery_count': delivery_count,
        'total_orders': orders_base.count(),
    }
    return render(request, 'dashboard/analytics.html', context)


@login_required
def reviews_view(request):
    """Customer Reviews & Feedback Ratings View strictly scoped by tenant."""
    if request.user.role == User.Role.CASHIER and not request.user.is_superuser:
        messages.warning(request, "Access restricted: Cashier accounts do not have permission to access Reviews.")
        return redirect('dashboard:cashier')

    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    if restaurant:
        reviews = CustomerReview.objects.filter(restaurant=restaurant).order_by('-created_at')
    elif request.user.is_superuser and not is_impersonating:
        reviews = CustomerReview.objects.all().order_by('-created_at')
    else:
        reviews = CustomerReview.objects.none()

    total_reviews = reviews.count()
    avg_rating = reviews.aggregate(avg=Avg('rating'))['avg'] or 5.0
    avg_food = reviews.aggregate(avg=Avg('food_rating'))['avg'] or 5.0
    avg_service = reviews.aggregate(avg=Avg('service_rating'))['avg'] or 5.0
    avg_ambiance = reviews.aggregate(avg=Avg('ambiance_rating'))['avg'] or 5.0

    star_5 = reviews.filter(rating=5).count()
    star_4 = reviews.filter(rating=4).count()
    star_3 = reviews.filter(rating=3).count()
    star_2 = reviews.filter(rating=2).count()
    star_1 = reviews.filter(rating=1).count()

    context = {
        'reviews': reviews,
        'total_reviews': total_reviews,
        'avg_rating': round(avg_rating, 1),
        'avg_food': round(avg_food, 1),
        'avg_service': round(avg_service, 1),
        'avg_ambiance': round(avg_ambiance, 1),
        'star_5': star_5,
        'star_4': star_4,
        'star_3': star_3,
        'star_2': star_2,
        'star_1': star_1,
    }
    return render(request, 'dashboard/reviews.html', context)


@login_required
def notifications_view(request):
    """Real-Time Notifications & Activity Log strictly filtered by restaurant tenant."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    if restaurant:
        orders_qs = Order.objects.filter(restaurant=restaurant).select_related('table', 'session').order_by('-created_at')
        payments_qs = Payment.objects.filter(restaurant=restaurant).select_related('invoice__session', 'invoice__table').order_by('-payment_date')
        sessions_qs = CustomerSession.objects.filter(restaurant=restaurant).select_related('table').order_by('-created_at')
        bill_reqs_qs = BillRequest.objects.filter(restaurant=restaurant).select_related('table', 'session', 'accepted_by').order_by('-requested_at')
        extra_food_qs = ExtraFoodOrderLog.objects.filter(order__restaurant=restaurant).select_related('order', 'table').order_by('-created_at')
    elif request.user.is_superuser and not is_impersonating:
        orders_qs = Order.objects.select_related('table', 'session').order_by('-created_at')
        payments_qs = Payment.objects.select_related('invoice__session', 'invoice__table').order_by('-payment_date')
        sessions_qs = CustomerSession.objects.select_related('table').order_by('-created_at')
        bill_reqs_qs = BillRequest.objects.select_related('table', 'session', 'accepted_by').order_by('-requested_at')
        extra_food_qs = ExtraFoodOrderLog.objects.select_related('order', 'table').order_by('-created_at')
    else:
        orders_qs = Order.objects.none()
        payments_qs = Payment.objects.none()
        sessions_qs = CustomerSession.objects.none()
        bill_reqs_qs = BillRequest.objects.none()
        extra_food_qs = ExtraFoodOrderLog.objects.none()

    recent_orders = orders_qs[:15]
    recent_payments = payments_qs[:15]
    recent_sessions = sessions_qs[:15]
    recent_bill_reqs = bill_reqs_qs[:15]
    recent_extra_food = extra_food_qs[:15]

    events = []
    for br in recent_bill_reqs:
        table_num = br.table.table_number if br.table else 'N/A'
        cust_name = br.session.session_name if br.session else 'Customer'
        events.append({
            'type': 'bill_req',
            'icon': 'bi-bell-fill',
            'color': 'warning',
            'title': f"Bill Requested: Table {table_num}",
            'desc': f"{cust_name} at Table {table_num} requested their bill of ₹{br.total_amount} [Status: {br.get_status_display()}]",
            'time': br.requested_at,
            'link': f"/billing/requests/{br.id}/",
        })

    for ef in recent_extra_food:
        table_str = ef.table_name or (ef.table.table_number if ef.table else 'N/A')
        events.append({
            'type': 'extra_food',
            'icon': 'bi-fire',
            'color': 'warning',
            'title': f"Extra Food Ordered: Table {table_str}",
            'desc': f"{ef.added_by_name} added extra {ef.quantity}x {ef.item_name} for Table {table_str} (₹{ef.total_price})",
            'time': ef.created_at,
            'link': f"/orders/{ef.order_id}/",
        })

    for o in recent_orders:
        table_str = f"Table {o.table.table_number}" if o.table else o.get_order_type_display()
        events.append({
            'type': 'order',
            'icon': 'bi-bag-check-fill',
            'color': 'primary',
            'title': f"Order {o.order_number} ({table_str})",
            'desc': f"{o.customer_name} placed order for ₹{o.total_amount} [{o.get_status_display()}]",
            'time': o.created_at,
            'link': f"/orders/{o.id}/",
        })

    for p in recent_payments:
        inv = p.invoice
        table_str = f"Table {inv.table.table_number}" if inv and inv.table else "Direct POS"
        events.append({
            'type': 'payment',
            'icon': 'bi-cash-coin',
            'color': 'success',
            'title': f"Payment Received: ₹{p.amount}",
            'desc': f"Settled via {p.get_payment_method_display()} for {table_str}",
            'time': p.payment_date,
            'link': f"/billing/invoices/{p.invoice_id}/",
        })

    for s in recent_sessions:
        table_str = f"Table {s.table.table_number}" if s.table else "Table"
        events.append({
            'type': 'session',
            'icon': 'bi-people-fill',
            'color': 'info',
            'title': f"Guest Seated: {table_str}",
            'desc': f"{s.customer_name} ({s.guest_count} guests) joined dining session [{s.session_name}]",
            'time': s.created_at,
            'link': f"/tables/{s.table.table_number if s.table else ''}/",
        })

    # Sort events by time descending
    events.sort(key=lambda x: x['time'] if x['time'] else timezone.now(), reverse=True)

    return render(request, 'dashboard/notifications.html', {
        'events': events[:40],
        'total_events': len(events),
    })


@login_required
def cashier_dashboard(request):
    """Dedicated Cashier Operations Desk strictly scoped by restaurant tenant."""
    today = timezone.localdate()
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    if restaurant:
        invoices_qs = Invoice.objects.filter(restaurant=restaurant).select_related('table', 'session', 'order').prefetch_related('payments')
        orders_qs = Order.objects.filter(restaurant=restaurant).select_related('table', 'session', 'server').prefetch_related('items__menu_item')
        tables_qs = DiningTable.objects.filter(restaurant=restaurant, is_active=True)
    elif request.user.is_superuser and not is_impersonating:
        invoices_qs = Invoice.objects.select_related('table', 'session', 'order').prefetch_related('payments')
        orders_qs = Order.objects.select_related('table', 'session', 'server').prefetch_related('items__menu_item')
        tables_qs = DiningTable.objects.filter(is_active=True)
    else:
        invoices_qs = Invoice.objects.none()
        orders_qs = Order.objects.none()
        tables_qs = DiningTable.objects.none()

    # 2. Key Operational Counter Metrics
    unpaid_invoices = invoices_qs.exclude(status=Invoice.Status.PAID).order_by('-created_at')
    unpaid_bills_count = unpaid_invoices.count()
    unpaid_balance_total = sum(inv.balance_due for inv in unpaid_invoices)

    today_paid_invoices = invoices_qs.filter(paid_at__date=today, status=Invoice.Status.PAID)
    today_paid_count = today_paid_invoices.count()
    today_collected_total = today_paid_invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    active_orders_count = orders_qs.exclude(status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.SERVED]).count()

    occupied_tables_count = tables_qs.filter(
        status__in=[DiningTable.Status.OCCUPIED, DiningTable.Status.PARTIALLY_OCCUPIED, DiningTable.Status.BILL_REQUESTED]
    ).count()
    total_tables_count = tables_qs.count()

    pending_kitchen_count = orders_qs.filter(
        status__in=[Order.Status.PENDING, Order.Status.ACCEPTED, 'PLACED', Order.Status.PREPARING, 'IN_KITCHEN']
    ).count()
    today_orders_count = orders_qs.filter(created_at__date=today).count()

    # 3. Search & Filter for Cashier Billing workflow
    search_q = request.GET.get('q', '').strip()
    table_filter = request.GET.get('table', '').strip()

    pending_bills = unpaid_invoices
    if search_q:
        pending_bills = pending_bills.filter(
            Q(invoice_number__icontains=search_q) |
            Q(order__order_number__icontains=search_q) |
            Q(table__table_number__icontains=search_q) |
            Q(order__customer_name__icontains=search_q) |
            Q(session__session_name__icontains=search_q) |
            Q(customer_name__icontains=search_q)
        )
    if table_filter:
        pending_bills = pending_bills.filter(table__table_number__iexact=table_filter)

    # 4. Recent settled transactions
    recent_settled = invoices_qs.filter(status=Invoice.Status.PAID).order_by('-paid_at')[:8]

    # 5. Tables for quick table selector
    tables_list = tables_qs.order_by('table_number')

    context = {
        'today': today,
        'unpaid_bills_count': unpaid_bills_count,
        'unpaid_balance_total': unpaid_balance_total,
        'today_paid_count': today_paid_count,
        'today_collected_total': today_collected_total,
        'active_orders_count': active_orders_count,
        'occupied_tables_count': occupied_tables_count,
        'total_tables_count': total_tables_count,
        'pending_kitchen_count': pending_kitchen_count,
        'today_orders_count': today_orders_count,
        'pending_bills': pending_bills,
        'recent_settled': recent_settled,
        'tables_list': tables_list,
        'search_q': search_q,
        'table_filter': table_filter,
    }
    return render(request, 'dashboard/cashier_desk.html', context)
