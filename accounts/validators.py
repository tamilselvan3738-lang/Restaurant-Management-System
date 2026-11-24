import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

class EnhancedSecurityPasswordValidator:
    """
    Validates that a password satisfies enterprise security rules:
    1. At least 8 characters
    2. At least one uppercase letter (A-Z)
    3. At least one lowercase letter (a-z)
    4. At least one numeric digit (0-9)
    5. At least one special symbol (!@#$%^&*...)
    """
    def __init__(self, min_length=8):
        self.min_length = min_length

    def validate(self, password, user=None):
        errors = []
        if len(password) < self.min_length:
            errors.append(
                ValidationError(
                    _("Password must contain at least %(min_length)d characters."),
                    code='password_too_short',
                    params={'min_length': self.min_length},
                )
            )
        if not re.search(r'[A-Z]', password):
            errors.append(
                ValidationError(
                    _("Password must contain at least one uppercase letter (A–Z)."),
                    code='password_no_upper',
                )
            )
        if not re.search(r'[a-z]', password):
            errors.append(
                ValidationError(
                    _("Password must contain at least one lowercase letter (a–z)."),
                    code='password_no_lower',
                )
            )
        if not re.search(r'[0-9]', password):
            errors.append(
                ValidationError(
                    _("Password must contain at least one number (0–9)."),
                    code='password_no_number',
                )
            )
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?~`]', password):
            errors.append(
                ValidationError(
                    _("Password must contain at least one special character (e.g. !@#$%^&*)."),
                    code='password_no_special',
                )
            )
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return _(
            "Your password must contain at least %(min_length)d characters, including uppercase, lowercase, numbers, and special symbols."
        ) % {'min_length': self.min_length}
