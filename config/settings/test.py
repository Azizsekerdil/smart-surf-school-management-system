"""Test settings — fast, hermetic, and offline.

No test may reach the network: the AI and surf-condition providers are pointed
at unroutable addresses so an accidental live call fails immediately instead of
silently succeeding.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .base import *  # noqa: F403
from .base import AI, BASE_DIR, SURF  # noqa: F401

DEBUG = False
SECRET_KEY = "test-only-secret-key-not-used-anywhere-else-0123456789"  # noqa: S105 - documented non-secret, see the surrounding comment  # nosec B105
ALLOWED_HOSTS = ["*"]

# Source strings are English (see docs/DEVELOPMENT_CONTRACT.md §10), so tests
# assert English. Pinning the locale keeps them independent of the
# deployment default, which is Turkish.
LANGUAGE_CODE = "en"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "OPTIONS": {"timeout": 20},
    }
}

# MD5 keeps 2000+ tests fast; it is a test-run-only choice. Tests that assert
# the *production* hashing contract re-enable the real chain with
# ``override_settings(PASSWORD_HASHERS=PRODUCTION_PASSWORD_HASHERS)`` --
# see apps/accounts/tests/test_bootstrap_admin.py.
PRODUCTION_PASSWORD_HASHERS = list(globals()["PASSWORD_HASHERS"])
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

AXES_ENABLED = False

# Isolate all filesystem side effects into a temp directory.
_TMP = Path(tempfile.mkdtemp(prefix="surf_test_"))
MEDIA_ROOT = _TMP / "media"
BACKUP = {**globals()["BACKUP"], "ROOT": _TMP / "backups"}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Point every outbound integration at a blackhole address.
_BLACKHOLE = "http://127.0.0.1:1/v1"
AI = {
    **AI,
    "ROUTING_MODE": "auto",
    "REQUEST_TIMEOUT": 2,
    "PROVIDERS": {
        name: {**cfg, "BASE_URL": _BLACKHOLE}
        for name, cfg in AI["PROVIDERS"].items()
    },
}
SURF = {
    **SURF,
    "TIMEOUT_SECONDS": 2,
    "OPEN_METEO_FORECAST_URL": "http://127.0.0.1:1/forecast",
    "OPEN_METEO_MARINE_URL": "http://127.0.0.1:1/marine",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
