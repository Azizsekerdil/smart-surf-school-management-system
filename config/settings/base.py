"""
Shared Django settings for the Smart Surf School Management System.

Design rules enforced here
--------------------------
1. Nothing secret is ever hard-coded. Every credential comes from the
   environment (``.env`` / real environment variables).
2. The application must start and remain usable when optional infrastructure
   (PostgreSQL, Redis, Celery, LM Studio, cloud AI) is unavailable.
3. Settings are grouped into namespaced dicts (``SURF``, ``AI``, ``BACKUP``,
   ``AI_TERMINAL``) so feature modules never reach for loose globals.
"""

from __future__ import annotations

from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# config/settings/base.py -> config/settings -> config -> <project root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env = environ.Env()

# Read .env if present. Absence is not an error: every value below has a
# development-safe default so `python manage.py runserver` works on a fresh
# checkout with zero configuration.
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.exists():
    env.read_env(str(_ENV_FILE))

DEBUG = env.bool("DJANGO_DEBUG", default=True)

# A deterministic development key is used only when DEBUG is on. `prod.py`
# refuses to boot without a real key.
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-dev-only-key-do-not-use-in-production-0000000000",
)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]", "testserver"],
)

CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:8000", "http://127.0.0.1:8000"],
)

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    # Makes logout, password change, capability revocation and deactivation
    # actually invalidate an issued refresh token. Without it, rotation leaves
    # the superseded token usable for its full 7-day lifetime.
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "django_htmx",
    "axes",
]

# Every business capability lives in its own app under `apps/`.
LOCAL_APPS = [
    # --- foundation -------------------------------------------------------
    "apps.core",
    "apps.accounts",
    "apps.audit",
    # --- CRM / people -----------------------------------------------------
    "apps.customers",
    "apps.students",
    "apps.instructors",
    "apps.crm",
    # --- operations -------------------------------------------------------
    "apps.locations",
    "apps.lessons",
    "apps.bookings",
    "apps.surf_camps",
    # --- equipment --------------------------------------------------------
    "apps.equipment",
    "apps.rentals",
    "apps.maintenance",
    # --- surf / safety ----------------------------------------------------
    "apps.surf_conditions",
    "apps.safety",
    # --- business ---------------------------------------------------------
    "apps.finance",
    "apps.pos",
    "apps.analytics",
    "apps.reporting",
    # --- platform ---------------------------------------------------------
    "apps.notifications",
    "apps.backups",
    "apps.dashboard",
    # --- artificial intelligence -----------------------------------------
    "apps.ai",
    "apps.ai_terminal",
    # --- guidance ---------------------------------------------------------
    "apps.help_center",
    "apps.training",
    "apps.onboarding",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # LocaleMiddleware must sit after Session and before Common so the active
    # language can come from the session / cookie / Accept-Language header.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Records who is performing the current request so model signals can write
    # audit entries without threading the request through every call site.
    "apps.audit.middleware.AuditContextMiddleware",
    # Holds an account carrying must_change_password on the change-password
    # screen. Placed after authentication so request.user is resolved, and
    # before the views so no screen can be reached around it.
    "apps.accounts.middleware.ForcePasswordChangeMiddleware",
    # Must be last: django-axes needs the fully resolved user.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.site_context",
                "apps.core.context_processors.navigation",
                "apps.notifications.context_processors.unread_notifications",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# SQLite by default so a fresh clone runs immediately; PostgreSQL in production
# by pointing DATABASE_URL at a postgres:// DSN.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # WAL + a real busy timeout keep the dev database usable while Celery-eager
    # tasks and the AI terminal touch it concurrently.
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].update(
        {
            "timeout": 20,
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA foreign_keys=ON;"
            ),
            "transaction_mode": "IMMEDIATE",
        }
    )
    DATABASES["default"]["CONN_MAX_AGE"] = 0

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    # django-axes must be first so lockouts are enforced before any real check.
    "axes.backends.AxesStandaloneBackend",
    # Subclasses ModelBackend, so it supplies the permission methods too.
    # Django's bare ModelBackend must NOT be listed after it: authenticate()
    # tries every backend in turn and accepts the first success, so a plain
    # ModelBackend would re-authorise the sign-ins this backend deliberately
    # refuses -- in particular a remote attempt with the first-run bootstrap
    # credential.
    "apps.accounts.backends.EmailOrUsernameModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    # Refuses the documented first-run password explicitly, so the promise
    # "admin/admin is dead after the first change" does not rely on a
    # third-party word list.
    {"NAME": "apps.accounts.validators.RejectBootstrapPasswordValidator"},
]

