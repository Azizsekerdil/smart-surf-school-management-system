"""Security boundary for the AI Development Terminal.

Threat model
------------
The agent's input is partly attacker-influenced: it reads source files, database
rows, customer notes and documents, any of which could contain text crafted to
make it run something harmful. So the rule is simple and absolute:

    **A command's safety is decided by this module, never by the model.**

Controls implemented here
-------------------------
1. **No shell, ever.** Commands are executed as an argument vector with
   ``shell=False``. A string is only ever *parsed* into a vector here, and the
   parse rejects every shell metacharacter, so ``&``, ``|``, ``;``, backticks,
   ``$(...)`` and redirection cannot chain a second command.
2. **Executable allowlist.** Only a fixed set of development tools can run.
   Anything else is refused outright — not "approved with a warning".
3. **Sub-command rules.** ``git status`` is safe; ``git push`` is not.
   ``python manage.py test`` is safe; ``python -c <anything>`` is arbitrary code
   execution and is blocked.
4. **Workspace jail.** Every path argument is resolved and must live inside the
   configured workspace. Windows-specific escapes are handled: drive-relative
   paths (``C:foo``), UNC paths, 8.3 short names, reserved device names
   (``CON``, ``NUL``, ``COM1``), and NTFS alternate data streams.
5. **Human approval gate.** Commands classified ``REQUIRES_APPROVAL`` are stored
   and do nothing until a user with ``ai_terminal.approve`` approves them.
6. **Everything is audited**, including refusals.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PureWindowsPath

from django.conf import settings
from django.utils.translation import gettext_lazy as _


class Risk(str, Enum):
    """Outcome of validating a proposed command."""

    SAFE = "safe"                          # run immediately
    REQUIRES_APPROVAL = "requires_approval"  # a human must say yes
    BLOCKED = "blocked"                    # never runs, no override in the UI


@dataclass
class ValidationResult:
    risk: Risk
    argv: list[str] = field(default_factory=list)
    reason: str = ""
    matched_rule: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.risk == Risk.BLOCKED

    @property
    def is_safe(self) -> bool:
        return self.risk == Risk.SAFE


# ---------------------------------------------------------------------------
# Lexical rejection
# ---------------------------------------------------------------------------
#: Characters that only make sense if you are trying to reach a shell.
SHELL_METACHARACTERS = re.compile(r"[;&|`$><\n\r\x00]|\$\(|\|\||&&")

#: Substrings that indicate an attempt to break out, whatever the executable.
FORBIDDEN_SUBSTRINGS = (
    "rm -rf",
    "del /f",
    "del /s",
    "format ",
    "mkfs",
    ":(){",           # fork bomb
    "curl ",
    "wget ",
    "invoke-webrequest",
    "iwr ",
    "start-process",
    "new-object net.webclient",
    "powershell",
    "cmd.exe",
    "cmd /c",
    "/etc/passwd",
    "shadow",
    "reg add",
    "reg delete",
    "schtasks",
    "net user",
    "netsh",
    "taskkill",
    "shutdown",
    "base64 -d",
    "certutil",
    "bitsadmin",
)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CommandRule:
    """One executable and what may follow it."""

    executable: str
    description: str
    #: First arguments that are safe to run without asking.
    safe_subcommands: frozenset[str] = frozenset()
    #: First arguments that run only after human approval.
    approval_subcommands: frozenset[str] = frozenset()
    #: First arguments that are never allowed.
    blocked_subcommands: frozenset[str] = frozenset()
    #: When True, an executable with no recognised sub-command is still safe.
    bare_is_safe: bool = False


#: ``manage.py`` sub-commands, classified separately because that is where the
#: dangerous operations live (``flush`` wipes the database; ``migrate`` changes
#: its schema).
MANAGE_SAFE = frozenset(
    {
        "check", "showmigrations", "diffsettings", "validate", "test",
        "makemessages", "compilemessages", "collectstatic", "shell_plus",
        "inspectdb", "dumpdata", "sqlmigrate", "spectacular", "help",
        "seed_demo_data", "bootstrap_roles", "health",
    }
)
MANAGE_APPROVAL = frozenset(
    {"makemigrations", "migrate", "loaddata", "createsuperuser", "changepassword", "backup", "restore"}
)
MANAGE_BLOCKED = frozenset({"flush", "sqlflush", "reset_db", "shell", "dbshell", "runserver"})

#: ``git`` sub-commands. Anything that rewrites history or contacts a remote
#: needs a human, because it is either destructive or publishes data.
# ``config`` is deliberately NOT here. Several git configuration keys hold
# values that git itself executes through a shell -- ``core.fsmonitor`` runs
# during an ordinary ``git status``, which is on this list. Auto-approving
# ``git config <key> <value>`` would therefore hand the module's headline
# guarantee ("no shell, ever") away in one command, and the workspace jail
# does not catch it because the jail inspects path-shaped arguments, not
# configuration values.
GIT_SAFE = frozenset(
    {"status", "diff", "log", "show", "branch", "remote", "blame",
     "shortlog", "describe", "ls-files", "rev-parse", "stash"}
)
GIT_APPROVAL = frozenset(
    {"add", "commit", "checkout", "switch", "restore", "merge", "tag", "fetch",
     "pull", "config"}
)
GIT_BLOCKED = frozenset({"push", "reset", "rebase", "clean", "filter-branch", "filter-repo", "gc", "prune", "reflog"})

#: ``pip``: reading is fine, mutating the environment is not.
PIP_SAFE = frozenset({"list", "show", "freeze", "check", "--version"})
PIP_BLOCKED = frozenset({"install", "uninstall", "download", "wheel", "config"})

ALLOWED_COMMANDS: dict[str, CommandRule] = {
    "git": CommandRule(
        "git",
        _("Version control"),
        safe_subcommands=GIT_SAFE,
        approval_subcommands=GIT_APPROVAL,
        blocked_subcommands=GIT_BLOCKED,
    ),
    "python": CommandRule(
        "python",
        _("Python interpreter (manage.py and modules only)"),
        safe_subcommands=frozenset({"manage.py", "-m", "--version", "-V"}),
    ),
    "pytest": CommandRule("pytest", _("Test runner"), bare_is_safe=True),
    "ruff": CommandRule("ruff", _("Linter and formatter"), bare_is_safe=True),
    "bandit": CommandRule("bandit", _("Security static analysis"), bare_is_safe=True),
    "pip": CommandRule(
        "pip",
        _("Package manager (read-only)"),
        safe_subcommands=PIP_SAFE,
        blocked_subcommands=PIP_BLOCKED,
    ),
    "coverage": CommandRule("coverage", _("Coverage reporting"), bare_is_safe=True),
}

#: ``python -m <module>``: only these modules may be run.
ALLOWED_PYTHON_MODULES = frozenset(
    {"pytest", "ruff", "bandit", "coverage", "pip", "json.tool", "compileall", "unittest"}
)

#: ``pip`` reached via ``python -m pip`` is still read-only.
PYTHON_MODULE_SUBCOMMAND_RULES = {"pip": (PIP_SAFE, PIP_BLOCKED)}


# ---------------------------------------------------------------------------
# Workspace jail
# ---------------------------------------------------------------------------
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def get_workspace() -> Path:
    """The single directory the terminal may touch."""
    configured = settings.AI_TERMINAL.get("WORKSPACE") or settings.BASE_DIR
    return Path(configured).resolve()


def is_within_workspace(candidate: str | Path) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a path argument.

    Rejects, in order: NUL bytes, NTFS alternate data streams, reserved device
    names, UNC paths, drive-relative paths, and finally anything that resolves
    outside the workspace (which covers ``..``, symlinks and junctions).
    """
    workspace = get_workspace()
    raw = str(candidate)

    if "\x00" in raw:
        return False, "Path contains a NUL byte."

    # NTFS alternate data stream: "file.txt:hidden". A drive letter (C:\...) is
    # a colon at index 1 and is legitimate.
    without_drive = raw[2:] if len(raw) > 2 and raw[1] == ":" else raw
    if ":" in without_drive:
        return False, "Alternate data streams are not permitted."

    pure = PureWindowsPath(raw)

    if pure.drive.startswith("\\\\"):
        return False, "UNC network paths are not permitted."

    # "C:foo" is relative to the *current directory on drive C*, not to C:\.
    if pure.drive and not pure.root:
        return False, "Drive-relative paths are ambiguous and not permitted."

    for part in pure.parts:
        stem = part.split(".")[0].upper()
        if stem in _WINDOWS_RESERVED:
            return False, f"'{part}' is a reserved device name."
        # 8.3 short names (PROGRA~1) can alias a long path past a naive check.
        if "~" in part and re.search(r"~\d", part):
            return False, "Short (8.3) path names are not permitted."

    try:
        resolved = (workspace / raw).resolve() if not pure.is_absolute() else Path(raw).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        return False, f"Path could not be resolved: {type(exc).__name__}"

    if not resolved.is_relative_to(workspace):
        return False, f"Path escapes the workspace ({workspace})."

    # Never allow the terminal near secrets, even inside the workspace.
    protected = {".env", ".git/config", "db.sqlite3"}
    relative = resolved.relative_to(workspace).as_posix()
    if relative in protected or relative.startswith((".venv/", "backups/", "media/private/")):
        return False, f"'{relative}' is protected."

    return True, ""


