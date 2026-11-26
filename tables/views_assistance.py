import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_http_methods, require_POST
from django.db.models import Q, Count, Avg, F, Case, When, Value, IntegerField
from django.utils import timezone
from accounts.models import User
from accounts.decorators import role_required
from accounts.tenant_utils import get_current_restaurant, get_tenant_object_or_404, filter_by_tenant
from .models import (
    DiningTable,
    CustomerSession,
    GroupMember,
    TableSection,
    WaiterTableAssignment,
    ServiceRequest,
    ServiceRequestAuditLog,
    ServiceSLAConfig,
    ServiceRequestEscalation,
)
from .services import (
    get_dining_table_or_404,
    create_service_request,
    update_service_request_status,
    get_assigned_waiter_for_table,
    broadcast_service_request_event,
    repeat_service_request,
    renotice_service_request,
    submit_service_request_feedback,
    report_service_request_not_fulfilled,
    reassign_service_request,
    check_and_mark_sla_breaches,
    check_unaccepted_service_requests,
    handle_waiter_shift_end_or_logout,
    create_guest_escalation,
    resolve_guest_escalation,
)
from .forms import WaiterAssignmentForm, TableSectionForm, ServiceSLAConfigForm


# ============================================================================
# 1. CUSTOMER-FACING SERVICE ASSISTANCE ENDPOINTS
# ============================================================================

def _get_customer_session(request, table):
    """
    Safely retrieves the authenticated CustomerSession for this guest and table.
    Validates token from GET, Django session, POST/JSON body, or cookie.
    Falls back to the table's current active session.
    """
    session_cookie_key = f'rms_session_{table.id}'
    session_token = (
        request.GET.get('session') or
        request.GET.get('token') or
        request.session.get(f'table_{table.id}_session_token') or
        request.COOKIES.get(session_cookie_key)
    )
    if not session_token:
        # Check Authorization header or POST body
        if request.content_type == 'application/json':
            try:
                body = json.loads(request.body.decode('utf-8'))
                session_token = body.get('session_token') or body.get('token')
            except Exception:
                pass
        else:
            session_token = request.POST.get('session_token') or request.POST.get('token')

    if session_token:
        sess = CustomerSession.objects.filter(
            table=table,
            session_token=session_token,
            status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]
        ).first()
        if sess:
            return sess
        # If an explicit session token was passed but not found, reject
        return None

    # Robust fallback: retrieve active session on this dining table if no token passed
    return CustomerSession.objects.filter(
        table=table,
        status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]
    ).order_by('-created_at').first()


@require_POST
def customer_service_request_create(request, table_number):
    """
    Customer submits a service assistance request (Water, Plates, Cleaning, Call Waiter, etc.).
    Automatically binds to table, session, and tenant. Applies validation & rate-limiting.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)

    if not session:
        return JsonResponse({
            'success': False,
            'message': 'No active dining session found. Please scan the table QR code to start your session.'
        }, status=403)

    # Read parameters from POST or JSON
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}
        request_type = data.get('request_type', '').strip().upper()
        request_category = data.get('request_category', '').strip().upper()
        quantity = data.get('quantity', 1)
        unit = data.get('unit', '').strip()
        priority = data.get('priority', '').strip().upper()
        custom_message = data.get('custom_message', '').strip()
    else:
        request_type = request.POST.get('request_type', '').strip().upper()
        request_category = request.POST.get('request_category', '').strip().upper()
        quantity = request.POST.get('quantity', 1)
        unit = request.POST.get('unit', '').strip()
        priority = request.POST.get('priority', '').strip().upper()
        custom_message = request.POST.get('custom_message', '').strip()

    type_normalization = {
        'COLD_WATER': 'WATER_COLD',
        'NORMAL_WATER': 'WATER_NORMAL',
        'BOTTLED_WATER': 'WATER_BOTTLE',
        'BOTTLE_WATER': 'WATER_BOTTLE',
        'MENU_HELP': 'HELP_MENU',
        'BILL_CLARIFY': 'CLARIFY_BILL',
        'PAYMENT_METHOD': 'PAYMENT_ASSIST',
        'SPILL_CLEAN': 'CLEAN_SPILL',
    }
    if request_type in type_normalization:
        request_type = type_normalization[request_type]

    valid_types = [choice[0] for choice in ServiceRequest.RequestType.choices]
    if request_type not in valid_types:
        return JsonResponse({
            'success': False,
            'message': f"Invalid assistance type. Please select one of the provided options."
        }, status=400)

    NO_QUANTITY_TYPES = {
        'OTHER',
        'BILL',
        'CLARIFY_BILL',
        'PAYMENT_ASSIST',
        'REMOVE_PLATES',
        'WAITER',
        'HELP_MENU',
        'FOOD_INFO',
        'CUSTOM_ASSIST',
        'CLEANING',
        'CLEAN_TABLE',
        'ARRANGE_TABLE',
        'CLEAN_SPILL',
        'TISSUE',
        'SAUCE',
        'SAUCE_OTHER',
        'KETCHUP',
        'MAYONNAISE',
        'CHILLI_SAUCE',
    }
    if request_type in NO_QUANTITY_TYPES:
        quantity = 1
        unit = ''

    if len(custom_message) > 255:
        return JsonResponse({
            'success': False,
            'message': "Custom message cannot exceed 255 characters."
        }, status=400)

    member_token = request.COOKIES.get(f'rms_member_{session.id}')
    member = None
    if member_token:
        member = session.members.filter(member_token=member_token, is_active=True).first()

    customer_name = member.name if member else session.customer_name

    service_req, created = create_service_request(
        table=table,
        session=session,
        request_type=request_type,
        request_category=request_category or None,
        quantity=quantity,
        unit=unit,
        custom_message=custom_message,
        priority=priority or ServiceRequest.Priority.NORMAL,
        member=member,
        customer_name=customer_name
    )

    if not created:
        msg = f"Server re-notified! Reminder #{service_req.re_notice_count} updated on your active request." if service_req.re_notice_count > 0 else "Your request was already received. Staff has been notified!"
        return JsonResponse({
            'success': True,
            'is_duplicate': True,
            'message': msg,
            'request': {
                'id': service_req.id,
                'request_number': service_req.request_number,
                'type': service_req.get_request_type_display(),
                'status': service_req.status,
                'status_display': service_req.get_status_display(),
                'badge_class': service_req.badge_class,
                'estimated_time': service_req.estimated_time_display,
                'waiter_name': service_req.assigned_waiter.get_full_name() if service_req.assigned_waiter else None,
            }
        })

    waiter_name = service_req.assigned_waiter.get_full_name() if service_req.assigned_waiter else None
    display_msg = f"Your request for {service_req.get_request_type_display()} has been received."
    if waiter_name:
        display_msg += f" Server {waiter_name} has been notified."
    else:
        display_msg += " Restaurant staff has been notified."

    return JsonResponse({
        'success': True,
        'message': display_msg,
        'request': {
            'id': service_req.id,
            'request_number': service_req.request_number,
            'type': service_req.get_request_type_display(),
            'status': service_req.status,
            'status_display': service_req.get_status_display(),
            'badge_class': service_req.badge_class,
            'estimated_time': service_req.estimated_time_display,
            'waiter_name': waiter_name,
        }
    })


def _order_requests_with_renotice_first(queryset):
    """
    Ensures active requests that received customer re-notices appear at the very top of the list.
    Secondary sort orders by most recent re-notice timestamp, followed by requested_at.
    """
    return queryset.annotate(
        has_renotice=Case(
            When(
                re_notice_count__gt=0,
                status__in=[
                    ServiceRequest.Status.PENDING,
                    ServiceRequest.Status.ACCEPTED,
                    ServiceRequest.Status.IN_PROGRESS
                ],
                then=Value(1)
            ),
            default=Value(0),
            output_field=IntegerField()
        )
    ).order_by('-has_renotice', F('last_renotice_at').desc(nulls_last=True), '-requested_at')


def customer_service_request_status(request, table_number):
    """
    Live polling endpoint for customer UI to receive real-time updates on active requests.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)

    if not session:
        return JsonResponse({'requests': [], 'has_active_request': False})

    requests_qs = _order_requests_with_renotice_first(
        ServiceRequest.objects.filter(
            table=table,
            session=session
        ).select_related('assigned_waiter', 'accepted_by')
    )[:10]

    req_list = []
    has_active = False

    for req in requests_qs:
        if req.status in [ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]:
            has_active = True

        waiter_obj = req.accepted_by or req.assigned_waiter
        renotice_cooldown_seconds = 0
        if req.last_renotice_at:
            elapsed_since_renotice = int((timezone.now() - req.last_renotice_at).total_seconds())
            if elapsed_since_renotice < 180:
                renotice_cooldown_seconds = 180 - elapsed_since_renotice

        req_list.append({
            'id': req.id,
            'request_number': req.request_number,
            'request_type': req.request_type,
            'type_display': req.get_request_type_display(),
            'request_category': req.request_category,
            'category_display': req.get_request_category_display(),
            'quantity': req.quantity,
            'unit': req.unit,
            'priority': req.priority,
            'priority_display': req.get_priority_display(),
            'priority_badge_class': req.priority_badge_class,
            'custom_message': req.custom_message,
            'icon_class': req.icon_class,
            'status': req.status,
            'status_display': req.get_status_display(),
            'badge_class': req.badge_class,
            'estimated_time': req.estimated_time_display,
            'waiter_name': waiter_obj.get_full_name() if waiter_obj else None,
            'requested_at_time': req.requested_at.strftime('%I:%M %p'),
            'requested_at_ts': int(req.requested_at.timestamp() * 1000),
            'waiting_seconds': req.waiting_duration_seconds,
            'waiting_duration': req.waiting_duration_display,
            're_notice_count': req.re_notice_count,
            'can_re_notice': (req.status in [ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]) and (req.waiting_duration_seconds >= 120),
            'last_renotice_at': req.last_renotice_at.strftime('%I:%M %p') if req.last_renotice_at else None,
            'last_renotice_at_ts': int(req.last_renotice_at.timestamp() * 1000) if req.last_renotice_at else None,
            'renotice_cooldown_seconds': renotice_cooldown_seconds,
            'staff_apology_message': req.staff_apology_message,
            'staff_apology_at_ts': int(req.staff_apology_at.timestamp() * 1000) if req.staff_apology_at else None,
            'is_pending': req.status == ServiceRequest.Status.PENDING,
            'is_accepted': req.status == ServiceRequest.Status.ACCEPTED,
            'is_in_progress': req.status == ServiceRequest.Status.IN_PROGRESS,
            'is_completed': req.status == ServiceRequest.Status.COMPLETED,
            'is_unable': req.status == ServiceRequest.Status.UNABLE_TO_FULFIL,
            'unable_reason': req.unable_to_fulfil_reason,
            'alternative_suggestion': req.alternative_suggestion,
            'rating': req.rating,
            'reported_not_fulfilled': req.reported_not_fulfilled,
        })

    return JsonResponse({
        'requests': req_list,
        'has_active_request': has_active,
        'table_number': table.table_number,
        'session_name': session.session_name,
    })


