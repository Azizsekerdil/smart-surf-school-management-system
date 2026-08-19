"""Subprocess execution for the AI terminal.

Everything here assumes the command has *already* been validated by
:mod:`apps.ai_terminal.security`. This module's job is to run an argument vector
safely and make sure it cannot outlive its timeout or flood the database.

Windows specifics that matter
-----------------------------
* ``CREATE_NEW_PROCESS_GROUP`` puts the child in its own group so a timeout can
  kill the whole tree rather than orphaning grandchildren (``pytest`` spawning
  workers, ``git`` spawning a pager).
* ``Popen.kill()`` only kills the direct child, so on timeout we escalate to
  ``taskkill /T /F`` for the process tree.
* A clean environment is passed: the child never inherits API keys.
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .security import get_workspace

logger = logging.getLogger("apps.ai_terminal")

IS_WINDOWS = sys.platform == "win32"

#: Environment variables that must never reach a child process.
SECRET_ENV_PREFIXES = (
    "NVIDIA_", "ANTHROPIC_", "OPENAI_", "AWS_", "AZURE_", "GITHUB_", "GH_",
    "STORMGLASS_", "DJANGO_SECRET", "DATABASE_URL", "EMAIL_HOST_PASSWORD",
    "FIELD_ENCRYPTION_KEY", "LM_STUDIO_API_KEY",
)
SECRET_ENV_NAMES = {"SECRET_KEY", "PASSWORD", "TOKEN", "API_KEY"}


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error

    @property
    def combined_output(self) -> str:
        parts = [self.stdout]
        if self.stderr:
            parts.append(f"\n--- stderr ---\n{self.stderr}")
        return "".join(parts).strip()


def build_environment() -> dict[str, str]:
    """A minimal environment with every credential stripped."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(SECRET_ENV_PREFIXES)
        and not any(marker in key.upper() for marker in SECRET_ENV_NAMES)
    }
    # Deterministic, non-interactive behaviour from the tools we allow.
    environment.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_TERMINAL_PROMPT": "0",  # never block waiting for credentials
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "NO_COLOR": "1",
            "DJANGO_SETTINGS_MODULE": os.environ.get(
                "DJANGO_SETTINGS_MODULE", "config.settings.dev"
            ),
        }
    )
    return environment


def resolve_executable(name: str) -> str:
    """Map an allowlisted name to a concrete path.

    ``python`` resolves to the project's virtualenv interpreter, not whatever
    happens to be first on PATH, so the terminal always operates on this
    project's dependencies.
    """
    workspace = get_workspace()
    if name in {"python", "python.exe"}:
        candidate = workspace / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")
        if candidate.exists():
            return str(candidate)
        return sys.executable

    if IS_WINDOWS:
        scripts = workspace / ".venv" / "Scripts"
        for suffix in (".exe", ".cmd", ".bat", ""):
            candidate = scripts / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
    else:
        candidate = workspace / ".venv" / "bin" / name
        if candidate.exists():
            return str(candidate)

    return name  # fall back to PATH resolution by the OS


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Terminate the child and everything it spawned."""
    if process.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(  # nosec
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],  # noqa: S607 - resolved through PATH on purpose so the platform's own toolchain is used
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), 9)
    except Exception:  # noqa: BLE001 - best-effort cleanup
        logger.warning("taskkill failed for PID %s; falling back to kill()", process.pid)
    finally:
        try:
            process.kill()
        except Exception:  # noqa: BLE001, S110 - deliberate best-effort cleanup; a failure here must not break the caller  # nosec B110
            pass


def execute(
    argv: list[str],
    *,
    timeout: int | None = None,
    max_output_bytes: int | None = None,
    cwd: Path | None = None,
) -> ExecutionResult:
    """Run *argv* with no shell. Never raises."""
    if not argv:
        return ExecutionResult(-1, "", "", 0, error="Empty command.")

    timeout = timeout or settings.AI_TERMINAL.get("TIMEOUT_SECONDS", 120)
    max_output = max_output_bytes or settings.AI_TERMINAL.get("MAX_OUTPUT_BYTES", 200_000)
    workspace = get_workspace()
    working_directory = Path(cwd).resolve() if cwd else workspace

    if not working_directory.is_relative_to(workspace):
        return ExecutionResult(-1, "", "", 0, error="Working directory is outside the workspace.")

    resolved = [resolve_executable(argv[0]), *argv[1:]]

    creation_flags = 0
    preexec = None
    if IS_WINDOWS:
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:  # pragma: no cover - the target platform is Windows
        preexec = os.setsid

    started = time.perf_counter()
    process = None
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is validated; shell=False
            resolved,
            cwd=str(working_directory),
            env=build_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # nothing can prompt for input
            shell=False,  # nosec B603
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
            preexec_fn=preexec,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        exit_code = process.returncode
        timed_out = False

    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
        exit_code, timed_out = -1, True
        stderr = f"{stderr}\n[Terminated after {timeout}s timeout]".strip()

    except FileNotFoundError:
        return ExecutionResult(
            -1, "", "", int((time.perf_counter() - started) * 1000),
            error=f"Executable not found: {argv[0]}",
        )
    except PermissionError:
        return ExecutionResult(
            -1, "", "", int((time.perf_counter() - started) * 1000),
            error=f"Permission denied running: {argv[0]}",
        )
    except OSError as exc:
        return ExecutionResult(
            -1, "", "", int((time.perf_counter() - started) * 1000),
            error=f"Could not start process: {exc}",
        )

    duration = int((time.perf_counter() - started) * 1000)

    truncated = False
    if len(stdout) > max_output:
        stdout = stdout[:max_output] + f"\n[... output truncated at {max_output} bytes ...]"
        truncated = True
    if len(stderr) > max_output:
        stderr = stderr[:max_output] + "\n[... truncated ...]"
        truncated = True

    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration,
        timed_out=timed_out,
        truncated=truncated,
    )
