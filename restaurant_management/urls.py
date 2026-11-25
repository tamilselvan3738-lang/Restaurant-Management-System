from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from accounts.views import user_login, user_logout
from accounts import views_forgot_password
from tables import views_queue

urlpatterns = [
    path('queue/', views_queue.public_queue_view, name='queue_root'),
    path('login/', user_login, name='login'),
    path('logout/', user_logout, name='logout'),
    path('forgot-password/', views_forgot_password.forgot_password_username, name='forgot_password'),
    path('forgot-password/channel/', views_forgot_password.forgot_password_channel, name='forgot_password_channel'),
    path('forgot-password/verify/', views_forgot_password.forgot_password_verify, name='forgot_password_verify'),
    path('forgot-password/resend-otp/', views_forgot_password.forgot_password_resend_otp, name='forgot_password_resend_otp'),
    path('forgot-password/reset/', views_forgot_password.forgot_password_reset, name='forgot_password_reset'),
    path('forgot-password/success/', views_forgot_password.forgot_password_success, name='forgot_password_success'),
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('superadmin/', include('superadmin.urls')),
    path('accounts/', include('accounts.urls')),
    path('menu/', include('menu.urls')),
    path('tables/', include('tables.urls')),
    path('orders/', include('orders.urls')),
    path('billing/', include('billing.urls')),
    path('kitchen/', include('kitchen.urls')),
    path('chef/', include('kitchen.urls_chef', namespace='chef')),
]

# Ensure media files (uploaded logos, profiles, dishes) are served across dev and production environments
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
