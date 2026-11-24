from django.contrib import admin
from .models import (
    DiningTable,
    Reservation,
    TableSection,
    WaiterTableAssignment,
    ServiceRequest,
    ServiceRequestAuditLog,
    ServiceSLAConfig,
    ServiceRequestEscalation,
)

@admin.register(DiningTable)
class DiningTableAdmin(admin.ModelAdmin):
    list_display = ('table_number', 'capacity', 'location', 'status', 'is_active')
    list_filter = ('location', 'status', 'is_active')
    list_editable = ('status', 'is_active')
    search_fields = ('table_number',)

@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'customer_phone', 'table', 'guests_count', 'reservation_date', 'reservation_time', 'status')
    list_filter = ('status', 'reservation_date')
    search_fields = ('customer_name', 'customer_phone', 'customer_email')

@admin.register(TableSection)
class TableSectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'restaurant', 'is_active', 'created_at')
    list_filter = ('restaurant', 'is_active')
    search_fields = ('name', 'code')
    filter_horizontal = ('tables', 'assigned_waiters')

@admin.register(WaiterTableAssignment)
class WaiterTableAssignmentAdmin(admin.ModelAdmin):
    list_display = ('waiter', 'table', 'section', 'restaurant', 'is_active', 'assigned_at')
    list_filter = ('restaurant', 'is_active')
    search_fields = ('waiter__username', 'waiter__first_name', 'table__table_number')

@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = ('request_number', 'table', 'request_type', 'status', 'priority', 'assigned_waiter', 'is_delayed', 'requested_at')
    list_filter = ('status', 'priority', 'request_category', 'is_delayed')
    search_fields = ('request_number', 'table__table_number', 'customer_name', 'assigned_waiter__username')

@admin.register(ServiceSLAConfig)
class ServiceSLAConfigAdmin(admin.ModelAdmin):
    list_display = ('request_type', 'restaurant', 'target_minutes', 'warning_minutes', 'escalation_minutes', 'priority', 'is_active')
    list_filter = ('restaurant', 'priority', 'is_active')

@admin.register(ServiceRequestEscalation)
class ServiceRequestEscalationAdmin(admin.ModelAdmin):
    list_display = ('escalation_number', 'service_request', 'table', 'customer_name', 'status', 'created_at')
    list_filter = ('status', 'restaurant')
    search_fields = ('escalation_number', 'customer_name', 'complaint_notes')