# Argon2id first: it is the hasher OWASP recommends for new applications and
# the one the bootstrap contract requires. The others stay in the list so an
# existing database keeps working and is transparently upgraded on next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 12  # 12 hours
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = False  # HTMX reads the token from the cookie
CSRF_COOKIE_SAMESITE = "Lax"

# --- django-axes (brute-force protection) ---------------------------------
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=8)
AXES_COOLOFF_TIME = 0.25  # 15 minutes
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ACCESS_FAILURE_LOG = True
AXES_LOCKOUT_TEMPLATE = "accounts/lockout.html"
AXES_VERBOSE = False

# ---------------------------------------------------------------------------
# Internationalisation — full Turkish + English support
# ---------------------------------------------------------------------------
LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE", default="tr")
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Europe/Istanbul")
USE_I18N = True
USE_L10N = True
USE_TZ = True

from django.utils.translation import gettext_lazy as _  # noqa: E402

LANGUAGES = [
    ("tr", _("Türkçe")),
    ("en", _("English")),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = "surf_language"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Upload hardening — see apps/core/validators.py for per-field enforcement.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000
MAX_UPLOAD_SIZE_BYTES = env.int("MAX_UPLOAD_SIZE_BYTES", default=10 * 1024 * 1024)
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif"]
ALLOWED_DOCUMENT_EXTENSIONS = ["pdf", "doc", "docx", "txt", "md", "csv", "xlsx"]

# ---------------------------------------------------------------------------
# Cache — Redis when reachable, local memory otherwise (never fails hard)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="")


def _build_caches() -> dict:
    """Return a Redis cache config when Redis is reachable, else local memory."""
    if REDIS_URL:
        try:
            import redis as _redis  # noqa: PLC0415 - optional dependency probe

            client = _redis.from_url(REDIS_URL, socket_connect_timeout=1)
            client.ping()
            return {
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": REDIS_URL,
                    "TIMEOUT": 300,
                }
            }
        except Exception:  # noqa: BLE001, S110 - Redis is strictly optional; deliberate best-effort cleanup; a failure here must not break the caller  # nosec B110
            pass
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "surf-school-locmem",
            "TIMEOUT": 300,
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }


CACHES = _build_caches()
CACHE_BACKEND_IS_REDIS = "redis" in CACHES["default"]["BACKEND"].lower()

# ---------------------------------------------------------------------------
# Celery — optional. Eager mode keeps every feature working without a worker.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="cache+memory://")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=not CACHE_BACKEND_IS_REDIS)
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540

# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------
if env("EMAIL_BACKEND_MODE", default="console") == "smtp":
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST", default="")
    EMAIL_PORT = env.int("EMAIL_PORT", default=587)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@surfschool.local")

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.api.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "2000/hour",
        "anon": "60/hour",
        "ai": "120/hour",
        "ai_terminal": "60/hour",
    },
    "EXCEPTION_HANDLER": "apps.core.api.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Smart Surf School Management System API",
    "DESCRIPTION": (
        "REST API for surf school operations: CRM, lessons, bookings, camps, "
        "equipment, rentals, maintenance, surf conditions, safety, finance, "
        "POS, analytics, reporting, backups and AI services."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/",
    "SORT_OPERATIONS": True,
}

# Rotation without blacklisting is rotation in name only: the superseded
# refresh token stays valid, so a stolen token keeps minting access tokens for
# its full lifetime and the theft is not even detectable. The blacklist app is
# installed (see LOCAL_APPS/THIRD_PARTY_APPS) and enabled here.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": __import__("datetime").timedelta(hours=2),
    "REFRESH_TOKEN_LIFETIME": __import__("datetime").timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Business defaults
# ---------------------------------------------------------------------------
SCHOOL = {
    "NAME": env("SCHOOL_NAME", default="Smart Surf School"),
    "CURRENCY": env("SCHOOL_CURRENCY", default="TRY"),
    "CURRENCY_SYMBOL": {"TRY": "₺", "EUR": "€", "USD": "$", "GBP": "£"}.get(
        env("SCHOOL_CURRENCY", default="TRY"), env("SCHOOL_CURRENCY", default="TRY")
    ),
    "DEFAULT_LATITUDE": env.float("SCHOOL_DEFAULT_LATITUDE", default=38.28),
    "DEFAULT_LONGITUDE": env.float("SCHOOL_DEFAULT_LONGITUDE", default=26.37),
    "DEFAULT_SPOT_NAME": env("SCHOOL_DEFAULT_SPOT_NAME", default="Alaçatı"),
}

