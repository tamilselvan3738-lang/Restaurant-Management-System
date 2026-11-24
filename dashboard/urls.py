from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('cashier/', views.cashier_dashboard, name='cashier'),
    path('customers/', views.customers_view, name='customers'),
    path('analytics/', views.analytics_view, name='analytics'),
    path('reviews/', views.reviews_view, name='reviews'),
    path('notifications/', views.notifications_view, name='notifications'),
]
