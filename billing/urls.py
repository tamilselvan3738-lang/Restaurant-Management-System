from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('', views.invoice_list, name='list'),
    path('order/<int:order_id>/generate/', views.generate_or_view_invoice, name='generate'),
    path('<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('<int:invoice_id>/pay/', views.process_payment, name='process_payment'),
    path('<int:invoice_id>/discount/', views.apply_discount, name='apply_discount'),
    path('<int:invoice_id>/split/', views.split_bill, name='split_bill'),
    path('<int:invoice_id>/print/', views.print_receipt, name='print_receipt'),

    # Merge Multiple Customer Bills & Consolidated Billing
    path('merge/', views.merge_bills_select, name='merge_bills_select'),
    path('merge/preview/', views.merge_bills_preview, name='merge_bills_preview'),
    path('merge/confirm/', views.merge_bills_confirm, name='merge_bills_confirm'),
    path('consolidated/', views.consolidated_bill_list, name='consolidated_list'),
    path('consolidated/<int:bill_id>/', views.consolidated_detail, name='consolidated_detail'),
    path('consolidated/<int:bill_id>/pay/', views.consolidated_pay, name='consolidated_pay'),
    path('consolidated/<int:bill_id>/void/', views.consolidated_void, name='consolidated_void'),
    path('consolidated/<int:bill_id>/print/', views.consolidated_print, name='consolidated_print'),

    # Customer Bill Requests Management (Admin & Cashier)
    path('requests/', views.bill_request_list, name='bill_request_list'),
    path('requests/<int:pk>/', views.bill_request_detail, name='bill_request_detail'),
    path('requests/<int:pk>/accept/', views.bill_request_accept, name='bill_request_accept'),
    path('requests/<int:pk>/generate-bill/', views.bill_request_generate_bill, name='bill_request_generate_bill'),
    path('requests/<int:pk>/cancel/', views.bill_request_cancel, name='bill_request_cancel'),
    path('requests/poll/', views.bill_request_poll_api, name='bill_request_poll_api'),
]