# ---------------------------------------------------------------------------
# Surf / weather data providers
# ---------------------------------------------------------------------------
SURF = {
    "PROVIDER": env("SURF_PROVIDER", default="open-meteo"),
    "CACHE_SECONDS": env.int("SURF_PROVIDER_CACHE_SECONDS", default=1800),
    "TIMEOUT_SECONDS": env.int("SURF_PROVIDER_TIMEOUT", default=20),
    # LICENCE NOTE: Open-Meteo's *data* is CC BY 4.0 (commercial use allowed),
    # but the *free hosted service* is non-commercial only. Setting
    # COMMERCIAL_MODE=True switches the default provider to met.no (CC BY 4.0,
    # commercial permitted) unless a paid Open-Meteo key is supplied.
    # See docs/research/SURF_WEATHER_APIS.md.
    "COMMERCIAL_MODE": env.bool("SURF_COMMERCIAL_MODE", default=False),
    "ATTRIBUTION_REQUIRED": True,
    "OPEN_METEO_FORECAST_URL": env(
        "OPEN_METEO_FORECAST_URL", default="https://api.open-meteo.com/v1/forecast"
    ),
    "OPEN_METEO_MARINE_URL": env(
        "OPEN_METEO_MARINE_URL", default="https://marine-api.open-meteo.com/v1/marine"
    ),
    # A paid key also changes the host to customer-api.open-meteo.com.
    "OPEN_METEO_API_KEY": env("OPEN_METEO_API_KEY", default=""),
    # met.no requires an identifying User-Agent as a terms-of-service condition.
    "METNO_URL": "https://api.met.no/weatherapi/locationforecast/2.0/compact",
    "METNO_USER_AGENT": env(
        "METNO_USER_AGENT",
        default="SmartSurfSchool/1.0 (https://github.com/Azizsekerdil/smart-surf-school-management-system)",
    ),
    "STORMGLASS_API_KEY": env("STORMGLASS_API_KEY", default=""),
    "STORMGLASS_URL": "https://api.stormglass.io/v2/weather/point",
}

# ---------------------------------------------------------------------------
# Artificial intelligence
# ---------------------------------------------------------------------------
AI = {
    # auto | local_only | cloud_only
    # Privacy-safe default: cloud routing requires an explicit operator choice.
    "ROUTING_MODE": env("AI_ROUTING_MODE", default="local_only"),
    "DEFAULT_PROVIDER": env("AI_DEFAULT_PROVIDER", default="lmstudio"),
    "REQUEST_TIMEOUT": env.int("AI_REQUEST_TIMEOUT", default=120),
    "MAX_TOKENS": env.int("AI_MAX_TOKENS", default=2048),
    "PROVIDERS": {
        "lmstudio": {
            "ENABLED": True,
            "BASE_URL": env("LM_STUDIO_BASE_URL", default="http://localhost:1234/v1"),
            "API_KEY": env("LM_STUDIO_API_KEY", default="lm-studio"),
            "MODELS": {
                "general": env("LM_STUDIO_MODEL_GENERAL", default="google/gemma-4-12b-qat"),
                "vision": env("LM_STUDIO_MODEL_VISION", default="qwen/qwen3-vl-8b"),
                "math": env("LM_STUDIO_MODEL_MATH", default="qwen2.5-math-7b-instruct"),
                "light_vision": env(
                    "LM_STUDIO_MODEL_LIGHT_VISION", default="moondream-2b-2025-04-14"
                ),
                "embedding": env(
                    "LM_STUDIO_MODEL_EMBEDDING",
                    default="text-embedding-nomic-embed-text-v1.5",
                ),
            },
        },
        "nvidia": {
            "ENABLED": bool(env("NVIDIA_API_KEY", default="")),
            "BASE_URL": env("NVIDIA_BASE_URL", default="https://integrate.api.nvidia.com/v1"),
            "API_KEY": env("NVIDIA_API_KEY", default=""),
            # Populated from docs/research/NVIDIA_MODEL_SELECTION.md; overridable
            # at runtime from the AI Control Center.
            "MODELS": {},
        },
        "anthropic": {
            "ENABLED": bool(env("ANTHROPIC_API_KEY", default="")),
            "BASE_URL": env("ANTHROPIC_BASE_URL", default="https://api.anthropic.com/v1"),
            "API_KEY": env("ANTHROPIC_API_KEY", default=""),
            "MODELS": {"general": env("ANTHROPIC_MODEL", default="claude-sonnet-5")},
        },
        "openai_compat": {
            "ENABLED": bool(env("OPENAI_COMPAT_BASE_URL", default="")),
            "BASE_URL": env("OPENAI_COMPAT_BASE_URL", default=""),
            "API_KEY": env("OPENAI_COMPAT_API_KEY", default=""),
            "MODELS": {"general": env("OPENAI_COMPAT_MODEL", default="")},
        },
    },
}

