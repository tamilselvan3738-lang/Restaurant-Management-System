from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    path('', views.order_list, name='list'),
    path('extra-food/', views.extra_food_orders, name='extra_food_orders'),
    path('extra-food/mark-seen/', views.api_mark_extra_food_seen, name='mark_extra_food_seen'),
    path('extra-food/<int:log_id>/seen/', views.api_mark_single_extra_food_seen, name='mark_single_extra_food_seen'),
    path('extra-food/poll/', views.api_poll_extra_food, name='poll_extra_food'),
    path('cancelled/', views.cancelled_orders, name='cancelled_orders'),
    path('cancellations/mark-seen/', views.api_mark_cancellations_seen, name='mark_cancellations_seen'),
    path('pos/', views.order_pos, name='pos'),
    path('pos/submit/', views.order_pos_submit, name='pos_submit'),
    path('<int:order_id>/', views.order_detail, name='detail'),
    path('<int:order_id>/dismiss-extra/', views.order_dismiss_extra_notification, name='dismiss_extra'),
    path('<int:order_id>/status/', views.order_update_status, name='update_status'),
    path('<int:order_id>/add-item/', views.order_add_item, name='add_item'),
    path('item/<int:item_id>/status/', views.order_item_update_status, name='item_update_status'),
    path('kds/', views.kitchen_display, name='kitchen_display'),
]
