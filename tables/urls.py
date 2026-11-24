from django.urls import path
from . import views
from . import views_assistance
from . import views_queue

app_name = 'tables'

urlpatterns = [
    # Floor Plan & Table Management
    path('', views.floor_plan, name='floor_plan'),
    path('table/add/', views.table_create, name='table_create'),
    path('table/<int:table_id>/edit/', views.table_edit, name='table_edit'),
    path('table/<int:table_id>/delete/', views.table_delete, name='table_delete'),
    path('table/<int:table_id>/status/', views.table_update_status, name='table_update_status'),
    path('table/<int:table_id>/regenerate-qr/', views.table_regenerate_qr, name='table_regenerate_qr'),
    path('table/<int:table_id>/release/', views.table_release, name='table_release'),
    path('table/<int:table_id>/qr/', views.table_qr_card, name='table_qr_card'),
    path('table/<int:table_id>/capacity/', views.table_update_capacity, name='table_update_capacity'),
    path('qr/print-all/', views.table_qr_print_all, name='table_qr_print_all'),

    # Seat-wise Occupancy Management & Audits
    path('seats/', views.seat_management, name='seat_management'),
    path('seats/<int:seat_id>/allocate/', views.seat_manual_allocate, name='seat_manual_allocate'),
    path('seats/<int:seat_id>/release/', views.seat_manual_release, name='seat_manual_release'),
    path('seats/<int:seat_id>/status/', views.seat_update_status, name='seat_update_status'),
    path('seats/audit-logs/', views.seat_audit_logs, name='seat_audit_logs'),
    path('api/seats-status/', views.api_seats_live, name='api_seats_live'),

    # Customer Sessions (Multiple groups at one table)
    path('sessions/', views.session_list, name='session_list'),
    path('sessions/<int:session_id>/', views.session_detail, name='session_detail'),
    path('sessions/<int:session_id>/close/', views.session_close, name='session_close'),

    # Reservations
    path('reservations/', views.reservation_list, name='reservation_list'),
    path('reservations/add/', views.reservation_create, name='reservation_create'),
    path('reservations/<int:reservation_id>/status/', views.reservation_update_status, name='reservation_update_status'),

    # Customer QR Ordering & Session Actions
    path('<str:table_number>/order/', views.customer_table_order, name='customer_order'),
    path('table/<str:table_number>/order/', views.customer_table_order),
    path('<str:table_number>/shop/', views.customer_shop, name='customer_shop'),
    path('table/<str:table_number>/shop/', views.customer_shop),
    path('<str:table_number>/shop/live/', views.api_customer_shop_live, name='customer_shop_live'),
    path('table/<str:table_number>/shop/live/', views.api_customer_shop_live),
    path('<str:table_number>/leave-session/', views.customer_leave_session, name='customer_leave_session'),
    path('<str:table_number>/session/close/', views.customer_close_session, name='customer_close_session'),
    path('<str:table_number>/request-bill/<str:session_token>/', views.customer_request_bill, name='customer_request_bill'),
    path('<str:table_number>/pay-bill/<str:session_token>/', views.customer_pay_bill, name='customer_pay_bill'),
    path('<str:table_number>/order/<int:order_id>/status/', views.customer_order_status, name='customer_order_status'),
    path('table/<str:table_number>/order/<int:order_id>/status/', views.customer_order_status),
    path('<str:table_number>/order/<int:order_id>/status-live/', views.api_customer_order_live, name='customer_order_status_live'),
    path('table/<str:table_number>/order/<int:order_id>/status-live/', views.api_customer_order_live),
    path('<str:table_number>/order/<int:order_id>/item/<int:item_id>/cancel/', views.customer_cancel_item, name='customer_cancel_item'),
    path('<str:table_number>/order/<int:order_id>/cancel/', views.customer_cancel_order, name='customer_cancel_order'),
    path('<str:table_number>/thank-you/', views.customer_thank_you, name='customer_thank_you'),

    # Real-Time Shared Cart & Group Management Endpoints
    path('<str:table_number>/group/cart/', views.api_group_cart_get, name='group_cart_get'),
    path('<str:table_number>/group/cart/', views.api_group_cart_get, name='api_group_cart_get'),
    path('<str:table_number>/group/cart/add/', views.api_group_cart_add, name='group_cart_add'),
    path('<str:table_number>/group/cart/add/', views.api_group_cart_add, name='api_group_cart_add'),
    path('<str:table_number>/group/cart/reduce/', views.api_group_cart_reduce, name='group_cart_reduce'),
    path('<str:table_number>/group/cart/reduce/', views.api_group_cart_reduce, name='api_group_cart_reduce'),
    path('<str:table_number>/group/cart/item/<int:item_id>/update/', views.api_group_cart_update, name='group_cart_update'),
    path('<str:table_number>/group/cart/item/<int:item_id>/update/', views.api_group_cart_update, name='api_group_cart_update'),
    path('<str:table_number>/group/cart/item/<int:item_id>/remove/', views.api_group_cart_remove, name='group_cart_remove'),
    path('<str:table_number>/group/cart/item/<int:item_id>/remove/', views.api_group_cart_remove, name='api_group_cart_remove'),
    path('<str:table_number>/group/order/submit/', views.api_group_submit_order, name='group_submit_order'),
    path('<str:table_number>/group/order/submit/', views.api_group_submit_order, name='api_group_submit_order'),
    path('<str:table_number>/group/cart/sync/', views.api_group_cart_sync, name='group_cart_sync'),
    path('<str:table_number>/group/cart/sync/', views.api_group_cart_sync, name='api_group_cart_sync'),
    path('<str:table_number>/group/members/', views.api_group_members, name='group_members'),
    path('<str:table_number>/group/members/', views.api_group_members, name='api_group_members'),
    path('<str:table_number>/group/member/<int:member_id>/remove/', views.api_group_member_remove, name='group_member_remove'),
    path('<str:table_number>/group/member/<int:member_id>/remove/', views.api_group_member_remove, name='api_group_member_remove'),
    path('<str:table_number>/group/leave/', views.customer_leave_group, name='group_leave'),
    path('<str:table_number>/group/leave/', views.customer_leave_group, name='api_group_leave'),

    # MODULE 1: SMART WAITER ASSISTANCE SYSTEM
    path('<str:table_number>/service-request/create/', views_assistance.customer_service_request_create, name='customer_service_request_create'),
    path('<str:table_number>/service-request/status/', views_assistance.customer_service_request_status, name='customer_service_request_status'),
    path('<str:table_number>/service-request/<int:request_id>/cancel/', views_assistance.customer_service_request_cancel, name='customer_service_request_cancel'),
    path('<str:table_number>/service-request/<int:request_id>/renotice/', views_assistance.customer_service_request_renotice, name='customer_service_request_renotice'),
    path('<str:table_number>/service-request/<int:request_id>/repeat/', views_assistance.customer_service_request_repeat, name='customer_service_request_repeat'),
    path('<str:table_number>/service-request/<int:request_id>/feedback/', views_assistance.customer_service_request_feedback, name='customer_service_request_feedback'),
    path('<str:table_number>/service-request/<int:request_id>/report-not-fulfilled/', views_assistance.customer_service_request_report_not_fulfilled, name='customer_service_request_report_not_fulfilled'),

    path('waiter/requests/', views_assistance.waiter_service_requests_view, name='waiter_requests'),
    path('service-requests/waiter/', views_assistance.waiter_service_requests_view),
    path('waiter/requests/poll/', views_assistance.waiter_service_requests_poll_api, name='waiter_requests_poll_api'),
    path('waiter/availability/', views_assistance.waiter_toggle_availability, name='waiter_toggle_availability'),
    path('waiter/requests/<int:request_id>/accept/', views_assistance.waiter_service_request_accept),
    path('waiter/requests/<int:request_id>/start/', views_assistance.waiter_service_request_start),
    path('waiter/requests/<int:request_id>/complete/', views_assistance.waiter_service_request_complete),
    path('waiter/requests/<int:request_id>/cancel/', views_assistance.waiter_service_request_cancel),
    path('waiter/requests/<int:request_id>/unable-to-fulfil/', views_assistance.waiter_service_request_unable),
    path('service-request/<int:request_id>/accept/', views_assistance.waiter_service_request_accept, name='waiter_request_accept'),
    path('service-request/<int:request_id>/start/', views_assistance.waiter_service_request_start, name='waiter_request_start'),
    path('service-request/<int:request_id>/complete/', views_assistance.waiter_service_request_complete, name='waiter_request_complete'),
    path('service-request/<int:request_id>/cancel/', views_assistance.waiter_service_request_cancel, name='waiter_request_cancel'),
    path('service-request/<int:request_id>/unable-to-fulfil/', views_assistance.waiter_service_request_unable, name='waiter_request_unable'),

    path('service-requests/admin/', views_assistance.admin_service_requests_view, name='admin_service_requests'),
    path('admin/service-requests/', views_assistance.admin_service_requests_view),
    path('service-requests/admin/poll/', views_assistance.admin_service_requests_poll_api, name='admin_service_requests_poll_api'),
    path('service-request/<int:request_id>/reassign/', views_assistance.admin_service_request_reassign, name='admin_service_request_reassign'),
    path('admin/service-requests/<int:request_id>/reassign/', views_assistance.admin_service_request_reassign),
    path('service-requests/admin/<int:request_id>/reassign/', views_assistance.admin_service_request_reassign),
    path('service-request/<int:request_id>/detail-json/', views_assistance.admin_service_request_detail_json, name='admin_service_request_detail_json'),
    path('service-request/<int:request_id>/dismiss-renotice/', views_assistance.service_request_dismiss_renotice_api, name='service_request_dismiss_renotice_api'),
    path('escalations/<int:escalation_id>/resolve/', views_assistance.admin_escalation_resolve_api, name='admin_escalation_resolve'),
    path('sla-config/update/', views_assistance.admin_sla_config_update, name='admin_sla_config_update'),
    path('floor-sections/', views_assistance.admin_floor_sections_view, name='floor_sections'),
    path('floor-sections/<int:section_id>/delete/', views_assistance.admin_floor_section_delete, name='floor_section_delete'),
    path('waiters/<int:waiter_id>/toggle-duty/', views_assistance.admin_waiter_duty_toggle, name='admin_waiter_duty_toggle'),
    path('waiter-assignments/', views_assistance.admin_waiter_assignments_view, name='waiter_assignments'),
    path('waiter-assignments/<int:assignment_id>/delete/', views_assistance.admin_waiter_assignment_delete, name='waiter_assignment_delete'),

    # MODULE 2: LIVE TABLE WAITING TIME & DIGITAL QUEUE MANAGEMENT
    path('queue/', views_queue.public_queue_view, name='queue_public'),
    path('queue/join/', views_queue.customer_join_queue, name='queue_join'),
    path('queue/track/<str:queue_token>/', views_queue.customer_track_queue_view, name='queue_track'),
    path('queue/api/<str:queue_token>/', views_queue.api_queue_status_live, name='queue_status_live'),
    path('queue/cancel/<str:queue_token>/', views_queue.customer_queue_cancel, name='queue_cancel'),

    path('waiting-list/', views_queue.admin_queue_management_view, name='admin_queue_manage'),
    path('waiting-list/<int:queue_id>/call/', views_queue.admin_queue_call_customer, name='admin_queue_call'),
    path('waiting-list/<int:queue_id>/assign/', views_queue.admin_queue_assign_table, name='admin_queue_assign'),
    path('waiting-list/<int:queue_id>/seat/', views_queue.admin_queue_mark_seated, name='admin_queue_seat'),
    path('waiting-list/<int:queue_id>/no-show/', views_queue.admin_queue_mark_noshow, name='admin_queue_noshow'),
    path('waiting-list/<int:queue_id>/cancel/', views_queue.admin_queue_cancel, name='admin_queue_cancel_entry'),
    path('waiting-list/poll/', views_queue.admin_queue_poll_api, name='admin_queue_poll_api'),
]
