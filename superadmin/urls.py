from django.urls import path
from . import views

app_name = 'superadmin'

urlpatterns = [
    # Admin Management
    path('admins/', views.admin_list, name='admin_list'),
    path('admins/add/', views.admin_create, name='admin_create'),
    path('admins/<int:user_id>/edit/', views.admin_edit, name='admin_edit'),
    path('admins/<int:user_id>/status/', views.admin_toggle_status, name='admin_toggle_status'),
    path('admins/<int:user_id>/reset-password/', views.admin_reset_password, name='admin_reset_password'),
    path('admins/<int:user_id>/employees/', views.admin_employees, name='admin_employees'),
    path('employees/<int:user_id>/status/', views.employee_toggle_status, name='employee_toggle_status'),
    path('audit-history/', views.audit_history, name='audit_history'),

    # Restaurant Tenant Management
    path('restaurants/', views.restaurant_list, name='restaurant_list'),
    path('restaurants/<int:restaurant_id>/edit/', views.restaurant_edit, name='restaurant_edit'),
    path('restaurants/<int:restaurant_id>/status/', views.restaurant_toggle_status, name='restaurant_toggle_status'),
    path('restaurants/<int:restaurant_id>/view-as/', views.view_as_restaurant, name='view_as_restaurant'),
    path('restaurants/exit-view-as/', views.exit_view_as_restaurant, name='exit_view_as_restaurant'),
    path('restaurant/details/', views.restaurant_details, name='restaurant_details'),
    path('restaurant/status/', views.restaurant_status, name='restaurant_status'),

    # Reports
    path('reports/', views.report_list, name='report_list'),
    path('reports/create/', views.report_create, name='report_create'),
    path('reports/<int:report_id>/', views.report_detail, name='report_detail'),

    # System Settings
    path('settings/general/', views.general_settings, name='general_settings'),
    path('settings/security/', views.security_settings, name='security_settings'),
    path('settings/backup/', views.backup_settings, name='backup_settings'),
    path('settings/backup/download/<str:filename>/', views.download_backup, name='download_backup'),
    path('currency/switch/', views.switch_currency, name='switch_currency'),

    # My Profile
    path('profile/', views.profile_view, name='profile'),
]
