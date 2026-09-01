"""Desktop (PyInstaller) build settings.

Selected by ``launcher.py`` inside the packaged ``.exe``. Behaves like
production (DEBUG off, real error pages, manifest static files) but is tuned
for a single machine serving plain HTTP on the loopback interface:

* no TLS redirect / secure-cookie requirement (there is no certificate),
* Celery always eager — no Redis or worker process is ever required,
* the AI development terminal is off by default,
* media files are served by Django itself (there is no reverse proxy),
* the secret key is generated once and persisted next to the exe.

Development and real production deployments are untouched: this module is
only ever imported when ``DJANGO_SETTINGS_MODULE=config.settings.desktop``.
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import DATA_DIR, env

DEBUG = False

# The desktop app is reachable only from the machine it runs on.
ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]"],
)

# ---------------------------------------------------------------------------
# Secret key — generated once, persisted next to the exe
# ---------------------------------------------------------------------------
# A desktop user cannot be asked to invent a Django secret key. If none is
# provided via the environment / .env, one is generated and stored in
# ``.secret.key`` beside the exe so sessions survive restarts. The file name
# ends in ``.key`` and is therefore covered by .gitignore's ``*.key`` rule.
_key = env("DJANGO_SECRET_KEY", default="")
if not _key or "django-insecure" in _key:
    _key_file = DATA_DIR / ".secret.key"
    if _key_file.is_file():
        _key = _key_file.read_text(encoding="utf-8").strip()
    if not _key or "django-insecure" in _key:
        from django.core.management.utils import get_random_secret_key

        _key = get_random_secret_key()
        try:
            _key_file.write_text(_key, encoding="utf-8")
        except OSError:
            # Unwritable disk: the key stays valid for this process only.
            pass
SECRET_KEY = _key

# ---------------------------------------------------------------------------
# Plain HTTP on loopback — no certificate exists on a desktop install
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ---------------------------------------------------------------------------
# Background work runs in-process; Redis/Celery stay optional extras
# ---------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)

# The AI terminal is a developer tool, not an end-user feature.
AI_TERMINAL = {
    **globals()["AI_TERMINAL"],
    "ENABLED": env.bool("AI_TERMINAL_ENABLED", default=False),
}

# With DEBUG off Django normally refuses to serve media; the desktop build
# has no reverse proxy in front of it, so config/urls.py checks this flag
# and serves MEDIA_ROOT itself.
SERVE_MEDIA = True

# No DEBUG-level log noise in the packaged app.
LOGGING["loggers"]["apps"]["level"] = "INFO"  # noqa: F405
