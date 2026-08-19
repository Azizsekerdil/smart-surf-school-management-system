"""ASGI entrypoint.

``DJANGO_SETTINGS_MODULE`` is read from the **process environment**, and there
is deliberately no development default here.

Why that matters: ``.env`` is loaded from inside ``config/settings/base.py``,
which only runs *after* Django has already chosen a settings module. Setting
``DJANGO_SETTINGS_MODULE`` in ``.env`` therefore cannot influence this decision
for a WSGI/ASGI server. The previous default of ``config.settings.dev`` meant a
deployment that followed the documented procedure silently ran with
``DEBUG = True``, ``ALLOWED_HOSTS = ["*"]``, the insecure fallback secret key
and brute-force protection disabled -- bound to 0.0.0.0.

Set the variable in the service definition, the scheduled task, the container
environment or the shell that starts the server::

    setx DJANGO_SETTINGS_MODULE config.settings.prod     # Windows, persistent
    $env:DJANGO_SETTINGS_MODULE = "config.settings.prod" # Windows, this shell
    export DJANGO_SETTINGS_MODULE=config.settings.prod   # POSIX

``manage.py`` still defaults to the development profile, which is where a
default belongs: an interactive command run by a developer.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    raise RuntimeError(
        "DJANGO_SETTINGS_MODULE is not set. Set it in the process environment "
        "before starting the server -- for a real deployment that is "
        "config.settings.prod. It cannot be set in .env: .env is read from "
        "inside the settings module, which is chosen before .env is loaded."
    )

application = get_asgi_application()
