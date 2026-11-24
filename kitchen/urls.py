from django.urls import path
from . import views

app_name = 'kitchen'

urlpatterns = [
    path('', views.kitchen_dashboard, name='dashboard'),
    path('order/<int:order_id>/status/', views.kitchen_update_order_status, name='update_order_status'),
    path('item/<int:item_id>/status/', views.kitchen_update_item_status, name='update_item_status'),
    path('item/<int:item_id>/approve-cancel/', views.kitchen_approve_cancellation, name='approve_cancellation'),
    path('item/<int:item_id>/reject-cancel/', views.kitchen_reject_cancellation, name='reject_cancellation'),
    path('api/live/', views.api_kitchen_live, name='api_live'),
]
