import re
import logging

SENSITIVE_PATTERNS = [
    # Passwords
    (re.compile(r'(password(?:1|2|_hash|_reset)?["\']?\s*[:=]\s*["\']?)([^"\'\s,;&]+)', re.IGNORECASE), r'\1[MASKED]'),
    # OTPs
    (re.compile(r'(otp(?:_code|_pin)?["\']?\s*[:=]\s*["\']?)([^"\'\s,;&]+)', re.IGNORECASE), r'\1[MASKED]'),
    # Tokens & Secrets
    (re.compile(r'((?:api[_-]?key|secret|token|auth(?:orization)?|access[_-]?token)["\']?\s*[:=]\s*["\']?)([^"\'\s,;&]+)', re.IGNORECASE), r'\1[MASKED]'),
    # Credit Card CVV / CVC
    (re.compile(r'(cvv2?|cvc|security_code)["\']?\s*[:=]\s*["\']?\d{3,4}', re.IGNORECASE), r'\1="[MASKED]"'),
    # Credit Card Numbers (13 to 16 digits)
    (re.compile(r'\b(?:\d[ -]*?){13,16}\b'), '[MASKED_CARD]'),
]


class SensitiveDataMaskingFilter(logging.Filter):
    """
    Log filter that masks passwords, OTPs, secret tokens, and credit card numbers
    preventing inadvertent leakage to log files or consoles.
    """
    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                msg = record.msg
                for pattern, repl in SENSITIVE_PATTERNS:
                    msg = pattern.sub(repl, msg)
                record.msg = msg
            elif isinstance(record.args, dict):
                cleaned_args = dict(record.args)
                for k, v in cleaned_args.items():
                    if any(term in k.lower() for term in ['password', 'otp', 'token', 'secret', 'key']):
                        cleaned_args[k] = '[MASKED]'
                record.args = cleaned_args
            elif isinstance(record.args, tuple):
                cleaned_args = list(record.args)
                for i, arg in enumerate(cleaned_args):
                    if isinstance(arg, str):
                        for pattern, repl in SENSITIVE_PATTERNS:
                            arg = pattern.sub(repl, arg)
                        cleaned_args[i] = arg
                record.args = tuple(cleaned_args)
        except Exception:
            pass
        return True
