import logging
from decimal import Decimal
from django.db import models, transaction
from django.db.models import Q, Count
from django.utils import timezone
from accounts.models import User
from .models import (
    DiningTable,
    RestaurantSeat,
    CustomerSession,
    TableSection,
    WaiterTableAssignment,
    ServiceRequest,
    ServiceRequestAuditLog,
    ServiceSLAConfig,
    ServiceRequestEscalation,
    TableWaitingQueue,
)

logger = logging.getLogger(__name__)


# ============================================================================
# MODULE 0: TABLE RESOLUTION & MULTI-TENANT LOOKUP ENGINE
# ============================================================================

def get_dining_table_or_404(request, table_number, is_active=None):
    """
    Safely resolves a DiningTable instance by table_number in both single-tenant
    and multi-tenant environments without ever throwing MultipleObjectsReturned.

    Resolution hierarchy:
    1. Direct QR token (GET/POST 'token' query param) -> globally unique on DiningTable.
    2. URL kwargs or request parameter 'session_token' -> globally unique on CustomerSession.
    3. Session cached table ID (e.g. active_table_<table_number>_id or active_table_id).
    4. Active session/member cookies (e.g. rms_session_<id>, rms_member_<id>).
    5. Active restaurant tenant context (request.restaurant, user.restaurant, get_current_restaurant).
    6. Exact table_number match:
       - If exactly 1 table matches: returns it.
       - If multiple tables match across tenants/orphans:
           a. Prioritizes table matching current restaurant tenant if known.
           b. Prioritize table with active sessions matching cookie session tokens.
           c. Deterministic fallback: prioritizes tables with valid restaurants, active status, lowest ID.
       - If 0 matches: raises Http404.
    """
    from django.http import Http404
    from accounts.tenant_utils import get_current_restaurant

    cleaned_number = str(table_number).strip() if table_number is not None else ''
    if not cleaned_number:
        raise Http404("Table number not specified.")

    base_qs = DiningTable.objects.all().select_related('restaurant')
    if is_active is not None:
        base_qs = base_qs.filter(is_active=is_active)

    # 1. Direct QR token check (globally unique on DiningTable)
    token = None
    if request:
        if hasattr(request, 'GET'):
            token = request.GET.get('token')
        if not token and hasattr(request, 'POST'):
            token = request.POST.get('token')
    if token:
        table_by_token = base_qs.filter(qr_token=token).first()
        if table_by_token:
            session = getattr(request, 'session', None)
            if session is not None:
                session[f'active_table_{cleaned_number}_id'] = table_by_token.id
            return table_by_token

    # 2. Check session_token in URL kwargs, query params, or POST body
    session_token = None
    if request:
        resolver_match = getattr(request, 'resolver_match', None)
        if resolver_match and getattr(resolver_match, 'kwargs', None):
            session_token = resolver_match.kwargs.get('session_token')
        if not session_token and hasattr(request, 'GET'):
            session_token = request.GET.get('session_token')
        if not session_token and hasattr(request, 'POST'):
            session_token = request.POST.get('session_token')

    if session_token:
        matched_session = CustomerSession.objects.filter(
            session_token=session_token
        ).select_related('table__restaurant').first()
        if matched_session and matched_session.table:
            table = matched_session.table
            if is_active is None or table.is_active == is_active:
                session = getattr(request, 'session', None)
                if session is not None:
                    session[f'active_table_{cleaned_number}_id'] = table.id
                return table

    # 3. Check cached table ID in Django session
    session = getattr(request, 'session', None) if request else None
    if session is not None:
        cached_id = session.get(f'active_table_{cleaned_number}_id') or session.get('active_table_id')
        if cached_id:
            cached_table = base_qs.filter(id=cached_id).first()
            if cached_table and cached_table.table_number.strip().lower() == cleaned_number.lower():
                return cached_table

    # 4. Check cookies for active table ID (rms_session_<id>, rms_member_<id>)
    cookies = getattr(request, 'COOKIES', {}) if request else {}
    if cookies:
        cookie_table_ids = []
        cookie_session_tokens = []
        for k, v in cookies.items():
            if k.startswith('rms_session_') or k.startswith('rms_member_'):
                try:
                    tid = int(k.split('_')[-1])
                    cookie_table_ids.append(tid)
                except (ValueError, TypeError):
                    pass
                if k.startswith('rms_session_') and v:
                    cookie_session_tokens.append(v)

        if cookie_session_tokens:
            session_obj = CustomerSession.objects.filter(
                session_token__in=cookie_session_tokens,
                table__table_number__iexact=cleaned_number
            ).select_related('table__restaurant').first()
            if session_obj and session_obj.table:
                table = session_obj.table
                if is_active is None or table.is_active == is_active:
                    if session is not None:
                        session[f'active_table_{cleaned_number}_id'] = table.id
                    return table

        if cookie_table_ids:
            cookie_table = base_qs.filter(id__in=cookie_table_ids, table_number__iexact=cleaned_number).first()
            if cookie_table:
                if session is not None:
                    session[f'active_table_{cleaned_number}_id'] = cookie_table.id
                return cookie_table

    # 5. Check tenant / restaurant context
    restaurant = None
    if request:
        try:
            restaurant = get_current_restaurant(request)
        except Exception:
            restaurant = None
        if not restaurant and hasattr(request, 'restaurant') and request.restaurant:
            restaurant = request.restaurant
        user = getattr(request, 'user', None)
        if not restaurant and user and getattr(user, 'is_authenticated', False):
            restaurant = getattr(user, 'restaurant', None)
        if not restaurant and hasattr(request, 'GET'):
            r_code = request.GET.get('restaurant_code') or request.GET.get('code')
            r_id = request.GET.get('restaurant_id') or request.GET.get('restaurant')
            if r_code:
                from accounts.models import Restaurant
                restaurant = Restaurant.objects.filter(code__iexact=r_code.strip()).first()
            elif r_id and str(r_id).isdigit():
                from accounts.models import Restaurant
                restaurant = Restaurant.objects.filter(id=int(r_id)).first()

    candidates = base_qs.filter(table_number__iexact=cleaned_number)

    # If tenant is known, filter to this restaurant
    if restaurant:
        tenant_match = candidates.filter(restaurant=restaurant).first()
        if tenant_match:
            if session is not None:
                session[f'active_table_{cleaned_number}_id'] = tenant_match.id
            return tenant_match

    # 6. Fallback across all matching tables
    candidate_count = candidates.count()
    if candidate_count == 1:
        table = candidates.first()
        if session is not None:
            session[f'active_table_{cleaned_number}_id'] = table.id
        return table
    elif candidate_count > 1:
        # Multiple tables exist with this table_number across restaurants/orphans.
        # Prefer table that has an active or bill-requested customer session
        active_sess_table = candidates.filter(
            sessions__status__in=[CustomerSession.Status.ACTIVE, CustomerSession.Status.BILL_REQUESTED]
        ).first()
        if active_sess_table:
            table = active_sess_table
        else:
            table = candidates.order_by(
                models.F('restaurant').desc(nulls_last=True),
                '-is_active',
                'id'
            ).first()
        if session is not None:
            session[f'active_table_{cleaned_number}_id'] = table.id
        return table

    # If none found and is_active was enforced, check if an inactive table exists
    if is_active is not None:
        fallback_inactive = DiningTable.objects.filter(table_number__iexact=cleaned_number).select_related('restaurant').first()
        if fallback_inactive:
            if session is not None:
                session[f'active_table_{cleaned_number}_id'] = fallback_inactive.id
            return fallback_inactive

    raise Http404(f"Table '{cleaned_number}' not found.")



