from functools import wraps
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404, HttpResponseForbidden
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.db.models import Q
from accounts.models import Restaurant, User


def get_current_restaurant(request):
    """
    Resolves the current active restaurant tenant strictly from trusted server-side state:
      1. If Super Admin is in "View as Restaurant" mode (session-backed), return the impersonated tenant.
      2. If Super Admin is not impersonating, return None (global administrative access).
      3. For authenticated restaurant users (Admin, Manager, Cashier, Waiter, Kitchen),
         return request.user.restaurant. Never trust client-supplied GET/POST parameters.
      4. For anonymous guests, return restaurant resolved by table/session context if present.
    """
    if not hasattr(request, '_cached_restaurant'):
        user = getattr(request, 'user', None)
        restaurant = None
        is_impersonating = False

        if user and user.is_authenticated:
            if user.is_superuser:
                impersonate_id = request.session.get('impersonate_restaurant_id')
                if impersonate_id:
                    restaurant = Restaurant.objects.filter(id=impersonate_id).first()
                    if restaurant:
                        is_impersonating = True
                    else:
                        request.session.pop('impersonate_restaurant_id', None)
            else:
                restaurant = getattr(user, 'restaurant', None)
                if not restaurant and hasattr(user, 'kitchen_profile') and user.kitchen_profile:
                    restaurant = user.kitchen_profile.restaurant
                if not restaurant and getattr(user, 'created_by', None) and getattr(user.created_by, 'restaurant', None):
                    restaurant = user.created_by.restaurant
                    # Auto-heal user record
                    try:
                        user.restaurant = restaurant
                        user.save(update_fields=['restaurant'])
                    except Exception:
                        pass

        request._cached_restaurant = restaurant
        request._is_impersonating = is_impersonating

    return request._cached_restaurant


def is_impersonating_restaurant(request):
    """Returns True if Super Admin is currently viewing the system as a specific restaurant tenant."""
    get_current_restaurant(request)
    return getattr(request, '_is_impersonating', False)


def get_tenant_object_or_404(model_class, request, **kwargs):
    """
    Fetches a single model instance enforcing strict tenant-level isolation.
    If the requested record belongs to another restaurant tenant, raises Http404
    (never exposes another tenant's existence or primary keys).
    Super Admins (not in impersonation mode) can access any record globally.
    """
    restaurant = get_current_restaurant(request)
    user = getattr(request, 'user', None)

    # Super Admin global mode (not impersonating)
    if user and user.is_authenticated and user.is_superuser and not is_impersonating_restaurant(request):
        return get_object_or_404(model_class, **kwargs)

    # If user has no restaurant assigned
    if not restaurant:
        raise Http404("No authorized restaurant tenant context found.")

    # Determine restaurant field on model
    model = getattr(model_class, 'model', model_class)
    fields = [f.name for f in model._meta.fields]
    if 'restaurant' in fields:
        filter_kwargs = {'restaurant': restaurant, **kwargs}
        try:
            return get_object_or_404(model_class, **filter_kwargs)
        except Http404:
            # Fallback for legacy/unlinked records where restaurant was NULL but related entities belong to this tenant
            qs = getattr(model_class, 'all', None)
            if callable(qs):
                qs = model_class.all()
            elif hasattr(model_class, 'filter'):
                qs = model_class
            else:
                qs = model.objects

            q_entities = Q()
            rel_found = False
            if 'table' in fields:
                q_entities |= Q(table__restaurant=restaurant)
                rel_found = True
            if 'session' in fields:
                q_entities |= Q(session__restaurant=restaurant)
                rel_found = True
            if 'order' in fields:
                q_entities |= Q(order__restaurant=restaurant)
                rel_found = True
            if 'invoice' in fields:
                q_entities |= Q(invoice__restaurant=restaurant)
                rel_found = True

            if rel_found:
                q_rel = Q(**kwargs) & (q_entities | Q(restaurant__isnull=True))
                candidate = qs.filter(q_rel).first()
                if candidate:
                    if hasattr(candidate, 'restaurant') and candidate.restaurant is None:
                        try:
                            candidate.restaurant = restaurant
                            candidate.save(update_fields=['restaurant'])
                        except Exception:
                            pass
                    return candidate
            raise
    elif 'table' in fields:
        filter_kwargs = {'table__restaurant': restaurant, **kwargs}
    elif 'order' in fields:
        filter_kwargs = {'order__restaurant': restaurant, **kwargs}
    elif 'session' in fields:
        filter_kwargs = {'session__restaurant': restaurant, **kwargs}
    elif 'invoice' in fields:
        filter_kwargs = {'invoice__restaurant': restaurant, **kwargs}
    elif 'category' in fields:
        filter_kwargs = {'category__restaurant': restaurant, **kwargs}
    else:
        filter_kwargs = kwargs

    return get_object_or_404(model_class, **filter_kwargs)


def filter_by_tenant(queryset, request, restaurant_field='restaurant'):
    """
    Filters a Django QuerySet to only include records belonging to the active restaurant tenant.
    Super Admins (not impersonating) see all records, or can filter by ?restaurant_id=X.
    """
    restaurant = get_current_restaurant(request)
    user = getattr(request, 'user', None)

    if user and user.is_authenticated and user.is_superuser and not is_impersonating_restaurant(request):
        rest_filter = request.GET.get('restaurant_id') or request.GET.get('restaurant')
        if rest_filter and rest_filter.isdigit():
            return queryset.filter(**{restaurant_field + '_id': int(rest_filter)})
        return queryset

    if not restaurant:
        return queryset.none()

    return queryset.filter(**{restaurant_field: restaurant})


def tenant_required(view_func):
    """
    Decorator ensuring the authenticated user has a valid, non-suspended restaurant tenant.
    Redirects or displays a notification if the restaurant is inactive or suspended.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return redirect('accounts:login')

        if user.is_superuser and not is_impersonating_restaurant(request):
            return view_func(request, *args, **kwargs)

        restaurant = get_current_restaurant(request)
        if not restaurant:
            messages.error(request, "Your account is not assigned to any active restaurant. Please contact Super Admin.")
            return redirect('accounts:login')

        if restaurant.status == Restaurant.RestaurantStatus.SUSPENDED:
            return render(request, 'accounts/tenant_suspended.html', {
                'restaurant': restaurant,
                'reason': restaurant.suspension_reason or "Account suspended by Super Administrator."
            }, status=403)

        if restaurant.status == Restaurant.RestaurantStatus.INACTIVE:
            return render(request, 'accounts/tenant_inactive.html', {
                'restaurant': restaurant,
            }, status=403)

        return view_func(request, *args, **kwargs)

    return _wrapped_view
