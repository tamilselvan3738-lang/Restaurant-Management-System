from django.shortcuts import render, redirect
from django.urls import reverse
from accounts.models import Restaurant
from accounts.tenant_utils import get_current_restaurant, is_impersonating_restaurant


class TenantContextMiddleware:
    """
    Middleware that attaches the resolved tenant (Restaurant) to `request.restaurant`
    and manages Super Admin impersonation / "View as Restaurant" mode.
    Also protects against suspended restaurant access across all endpoints.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        restaurant = get_current_restaurant(request)
        request.restaurant = restaurant
        request.is_impersonating = is_impersonating_restaurant(request)

        user = getattr(request, 'user', None)

        # Enforce user active & suspension policy for non-superadmins
        if user and user.is_authenticated and not user.is_superuser:
            exempt_prefixes = [
                '/accounts/logout/',
                '/logout/',
                '/static/',
                '/media/',
            ]
            if not any(request.path.startswith(prefix) for prefix in exempt_prefixes):
                if not user.is_active or user.is_suspended:
                    from django.contrib.auth import logout as auth_logout
                    from django.contrib import messages
                    from django.http import JsonResponse

                    auth_logout(request)
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                        return JsonResponse({
                            'error': 'Your account has been deactivated or suspended. Please contact your administrator.',
                            'status': 'forbidden'
                        }, status=403)
                    messages.error(request, "Your account has been deactivated or suspended. Please contact your administrator.")
                    return redirect('accounts:login')

                # Enforce restaurant suspension & inactive policy for non-superadmins
                if restaurant and restaurant.status == Restaurant.RestaurantStatus.SUSPENDED:
                    return render(request, 'accounts/tenant_suspended.html', {
                        'restaurant': restaurant,
                        'reason': restaurant.suspension_reason or "Account suspended by Super Administrator."
                    }, status=403)
                elif restaurant and restaurant.status == Restaurant.RestaurantStatus.INACTIVE:
                    return render(request, 'accounts/tenant_suspended.html', {
                        'restaurant': restaurant,
                        'reason': "This restaurant account is currently inactive. Please contact the administrator."
                    }, status=403)

        response = self.get_response(request)
        return response
