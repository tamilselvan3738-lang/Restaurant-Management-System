"""
Django settings for restaurant_management project.
Production-ready, highly secure configuration.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file (override=True ensures .env values take precedence over stale shell variables)
load_dotenv(BASE_DIR / '.env', override=True)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("DJANGO_SECRET_KEY", "django-insecure-rms-production-key-change-in-env-please!")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = (os.environ.get("DEBUG") or os.environ.get("DJANGO_DEBUG", "True")).lower() in ("true", "1", "yes")

# Hosts Configuration
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if render_hostname:
    ALLOWED_HOSTS.append(render_hostname)

# Support additional hosts configured via environment variable
extra_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS")
if extra_hosts:
    if extra_hosts == "*":
        ALLOWED_HOSTS = ["*"]
    else:
        for host in extra_hosts.split(","):
            host = host.strip()
            if host and host not in ALLOWED_HOSTS:
                ALLOWED_HOSTS.append(host)


# Application definition

INSTALLED_APPS = [
    # Custom Core Apps with command overrides (ensures 0.0.0.0 runserver takes precedence)
    'accounts.apps.AccountsConfig',
    'daphne',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Channels
    'channels',

    # Custom Core Apps
    'superadmin.apps.SuperadminConfig',
    'menu.apps.MenuConfig',
    'tables.apps.TablesConfig',
    'orders.apps.OrdersConfig',
    'billing.apps.BillingConfig',
    'kitchen.apps.KitchenConfig',
    'dashboard.apps.DashboardConfig',
]

ASGI_APPLICATION = 'restaurant_management.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'accounts.middleware.TenantContextMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'restaurant_management.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'restaurant_management.context_processors.restaurant_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'restaurant_management.wsgi.application'


# Database
# Supports PostgreSQL in cloud deployments via DATABASE_URL or POSTGRES_* environment variables, with SQLite fallback
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
elif os.getenv('POSTGRES_DB'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('POSTGRES_DB', 'rms_db'),
            'USER': os.getenv('POSTGRES_USER', 'rms_user'),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'rms_secure_pass123'),
            'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


# Custom User Model
AUTH_USER_MODEL = 'accounts.User'


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
    {
        'NAME': 'accounts.validators.EnhancedSecurityPasswordValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
]

AUTHENTICATION_BACKENDS = [
    'accounts.backends.MultiCredentialBackend',
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

# Static files storage with WhiteNoise for production compression and caching
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
WHITENOISE_MANIFEST_STRICT = False

# Media files (User uploaded images like food items, receipts)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Authentication URLs
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'dashboard:index'
LOGOUT_REDIRECT_URL = 'accounts:login'

# Restaurant Configurations
RESTAURANT_NAME = os.getenv('RESTAURANT_NAME', 'The Grand Bistro')
RESTAURANT_CURRENCY = os.getenv('RESTAURANT_CURRENCY', '$')
RESTAURANT_TAX_RATE = float(os.getenv('RESTAURANT_TAX_RATE', '0.0825'))
RESTAURANT_SERVICE_CHARGE_RATE = float(os.getenv('RESTAURANT_SERVICE_CHARGE_RATE', '0.05'))

# Silenced system checks
# auth.W004: User.username is intentionally non-unique across restaurants and handled by MultiCredentialBackend
SILENCED_SYSTEM_CHECKS = ['auth.W004']

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Email & SMS Delivery Configuration
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '').replace(' ', '').strip()

if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    # When SMTP credentials are present, route mail through SMTP backend unless forced otherwise
    configured_backend = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
    if configured_backend == 'django.core.mail.backends.console.EmailBackend' and os.getenv('EMAIL_BACKEND_FORCE_CONSOLE', 'False').lower() not in ('true', '1', 'yes'):
        EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    else:
        EMAIL_BACKEND = configured_backend
else:
    EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')

DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', f"The Grand Bistro <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else 'noreply@restaurantpos.com')

# SMS Provider Integration (Fast2SMS / MSG91 / Twilio)
FAST2SMS_API_KEY = os.getenv('FAST2SMS_API_KEY', '')
FAST2SMS_ROUTE = os.getenv('FAST2SMS_ROUTE', 'q')
FAST2SMS_SENDER_ID = os.getenv('FAST2SMS_SENDER_ID', 'FSTSMS')
FAST2SMS_MESSAGE_ID = os.getenv('FAST2SMS_MESSAGE_ID', '')
MSG91_AUTH_KEY = os.getenv('MSG91_AUTH_KEY', '')
MSG91_TEMPLATE_ID = os.getenv('MSG91_TEMPLATE_ID', '')
MSG91_SENDER_ID = os.getenv('MSG91_SENDER_ID', 'RESTRO')
MSG91_DLT_TE_ID = os.getenv('MSG91_DLT_TE_ID', '')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN', '')
TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER', '')
SMS_PROVIDER_MOCK = os.getenv('SMS_PROVIDER_MOCK', 'False').lower() in ('true', '1')

# OTP & Password Recovery Configuration
OTP_EXPIRY_SECONDS = int(os.getenv('OTP_EXPIRY_SECONDS', '300'))  # 5 minutes
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv('OTP_RESEND_COOLDOWN_SECONDS', '60'))  # 60 seconds
OTP_MAX_VERIFY_ATTEMPTS = int(os.getenv('OTP_MAX_VERIFY_ATTEMPTS', '5'))  # 5 attempts
OTP_MAX_RESENDS = int(os.getenv('OTP_MAX_RESENDS', '3'))  # 3 resends per session
OTP_RESET_TOKEN_EXPIRY_SECONDS = int(os.getenv('OTP_RESET_TOKEN_EXPIRY_SECONDS', '600'))  # 10 minutes
OTP_DIGITS_COUNT = int(os.getenv('OTP_DIGITS_COUNT', '4'))


# Security Hardening
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True

# HTTPS, Proxy SSL & CSRF Configuration for Render & Production
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

if render_hostname:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_hostname}")

# Support additional trusted origins via environment variable or default to *.onrender.com
csrf_origins_env = os.environ.get("CSRF_TRUSTED_ORIGINS")
if csrf_origins_env:
    for origin in csrf_origins_env.split(","):
        origin = origin.strip()
        if origin and origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)
else:
    CSRF_TRUSTED_ORIGINS.append("https://*.onrender.com")

if not DEBUG:
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} [{name}:{lineno}] {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': True,
        },
        'restaurant_management': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