# ============================================================================
# MODULE 1: SMART WAITER ASSISTANCE & ROUTING SERVICES
# ============================================================================

def get_assigned_waiter_for_table(table, restaurant=None, exclude_waiter_id=None):
    """
    Smart Waiter Routing Engine:
    1. Checks if the table belongs to a TableSection with assigned on-duty & available waiters,
       and automatically selects the one with the lowest active request load.
    2. Checks if a waiter is directly assigned to this table and is currently on duty & available.
    3. If no direct/section assignment or assigned waiter is off duty, looks for on-duty available waiters
       in the same restaurant and automatically selects the one with the lowest active request load.
    4. If no on-duty waiters exist, returns None (Admin/Supervisor review required).
    """
    if not table:
        return None

    if not restaurant and table.restaurant:
        restaurant = table.restaurant

    active_statuses = [
        ServiceRequest.Status.PENDING,
        ServiceRequest.Status.ACCEPTED,
        ServiceRequest.Status.IN_PROGRESS
    ]

    # 1. Section-based assignment check
    section = TableSection.objects.filter(tables=table, is_active=True).first()
    if section:
        section_waiters = section.assigned_waiters.filter(
            is_active=True,
            is_suspended=False,
            is_on_duty=True,
            availability_status=User.AvailabilityStatus.AVAILABLE
        )
        if exclude_waiter_id:
            section_waiters = section_waiters.exclude(id=exclude_waiter_id)
        if restaurant:
            section_waiters = section_waiters.filter(restaurant=restaurant)
        if section_waiters.exists():
            best_sec_waiter = section_waiters.annotate(
                active_load=Count(
                    'assigned_service_requests',
                    filter=models.Q(assigned_service_requests__status__in=active_statuses)
                )
            ).order_by('active_load', 'id').first()
            if best_sec_waiter:
                return best_sec_waiter

    # 2. Direct Table Assignment Check
    direct_assignments = WaiterTableAssignment.objects.filter(
        table=table,
        is_active=True,
        waiter__is_active=True,
        waiter__is_suspended=False,
        waiter__is_on_duty=True,
        waiter__availability_status=User.AvailabilityStatus.AVAILABLE
    ).select_related('waiter')

    if exclude_waiter_id:
        direct_assignments = direct_assignments.exclude(waiter_id=exclude_waiter_id)

    if restaurant:
        direct_assignments = direct_assignments.filter(restaurant=restaurant)

    assigned_obj = direct_assignments.first()
    if assigned_obj and assigned_obj.waiter:
        return assigned_obj.waiter

    # 3. Dynamic Workload-Based Fallback to on-duty available waiters in the same restaurant
    waiters_qs = User.objects.filter(
        role=User.Role.WAITER,
        is_active=True,
        is_suspended=False,
        is_on_duty=True,
        availability_status=User.AvailabilityStatus.AVAILABLE
    )
    if exclude_waiter_id:
        waiters_qs = waiters_qs.exclude(id=exclude_waiter_id)

    if restaurant:
        waiters_qs = waiters_qs.filter(restaurant=restaurant)
    else:
        return None

    # Annotate with active pending/in-progress service requests count to balance workload
    waiters_by_load = waiters_qs.annotate(
        active_load=Count(
            'assigned_service_requests',
            filter=models.Q(assigned_service_requests__status__in=active_statuses)
        )
    ).order_by('active_load', 'id')

    best_waiter = waiters_by_load.first()
    return best_waiter


