"""Production settings — hardened, fails fast on missing configuration."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import env

DEBUG = False

# --- Fail fast rather than run insecurely ----------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
if "django-insecure" in SECRET_KEY or len(SECRET_KEY) < 40:
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be a real random key of at least 40 characters in "
        "production. Generate one with:\n"
        "  python -c \"from django.core.management.utils import get_random_secret_key as k; print(k())\""
    )

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise RuntimeError("DJANGO_ALLOWED_HOSTS must list explicit hostnames in production.")

# --- Transport security -----------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# --- Passwords --------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

# --- Brute-force protection stays on ---------------------------------------
AXES_ENABLED = True

# --- The AI terminal is a developer tool, not a production feature ----------
AI_TERMINAL = {**globals()["AI_TERMINAL"], "ENABLED": env.bool("AI_TERMINAL_ENABLED", default=False)}

# --- Logging: no DEBUG noise -----------------------------------------------
LOGGING["loggers"]["apps"]["level"] = "INFO"  # noqa: F405