@require_POST
def customer_service_request_cancel(request, table_number, request_id):
    """
    Allows dining guests to cancel their pending service request if no longer needed.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)

    if not session:
        return JsonResponse({'success': False, 'message': 'Unauthorized session.'}, status=403)

    req = get_object_or_404(ServiceRequest, id=request_id, table=table, session=session)
    if req.status != ServiceRequest.Status.PENDING:
        return JsonResponse({
            'success': False,
            'message': f"Request cannot be cancelled because it is already {req.get_status_display().lower()}."
        }, status=400)

    updated_req, success, msg = update_service_request_status(
        req.id,
        ServiceRequest.Status.CANCELLED,
        user=None,
        reason="Cancelled by customer"
    )
    return JsonResponse({'success': success, 'message': msg})


@require_POST
def customer_service_request_renotice(request, table_number, request_id):
    """
    Customer sends a re-notice / reminder to staff for an active service request waiting for 2+ minutes.
    Re-alerts waiters and pops notification again across staff dashboards.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)
    if not session:
        return JsonResponse({'success': False, 'message': 'Unauthorized session.'}, status=403)

    req, success, msg = renotice_service_request(request_id, session=session)
    if not success:
        return JsonResponse({'success': False, 'message': msg}, status=400)

    return JsonResponse({
        'success': True,
        'message': msg,
        're_notice_count': req.re_notice_count,
        'cooldown_seconds': 180,
        'waiting_duration': req.waiting_duration_display,
        'last_renotice_at': req.last_renotice_at.strftime('%I:%M %p') if req.last_renotice_at else None,
        'last_renotice_at_ts': int(req.last_renotice_at.timestamp() * 1000) if req.last_renotice_at else None,
    })


@require_POST
def customer_service_request_repeat(request, table_number, request_id):
    """
    Customer repeats an earlier completed request (e.g., more water).
    Creates a new distinct request while preserving the original history.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)
    if not session:
        return JsonResponse({'success': False, 'message': 'Unauthorized session.'}, status=403)

    member_token = request.COOKIES.get(f'rms_member_{session.id}')
    member = session.members.filter(member_token=member_token, is_active=True).first() if member_token else None

    new_req, created = repeat_service_request(request_id, session=session, member=member)
    return JsonResponse({
        'success': True,
        'message': f"Repeated request for {new_req.get_request_type_display()} has been submitted!",
        'request': {
            'id': new_req.id,
            'request_number': new_req.request_number,
            'type': new_req.get_request_type_display(),
            'status': new_req.status,
            'status_display': new_req.get_status_display(),
            'badge_class': new_req.badge_class,
        }
    })


@require_POST
def customer_service_request_feedback(request, table_number, request_id):
    """
    Customer submits 1-5 star rating and optional comments on completed service requests.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)
    if not session:
        return JsonResponse({'success': False, 'message': 'Unauthorized session.'}, status=403)

    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}
        rating = data.get('rating', 5)
        notes = data.get('notes', '')
    else:
        rating = request.POST.get('rating', 5)
        notes = request.POST.get('notes', '')

    req, success, msg = submit_service_request_feedback(request_id, session, rating=rating, feedback_notes=notes)
    return JsonResponse({'success': success, 'message': msg})