def create_service_request(
    table,
    session,
    request_type,
    request_category=None,
    quantity=1,
    unit='',
    custom_message='',
    priority=ServiceRequest.Priority.NORMAL,
    member=None,
    customer_name=None
):
    """
    Safely creates a new customer service request with rate-limiting & smart waiter assignment.
    Supports request categorization, quantity, unit, priority, and time estimation.
    """
    restaurant = getattr(table, 'restaurant', None)
    if not restaurant and session.table and session.table.restaurant:
        restaurant = session.table.restaurant

    try:
        qty = max(1, int(quantity))
    except (ValueError, TypeError):
        qty = 1

    NO_QUANTITY_TYPES = {
        ServiceRequest.RequestType.OTHER,
        ServiceRequest.RequestType.BILL,
        ServiceRequest.RequestType.CLARIFY_BILL,
        ServiceRequest.RequestType.PAYMENT_ASSIST,
        ServiceRequest.RequestType.REMOVE_PLATES,
        ServiceRequest.RequestType.WAITER,
        ServiceRequest.RequestType.HELP_MENU,
        ServiceRequest.RequestType.FOOD_INFO,
        ServiceRequest.RequestType.CUSTOM_ASSIST,
        ServiceRequest.RequestType.CLEANING,
        ServiceRequest.RequestType.CLEAN_TABLE,
        ServiceRequest.RequestType.ARRANGE_TABLE,
        ServiceRequest.RequestType.CLEAN_SPILL,
        ServiceRequest.RequestType.TISSUE,
        ServiceRequest.RequestType.SAUCE,
        ServiceRequest.RequestType.SAUCE_OTHER,
        ServiceRequest.RequestType.KETCHUP,
        ServiceRequest.RequestType.MAYONNAISE,
        ServiceRequest.RequestType.CHILLI_SAUCE,
    }
    if request_type in NO_QUANTITY_TYPES:
        qty = 1
        unit = ''

    # If an active request already exists for this table, session and type, do not create a separate card.
    # Instead, update the existing request on the SAME card as a re-notice!
    existing_active = ServiceRequest.objects.filter(
        table=table,
        session=session,
        request_type=request_type,
        status__in=[ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]
    ).order_by('-requested_at').first()

    if existing_active:
        now = timezone.now()
        # Only increment re-notice if not within rapid 10s double-tap
        if (now - existing_active.requested_at).total_seconds() > 10:
            existing_active.re_notice_count += 1
            existing_active.last_renotice_at = now
            existing_active.is_seen = False
            if custom_message and not existing_active.custom_message:
                existing_active.custom_message = custom_message.strip()[:255]
            existing_active.save(update_fields=['re_notice_count', 'last_renotice_at', 'is_seen', 'custom_message'])

            ServiceRequestAuditLog.objects.create(
                service_request=existing_active,
                action='CUSTOMER_RENOTICED',
                previous_status=existing_active.status,
                new_status=existing_active.status,
                performed_by=None,
                notes=f"Customer re-notified (Count: {existing_active.re_notice_count}) for '{existing_active.get_request_type_display()}'. Updated on same card."
            )
            broadcast_service_request_event(existing_active, event_type='renotice')
        return existing_active, False

    assigned_waiter = get_assigned_waiter_for_table(table, restaurant=restaurant)
    name = customer_name or (member.name if member else session.customer_name) or 'Guest'
    section = TableSection.objects.filter(tables=table, is_active=True).first()

    # Look up configured SLA target
    sla_cfg = ServiceSLAConfig.objects.filter(
        Q(restaurant=restaurant) | Q(restaurant__isnull=True),
        request_type=request_type,
        is_active=True
    ).order_by('-restaurant_id').first()

    if sla_cfg:
        estimated_mins = sla_cfg.target_minutes
    else:
        # Estimate duration based on historical requests or standard benchmarks
        hist_avg = ServiceRequest.objects.filter(
            restaurant=restaurant,
            request_type=request_type,
            status=ServiceRequest.Status.COMPLETED,
            completed_at__isnull=False
        ).exclude(requested_at__isnull=True)[:50]

        estimated_mins = 4
        if hist_avg.exists():
            durations = [(r.completed_at - r.requested_at).total_seconds() / 60.0 for r in hist_avg if r.completed_at > r.requested_at]
            if durations:
                avg_m = int(round(sum(durations) / len(durations)))
                estimated_mins = max(2, min(15, avg_m))
        else:
            if request_type in [ServiceRequest.RequestType.WATER, ServiceRequest.RequestType.WATER_COLD, ServiceRequest.RequestType.WATER_NORMAL]:
                estimated_mins = 3
            elif request_type in [ServiceRequest.RequestType.TISSUE]:
                estimated_mins = 2
            elif request_type in [ServiceRequest.RequestType.CLEANING, ServiceRequest.RequestType.CLEAN_TABLE]:
                estimated_mins = 6
            elif request_type in [ServiceRequest.RequestType.BILL]:
                estimated_mins = 4

    with transaction.atomic():
        service_req = ServiceRequest.objects.create(
            restaurant=restaurant,
            table=table,
            session=session,
            member=member,
            table_section=section,
            customer_name=name,
            request_category=request_category or ServiceRequest.RequestCategory.WAITER_ASSIST,
            request_type=request_type,
            quantity=qty,
            unit=unit or '',
            custom_message=custom_message.strip()[:255],
            priority=priority or ServiceRequest.Priority.NORMAL,
            assigned_waiter=assigned_waiter,
            assigned_at=timezone.now() if assigned_waiter else None,
            status=ServiceRequest.Status.PENDING,
            estimated_duration_minutes=estimated_mins
        )

        # Create initial audit log
        ServiceRequestAuditLog.objects.create(
            service_request=service_req,
            action='REQUEST_CREATED',
            previous_status='',
            new_status=ServiceRequest.Status.PENDING,
            performed_by=None,
            notes=f"Customer submitted '{service_req.get_request_type_display()}' (Qty: {qty}). Priority: {service_req.get_priority_display()}. Assigned to: {assigned_waiter.get_full_name() if assigned_waiter else 'Unassigned'}"
        )

        if not assigned_waiter:
            ServiceRequestAuditLog.objects.create(
                service_request=service_req,
                action='UNASSIGNED_PENDING',
                previous_status='',
                new_status=ServiceRequest.Status.PENDING,
                performed_by=None,
                notes="No waiter is currently available. This request requires attention."
            )

    # Broadcast real-time update
    broadcast_service_request_event(service_req, event_type='new_request')
    return service_req, True


def update_service_request_status(service_req_id, new_status, user=None, reason='', alternative_suggestion=''):
    """
    Atomic status transition for a ServiceRequest with row locking (select_for_update).
    Prevents race conditions (e.g. two waiters accepting the same request simultaneously).
    Supports Unable to Fulfil with custom reason and alternative suggestions.
    """
    with transaction.atomic():
        locked_req = ServiceRequest.objects.select_for_update().get(id=service_req_id)
        prev_status = locked_req.status

        # Prevent invalid transitions or double acceptance
        if prev_status == new_status:
            return locked_req, False, "Request already in this status."

        if prev_status in [ServiceRequest.Status.COMPLETED, ServiceRequest.Status.CANCELLED, ServiceRequest.Status.UNABLE_TO_FULFIL]:
            return locked_req, False, f"Cannot update a {prev_status.lower()} request."

        now = timezone.now()
        if new_status == ServiceRequest.Status.ACCEPTED:
            if prev_status != ServiceRequest.Status.PENDING:
                return locked_req, False, f"Cannot accept a request that is {prev_status.lower()}."
            locked_req.status = ServiceRequest.Status.ACCEPTED
            locked_req.accepted_by = user
            locked_req.accepted_at = now
            if not locked_req.assigned_waiter and user:
                locked_req.assigned_waiter = user

        elif new_status == ServiceRequest.Status.IN_PROGRESS:
            locked_req.status = ServiceRequest.Status.IN_PROGRESS
            locked_req.in_progress_at = now
            if not locked_req.accepted_by and user:
                locked_req.accepted_by = user
                locked_req.accepted_at = now

        elif new_status == ServiceRequest.Status.COMPLETED:
            locked_req.status = ServiceRequest.Status.COMPLETED
            locked_req.completed_by = user
            locked_req.completed_at = now
            if locked_req.is_sla_breached:
                locked_req.is_delayed = True
                if not locked_req.sla_breached_at:
                    locked_req.sla_breached_at = now

        elif new_status == ServiceRequest.Status.UNABLE_TO_FULFIL:
            if not reason or not reason.strip():
                return locked_req, False, "Please provide a valid reason why this request cannot be fulfilled."
            locked_req.status = ServiceRequest.Status.UNABLE_TO_FULFIL
            locked_req.completed_by = user
            locked_req.completed_at = now
            locked_req.unable_to_fulfil_reason = reason.strip()
            locked_req.alternative_suggestion = alternative_suggestion.strip() or ''

        elif new_status == ServiceRequest.Status.CANCELLED:
            locked_req.status = ServiceRequest.Status.CANCELLED
            locked_req.cancelled_at = now
            locked_req.cancellation_reason = reason.strip() or "Cancelled by staff/customer"

        locked_req.is_seen = True
        locked_req.save()

        # Audit Log
        ServiceRequestAuditLog.objects.create(
            service_request=locked_req,
            action=f"STATUS_{new_status}",
            previous_status=prev_status,
            new_status=new_status,
            performed_by=user,
            notes=reason or f"Status changed to {new_status} by {user.get_full_name() if user else 'System'}"
        )

    broadcast_service_request_event(locked_req, event_type='status_updated')
    return locked_req, True, "Status updated successfully."


