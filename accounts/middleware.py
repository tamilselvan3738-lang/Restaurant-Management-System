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

        # Enforce restaurant suspension policy for non-superadmins
        if user and user.is_authenticated and not user.is_superuser:
            if restaurant and restaurant.status == Restaurant.RestaurantStatus.SUSPENDED:
                exempt_prefixes = [
                    '/accounts/logout/',
                    '/logout/',
                    '/static/',
                    '/media/',
                ]
                if not any(request.path.startswith(prefix) for prefix in exempt_prefixes):
                    return render(request, 'accounts/tenant_suspended.html', {
                        'restaurant': restaurant,
                        'reason': restaurant.suspension_reason or "Account suspended by Super Administrator."
                    }, status=403)

        response = self.get_response(request)
        return response
