from django.conf import settings
from django.db.models import Q
from superadmin.models import RestaurantConfig
from accounts.tenant_utils import get_current_restaurant, is_impersonating_restaurant
from restaurant_management.cache_utils import get_cached_tenant_config


def restaurant_context(request):
    """
    Exposes dynamic company logo, restaurant branding, administrative currency settings,
    live notification counters, and Super Admin impersonation banner context.
    All data is strictly scoped to the active tenant.
    """
    user = getattr(request, 'user', None)
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    # Retrieve live configuration for this tenant from RestaurantConfig (cached)
    try:
        config = get_cached_tenant_config(restaurant)
        currency = config.currency if config and config.currency else getattr(settings, 'RESTAURANT_CURRENCY', '$')
        tax_rate = float(config.tax_rate) if config and config.tax_rate else getattr(settings, 'RESTAURANT_TAX_RATE', 0.0825)
        service_charge_rate = float(config.service_charge_rate) if config and config.service_charge_rate else getattr(settings, 'RESTAURANT_SERVICE_CHARGE_RATE', 0.05)
        restaurant_name = restaurant.name if restaurant else (config.restaurant_name if config and config.restaurant_name else getattr(settings, 'RESTAURANT_NAME', 'The Grand Bistro'))
    except Exception:
        config = None
        currency = getattr(settings, 'RESTAURANT_CURRENCY', '$')
        tax_rate = getattr(settings, 'RESTAURANT_TAX_RATE', 0.0825)
        service_charge_rate = getattr(settings, 'RESTAURANT_SERVICE_CHARGE_RATE', 0.05)
        restaurant_name = restaurant.name if restaurant else getattr(settings, 'RESTAURANT_NAME', 'The Grand Bistro')

    default_logo_url = settings.STATIC_URL + 'images/default_restaurant_logo.svg'
    restaurant_logo_url = None
    if restaurant:
        try:
            restaurant_logo_url = restaurant.logo_url
        except Exception:
            restaurant_logo_url = None
    restaurant_logo_icon = getattr(restaurant, 'logo_icon', None) or 'bi-fire'

    # Live notification counters (strictly tenant-filtered)
    unseen_cancellations_count = 0
    unseen_extra_food_count = 0
    pending_bill_requests_count = 0
    pending_bill_request_table = None
    pending_bill_requests_summary = None
    pending_service_requests_count = 0
    waiting_queue_count = 0

    if user and user.is_authenticated:
        try:
            from orders.models import OrderCancellationLog, ExtraFoodOrderLog
            c_qs = OrderCancellationLog.objects.filter(is_seen=False)
            ef_qs = ExtraFoodOrderLog.objects.filter(is_seen=False)
            if restaurant:
                c_qs = c_qs.filter(Q(restaurant=restaurant) | Q(order__restaurant=restaurant))
                ef_qs = ef_qs.filter(Q(order__restaurant=restaurant))
            elif not user.is_superuser:
                c_qs = c_qs.none()
                ef_qs = ef_qs.none()
            unseen_cancellations_count = c_qs.count()
            unseen_extra_food_count = ef_qs.count()
        except Exception:
            unseen_cancellations_count = 0
            unseen_extra_food_count = 0

        try:
            from billing.models import BillRequest
            br_qs = BillRequest.objects.filter(status=BillRequest.Status.PENDING).select_related('table')
            if restaurant:
                br_qs = br_qs.filter(restaurant=restaurant)
            elif not user.is_superuser:
                br_qs = br_qs.none()

            pending_bill_requests_count = br_qs.count()
            if pending_bill_requests_count > 0:
                latest_br = br_qs.order_by('-requested_at').first()
                if latest_br and latest_br.table:
                    pending_bill_request_table = latest_br.table.table_number
                    if pending_bill_requests_count == 1:
                        pending_bill_requests_summary = f"Table {pending_bill_request_table}"
                    else:
                        pending_bill_requests_summary = f"Table {pending_bill_request_table} (+{pending_bill_requests_count - 1})"
        except Exception:
            pending_bill_requests_count = 0
            pending_bill_request_table = None
            pending_bill_requests_summary = None

        try:
            from tables.models import ServiceRequest, TableWaitingQueue
            sr_qs = ServiceRequest.objects.filter(status=ServiceRequest.Status.PENDING, is_seen=False)
            wq_qs = TableWaitingQueue.objects.filter(status=TableWaitingQueue.Status.WAITING)
            if restaurant:
                sr_qs = sr_qs.filter(restaurant=restaurant)
                wq_qs = wq_qs.filter(restaurant=restaurant)
            elif not user.is_superuser:
                sr_qs = sr_qs.none()
                wq_qs = wq_qs.none()

            if user.role == 'WAITER':
                from tables.models import WaiterTableAssignment
                assigned_tbls = WaiterTableAssignment.objects.filter(waiter=user, is_active=True).values_list('table_id', flat=True)
                sr_qs = sr_qs.filter(Q(assigned_waiter=user) | Q(table_id__in=assigned_tbls) | Q(assigned_waiter__isnull=True))

            pending_service_requests_count = sr_qs.count()
            waiting_queue_count = wq_qs.count()
        except Exception:
            pending_service_requests_count = 0
            waiting_queue_count = 0

    return {
        'CURRENT_RESTAURANT': restaurant,
        'IS_IMPERSONATING': is_impersonating,
        'RESTAURANT_CONFIG': config,
        'RESTAURANT_NAME': restaurant_name,
        'RESTAURANT_LOGO_URL': restaurant_logo_url,
        'RESTAURANT_LOGO_ICON': restaurant_logo_icon,
        'DEFAULT_RESTAURANT_LOGO_URL': default_logo_url,
        'RESTAURANT_CURRENCY': currency,
        'RESTAURANT_TAX_RATE': tax_rate,
        'RESTAURANT_SERVICE_CHARGE_RATE': service_charge_rate,
        'unseen_cancellations_count': unseen_cancellations_count,
        'unseen_extra_food_count': unseen_extra_food_count,
        'pending_bill_requests_count': pending_bill_requests_count,
        'pending_bill_request_table': pending_bill_request_table,
        'pending_bill_requests_summary': pending_bill_requests_summary,
        'pending_service_requests_count': pending_service_requests_count,
        'waiting_queue_count': waiting_queue_count,
    }