def _looks_like_path(token: str) -> bool:
    """Heuristic: does this argument reference the filesystem?"""
    if token.startswith("-"):
        return False
    return any(marker in token for marker in ("/", "\\", ".py", ".html", ".json", ".txt", ".md", ".."))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def parse_command(command: str) -> tuple[list[str] | None, str]:
    """Split a command string into an argv vector, or explain why not.

    ``posix=False`` keeps Windows backslash paths intact — ``shlex`` in POSIX
    mode would eat them as escape characters.
    """
    command = (command or "").strip()
    if not command:
        return None, "Empty command."
    if len(command) > 2000:
        return None, "Command is too long."

    if SHELL_METACHARACTERS.search(command):
        return None, (
            "Shell metacharacters are not permitted. Commands run without a shell, "
            "so chaining, piping and redirection are unavailable by design."
        )

    lowered = command.lower()
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle in lowered:
            return None, f"Command contains the forbidden sequence '{needle.strip()}'."

    try:
        argv = shlex.split(command, posix=False)
    except ValueError as exc:
        return None, f"Command could not be parsed: {exc}"

    # shlex(posix=False) keeps surrounding quotes; strip them.
    argv = [token.strip('"').strip("'") for token in argv if token.strip('"').strip("'")]
    if not argv:
        return None, "Empty command."
    return argv, ""