def repeat_service_request(original_request_id, session, member=None):
    """
    Allows a customer to repeat an earlier completed request (e.g. 2 glasses of water again).
    Creates a new independent ServiceRequest record without overwriting original history.
    """
    orig = ServiceRequest.objects.get(id=original_request_id, session=session)
    return create_service_request(
        table=orig.table,
        session=session,
        request_type=orig.request_type,
        request_category=orig.request_category,
        quantity=orig.quantity,
        unit=orig.unit,
        custom_message=orig.custom_message,
        priority=orig.priority,
        member=member or orig.member,
        customer_name=orig.customer_name
    )


def submit_service_request_feedback(service_req_id, session, rating, feedback_notes=''):
    """
    Saves optional 1-5 star customer feedback on a completed service request.
    """
    with transaction.atomic():
        req = ServiceRequest.objects.select_for_update().filter(id=service_req_id, session=session).first()
        if not req:
            return None, False, "Service request not found."
        if req.status != ServiceRequest.Status.COMPLETED:
            return req, False, "Can only rate completed requests."

        try:
            r = max(1, min(5, int(rating)))
        except (ValueError, TypeError):
            r = 5

        req.rating = r
        req.feedback_notes = feedback_notes.strip()[:500]
        req.feedback_at = timezone.now()
        req.save(update_fields=['rating', 'feedback_notes', 'feedback_at'])

        ServiceRequestAuditLog.objects.create(
            service_request=req,
            action='FEEDBACK_SUBMITTED',
            previous_status=req.status,
            new_status=req.status,
            performed_by=None,
            notes=f"Customer rated {r}/5 stars: {feedback_notes.strip()[:100]}"
        )

    return req, True, "Thank you for your feedback!"


def renotice_service_request(service_req_id, session):
    """
    Customer re-notifies / reminds staff about an active service request that has been waiting for 2+ minutes.
    Atomically updates re_notice_count, sets last_renotice_at, resets is_seen=False (so staff badges/notifications re-pop),
    logs the event in audit trail, and broadcasts the renotice event.
    """
    with transaction.atomic():
        req = ServiceRequest.objects.select_for_update().filter(id=service_req_id, session=session).first()
        if not req:
            return None, False, "Service request not found."

        if req.status not in [ServiceRequest.Status.PENDING, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]:
            return req, False, f"Cannot re-notify for a request that is {req.get_status_display().lower()}."

        # Verify request has been waiting for at least 2 minutes (120 seconds)
        waiting_sec = req.waiting_duration_seconds
        if waiting_sec < 120:
            remaining = 120 - waiting_sec
            return req, False, f"Re-notice available after 2 minutes of waiting. ({remaining}s remaining)"

        # Prevent re-notice within 3-minute (180 seconds) cooldown of previous re-notice
        now = timezone.now()
        if req.last_renotice_at and (now - req.last_renotice_at).total_seconds() < 180:
            sec_left = int(180 - (now - req.last_renotice_at).total_seconds())
            m = sec_left // 60
            s = sec_left % 60
            return req, False, f"Notification sent successfully! You can send another notification in {m:02d}:{s:02d}."

        req.re_notice_count += 1
        req.last_renotice_at = now
        req.is_seen = False  # Reset so staff notification counts/badges re-pop
        req.save(update_fields=['re_notice_count', 'last_renotice_at', 'is_seen'])

        assigned_str = req.assigned_waiter.get_full_name() if req.assigned_waiter else 'Staff'
        ServiceRequestAuditLog.objects.create(
            service_request=req,
            action='CUSTOMER_RENOTICED',
            previous_status=req.status,
            new_status=req.status,
            performed_by=None,
            notes=f"Customer sent reminder #{req.re_notice_count} after waiting {req.waiting_duration_display}. Re-alerted {assigned_str}."
        )

    # Broadcast real-time renotice event
    broadcast_service_request_event(req, event_type='renotice')
    return req, True, "Notification sent successfully! You can send another notification in 3 minutes."