@require_POST
def customer_service_request_report_not_fulfilled(request, table_number, request_id):
    """
    Customer reports that an item marked completed was not received.
    Escalates directly to management.
    """
    table = get_dining_table_or_404(request, table_number, is_active=True)
    session = _get_customer_session(request, table)
    if not session:
        return JsonResponse({'success': False, 'message': 'Unauthorized session.'}, status=403)

    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}
        reason = data.get('reason', '')
    else:
        reason = request.POST.get('reason', '')

    req, success, msg = report_service_request_not_fulfilled(request_id, session, reason=reason)
    return JsonResponse({'success': success, 'message': msg})


# ============================================================================
# 2. WAITER SERVICE REQUESTS DASHBOARD & ACTIONS
# ============================================================================

@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
def waiter_service_requests_view(request):
    """
    Dedicated Waiter Service Requests Dashboard.
    Displays pending, in-progress, and completed requests for tables assigned to this waiter,
    or across their assigned restaurant.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    check_and_mark_sla_breaches(restaurant=restaurant)
    check_unaccepted_service_requests(restaurant=restaurant)
    check_unaccepted_service_requests(restaurant=restaurant)

    status_filter = request.GET.get('status', '').upper()
    view_mode = request.GET.get('view', 'assigned')  # 'assigned' or 'all'

    base_qs = ServiceRequest.objects.select_related(
        'table', 'session', 'assigned_waiter', 'accepted_by', 'restaurant'
    ).order_by('-requested_at')

    if restaurant:
        base_qs = base_qs.filter(restaurant=restaurant)
    elif not request.user.is_superuser:
        base_qs = base_qs.none()

    # Waiter-specific scoping: by default show requests assigned to them OR unassigned
    if request.user.role == User.Role.WAITER and view_mode == 'assigned':
        assigned_table_ids = WaiterTableAssignment.objects.filter(
            waiter=request.user,
            is_active=True
        ).values_list('table_id', flat=True)

        base_qs = base_qs.filter(
            Q(assigned_waiter=request.user) |
            Q(table_id__in=assigned_table_ids) |
            Q(assigned_waiter__isnull=True)
        )

    # Mark requests as seen once waiter opens this dashboard
    base_qs.filter(is_seen=False).update(is_seen=True)

    # Counts
    pending_count = base_qs.filter(status=ServiceRequest.Status.PENDING).count()
    accepted_count = base_qs.filter(status=ServiceRequest.Status.ACCEPTED).count()
    in_progress_count = base_qs.filter(status=ServiceRequest.Status.IN_PROGRESS).count()
    completed_count = base_qs.filter(status=ServiceRequest.Status.COMPLETED).count()
    unable_count = base_qs.filter(status=ServiceRequest.Status.UNABLE_TO_FULFIL).count()
    total_count = base_qs.count()

    # Filter by status tab
    if status_filter == 'PENDING':
        filtered_qs = base_qs.filter(status=ServiceRequest.Status.PENDING)
    elif status_filter == 'ACCEPTED':
        filtered_qs = base_qs.filter(status=ServiceRequest.Status.ACCEPTED)
    elif status_filter == 'IN_PROGRESS':
        filtered_qs = base_qs.filter(status=ServiceRequest.Status.IN_PROGRESS)
    elif status_filter == 'COMPLETED':
        filtered_qs = base_qs.filter(status=ServiceRequest.Status.COMPLETED)
    elif status_filter == 'CANCELLED':
        filtered_qs = base_qs.filter(status=ServiceRequest.Status.CANCELLED)
    elif status_filter == 'UNABLE_TO_FULFIL':
        filtered_qs = base_qs.filter(status=ServiceRequest.Status.UNABLE_TO_FULFIL)
    else:
        # Default: show active requests first (PENDING, ACCEPTED, IN_PROGRESS)
        filtered_qs = base_qs.filter(
            status__in=[
                ServiceRequest.Status.PENDING,
                ServiceRequest.Status.ACCEPTED,
                ServiceRequest.Status.IN_PROGRESS
            ]
        )

    filtered_qs = _order_requests_with_renotice_first(filtered_qs)

    context = {
        'requests': filtered_qs[:50],
        'pending_count': pending_count,
        'accepted_count': accepted_count,
        'in_progress_count': in_progress_count,
        'completed_count': completed_count,
        'unable_count': unable_count,
        'total_count': total_count,
        'status_filter': status_filter or 'ACTIVE',
        'view_mode': view_mode,
        'is_waiter': request.user.role == User.Role.WAITER,
        'user_availability': getattr(request.user, 'availability_status', 'AVAILABLE'),
        'user_is_on_duty': getattr(request.user, 'is_on_duty', True),
        'availability_choices': User.AvailabilityStatus.choices,
    }
    return render(request, 'tables/waiter_service_requests.html', context)


@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def waiter_service_request_accept(request, request_id):
    """
    Waiter accepts a pending service request.
    Uses atomic database row-locking (select_for_update) to prevent race conditions.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    req = get_tenant_object_or_404(ServiceRequest, request, id=request_id)

    if restaurant and req.restaurant and req.restaurant != restaurant:
        messages.error(request, "Permission denied: Request belongs to another restaurant.")
        return redirect('tables:waiter_requests')

    updated_req, success, msg = update_service_request_status(
        request_id,
        ServiceRequest.Status.ACCEPTED,
        user=request.user
    )

    if success:
        messages.success(request, f"Accepted request #{updated_req.request_number} for Table {updated_req.table.table_number}.")
    else:
        messages.error(request, msg)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': success, 'message': msg})

    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_requests'))


@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def waiter_service_request_start(request, request_id):
    """
    Waiter marks service as In Progress (physically attending to table).
    """
    updated_req, success, msg = update_service_request_status(
        request_id,
        ServiceRequest.Status.IN_PROGRESS,
        user=request.user
    )

    if success:
        messages.info(request, f"Service started for Table {updated_req.table.table_number}.")
    else:
        messages.error(request, msg)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': success, 'message': msg})

    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_requests'))


@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def waiter_service_request_complete(request, request_id):
    """
    Waiter completes the service request.
    """
    updated_req, success, msg = update_service_request_status(
        request_id,
        ServiceRequest.Status.COMPLETED,
        user=request.user
    )

    if success:
        messages.success(request, f"Service request #{updated_req.request_number} completed.")
    else:
        messages.error(request, msg)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': success, 'message': msg})

    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_requests'))


@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def waiter_service_request_unable(request, request_id):
    """
    Waiter marks request as Unable to Fulfil with reason and alternative suggestion.
    Notifies customer and updates request tracking.
    """
    reason = (
        request.POST.get('reason', '').strip() or
        request.POST.get('unable_reason', '').strip() or
        "Item or service currently unavailable"
    )
    alternative = request.POST.get('alternative_suggestion', '').strip()

    updated_req, success, msg = update_service_request_status(
        request_id,
        ServiceRequest.Status.UNABLE_TO_FULFIL,
        user=request.user,
        reason=reason,
        alternative_suggestion=alternative
    )

    if success:
        messages.warning(request, f"Service request #{updated_req.request_number} marked as unable to fulfil.")
    else:
        messages.error(request, msg)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': success, 'message': msg})

    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_requests'))


