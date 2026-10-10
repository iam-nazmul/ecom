import os
import sys
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(int(default))).lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


TESTING = "test" in sys.argv

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.catalog",
    "apps.cart",
    "apps.orders",
    "apps.accounts",
    "apps.reviews",
    "apps.inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Optional apps: switched on/off by env flags. Nothing else imports them, so disabling
# one removes it completely (no URLs, models, migrations or menu links).
POS_ENABLED = env_bool("POS_ENABLED", False)
if POS_ENABLED:
    INSTALLED_APPS.append("apps.pos")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    # Storefront: Jinja2 templates in template/ (project) and <app>/jinja2/.
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "template"],
        "APP_DIRS": True,
        "OPTIONS": {"environment": "config.jinja2.environment"},
    },
    # Django admin keeps using the Django template language.
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "ecom"),
        "USER": os.environ.get("POSTGRES_USER", "ecom"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "ecom"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Dhaka")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or TESTING
            else "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14

# Running behind Nginx.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Store settings
CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "৳")
SHIPPING_FLAT_RATE = Decimal(os.environ.get("SHIPPING_FLAT_RATE", "5.00"))
FREE_SHIPPING_THRESHOLD = Decimal(os.environ.get("FREE_SHIPPING_THRESHOLD", "5000.00"))
PRODUCTS_PER_PAGE = 12

# Point of sale (apps/pos) — only used when POS_ENABLED.
POS_STORE_NAME = os.environ.get("POS_STORE_NAME", "Ecom Store")
POS_STORE_ADDRESS = os.environ.get("POS_STORE_ADDRESS", "Dhaka, Bangladesh")
POS_RECEIPT_FOOTER = os.environ.get("POS_RECEIPT_FOOTER", "Thank you for shopping with us!")
POS_MAX_DISCOUNT_PERCENT = Decimal(os.environ.get("POS_MAX_DISCOUNT_PERCENT", "20"))  # superusers: up to 100