# ---------------------------------------------------------------------------
# AI Development Terminal — security boundary
# ---------------------------------------------------------------------------
AI_TERMINAL = {
    "ENABLED": env.bool("AI_TERMINAL_ENABLED", default=True),
    "WORKSPACE": Path(env("AI_TERMINAL_WORKSPACE", default=str(BASE_DIR))).resolve(),
    "TIMEOUT_SECONDS": env.int("AI_TERMINAL_TIMEOUT_SECONDS", default=120),
    "MAX_OUTPUT_BYTES": env.int("AI_TERMINAL_MAX_OUTPUT_BYTES", default=200_000),
    # Must stay False on any shared machine: it disables the allowlist.
    "ALLOW_UNSAFE": env.bool("AI_TERMINAL_ALLOW_UNSAFE", default=False),
}

# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
BACKUP = {
    "ROOT": Path(env("BACKUP_ROOT", default=str(BASE_DIR / "backups"))),
    "RETENTION_DAILY": env.int("BACKUP_RETENTION_DAILY", default=7),
    "RETENTION_WEEKLY": env.int("BACKUP_RETENTION_WEEKLY", default=4),
    "RETENTION_MONTHLY": env.int("BACKUP_RETENTION_MONTHLY", default=12),
    "PG_DUMP_PATH": env("PG_DUMP_PATH", default=""),
    "PG_RESTORE_PATH": env("PG_RESTORE_PATH", default=""),
    "INCLUDE_MEDIA": env.bool("BACKUP_INCLUDE_MEDIA", default=True),
}

# Encryption key for provider credentials stored in the database.
# NOTE: a FIELD_ENCRYPTION_KEY setting used to live here and advertised
# "encryption of provider API keys stored in the database". No such code ever
# existed, and the design is the opposite: provider keys are read from the
# environment and are never written to the database at all
# (apps/ai/models.py). The dead setting has been removed rather than left
# advertising a control the product does not implement. The environment
# variable name is still stripped from AI-terminal child processes
# (apps/ai_terminal/executor.py) so an operator who kept it in their .env is
# not surprised.

# ---------------------------------------------------------------------------
# Logging — structured, with automatic secret redaction
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_secrets": {"()": "apps.core.logging.SecretRedactionFilter"},
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname:<8} {name}:{lineno} [{process}] {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname:<8} {name}: {message}", "style": "{"},
        "json": {"()": "apps.core.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["redact_secrets"],
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "surf_school.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "encoding": "utf-8",
            "formatter": "verbose",
            "filters": ["redact_secrets"],
        },
        "security_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "security.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 20,
            "encoding": "utf-8",
            "formatter": "json",
            "filters": ["redact_secrets"],
        },
        "ai_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "ai.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "encoding": "utf-8",
            "formatter": "json",
            "filters": ["redact_secrets"],
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.request": {
            "handlers": ["console", "file"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console", "file"], "level": "DEBUG" if DEBUG else "INFO", "propagate": False},
        "apps.ai": {"handlers": ["console", "ai_file"], "level": "INFO", "propagate": False},
        "apps.ai_terminal": {
            "handlers": ["console", "ai_file", "security_file"],
            "level": "INFO",
            "propagate": False,
        },
        "apps.audit": {"handlers": ["security_file"], "level": "INFO", "propagate": False},
        "axes": {"handlers": ["security_file"], "level": "WARNING", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Messages framework -> Tailwind alert classes
# ---------------------------------------------------------------------------
from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "debug",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "error",
}

# ---------------------------------------------------------------------------
# Security defaults (tightened further in prod.py)
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Application version, surfaced in the UI footer and the health endpoint.
APP_VERSION = "1.0.0"