def create_guest_escalation(service_req_id, session, complaint_notes=''):
    """
    Creates a formal Guest Escalation record for a service request.
    Prevents accidental duplicate escalation submissions.
    """
    with transaction.atomic():
        req = ServiceRequest.objects.select_for_update().filter(id=service_req_id, session=session).first()
        if not req:
            return None, False, "Service request not found."
        if req.status != ServiceRequest.Status.COMPLETED:
            return req, False, "Escalations can only be reported for completed requests."

        # Check if open escalation already exists
        existing = ServiceRequestEscalation.objects.filter(
            service_request=req,
            status__in=[
                ServiceRequestEscalation.EscalationStatus.NEW,
                ServiceRequestEscalation.EscalationStatus.UNDER_REVIEW,
                ServiceRequestEscalation.EscalationStatus.REASSIGNED
            ]
        ).first()
        if existing:
            return existing, False, "An escalation is already open for this request."

        reason_text = complaint_notes.strip() or "Customer reported item/service was not received."

        escalation = ServiceRequestEscalation.objects.create(
            restaurant=req.restaurant,
            service_request=req,
            table=req.table,
            session=session,
            customer_name=req.customer_name or session.customer_name,
            original_waiter=req.assigned_waiter or req.accepted_by,
            assigned_staff=None,
            complaint_notes=reason_text,
            status=ServiceRequestEscalation.EscalationStatus.NEW
        )

        req.reported_not_fulfilled = True
        req.report_reason = reason_text
        req.reported_at = timezone.now()
        req.save(update_fields=['reported_not_fulfilled', 'report_reason', 'reported_at'])

        ServiceRequestAuditLog.objects.create(
            service_request=req,
            action='GUEST_ESCALATION_FILED',
            previous_status=req.status,
            new_status=req.status,
            performed_by=None,
            notes=f"Escalation {escalation.escalation_number} opened: {reason_text}"
        )

    broadcast_service_request_event(req, event_type='reported_not_fulfilled')
    return escalation, True, f"Escalation #{escalation.escalation_number} has been logged and escalated to management."


def report_service_request_not_fulfilled(service_req_id, session, reason=''):
    """
    Guest escalation entry point: Customer reports that a request marked 'Completed' was not actually received.
    Delegates to atomic create_guest_escalation.
    """
    escalation, success, msg = create_guest_escalation(service_req_id, session, complaint_notes=reason)
    return escalation, success, msg


def resolve_guest_escalation(escalation_id, status, resolution_notes='', resolved_by=None, reassign_waiter_id=None):
    """
    Admin resolves or updates a Guest Escalation.
    Can reassign to a new waiter or supervisor and records full audit trail.
    """
    with transaction.atomic():
        esc = ServiceRequestEscalation.objects.select_for_update().get(id=escalation_id)
        esc.status = status
        esc.resolution_notes = resolution_notes.strip()
        esc.resolved_by = resolved_by
        if status in [ServiceRequestEscalation.EscalationStatus.RESOLVED, ServiceRequestEscalation.EscalationStatus.CLOSED]:
            esc.resolved_at = timezone.now()

        if reassign_waiter_id:
            new_w = User.objects.filter(id=reassign_waiter_id, role=User.Role.WAITER).first()
            if new_w:
                esc.assigned_staff = new_w
                esc.status = ServiceRequestEscalation.EscalationStatus.REASSIGNED
                reassign_service_request(
                    esc.service_request_id,
                    new_waiter_id=new_w.id,
                    performed_by=resolved_by,
                    reason=f"Escalation #{esc.escalation_number} reassignment: {resolution_notes.strip()}"
                )

        esc.save()

        ServiceRequestAuditLog.objects.create(
            service_request=esc.service_request,
            action=f"ESCALATION_{status}",
            previous_status=esc.service_request.status,
            new_status=esc.service_request.status,
            performed_by=resolved_by,
            notes=f"Escalation #{esc.escalation_number} status updated to {status}. Notes: {esc.resolution_notes}"
        )

    broadcast_service_request_event(esc.service_request, event_type='escalation_updated')
    return esc, True, f"Escalation #{esc.escalation_number} updated to {esc.get_status_display()}."


def reassign_service_request(service_req_id, new_waiter_id, performed_by=None, reason=''):
    """
    Admin reassigns a service request to another waiter.
    Validates tenant security, eligibility, workload, logs audit trail, and broadcasts event.
    """
    with transaction.atomic():
        req = ServiceRequest.objects.select_for_update().get(id=service_req_id)
        if req.status in [ServiceRequest.Status.COMPLETED, ServiceRequest.Status.CANCELLED]:
            return req, False, f"Cannot reassign a {req.get_status_display().lower()} request."

        if not reason or not reason.strip():
            return req, False, "Reason for reassignment is mandatory."

        prev_waiter = req.assigned_waiter
        new_waiter = None
        if new_waiter_id:
            new_waiter = User.objects.filter(id=new_waiter_id, role=User.Role.WAITER, is_active=True).first()
            if not new_waiter:
                return req, False, "Selected waiter is not eligible or active."
            if req.restaurant and new_waiter.restaurant and req.restaurant != new_waiter.restaurant:
                return req, False, "Selected waiter does not belong to this restaurant."

        req.reassigned_from = prev_waiter
        req.assigned_waiter = new_waiter
        req.assigned_at = timezone.now() if new_waiter else None
        req.reassignment_reason = reason.strip()
        req.reassigned_by = performed_by
        req.reassigned_at = timezone.now()
        req.save(update_fields=[
            'assigned_waiter', 'assigned_at', 'reassigned_from', 'reassignment_reason',
            'reassigned_by', 'reassigned_at'
        ])

        prev_name = prev_waiter.get_full_name() if prev_waiter else 'Unassigned'
        new_name = new_waiter.get_full_name() if new_waiter else 'Unassigned'
        by_name = performed_by.get_full_name() if performed_by else 'Admin'

        ServiceRequestAuditLog.objects.create(
            service_request=req,
            action='REASSIGNED_WAITER',
            previous_status=req.status,
            new_status=req.status,
            performed_by=performed_by,
            notes=f"Reassigned from {prev_name} to {new_name} by {by_name}. Reason: {req.reassignment_reason}"
        )

    broadcast_service_request_event(req, event_type='reassigned')
    return req, True, f"Request #{req.request_number} successfully reassigned to {new_name}."