def normalise_executable(token: str) -> str:
    """Reduce ``.\\.venv\\Scripts\\python.exe`` to ``python``."""
    name = PureWindowsPath(token).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def validate_command(command: str) -> ValidationResult:
    """Classify *command* as safe, needing approval, or blocked."""
    if not settings.AI_TERMINAL.get("ENABLED", False):
        return ValidationResult(Risk.BLOCKED, reason=str(_("The AI terminal is disabled.")))

    argv, error = parse_command(command)
    if argv is None:
        return ValidationResult(Risk.BLOCKED, reason=error, matched_rule="lexical")

    executable = normalise_executable(argv[0])
    rule = ALLOWED_COMMANDS.get(executable)
    if rule is None:
        return ValidationResult(
            Risk.BLOCKED,
            argv=argv,
            reason=(
                f"'{executable}' is not on the allowlist. Permitted: "
                f"{', '.join(sorted(ALLOWED_COMMANDS))}."
            ),
            matched_rule="allowlist",
        )

    arguments = argv[1:]

    # --- path arguments must stay inside the workspace --------------------
    for token in arguments:
        if _looks_like_path(token):
            ok, why = is_within_workspace(token)
            if not ok:
                return ValidationResult(
                    Risk.BLOCKED, argv=argv, reason=f"{why} ({token})", matched_rule="workspace"
                )

    # --- python needs its own analysis ------------------------------------
    if executable == "python":
        return _validate_python(argv, arguments)

    if not arguments:
        if rule.bare_is_safe:
            return ValidationResult(Risk.SAFE, argv=argv, matched_rule=executable)
        return ValidationResult(
            Risk.REQUIRES_APPROVAL,
            argv=argv,
            reason=f"'{executable}' without a sub-command.",
            matched_rule=executable,
        )

    subcommand = arguments[0].lower()

    if subcommand in rule.blocked_subcommands:
        return ValidationResult(
            Risk.BLOCKED,
            argv=argv,
            reason=(
                f"'{executable} {subcommand}' is never permitted from the terminal — "
                "it is destructive, rewrites history, or publishes data."
            ),
            matched_rule=f"{executable}.{subcommand}",
        )
    if subcommand in rule.safe_subcommands:
        return ValidationResult(Risk.SAFE, argv=argv, matched_rule=f"{executable}.{subcommand}")
    if subcommand in rule.approval_subcommands:
        return ValidationResult(
            Risk.REQUIRES_APPROVAL,
            argv=argv,
            reason=f"'{executable} {subcommand}' changes state and needs approval.",
            matched_rule=f"{executable}.{subcommand}",
        )
    if rule.bare_is_safe and subcommand.startswith("-"):
        return ValidationResult(Risk.SAFE, argv=argv, matched_rule=executable)
    if rule.bare_is_safe:
        return ValidationResult(Risk.SAFE, argv=argv, matched_rule=executable)

    return ValidationResult(
        Risk.REQUIRES_APPROVAL,
        argv=argv,
        reason=f"'{executable} {subcommand}' is not a recognised sub-command.",
        matched_rule=f"{executable}.unknown",
    )


