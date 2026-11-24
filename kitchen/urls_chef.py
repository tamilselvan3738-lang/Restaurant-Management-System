from django.urls import path
from . import views_chef

app_name = 'chef'

urlpatterns = [
    # 1. Dashboard
    path('', views_chef.chef_dashboard, name='dashboard_root'),
    path('dashboard/', views_chef.chef_dashboard, name='dashboard'),

    # 2-7. Order Management & Status Queues
    path('orders/', views_chef.chef_order_list, name='orders'),
    path('orders/new/', views_chef.chef_order_list, {'status_filter': 'new'}, name='orders_new'),
    path('orders/preparing/', views_chef.chef_order_list, {'status_filter': 'preparing'}, name='orders_preparing'),
    path('orders/ready/', views_chef.chef_order_list, {'status_filter': 'ready'}, name='orders_ready'),
    path('orders/completed/', views_chef.chef_order_list, {'status_filter': 'completed'}, name='orders_completed'),
    path('orders/cancelled/', views_chef.chef_order_list, {'status_filter': 'cancelled'}, name='orders_cancelled'),

    # Order details & actions
    path('orders/<int:order_id>/', views_chef.chef_order_detail, name='order_detail'),
    path('orders/<int:order_id>/accept/', views_chef.chef_accept_order, name='order_accept'),
    path('orders/<int:order_id>/status/', views_chef.chef_update_order_status, name='order_status'),
    path('orders/<int:order_id>/request-cancel/', views_chef.chef_request_order_cancel, name='order_request_cancel'),

    # Item actions
    path('items/<int:item_id>/status/', views_chef.chef_update_item_status, name='item_status'),
    path('items/<int:item_id>/cancel/', views_chef.chef_cancel_item, name='item_cancel'),

    # 8. Cancellation Requests & Logs
    path('cancellations/', views_chef.chef_cancellations, name='cancellations'),

    # 9. Kitchen Notifications
    path('notifications/', views_chef.chef_notifications, name='notifications'),
    path('notifications/<int:notif_id>/read/', views_chef.chef_mark_notification_read, name='notification_read'),
    path('notifications/read-all/', views_chef.chef_mark_all_read, name='notification_read_all'),

    # Stock / Ingredient Unavailable Reporting
    path('menu/report-unavailable/', views_chef.chef_report_unavailable, name='report_unavailable'),

    # 10. My Profile
    path('profile/', views_chef.chef_profile, name='profile'),

    # 11. Logout
    path('logout/', views_chef.chef_logout, name='logout'),

    # Live polling API for kitchen screens & sound alerts
    path('api/live/', views_chef.chef_api_live, name='api_live'),
]