def check_unaccepted_service_requests(restaurant=None):
    """
    Automatic Acceptance Monitoring & Reassignment Engine:
    Monitors unaccepted PENDING requests assigned to a waiter.
    If the request has been assigned longer than the configured timeout (default 60s),
    automatically reassigns to the next eligible on-duty available waiter in the restaurant.
    If no other waiter is available, preserves request in queue and logs audit alert.
    """
    now = timezone.now()
    pending_assigned = ServiceRequest.objects.filter(
        status=ServiceRequest.Status.PENDING,
        assigned_waiter__isnull=False
    ).select_related('table', 'restaurant', 'assigned_waiter')

    if restaurant:
        pending_assigned = pending_assigned.filter(
            Q(restaurant=restaurant) | Q(restaurant__isnull=True)
        )

    reassigned_items = []
    for req in pending_assigned:
        cfg = ServiceSLAConfig.objects.filter(
            request_type=req.request_type,
            is_active=True
        )
        if req.restaurant:
            cfg = cfg.filter(Q(restaurant=req.restaurant) | Q(restaurant__isnull=True))
        cfg_obj = cfg.order_by('-restaurant_id').first()
        timeout_seconds = cfg_obj.acceptance_timeout_seconds if cfg_obj else 60

        assigned_time = req.assigned_at or req.requested_at
        elapsed_seconds = (now - assigned_time).total_seconds()

        if elapsed_seconds >= timeout_seconds:
            new_waiter = get_assigned_waiter_for_table(
                req.table,
                restaurant=req.restaurant,
                exclude_waiter_id=req.assigned_waiter_id
            )
            if new_waiter and new_waiter.id != req.assigned_waiter_id:
                with transaction.atomic():
                    locked = ServiceRequest.objects.select_for_update().get(id=req.id)
                    if locked.status == ServiceRequest.Status.PENDING and locked.assigned_waiter_id == req.assigned_waiter_id:
                        prev_w = locked.assigned_waiter
                        locked.reassigned_from = prev_w
                        locked.assigned_waiter = new_waiter
                        locked.assigned_at = now
                        locked.reassigned_at = now
                        locked.reassigned_by = None
                        locked.reassignment_reason = f"Automatic acceptance timeout ({int(elapsed_seconds)}s >= {timeout_seconds}s)"
                        locked.save(update_fields=[
                            'assigned_waiter', 'assigned_at', 'reassigned_from',
                            'reassigned_at', 'reassigned_by', 'reassignment_reason'
                        ])

                        ServiceRequestAuditLog.objects.create(
                            service_request=locked,
                            action='AUTO_TIMEOUT_REASSIGNMENT',
                            previous_status=locked.status,
                            new_status=locked.status,
                            performed_by=None,
                            notes=f"Auto-reassigned from {prev_w.username} to {new_waiter.username} after {int(elapsed_seconds)}s acceptance timeout."
                        )
                        broadcast_service_request_event(locked, event_type='auto_reassigned')
                        reassigned_items.append(locked)
            else:
                already_alerted = ServiceRequestAuditLog.objects.filter(
                    service_request=req,
                    action='TIMEOUT_WARNING'
                ).exists()
                if not already_alerted:
                    ServiceRequestAuditLog.objects.create(
                        service_request=req,
                        action='TIMEOUT_WARNING',
                        previous_status=req.status,
                        new_status=req.status,
                        performed_by=None,
                        notes=f"Acceptance timeout warning: Waiter {req.assigned_waiter.username} has not accepted request within {timeout_seconds}s. No other available waiter found for automatic dispatch."
                    )
                    broadcast_service_request_event(req, event_type='acceptance_timeout_warning')

    return reassigned_items


def handle_waiter_shift_end_or_logout(waiter, restaurant=None):
    """
    Called when a waiter logs out, ends their shift, or is set to OFF_DUTY.
    Safely transfers all unresolved PENDING requests assigned to this waiter to another
    available waiter or moves them to the unassigned queue so no requests are lost.
    """
    if not waiter:
        return []

    rest = restaurant or getattr(waiter, 'restaurant', None)
    now = timezone.now()

    pending_reqs = ServiceRequest.objects.filter(
        assigned_waiter=waiter,
        status=ServiceRequest.Status.PENDING
    ).select_related('table', 'restaurant')

    transferred = []
    for req in pending_reqs:
        new_waiter = get_assigned_waiter_for_table(
            req.table,
            restaurant=req.restaurant or rest,
            exclude_waiter_id=waiter.id
        )
        with transaction.atomic():
            locked = ServiceRequest.objects.select_for_update().get(id=req.id)
            if locked.status == ServiceRequest.Status.PENDING and locked.assigned_waiter_id == waiter.id:
                locked.reassigned_from = waiter
                locked.reassigned_at = now
                locked.reassigned_by = None
                if new_waiter:
                    locked.assigned_waiter = new_waiter
                    locked.assigned_at = now
                    locked.reassignment_reason = f"Shift handover: Waiter {waiter.username} off duty / logged out."
                    note = f"Handover: Waiter {waiter.username} ended shift. Reassigned to {new_waiter.username}."
                else:
                    locked.assigned_waiter = None
                    locked.assigned_at = None
                    locked.reassignment_reason = f"Shift handover: Waiter {waiter.username} off duty / logged out (No replacement available)."
                    note = f"Handover: Waiter {waiter.username} ended shift. Moved to unassigned queue."

                locked.save(update_fields=[
                    'assigned_waiter', 'assigned_at', 'reassigned_from',
                    'reassigned_at', 'reassigned_by', 'reassignment_reason'
                ])

                ServiceRequestAuditLog.objects.create(
                    service_request=locked,
                    action='SHIFT_HANDOVER',
                    previous_status=locked.status,
                    new_status=locked.status,
                    performed_by=waiter,
                    notes=note
                )
                broadcast_service_request_event(locked, event_type='shift_handover')
                transferred.append(locked)

    # Set waiter availability to OFF_DUTY
    if waiter.is_on_duty or waiter.availability_status != User.AvailabilityStatus.OFF_DUTY:
        waiter.is_on_duty = False
        waiter.availability_status = User.AvailabilityStatus.OFF_DUTY
        waiter.save(update_fields=['is_on_duty', 'availability_status'])

    return transferred


def check_and_mark_sla_breaches(restaurant=None):
    """
    Identifies active requests that have exceeded their configured SLA limit.
    Marks them delayed, records breach timestamp, adds audit log, and broadcasts alert.
    """
    active_statuses = [
        ServiceRequest.Status.PENDING,
        ServiceRequest.Status.ACCEPTED,
        ServiceRequest.Status.IN_PROGRESS
    ]
    qs = ServiceRequest.objects.filter(status__in=active_statuses)
    if restaurant:
        qs = qs.filter(Q(restaurant=restaurant) | Q(restaurant__isnull=True))

    breached_count = 0
    now = timezone.now()
    for req in qs:
        if req.is_sla_breached and not req.is_delayed:
            with transaction.atomic():
                locked_req = ServiceRequest.objects.select_for_update().get(id=req.id)
                if not locked_req.is_delayed:
                    locked_req.is_delayed = True
                    locked_req.sla_breached_at = now
                    locked_req.save(update_fields=['is_delayed', 'sla_breached_at'])

                    ServiceRequestAuditLog.objects.create(
                        service_request=locked_req,
                        action='SLA_BREACHED',
                        previous_status=locked_req.status,
                        new_status=locked_req.status,
                        performed_by=None,
                        notes=f"Request exceeded SLA threshold ({locked_req.escalation_sla_minutes}m). Marked delayed."
                    )
                    broadcast_service_request_event(locked_req, event_type='sla_breached')
                    breached_count += 1
    return breached_count


