from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


class MultiCredentialBackend(ModelBackend):
    """
    Restaurant authentication backend allowing staff and admins to log in
    using Username or Email Address:
      1. Username (scoped by organization if duplicate exists across restaurants)
      2. Email Address (globally unique)
    """

    def authenticate(self, request, username=None, password=None, organization=None, **kwargs):
        if not username or not password:
            return None

        identifier = str(username).strip()
        raw_secret = str(password).strip()
        org_code = str(organization).strip() if organization else None

        # Support embedded organization syntax (e.g. REST-0001/rahul or REST-0001\rahul)
        if not org_code:
            if '/' in identifier:
                org_part, user_part = identifier.split('/', 1)
                org_code = org_part.strip()
                identifier = user_part.strip()
            elif '\\' in identifier:
                org_part, user_part = identifier.split('\\', 1)
                org_code = org_part.strip()
                identifier = user_part.strip()

        # Query matching user
        if '@' in identifier:
            # Email lookup
            users = User.objects.filter(email__iexact=identifier)
            if org_code:
                users = users.filter(
                    Q(restaurant__code__iexact=org_code) |
                    Q(restaurant__name__iexact=org_code)
                )
        else:
            # Username lookup (case-insensitive normalized)
            norm = identifier.lower()
            if org_code:
                users = User.objects.filter(
                    normalized_username=norm
                ).filter(
                    Q(restaurant__code__iexact=org_code) |
                    Q(restaurant__name__iexact=org_code)
                )
            else:
                matching = User.objects.filter(normalized_username=norm)
                if matching.count() == 1:
                    users = matching
                else:
                    # Multiple accounts share this username across different restaurants.
                    # Resolve by validating credentials against candidates.
                    candidates = []
                    for cand in matching:
                        pwd_valid = cand.check_password(raw_secret)
                        p_valid = cand.check_pin(raw_secret) if hasattr(cand, 'check_pin') else False
                        if (pwd_valid or p_valid) and self.user_can_authenticate(cand):
                            if not getattr(cand, 'is_suspended', False):
                                candidates.append(cand)
                    if len(candidates) == 1:
                        return candidates[0]
                    return None

        user = users.first()
        if not user:
            return None

        # Verify password or quick staff PIN
        password_valid = user.check_password(raw_secret)
        pin_valid = user.check_pin(raw_secret) if hasattr(user, 'check_pin') else False

        if (password_valid or pin_valid) and self.user_can_authenticate(user):
            # Check if user is suspended
            if getattr(user, 'is_suspended', False):
                return None
            # Check if restaurant tenant is suspended (non-superadmins)
            if not user.is_superuser and getattr(user, 'restaurant', None):
                if user.restaurant.status == 'SUSPENDED':
                    return None
            return user

        return None
