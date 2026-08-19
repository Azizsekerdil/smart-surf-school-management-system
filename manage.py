#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def main() -> None:
    # Default to the development settings module; override with DJANGO_SETTINGS_MODULE.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    # Make sure the project root is importable regardless of the working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - import guard
        raise ImportError(
            "Could not import Django. Are you sure it is installed and available on "
            "your PYTHONPATH environment variable? Did you forget to activate a "
            "virtual environment?\n"
            "    .\\.venv\\Scripts\\Activate.ps1"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