def _validate_python(argv: list[str], arguments: list[str]) -> ValidationResult:
    """Python is an interpreter — most of its flags are arbitrary code execution."""
    if not arguments:
        return ValidationResult(
            Risk.BLOCKED,
            argv=argv,
            reason="An interactive Python REPL is not available from the terminal.",
            matched_rule="python.repl",
        )

    first = arguments[0]

    # -c / -i / - are arbitrary code, whatever follows.
    if first in {"-c", "-i", "-"} or first.startswith("-c"):
        return ValidationResult(
            Risk.BLOCKED,
            argv=argv,
            reason="'python -c' executes arbitrary code and is never permitted.",
            matched_rule="python.-c",
        )

    if first in {"--version", "-V"}:
        return ValidationResult(Risk.SAFE, argv=argv, matched_rule="python.version")

    if first == "-m":
        if len(arguments) < 2:
            return ValidationResult(
                Risk.BLOCKED, argv=argv, reason="'python -m' needs a module.", matched_rule="python.-m"
            )
        module = arguments[1].lower()
        if module not in ALLOWED_PYTHON_MODULES:
            return ValidationResult(
                Risk.BLOCKED,
                argv=argv,
                reason=(
                    f"Module '{module}' is not permitted. Allowed: "
                    f"{', '.join(sorted(ALLOWED_PYTHON_MODULES))}."
                ),
                matched_rule="python.-m",
            )
        safe_set, blocked_set = PYTHON_MODULE_SUBCOMMAND_RULES.get(module, (None, None))
        if blocked_set is not None and len(arguments) > 2:
            module_subcommand = arguments[2].lower()
            if module_subcommand in blocked_set:
                return ValidationResult(
                    Risk.BLOCKED,
                    argv=argv,
                    reason=f"'python -m {module} {module_subcommand}' modifies the environment.",
                    matched_rule=f"python.-m.{module}",
                )
            if safe_set and module_subcommand not in safe_set:
                return ValidationResult(
                    Risk.REQUIRES_APPROVAL,
                    argv=argv,
                    reason=f"Unrecognised '{module}' sub-command.",
                    matched_rule=f"python.-m.{module}",
                )
        return ValidationResult(Risk.SAFE, argv=argv, matched_rule=f"python.-m.{module}")

    # python manage.py <subcommand>
    if PureWindowsPath(first).name.lower() == "manage.py":
        if len(arguments) < 2:
            return ValidationResult(
                Risk.SAFE, argv=argv, matched_rule="python.manage.help"
            )
        management_command = arguments[1].lower()
        if management_command in MANAGE_BLOCKED:
            return ValidationResult(
                Risk.BLOCKED,
                argv=argv,
                reason=(
                    f"'manage.py {management_command}' is not permitted: it destroys data "
                    "or opens an interactive session."
                ),
                matched_rule=f"manage.{management_command}",
            )
        if management_command in MANAGE_SAFE:
            return ValidationResult(
                Risk.SAFE, argv=argv, matched_rule=f"manage.{management_command}"
            )
        if management_command in MANAGE_APPROVAL:
            return ValidationResult(
                Risk.REQUIRES_APPROVAL,
                argv=argv,
                reason=f"'manage.py {management_command}' changes data or schema.",
                matched_rule=f"manage.{management_command}",
            )
        return ValidationResult(
            Risk.REQUIRES_APPROVAL,
            argv=argv,
            reason=f"'manage.py {management_command}' is not a known command.",
            matched_rule="manage.unknown",
        )

    # Running an arbitrary .py file is arbitrary code execution.
    return ValidationResult(
        Risk.BLOCKED,
        argv=argv,
        reason="Only 'manage.py' and allowlisted modules may be run with python.",
        matched_rule="python.script",
    )


def describe_policy() -> dict:
    """Machine-readable policy summary, rendered on the terminal help panel."""
    return {
        "workspace": str(get_workspace()),
        "enabled": settings.AI_TERMINAL.get("ENABLED", False),
        "timeout_seconds": settings.AI_TERMINAL.get("TIMEOUT_SECONDS", 120),
        "max_output_bytes": settings.AI_TERMINAL.get("MAX_OUTPUT_BYTES", 200_000),
        "allowed_executables": sorted(ALLOWED_COMMANDS),
        "always_blocked": {
            "git": sorted(GIT_BLOCKED),
            "manage.py": sorted(MANAGE_BLOCKED),
            "pip": sorted(PIP_BLOCKED),
            "python": ["-c", "-i", "arbitrary scripts"],
        },
        "needs_approval": {
            "git": sorted(GIT_APPROVAL),
            "manage.py": sorted(MANAGE_APPROVAL),
        },
        "no_shell": True,
    }
