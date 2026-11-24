from django.urls import path
from . import views
from . import views_forgot_password

app_name = 'accounts'

urlpatterns = [
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('profile/', views.user_profile, name='profile'),
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/create/', views.staff_create, name='staff_create'),
    path('staff/<int:user_id>/edit/', views.staff_edit, name='staff_edit'),
    path('staff/<int:user_id>/delete/', views.staff_delete, name='staff_delete'),
    path('staff/<int:user_id>/toggle-duty/', views.staff_toggle_duty, name='staff_toggle_duty'),

    # Password Recovery & OTP Flow
    path('forgot-password/', views_forgot_password.forgot_password_username, name='forgot_password'),
    path('forgot-password/channel/', views_forgot_password.forgot_password_channel, name='forgot_password_channel'),
    path('forgot-password/verify/', views_forgot_password.forgot_password_verify, name='forgot_password_verify'),
    path('forgot-password/resend-otp/', views_forgot_password.forgot_password_resend_otp, name='forgot_password_resend_otp'),
    path('forgot-password/reset/', views_forgot_password.forgot_password_reset, name='forgot_password_reset'),
    path('forgot-password/success/', views_forgot_password.forgot_password_success, name='forgot_password_success'),
]
