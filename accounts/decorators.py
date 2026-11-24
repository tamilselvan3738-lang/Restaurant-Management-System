from functools import wraps
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import redirect

def role_required(allowed_roles=None):
    """
    Decorator for views that checks that the logged-in user is active,
    and has one of the allowed roles (or is a superuser).
    """
    if allowed_roles is None:
        allowed_roles = []

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')

            if request.user.is_superuser or request.user.role in allowed_roles:
                return view_func(request, *args, **kwargs)

            messages.error(request, "Access denied. You do not have permission to view this resource.")
            if getattr(request.user, 'role', None) in ['CHEF', 'KITCHEN']:
                return redirect('chef:dashboard')
            if getattr(request.user, 'role', None) == 'CASHIER':
                return redirect('dashboard:cashier')
            return redirect('dashboard:index')

        return _wrapped_view
    return decorator


def chef_required(view_func):
    """
    Decorator for views reserved for Chef / Kitchen Staff (or Restaurant Admin/Manager).
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')

        if request.user.is_superuser or request.user.role in ['CHEF', 'KITCHEN', 'ADMIN', 'MANAGER']:
            return view_func(request, *args, **kwargs)

        messages.error(request, "Access restricted to authorized Kitchen & Chef personnel.")
        if getattr(request.user, 'role', None) == 'CASHIER':
            return redirect('dashboard:cashier')
        return redirect('dashboard:index')

    return _wrapped_view


class RoleRequiredMixin(UserPassesTestMixin):
    """
    CBV mixin that checks user role against allowed_roles.
    """
    allowed_roles = []

    def test_func(self):
        user = self.request.user
        if not user.is_authenticated:
            return False
        return user.is_superuser or user.role in self.allowed_roles

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return redirect('accounts:login')
        messages.error(self.request, "Access denied. Insufficient permissions.")
        if getattr(self.request.user, 'role', None) in ['CHEF', 'KITCHEN']:
            return redirect('chef:dashboard')
        if getattr(self.request.user, 'role', None) == 'CASHIER':
            return redirect('dashboard:cashier')
        return redirect('dashboard:index')
