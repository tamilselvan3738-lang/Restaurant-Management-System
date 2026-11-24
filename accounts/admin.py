from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Restaurant, PasswordResetOTP

@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'created_at')
    search_fields = ('name', 'email', 'phone')

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Restaurant & Role Details', {
            'fields': ('restaurant', 'role', 'admin_access_level', 'job_title', 'phone_number', 'profile_picture', 'is_on_duty', 'is_suspended', 'suspension_reason')
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Restaurant & Role Details', {
            'fields': ('restaurant', 'role', 'admin_access_level', 'job_title', 'phone_number', 'is_on_duty')
        }),
    )
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'restaurant', 'is_on_duty', 'is_staff')
    list_filter = ('role', 'restaurant', 'is_on_duty', 'is_staff', 'is_active', 'is_suspended')
    search_fields = ('username', 'first_name', 'last_name', 'email', 'phone_number')


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'channel', 'destination', 'attempts', 'resend_count', 'is_verified', 'is_used', 'expires_at', 'created_at')
    list_filter = ('channel', 'is_verified', 'is_used', 'created_at')
    search_fields = ('user__username', 'user__email', 'destination', 'ip_address')
    readonly_fields = ('otp_hash', 'reset_token', 'created_at', 'used_at', 'last_resend_at')