@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def waiter_service_request_cancel(request, request_id):
    """
    Waiter or Supervisor cancels a service request with reason.
    """
    reason = request.POST.get('cancellation_reason', '').strip() or "Cancelled by staff"
    updated_req, success, msg = update_service_request_status(
        request_id,
        ServiceRequest.Status.CANCELLED,
        user=request.user,
        reason=reason
    )

    if success:
        messages.warning(request, f"Service request #{updated_req.request_number} cancelled.")
    else:
        messages.error(request, msg)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': success, 'message': msg})

    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_requests'))


@login_required
def waiter_service_requests_poll_api(request):
    """
    Real-time polling API endpoint for Waiter & Admin dashboards.
    Returns counts and pending requests to trigger chime notifications and table updates.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    check_and_mark_sla_breaches(restaurant=restaurant)
    check_unaccepted_service_requests(restaurant=restaurant)
    check_unaccepted_service_requests(restaurant=restaurant)

    qs = ServiceRequest.objects.all()

    if restaurant:
        qs = qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    if request.user.role == User.Role.WAITER:
        assigned_table_ids = WaiterTableAssignment.objects.filter(
            waiter=request.user,
            is_active=True
        ).values_list('table_id', flat=True)

        qs = qs.filter(
            Q(assigned_waiter=request.user) |
            Q(table_id__in=assigned_table_ids) |
            Q(assigned_waiter__isnull=True)
        )

    pending_qs = _order_requests_with_renotice_first(qs.filter(status=ServiceRequest.Status.PENDING))
    pending_count = pending_qs.count()

    pending_list = []
    for r in pending_qs[:5]:
        pending_list.append({
            'id': r.id,
            'request_number': r.request_number,
            'table_number': r.table.table_number,
            'customer_name': r.customer_name,
            'request_type': r.get_request_type_display(),
            'custom_message': r.custom_message,
            'requested_at': r.requested_at.strftime('%I:%M %p'),
            'waiting_duration': r.waiting_duration_display,
        })

    recent_renotice_cutoff = timezone.now() - timezone.timedelta(seconds=60)
    renotice_req = qs.filter(
        status__in=[ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS],
        re_notice_count__gt=0,
        last_renotice_at__gte=recent_renotice_cutoff
    ).order_by('-last_renotice_at').first()

    latest_renotice_data = None
    if renotice_req:
        latest_renotice_data = {
            'id': renotice_req.id,
            'request_number': renotice_req.request_number,
            'table_number': renotice_req.table.table_number,
            'customer_name': renotice_req.customer_name,
            'request_type': renotice_req.get_request_type_display(),
            'custom_message': renotice_req.custom_message,
            're_notice_count': renotice_req.re_notice_count,
            'waiting_duration': renotice_req.waiting_duration_display,
            'renotice_time': renotice_req.last_renotice_at.strftime('%I:%M %p') if renotice_req.last_renotice_at else '',
            'renotice_ts': int(renotice_req.last_renotice_at.timestamp() * 1000) if renotice_req.last_renotice_at else 0,
        }

    latest_req = pending_qs.first()
    latest_request_data = None
    if latest_req:
        latest_request_data = {
            'id': latest_req.id,
            'request_number': latest_req.request_number,
            'table_number': latest_req.table.table_number,
            'customer_name': latest_req.customer_name,
            'request_type': latest_req.get_request_type_display(),
            'custom_message': latest_req.custom_message,
            'requested_at': latest_req.requested_at.strftime('%I:%M %p'),
            'waiting_duration': latest_req.waiting_duration_display,
            'is_seen': latest_req.is_seen,
        }

    return JsonResponse({
        'pending_count': pending_count,
        'unseen_count': pending_qs.filter(is_seen=False).count(),
        'pending_requests': pending_list,
        'latest_request': latest_request_data,
        'latest_renotice': latest_renotice_data,
        'total_active': qs.filter(status__in=[ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]).count(),
    })


# ============================================================================
# 3. ADMIN SERVICE MANAGEMENT DASHBOARD
# ============================================================================

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def admin_service_requests_view(request):
    """
    Comprehensive Admin & Manager Service Management Dashboard.
    Provides analytics, filters (table, waiter, type, status, date, delay, escalation),
    workload monitoring, reassignment popup, SLA management, and audit log inspection.
    """
    restaurant = getattr(request.user, 'restaurant', None)

    # 1. Real-time SLA Check & Delayed Requests Detection & Acceptance Timeout Check
    check_and_mark_sla_breaches(restaurant=restaurant)
    check_unaccepted_service_requests(restaurant=restaurant)

    status_filter = request.GET.get('status', '').upper()
    table_filter = request.GET.get('table', '').strip()
    waiter_filter = request.GET.get('waiter', '').strip()
    type_filter = request.GET.get('type', '').strip().upper()
    delayed_filter = request.GET.get('delayed', '').strip()
    unable_filter = request.GET.get('unable', '').strip()
    escalated_filter = request.GET.get('escalated', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    query = request.GET.get('q', '').strip()

    base_qs = ServiceRequest.objects.select_related(
        'table', 'session', 'assigned_waiter', 'accepted_by', 'completed_by', 'restaurant', 'table_section'
    ).order_by('-requested_at')

    if restaurant:
        base_qs = base_qs.filter(restaurant=restaurant)
    elif not request.user.is_superuser:
        base_qs = base_qs.none()

    # Mark requests as seen once admin opens this dashboard
    base_qs.filter(is_seen=False).update(is_seen=True)

    # Metric counts (calculated from database records)
    total_count = base_qs.count()
    pending_count = base_qs.filter(status=ServiceRequest.Status.PENDING).count()
    accepted_count = base_qs.filter(status=ServiceRequest.Status.ACCEPTED).count()
    in_progress_count = base_qs.filter(status=ServiceRequest.Status.IN_PROGRESS).count()
    completed_count = base_qs.filter(status=ServiceRequest.Status.COMPLETED).count()
    cancelled_count = base_qs.filter(status=ServiceRequest.Status.CANCELLED).count()
    unable_count = base_qs.filter(status=ServiceRequest.Status.UNABLE_TO_FULFIL).count()

    # Active delayed requests (unresolved and is_delayed=True)
    active_statuses = [
        ServiceRequest.Status.PENDING,
        ServiceRequest.Status.ACCEPTED,
        ServiceRequest.Status.IN_PROGRESS
    ]
    delayed_count = base_qs.filter(status__in=active_statuses, is_delayed=True).count()

    # Escalations (unresolved guest reports)
    reported_count_qs = ServiceRequestEscalation.objects.filter(
        status__in=[
            ServiceRequestEscalation.EscalationStatus.NEW,
            ServiceRequestEscalation.EscalationStatus.UNDER_REVIEW,
            ServiceRequestEscalation.EscalationStatus.REASSIGNED
        ]
    )
    if restaurant:
        reported_count_qs = reported_count_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
    reported_escalations_count = reported_count_qs.count()

    # Fallback to reported_not_fulfilled flag if no escalation objects yet
    if reported_escalations_count == 0:
        reported_escalations_count = base_qs.filter(reported_not_fulfilled=True).count()

    # Customer Ratings
    ratings_qs = base_qs.filter(rating__isnull=False)
    total_ratings = ratings_qs.count()
    avg_rating_agg = ratings_qs.aggregate(Avg('rating'))['rating__avg']
    avg_rating = round(avg_rating_agg, 1) if avg_rating_agg else None

    # Recent customer feedbacks
    recent_feedbacks = ratings_qs.exclude(feedback_notes='').order_by('-feedback_at')[:5]

    # Average Completion Latency
    completed_requests = base_qs.filter(status=ServiceRequest.Status.COMPLETED, completed_at__isnull=False)
    total_duration_sec = 0
    comp_count = completed_requests.count()
    if comp_count > 0:
        for r in completed_requests[:50]:
            total_duration_sec += r.waiting_duration_seconds
        avg_completion_min = round((total_duration_sec / max(1, min(50, comp_count))) / 60, 1)
    else:
        avg_completion_min = 0.0

    # Build filtered QuerySet
    qs = base_qs

    # Direct click filter shortcuts
    if delayed_filter == '1' or status_filter == 'DELAYED':
        qs = qs.filter(status__in=active_statuses, is_delayed=True)
        status_filter = 'DELAYED'
    elif unable_filter == '1' or status_filter == 'UNABLE_TO_FULFIL':
        qs = qs.filter(status=ServiceRequest.Status.UNABLE_TO_FULFIL)
        status_filter = 'UNABLE_TO_FULFIL'
    elif escalated_filter == '1' or status_filter == 'ESCALATED':
        qs = qs.filter(Q(reported_not_fulfilled=True) | Q(escalations__isnull=False)).distinct()
        status_filter = 'ESCALATED'
    elif status_filter and status_filter != 'ALL':
        qs = qs.filter(status=status_filter)

    if table_filter:
        qs = qs.filter(table__table_number__iexact=table_filter)
    if waiter_filter:
        qs = qs.filter(Q(assigned_waiter_id=waiter_filter) | Q(accepted_by_id=waiter_filter))
    if type_filter:
        qs = qs.filter(request_type=type_filter)

    if date_from:
        try:
            qs = qs.filter(requested_at__date__gte=date_from)
        except Exception:
            pass
    if date_to:
        try:
            qs = qs.filter(requested_at__date__lte=date_to)
        except Exception:
            pass

    if query:
        qs = qs.filter(
            Q(request_number__icontains=query) |
            Q(table__table_number__icontains=query) |
            Q(session__session_name__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(custom_message__icontains=query) |
            Q(assigned_waiter__username__icontains=query) |
            Q(assigned_waiter__first_name__icontains=query) |
            Q(assigned_waiter__last_name__icontains=query) |
            Q(unable_to_fulfil_reason__icontains=query)
        )

    # Tables & Waiters for dropdown filters
    tables_list = DiningTable.objects.filter(is_active=True).order_by('table_number')
    waiters_list = User.objects.filter(role=User.Role.WAITER, is_active=True, is_suspended=False).order_by('first_name', 'username')
    if restaurant:
        tables_list = tables_list.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
        waiters_list = waiters_list.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    # Waiter Performance and SLA Tracking
    waiter_performance = []
    for w in waiters_list:
        w_qs = ServiceRequest.objects.filter(Q(assigned_waiter=w) | Q(accepted_by=w))
        if restaurant:
            w_qs = w_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

        w_assigned = w_qs.count()
        w_pending = w_qs.filter(status=ServiceRequest.Status.PENDING).count()
        w_accepted = w_qs.filter(status=ServiceRequest.Status.ACCEPTED).count()
        w_in_progress = w_qs.filter(status=ServiceRequest.Status.IN_PROGRESS).count()
        w_completed = w_qs.filter(status=ServiceRequest.Status.COMPLETED).count()
        w_delayed = w_qs.filter(is_delayed=True).count()
        w_unable = w_qs.filter(status=ServiceRequest.Status.UNABLE_TO_FULFIL).count()

        # Average acceptance time (accepted_at - requested_at)
        accepted_set = w_qs.filter(accepted_at__isnull=False)[:50]
        if accepted_set.exists():
            acc_durations = [(r.accepted_at - r.requested_at).total_seconds() for r in accepted_set if r.accepted_at >= r.requested_at]
            avg_acc_sec = (sum(acc_durations) / len(acc_durations)) if acc_durations else 0
            m, s = divmod(int(avg_acc_sec), 60)
            avg_acc_display = f"{m}m {s}s" if m > 0 else f"{s}s"
        else:
            avg_acc_display = "N/A"

        # Average completion time (completed_at - requested_at)
        completed_set = w_qs.filter(status=ServiceRequest.Status.COMPLETED, completed_at__isnull=False)[:50]
        if completed_set.exists():
            comp_durations = [(r.completed_at - r.requested_at).total_seconds() / 60.0 for r in completed_set if r.completed_at >= r.requested_at]
            avg_comp_min = round(sum(comp_durations) / len(comp_durations), 1) if comp_durations else 0.0
            avg_comp_display = f"{avg_comp_min}m"
        else:
            avg_comp_display = "N/A"

        w_rating = w_qs.filter(rating__isnull=False).aggregate(Avg('rating'))['rating__avg']

        # Assigned sections/tables
        sec_names = list(w.assigned_sections.filter(is_active=True).values_list('name', flat=True))
        if not sec_names:
            tbl_nums = list(WaiterTableAssignment.objects.filter(waiter=w, is_active=True).values_list('table__table_number', flat=True))
            sec_display = f"Tables: {', '.join(tbl_nums)}" if tbl_nums else "Floor Float"
        else:
            sec_display = ", ".join(sec_names)

        # Active load (pending + accepted + in-progress)
        current_active_load = w_pending + w_accepted + w_in_progress

        waiter_performance.append({
            'waiter': w,
            'employee_id': w.employee_id or f"EMP-{w.id:04d}",
            'section_display': sec_display,
            'is_on_duty': w.is_on_duty,
            'assigned': w_assigned,
            'pending': w_pending,
            'accepted': w_accepted,
            'in_progress': w_in_progress,
            'completed': w_completed,
            'delayed': w_delayed,
            'unable': w_unable,
            'active_load': current_active_load,
            'avg_acceptance_display': avg_acc_display,
            'avg_completion_display': avg_comp_display,
            'avg_rating': round(w_rating, 1) if w_rating else None,
        })

    # Preload SLA configurations for the SLA configuration modal
    sla_configs = ServiceSLAConfig.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True),
        is_active=True
    ).order_by('request_type')

    # Preload floor sections for Floor section modal
    floor_sections = TableSection.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True),
        is_active=True
    ).prefetch_related('tables', 'assigned_waiters')

    qs = _order_requests_with_renotice_first(qs)

    context = {
        'requests': qs[:150],
        'total_count': total_count,
        'pending_count': pending_count,
        'accepted_count': accepted_count,
        'in_progress_count': in_progress_count,
        'completed_count': completed_count,
        'cancelled_count': cancelled_count,
        'unable_count': unable_count,
        'reported_count': reported_escalations_count,
        'delayed_count': delayed_count,
        'avg_rating': avg_rating,
        'total_ratings': total_ratings,
        'recent_feedbacks': recent_feedbacks,
        'avg_completion_min': avg_completion_min,
        'status_filter': status_filter or 'ALL',
        'table_filter': table_filter,
        'waiter_filter': waiter_filter,
        'type_filter': type_filter,
        'delayed_filter': delayed_filter,
        'date_from': date_from,
        'date_to': date_to,
        'query': query,
        'tables_list': tables_list,
        'waiters_list': waiters_list,
        'waiter_performance': waiter_performance,
        'sla_configs': sla_configs,
        'floor_sections': floor_sections,
        'request_types': ServiceRequest.RequestType.choices,
    }
    return render(request, 'tables/admin_service_requests.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def admin_service_request_reassign(request, request_id):
    """
    Admin manually reassigns a service request to another waiter.
    Validates tenant and permission, records required reason, updates atomically,
    logs audit entry, and notifies new waiter.
    """
    waiter_id = request.POST.get('waiter_id')
    reason = request.POST.get('reason', '').strip()
    if not reason:
        reason = "Reassigned by Administrator"

    req, success, msg = reassign_service_request(
        service_req_id=request_id,
        new_waiter_id=waiter_id,
        performed_by=request.user,
        reason=reason
    )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'success': success, 'message': msg})

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect(request.META.get('HTTP_REFERER', 'tables:admin_service_requests'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.WAITER])
def admin_service_request_detail_json(request, request_id):
    """
    Returns complete detailed JSON payload for a service request,
    including timestamps, durations, SLA target, customer feedback, escalation,
    and the chronological activity timeline from ServiceRequestAuditLog.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    qs = ServiceRequest.objects.select_related(
        'table', 'session', 'assigned_waiter', 'accepted_by', 'completed_by',
        'reassigned_from', 'reassigned_by', 'restaurant', 'table_section'
    ).prefetch_related('audit_logs', 'escalations')

    if restaurant:
        qs = qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    req = get_object_or_404(qs, id=request_id)

    # Build chronological activity timeline
    timeline = []
    for log in req.audit_logs.all().order_by('timestamp'):
        timeline.append({
            'action': log.action,
            'time_display': log.timestamp.strftime('%I:%M %p'),
            'date_display': log.timestamp.strftime('%b %d, %Y'),
            'previous_status': log.previous_status,
            'new_status': log.new_status,
            'performer': log.performed_by.get_full_name() if log.performed_by else (
                'Customer' if 'CUSTOMER' in log.action or 'FEEDBACK' in log.action or 'ESCALATION' in log.action else 'System'
            ),
            'notes': log.notes,
        })

    # Escalation info if present
    latest_esc = req.escalations.order_by('-created_at').first()
    escalation_data = None
    if latest_esc:
        escalation_data = {
            'id': latest_esc.id,
            'escalation_number': latest_esc.escalation_number,
            'status': latest_esc.status,
            'status_display': latest_esc.get_status_display(),
            'badge_class': latest_esc.badge_class,
            'complaint_notes': latest_esc.complaint_notes,
            'resolution_notes': latest_esc.resolution_notes,
            'assigned_staff': latest_esc.assigned_staff.get_full_name() if latest_esc.assigned_staff else None,
            'created_at': latest_esc.created_at.strftime('%b %d, %I:%M %p'),
            'resolved_at': latest_esc.resolved_at.strftime('%b %d, %I:%M %p') if latest_esc.resolved_at else None,
        }

    data = {
        'id': req.id,
        'request_number': req.request_number,
        'table_number': req.table.table_number,
        'table_location': req.table.get_location_display(),
        'session_name': req.session.session_name or req.customer_name,
        'customer_name': req.customer_name,
        'section_name': req.table_section.name if req.table_section else '',
        'request_type': req.request_type,
        'request_type_display': req.get_request_type_display(),
        'category_display': req.get_request_category_display(),
        'icon_class': req.icon_class,
        'quantity': req.quantity,
        'unit': req.unit,
        'custom_message': req.custom_message,
        'priority': req.priority,
        'priority_display': req.get_priority_display(),
        'priority_badge_class': req.priority_badge_class,
        'status': req.status,
        'status_display': req.get_status_display(),
        'badge_class': req.badge_class,
        # Timestamps
        'requested_at': req.requested_at.strftime('%b %d, %Y %I:%M %p'),
        'accepted_at': req.accepted_at.strftime('%I:%M %p') if req.accepted_at else None,
        'in_progress_at': req.in_progress_at.strftime('%I:%M %p') if req.in_progress_at else None,
        'completed_at': req.completed_at.strftime('%I:%M %p') if req.completed_at else None,
        'cancelled_at': req.cancelled_at.strftime('%I:%M %p') if req.cancelled_at else None,
        # Durations
        'waiting_duration': req.waiting_duration_display,
        'acceptance_duration': req.acceptance_duration_display,
        'completion_duration': req.completion_duration_display,
        'service_duration': req.service_duration_display,
        # SLA
        'target_sla_minutes': req.target_sla_minutes,
        'escalation_sla_minutes': req.escalation_sla_minutes,
        'is_delayed': req.is_delayed,
        'sla_breached': req.is_sla_breached,
        # Waitstaff
        'assigned_waiter': req.assigned_waiter.get_full_name() if req.assigned_waiter else None,
        'assigned_waiter_id': req.assigned_waiter_id,
        'accepted_by': req.accepted_by.get_full_name() if req.accepted_by else None,
        'completed_by': req.completed_by.get_full_name() if req.completed_by else None,
        'reassigned_from': req.reassigned_from.get_full_name() if req.reassigned_from else None,
        'reassigned_by': req.reassigned_by.get_full_name() if req.reassigned_by else None,
        'reassignment_reason': req.reassignment_reason,
        # Notes & Feedback
        'cancellation_reason': req.cancellation_reason,
        'unable_to_fulfil_reason': req.unable_to_fulfil_reason,
        'alternative_suggestion': req.alternative_suggestion,
        'rating': req.rating,
        'feedback_notes': req.feedback_notes,
        'feedback_at': req.feedback_at.strftime('%b %d, %Y %I:%M %p') if req.feedback_at else None,
        # Escalation
        'reported_not_fulfilled': req.reported_not_fulfilled,
        'report_reason': req.report_reason,
        'escalation': escalation_data,
        'timeline': timeline,
    }
    return JsonResponse({'success': True, 'request': data})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def admin_service_requests_poll_api(request):
    """
    Lightweight telemetry polling API for the Admin dashboard.
    Auto-detects SLA breaches, returns updated metric counts, and refreshed request cards.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    check_and_mark_sla_breaches(restaurant=restaurant)
    check_unaccepted_service_requests(restaurant=restaurant)

    base_qs = ServiceRequest.objects.all()
    if restaurant:
        base_qs = base_qs.filter(restaurant=restaurant)
    elif not request.user.is_superuser:
        base_qs = base_qs.none()

    active_statuses = [
        ServiceRequest.Status.PENDING,
        ServiceRequest.Status.ACCEPTED,
        ServiceRequest.Status.IN_PROGRESS
    ]

    total_count = base_qs.count()
    pending_count = base_qs.filter(status=ServiceRequest.Status.PENDING).count()
    accepted_count = base_qs.filter(status=ServiceRequest.Status.ACCEPTED).count()
    in_progress_count = base_qs.filter(status=ServiceRequest.Status.IN_PROGRESS).count()
    completed_count = base_qs.filter(status=ServiceRequest.Status.COMPLETED).count()
    unable_count = base_qs.filter(status=ServiceRequest.Status.UNABLE_TO_FULFIL).count()
    delayed_count = base_qs.filter(status__in=active_statuses, is_delayed=True).count()

    reported_count_qs = ServiceRequestEscalation.objects.filter(
        status__in=[
            ServiceRequestEscalation.EscalationStatus.NEW,
            ServiceRequestEscalation.EscalationStatus.UNDER_REVIEW,
            ServiceRequestEscalation.EscalationStatus.REASSIGNED
        ]
    )
    if restaurant:
        reported_count_qs = reported_count_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
    escalations_count = reported_count_qs.count()
    if escalations_count == 0:
        escalations_count = base_qs.filter(reported_not_fulfilled=True).count()

    avg_rating_agg = base_qs.filter(rating__isnull=False).aggregate(Avg('rating'))['rating__avg']
    avg_rating = round(avg_rating_agg, 1) if avg_rating_agg else None

    # Fetch active cards for live sync (re-notified cards come first)
    active_cards = []
    for r in _order_requests_with_renotice_first(base_qs.filter(status__in=active_statuses))[:40]:
        active_cards.append({
            'id': r.id,
            'request_number': r.request_number,
            'table_number': r.table.table_number,
            'type_display': r.get_request_type_display(),
            'status': r.status,
            'status_display': r.get_status_display(),
            'badge_class': r.badge_class,
            'waiting_duration': r.waiting_duration_display,
            'is_delayed': r.is_delayed,
            're_notice_count': r.re_notice_count,
            'assigned_waiter': r.assigned_waiter.get_full_name() if r.assigned_waiter else 'Unassigned',
        })

    recent_renotice_cutoff = timezone.now() - timezone.timedelta(seconds=60)
    renotice_req = base_qs.filter(
        status__in=active_statuses,
        re_notice_count__gt=0,
        last_renotice_at__gte=recent_renotice_cutoff
    ).order_by('-last_renotice_at').first()

    latest_renotice_data = None
    if renotice_req:
        latest_renotice_data = {
            'id': renotice_req.id,
            'request_number': renotice_req.request_number,
            'table_number': renotice_req.table.table_number,
            'customer_name': renotice_req.customer_name,
            'request_type': renotice_req.get_request_type_display(),
            're_notice_count': renotice_req.re_notice_count,
            'renotice_ts': int(renotice_req.last_renotice_at.timestamp() * 1000) if renotice_req.last_renotice_at else 0,
        }

    return JsonResponse({
        'success': True,
        'metrics': {
            'total': total_count,
            'pending': pending_count,
            'accepted': accepted_count,
            'in_progress': in_progress_count,
            'completed': completed_count,
            'unable': unable_count,
            'delayed': delayed_count,
            'reported': escalations_count,
            'avg_rating': avg_rating,
        },
        'active_requests': active_cards,
        'latest_renotice': latest_renotice_data,
    })


@login_required
@require_POST
def service_request_dismiss_renotice_api(request, request_id):
    """
    Staff / Waiter / Admin acknowledges and clears the customer re-notice notification on a card.
    Resets re_notice_count to 0 so the notification badge vanishes and card returns to normal order.
    Sends an apology popup message to that particular customer:
    "Sorry for the delay, your particular requirement will come soon"
    """
    req = get_tenant_object_or_404(ServiceRequest, request, id=request_id)
    prev_count = req.re_notice_count
    req.re_notice_count = 0
    req.is_seen = True
    req.staff_apology_message = f"Sorry for the delay, your {req.get_request_type_display()} will come soon!"
    req.staff_apology_at = timezone.now()
    req.save(update_fields=['re_notice_count', 'is_seen', 'staff_apology_message', 'staff_apology_at'])

    ServiceRequestAuditLog.objects.create(
        service_request=req,
        action='RENOTICE_DISMISSED',
        previous_status=req.status,
        new_status=req.status,
        performed_by=request.user,
        notes=f"Staff {request.user.get_full_name() or request.user.username} acknowledged customer re-notice ({prev_count}x). Apology notification sent to customer: '{req.staff_apology_message}'"
    )
    return JsonResponse({
        'success': True,
        'message': 'Notification acknowledged and cleared.',
        'request_id': req.id,
        're_notice_count': 0,
        'staff_apology_message': req.staff_apology_message,
        'staff_apology_at_ts': int(req.staff_apology_at.timestamp() * 1000) if req.staff_apology_at else None
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def admin_escalation_resolve_api(request, escalation_id):
    """
    Admin resolves or reassigns a guest escalation.
    """
    status = request.POST.get('status', 'RESOLVED').upper()
    notes = request.POST.get('resolution_notes', '').strip()
    reassign_waiter_id = request.POST.get('reassign_waiter_id')

    esc, success, msg = resolve_guest_escalation(
        escalation_id=escalation_id,
        status=status,
        resolution_notes=notes,
        resolved_by=request.user,
        reassign_waiter_id=reassign_waiter_id or None
    )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'success': success, 'message': msg})

    messages.success(request, msg)
    return redirect(request.META.get('HTTP_REFERER', 'tables:admin_service_requests'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def admin_sla_config_update(request):
    """
    Admin configures target minutes, warning threshold, and escalation limit per service type.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    request_type = request.POST.get('request_type')
    try:
        target_mins = int(request.POST.get('target_minutes', 4))
        warn_mins = int(request.POST.get('warning_minutes', 3))
        esc_mins = int(request.POST.get('escalation_minutes', 5))
        timeout_secs = int(request.POST.get('acceptance_timeout_seconds', 60))
    except (ValueError, TypeError):
        messages.error(request, "Invalid numeric values for SLA minutes.")
        return redirect('tables:admin_service_requests')

    cfg, _ = ServiceSLAConfig.objects.update_or_create(
        restaurant=restaurant,
        request_type=request_type,
        defaults={
            'target_minutes': max(1, target_mins),
            'warning_minutes': max(1, warn_mins),
            'escalation_minutes': max(1, esc_mins),
            'acceptance_timeout_seconds': max(10, timeout_secs),
            'is_active': True,
        }
    )
    messages.success(request, f"Updated SLA for {cfg.get_request_type_display()}: Target {cfg.target_minutes}m, Warning {cfg.warning_minutes}m, Breach {cfg.escalation_minutes}m.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:admin_service_requests'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def admin_floor_sections_view(request):
    """
    View, create, and manage Floor Sections & Table Section Assignments.
    """
    restaurant = getattr(request.user, 'restaurant', None)
    sections_qs = TableSection.objects.prefetch_related('tables', 'assigned_waiters')
    if restaurant:
        sections_qs = sections_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    form = TableSectionForm(request.POST or None, restaurant=restaurant)
    if request.method == 'POST' and form.is_valid():
        sec = form.save(commit=False)
        if restaurant:
            sec.restaurant = restaurant
        sec.save()
        form.save_m2m()
        messages.success(request, f"Floor Section '{sec.name}' saved successfully!")
        return redirect('tables:floor_sections')

    tables_qs = DiningTable.objects.filter(is_active=True).order_by('table_number')
    waiters_qs = User.objects.filter(role=User.Role.WAITER, is_active=True, is_suspended=False)
    if restaurant:
        tables_qs = tables_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
        waiters_qs = waiters_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    context = {
        'sections': sections_qs,
        'form': form,
        'tables': tables_qs,
        'waiters': waiters_qs,
    }
    return render(request, 'tables/floor_sections.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def admin_floor_section_delete(request, section_id):
    """
    Delete a Floor Section.
    """
    sec = get_tenant_object_or_404(TableSection, request, id=section_id)
    name = sec.name
    sec.delete()
    messages.success(request, f"Floor Section '{name}' deleted.")
    return redirect('tables:floor_sections')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def admin_waiter_duty_toggle(request, waiter_id):
    """
    Toggle a waiter's on_duty status live.
    When set to Off Duty, safely hands over any unaccepted pending requests.
    """
    waiter = get_tenant_object_or_404(User.objects.filter(role=User.Role.WAITER), request, id=waiter_id)
    if waiter.is_on_duty:
        handle_waiter_shift_end_or_logout(waiter, restaurant=getattr(waiter, 'restaurant', None))
    else:
        waiter.is_on_duty = True
        waiter.availability_status = User.AvailabilityStatus.AVAILABLE
        waiter.save(update_fields=['is_on_duty', 'availability_status'])

    status_str = "On Duty" if waiter.is_on_duty else "Off Duty"
    messages.success(request, f"Waiter {waiter.get_full_name() or waiter.username} is now {status_str}.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_assignments'))


@login_required
@role_required([User.Role.WAITER, User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def waiter_toggle_availability(request):
    """
    Waiter or Admin toggles waiter real-time availability:
    AVAILABLE, BUSY, ON_BREAK, OFF_DUTY.
    If OFF_DUTY, triggers automatic handover of unaccepted pending requests.
    """
    new_status = request.POST.get('status', '').upper().strip()
    waiter_id = request.POST.get('waiter_id')

    target_user = request.user
    if waiter_id and request.user.role in [User.Role.ADMIN, User.Role.MANAGER]:
        target_user = get_tenant_object_or_404(User.objects.filter(role=User.Role.WAITER), request, id=waiter_id)

    valid_statuses = [
        User.AvailabilityStatus.AVAILABLE,
        User.AvailabilityStatus.BUSY,
        User.AvailabilityStatus.ON_BREAK,
        User.AvailabilityStatus.OFF_DUTY,
    ]
    if new_status not in valid_statuses:
        return JsonResponse({'success': False, 'message': 'Invalid availability status.'}, status=400)

    if new_status == User.AvailabilityStatus.OFF_DUTY:
        handle_waiter_shift_end_or_logout(target_user, restaurant=getattr(target_user, 'restaurant', None))
    else:
        target_user.is_on_duty = True
        target_user.availability_status = new_status
        target_user.save(update_fields=['is_on_duty', 'availability_status'])

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('format') == 'json':
        return JsonResponse({
            'success': True,
            'status': target_user.availability_status,
            'status_display': target_user.get_availability_status_display(),
            'is_on_duty': target_user.is_on_duty,
            'message': f"Availability updated to {target_user.get_availability_status_display()}."
        })

    messages.success(request, f"Availability set to {target_user.get_availability_status_display()}.")
    return redirect(request.META.get('HTTP_REFERER', 'tables:waiter_requests'))


# ============================================================================
# 4. WAITER TABLE ASSIGNMENT MANAGEMENT
# ============================================================================

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def admin_waiter_assignments_view(request):
    """
    Manage Waiter <-> Dining Table and Section Assignments.
    Shows current floor assignments, server duty status, and allows assigning/reassigning tables.
    """
    restaurant = getattr(request.user, 'restaurant', None)

    assignments_qs = WaiterTableAssignment.objects.select_related('waiter', 'table', 'section', 'restaurant').order_by('table__table_number')
    waiters_qs = User.objects.filter(role=User.Role.WAITER, is_active=True, is_suspended=False)
    tables_qs = DiningTable.objects.filter(is_active=True).order_by('table_number')
    sections_qs = TableSection.objects.filter(is_active=True).prefetch_related('tables', 'assigned_waiters')

    if restaurant:
        assignments_qs = assignments_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
        waiters_qs = waiters_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
        tables_qs = tables_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))
        sections_qs = sections_qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    form = WaiterAssignmentForm(request.POST or None, restaurant=restaurant)
    if request.method == 'POST' and form.is_valid():
        assignment = form.save(commit=False)
        if restaurant:
            assignment.restaurant = restaurant
        assignment.save()
        messages.success(request, f"Assigned Table {assignment.table.table_number} to {assignment.waiter.get_full_name() or assignment.waiter.username}.")
        return redirect('tables:waiter_assignments')

    # Waiter workload stats
    waiter_workloads = []
    for w in waiters_qs:
        active_req_count = ServiceRequest.objects.filter(
            assigned_waiter=w,
            status__in=[ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]
        ).count()
        assigned_tables = WaiterTableAssignment.objects.filter(waiter=w, is_active=True).select_related('table')
        assigned_sections = w.assigned_sections.filter(is_active=True)
        waiter_workloads.append({
            'waiter': w,
            'active_requests': active_req_count,
            'assigned_tables': [a.table.table_number for a in assigned_tables],
            'assigned_sections': [s.name for s in assigned_sections],
            'is_on_duty': w.is_on_duty,
        })

    context = {
        'assignments': assignments_qs,
        'form': form,
        'waiter_workloads': waiter_workloads,
        'sections': sections_qs,
        'total_assignments': assignments_qs.count(),
        'total_tables': tables_qs.count(),
        'total_waiters': waiters_qs.count(),
    }
    return render(request, 'tables/waiter_assignments.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER])
@require_POST
def admin_waiter_assignment_delete(request, assignment_id):
    """
    Remove an existing Waiter <-> Table assignment.
    """
    assignment = get_tenant_object_or_404(WaiterTableAssignment, request, id=assignment_id)
    table_num = assignment.table.table_number
    waiter_name = assignment.waiter.get_full_name() or assignment.waiter.username
    assignment.delete()
    messages.success(request, f"Removed Table {table_num} assignment from {waiter_name}.")
    return redirect('tables:waiter_assignments')