def broadcast_service_request_event(service_req, event_type='request_updated'):
    """
    Broadcasts real-time service request updates via Channels (if available)
    and prepares payload for polling consumers.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        if channel_layer:
            # 1. Broadcast to table/session group (Customer UI)
            session_group = f"service_req_session_{service_req.session.session_token}"
            async_to_sync(channel_layer.group_send)(
                session_group,
                {
                    'type': 'service_message',
                    'event_type': event_type,
                    'data': {
                        'id': service_req.id,
                        'request_number': service_req.request_number,
                        'status': service_req.status,
                        'status_display': service_req.get_status_display(),
                        'badge_class': service_req.badge_class,
                        'waiter_name': service_req.accepted_by.get_full_name() if service_req.accepted_by else (
                            service_req.assigned_waiter.get_full_name() if service_req.assigned_waiter else None
                        ),
                    }
                }
            )

            # 2. Broadcast to restaurant staff group (Waiter/Admin dashboard)
            rest_id = service_req.restaurant_id or 'all'
            staff_group = f"service_req_staff_{rest_id}"
            async_to_sync(channel_layer.group_send)(
                staff_group,
                {
                    'type': 'service_message',
                    'event_type': event_type,
                    'data': {
                        'id': service_req.id,
                        'request_number': service_req.request_number,
                        'table_number': service_req.table.table_number,
                        'status': service_req.status,
                    }
                }
            )
    except Exception as exc:
        logger.debug(f"Channels broadcast notice (using polling fallback): {exc}")


# ============================================================================
# MODULE 2: LIVE TABLE WAITING TIME & DIGITAL QUEUE SERVICES
# ============================================================================

def calculate_live_waiting_time(party_size, restaurant=None):
    """
    Data-Driven Live Waiting Time Calculation Engine:
    Considers:
      1. Compatible Dining Tables (capacity >= party_size).
      2. Table Availability: A table is available only when is_active=True, status=AVAILABLE,
         occupied_seats_count=0, and no currently active session or held queue assignment exists.
      3. Customers in Queue Ahead: Count of waiting customers needing compatible capacity.
      4. Active Dining Sessions: Remaining turnover time based on historical dining duration (avg 40 mins).
    Returns:
      dict with min_minutes, max_minutes, display range, compatible tables, and availability status.
    """
    try:
        party_size = max(1, int(party_size))
    except (ValueError, TypeError):
        party_size = 2

    # 1. Find compatible tables
    compatible_tables = DiningTable.objects.filter(is_active=True, capacity__gte=party_size)
    if restaurant:
        compatible_tables = compatible_tables.filter(
            Q(restaurant=restaurant) | Q(restaurant__isnull=True)
        )

    total_compatible = compatible_tables.count()
    if total_compatible == 0:
        # Fallback if no single table is large enough (table combination or large party)
        return {
            'min_minutes': 35,
            'max_minutes': 55,
            'display': '35–55 mins',
            'compatible_tables_count': 0,
            'available_tables_count': 0,
            'queue_ahead_count': 0,
            'is_busy': True,
        }

    # 2. Count genuinely available compatible tables
    # Must NOT have active sessions and must NOT have held queue assignment
    active_held_table_ids = TableWaitingQueue.objects.filter(
        status=TableWaitingQueue.Status.TABLE_ASSIGNED,
        assigned_table__isnull=False
    ).values_list('assigned_table_id', flat=True)

    available_tables = []
    occupied_tables = []

    for t in compatible_tables:
        has_active_session = t.sessions.filter(
            status=CustomerSession.Status.ACTIVE
        ).exists()
        is_held = t.id in active_held_table_ids

        if t.status == DiningTable.Status.AVAILABLE and not has_active_session and t.occupied_seats_count == 0 and not is_held:
            available_tables.append(t)
        else:
            occupied_tables.append(t)

    available_count = len(available_tables)

    # 3. Count waiting customers ahead in queue for similar party size
    queue_ahead_qs = TableWaitingQueue.objects.filter(
        status__in=[TableWaitingQueue.Status.WAITING, TableWaitingQueue.Status.CALLED],
        guest_count__gte=max(1, party_size - 1)
    )
    if restaurant:
        queue_ahead_qs = queue_ahead_qs.filter(
            Q(restaurant=restaurant) | Q(restaurant__isnull=True)
        )
    queue_ahead_count = queue_ahead_qs.count()

    # 4. Compute realistic wait estimate
    AVG_MEAL_DURATION = 40  # average table turnover in minutes

    if available_count > queue_ahead_count:
        # Tables are ready right now!
        min_wait = 0
        max_wait = 5
    else:
        # Deficit of tables
        effective_queue_position = max(1, queue_ahead_count - available_count + 1)
        # Average rate of turnover per table
        turnover_interval = max(5, int(AVG_MEAL_DURATION / max(1, total_compatible)))
        base_wait = effective_queue_position * turnover_interval

        # Check elapsed time of active sessions to refine estimate
        active_sessions = CustomerSession.objects.filter(
            table__in=occupied_tables,
            status=CustomerSession.Status.ACTIVE
        ).order_by('created_at')

        if active_sessions.exists():
            oldest_session = active_sessions.first()
            elapsed_mins = int((timezone.now() - oldest_session.created_at).total_seconds() / 60)
            remaining_turnover = max(5, AVG_MEAL_DURATION - elapsed_mins)
            base_wait = min(base_wait, remaining_turnover + (effective_queue_position - 1) * turnover_interval)

        min_wait = max(5, int(base_wait * 0.8))
        max_wait = max(min_wait + 10, int(base_wait * 1.25))

    return {
        'min_minutes': min_wait,
        'max_minutes': max_wait,
        'display': f"{min_wait}–{max_wait} mins",
        'compatible_tables_count': total_compatible,
        'available_tables_count': available_count,
        'queue_ahead_count': queue_ahead_count,
        'is_busy': available_count == 0 or queue_ahead_count > 0,
    }


def update_all_waiting_queue_estimates(restaurant=None):
    """
    Recalculates and updates estimated waiting times for all active queue entries
    whenever table occupancy or queue membership changes.
    """
    active_entries = TableWaitingQueue.objects.filter(
        status=TableWaitingQueue.Status.WAITING
    )
    if restaurant:
        active_entries = active_entries.filter(
            Q(restaurant=restaurant) | Q(restaurant__isnull=True)
        )

    for entry in active_entries:
        est = calculate_live_waiting_time(entry.guest_count, restaurant=entry.restaurant)
        entry.estimated_wait_minutes_min = est['min_minutes']
        entry.estimated_wait_minutes_max = est['max_minutes']
        entry.save(update_fields=['estimated_wait_minutes_min', 'estimated_wait_minutes_max'])


def assign_table_to_queue_entry(queue_id, table_id, staff_user=None):
    """
    Atomic table assignment for waiting-list customer:
    1. Locks table and queue entry.
    2. Validates table is genuinely available (not occupied, not held, capacity >= guest_count).
    3. Sets status = TABLE_ASSIGNED and hold expiry (10 minutes).
    4. Recalculates waiting times for other customers in queue.
    """
    with transaction.atomic():
        locked_queue = TableWaitingQueue.objects.select_for_update().get(id=queue_id)
        locked_table = DiningTable.objects.select_for_update().get(id=table_id)

        if locked_queue.status not in [TableWaitingQueue.Status.WAITING, TableWaitingQueue.Status.CALLED]:
            return None, False, f"Cannot assign table to a queue entry with status '{locked_queue.get_status_display()}'."

        if locked_table.capacity < locked_queue.guest_count:
            return None, False, f"Table {locked_table.table_number} capacity ({locked_table.capacity}) is too small for {locked_queue.guest_count} guests."

        # Check if table already occupied or held by another waiting customer
        has_active_session = locked_table.sessions.filter(
            status=CustomerSession.Status.ACTIVE
        ).exists()
        if has_active_session or locked_table.occupied_seats_count > 0:
            return None, False, f"Table {locked_table.table_number} currently has an active dining session."

        already_held = TableWaitingQueue.objects.filter(
            assigned_table=locked_table,
            status=TableWaitingQueue.Status.TABLE_ASSIGNED
        ).exclude(id=locked_queue.id).exists()
        if already_held:
            return None, False, f"Table {locked_table.table_number} is already held for another waiting customer."

        now = timezone.now()
        locked_queue.assigned_table = locked_table
        locked_queue.status = TableWaitingQueue.Status.TABLE_ASSIGNED
        locked_queue.table_assigned_at = now
        locked_queue.table_hold_expires_at = now + timezone.timedelta(minutes=10)
        locked_queue.called_at = now
        locked_queue.called_by = staff_user
        locked_queue.save()

    update_all_waiting_queue_estimates(restaurant=locked_queue.restaurant)
    broadcast_queue_event(locked_queue, event_type='table_assigned')
    return locked_queue, True, f"Table {locked_table.table_number} assigned successfully to {locked_queue.customer_name}."


def seat_queue_customer(queue_id, staff_user=None):
    """
    Seats a waiting-list customer when they arrive at the restaurant:
    1. Verifies queue entry has an assigned table.
    2. Atomically creates a genuine CustomerSession for that table.
    3. Allocates required seats on that table via SessionSeatAllocation.
    4. Updates table status to OCCUPIED.
    5. Sets queue status = SEATED and links seated_session.
    """
    with transaction.atomic():
        locked_queue = TableWaitingQueue.objects.select_for_update().get(id=queue_id)

        if not locked_queue.assigned_table:
            return None, False, "Cannot seat customer without an assigned table. Please assign a table first."

        if locked_queue.status == TableWaitingQueue.Status.SEATED:
            return locked_queue, False, "Customer is already seated."

        table = DiningTable.objects.select_for_update().get(id=locked_queue.assigned_table.id)
        table.sync_seats()

        # Allocate available seats
        available_seats = list(table.seats.select_for_update().filter(
            is_active=True,
            status=RestaurantSeat.Status.AVAILABLE
        ).order_by('seat_number'))

        guest_count = locked_queue.guest_count
        group_name = f"Queue {locked_queue.queue_token}"

        new_session = CustomerSession.objects.create(
            table=table,
            session_name=group_name,
            customer_name=locked_queue.customer_name,
            customer_phone=locked_queue.customer_phone,
            guest_count=guest_count,
            special_requests=locked_queue.special_requirements or '',
            status=CustomerSession.Status.ACTIVE
        )

        from .models import SessionCart, SessionSeatAllocation
        SessionCart.objects.get_or_create(session=new_session)

        # Allocate seats
        allocated_count = 0
        for seat in available_seats[:guest_count]:
            seat.status = RestaurantSeat.Status.OCCUPIED
            seat.save(update_fields=['status', 'updated_at'])
            SessionSeatAllocation.objects.create(
                session=new_session,
                seat=seat,
                status=SessionSeatAllocation.AllocationStatus.ACTIVE
            )
            allocated_count += 1

        table.sync_status()

        # Update queue entry
        locked_queue.status = TableWaitingQueue.Status.SEATED
        locked_queue.seated_at = timezone.now()
        locked_queue.seated_by = staff_user
        locked_queue.seated_session = new_session
        locked_queue.save()

    update_all_waiting_queue_estimates(restaurant=locked_queue.restaurant)
    broadcast_queue_event(locked_queue, event_type='customer_seated')
    return locked_queue, True, f"Customer {locked_queue.customer_name} seated at Table {table.table_number} ({allocated_count} seats allocated)."


def broadcast_queue_event(queue_entry, event_type='queue_updated'):
    """
    Broadcasts real-time digital queue events.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        if channel_layer:
            # Customer individual token tracking group
            token_group = f"queue_token_{queue_entry.queue_token}"
            async_to_sync(channel_layer.group_send)(
                token_group,
                {
                    'type': 'queue_message',
                    'event_type': event_type,
                    'data': {
                        'token': queue_entry.queue_token,
                        'position': queue_entry.queue_position,
                        'status': queue_entry.status,
                        'status_display': queue_entry.get_status_display(),
                        'table': queue_entry.assigned_table.table_number if queue_entry.assigned_table else None,
                        'wait_display': queue_entry.wait_time_display,
                    }
                }
            )
    except Exception as exc:
        logger.debug(f"Queue channels broadcast notice (using polling fallback): {exc}")
