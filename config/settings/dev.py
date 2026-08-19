"""Local development settings."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import BASE_DIR, INSTALLED_APPS, MIDDLEWARE, env  # noqa: F401

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Passwords are hashed with a fast hasher locally so fixtures and tests are quick.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# django-debug-toolbar is optional; skip silently when it is not installed.
try:  # pragma: no cover - dev convenience
    import debug_toolbar  # noqa: F401

    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware", *MIDDLEWARE]
    INTERNAL_IPS = ["127.0.0.1", "localhost"]
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: False}
except ImportError:
    pass

# Relaxed API throttling while developing.
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_THROTTLE_RATES": {
        "user": "100000/hour",
        "anon": "10000/hour",
        "ai": "10000/hour",
        "ai_terminal": "1000/hour",
    },
}

# Never lock a developer out of their own machine.
AXES_ENABLED = env.bool("AXES_ENABLED", default=False)
