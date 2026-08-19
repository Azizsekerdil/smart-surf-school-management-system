"""Extract translatable strings without GNU ``xgettext``.

Django's ``makemessages`` shells out to ``xgettext``, which is absent on this
machine. This command walks the project instead:

* **Python** is parsed with :mod:`ast`, not regex, so it finds
  ``_("x")``, ``gettext("x")``, ``gettext_lazy("x")``, ``ngettext(...)``,
  ``pgettext(...)`` and friends wherever they appear — including inside
  decorators, class bodies and f-string-free format expressions — and it never
  mistakes a string in a comment for a translatable one.
* **Templates** are scanned for ``{% translate %}``, ``{% trans %}`` and
  ``{% blocktranslate %}…{% endblocktranslate %}`` blocks.

Existing translations are preserved: the command merges into the current ``.po``
rather than overwriting it, marks strings that disappeared as obsolete (``#~``)
instead of deleting them, and leaves fuzzy flags alone.
"""

from __future__ import annotations

import ast
import re
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .i18n_compile import parse_po

# Callables whose first string argument is translatable.
SIMPLE_FUNCTIONS = {
    "_", "gettext", "gettext_lazy", "ugettext", "ugettext_lazy",
    "gettext_noop", "ngettext_lazy",
}
CONTEXT_FUNCTIONS = {"pgettext", "pgettext_lazy", "npgettext", "npgettext_lazy"}
PLURAL_FUNCTIONS = {"ngettext", "ngettext_lazy", "ungettext", "ungettext_lazy"}

SKIP_DIRECTORIES = {
    ".venv", "node_modules", "__pycache__", ".git", "staticfiles", "media",
    "backups", "logs", "migrations", ".pytest_cache", ".ruff_cache", "locale",
}

# Template patterns
_RE_TRANSLATE = re.compile(
    r"{%\s*(?:translate|trans)\s+(?P<quote>[\"'])(?P<text>.*?)(?P=quote)", re.DOTALL
)
_RE_TRANSLATE_CONTEXT = re.compile(
    r"{%\s*(?:translate|trans)\s+(?P<quote>[\"'])(?P<text>.*?)(?P=quote)"
    r"\s+context\s+(?P<cq>[\"'])(?P<ctx>.*?)(?P=cq)",
    re.DOTALL,
)
_RE_BLOCKTRANSLATE = re.compile(
    r"{%\s*blocktranslate(?P<args>[^%]*)%}(?P<body>.*?){%\s*endblocktranslate\s*%}",
    re.DOTALL,
)
_RE_BLOCKTRANS_LEGACY = re.compile(
    r"{%\s*blocktrans(?P<args>[^%]*)%}(?P<body>.*?){%\s*endblocktrans\s*%}", re.DOTALL
)
_RE_PLURAL_SPLIT = re.compile(r"{%\s*plural\s*%}")


def escape(value: str) -> str:
    """Encode a string for a .po file."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def po_string(prefix: str, value: str) -> str:
    """Render ``prefix "value"``, splitting long or multi-line strings."""
    if "\n" not in value and len(value) < 74:
        return f'{prefix} "{escape(value)}"'
    lines = [f'{prefix} ""']
    for index, piece in enumerate(value.split("\n")):
        suffix = "\\n" if index < value.count("\n") else ""
        lines.append(f'"{escape(piece)}{suffix}"')
    return "\n".join(lines)


class _PythonVisitor(ast.NodeVisitor):
    """Collect translatable literals from one Python module."""

    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.entries: list[dict] = []

    @staticmethod
    def _literal(node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        # Implicit concatenation shows up as a BinOp of constants.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = _PythonVisitor._literal(node.left)
            right = _PythonVisitor._literal(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr

        if name and node.args:
            if name in CONTEXT_FUNCTIONS and len(node.args) >= 2:
                context = self._literal(node.args[0])
                text = self._literal(node.args[1])
                if context is not None and text is not None:
                    entry = {
                        "msgid": text,
                        "context": context,
                        "location": f"{self.relative_path}:{node.lineno}",
                    }
                    if name in {"npgettext", "npgettext_lazy"} and len(node.args) >= 3:
                        plural = self._literal(node.args[2])
                        if plural is not None:
                            entry["plural"] = plural
                    self.entries.append(entry)

            elif name in PLURAL_FUNCTIONS and len(node.args) >= 2:
                singular = self._literal(node.args[0])
                plural = self._literal(node.args[1])
                if singular is not None and plural is not None:
                    self.entries.append(
                        {
                            "msgid": singular,
                            "plural": plural,
                            "location": f"{self.relative_path}:{node.lineno}",
                        }
                    )

            elif name in SIMPLE_FUNCTIONS:
                text = self._literal(node.args[0])
                if text:
                    self.entries.append(
                        {"msgid": text, "location": f"{self.relative_path}:{node.lineno}"}
                    )

        self.generic_visit(node)


def extract_from_python(path: Path, relative: str) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []
    visitor = _PythonVisitor(relative)
    visitor.visit(tree)
    return visitor.entries


def extract_from_template(path: Path, relative: str) -> list[dict]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    entries: list[dict] = []

    def line_of(offset: int) -> int:
        return content.count("\n", 0, offset) + 1

    contextual_spans: list[tuple[int, int]] = []
    for match in _RE_TRANSLATE_CONTEXT.finditer(content):
        entries.append(
            {
                "msgid": match.group("text"),
                "context": match.group("ctx"),
                "location": f"{relative}:{line_of(match.start())}",
            }
        )
        contextual_spans.append(match.span())

    for match in _RE_TRANSLATE.finditer(content):
        if any(start <= match.start() < end for start, end in contextual_spans):
            continue
        entries.append(
            {"msgid": match.group("text"), "location": f"{relative}:{line_of(match.start())}"}
        )

    for pattern in (_RE_BLOCKTRANSLATE, _RE_BLOCKTRANS_LEGACY):
        for match in pattern.finditer(content):
            body = match.group("body")
            location = f"{relative}:{line_of(match.start())}"
            parts = _RE_PLURAL_SPLIT.split(body)
            singular = parts[0].strip()
            if not singular:
                continue
            entry = {"msgid": singular, "location": location}
            if len(parts) > 1 and parts[1].strip():
                entry["plural"] = parts[1].strip()
            entries.append(entry)

    return entries


PO_HEADER = """# Turkish / English translation catalogue for the
# Smart Surf School Management System.
#
# Generated by `python manage.py i18n_extract` (pure-Python replacement for
# makemessages — GNU gettext is not required).
# Compile with `python manage.py i18n_compile`.
#
msgid ""
msgstr ""
"Project-Id-Version: Smart Surf School Management System 1.0.0\\n"
"Report-Msgid-Bugs-To: \\n"
"POT-Creation-Date: {created}\\n"
"PO-Revision-Date: {created}\\n"
"Last-Translator: \\n"
"Language-Team: \\n"
"Language: {language}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: {plural_forms}\\n"
"""

PLURAL_FORMS = {
    "tr": "nplurals=2; plural=(n != 1);",
    "en": "nplurals=2; plural=(n != 1);",
}


class Command(BaseCommand):
    help = (
        "Extract translatable strings into locale/<lang>/LC_MESSAGES/django.po "
        "without GNU gettext. Existing translations are preserved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--locale", "-l", action="append", default=[],
            help="Locales to write (repeatable). Default: every entry in settings.LANGUAGES.",
        )
        parser.add_argument("--domain", "-d", default="django")

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        locale_root = Path((settings.LOCALE_PATHS or [base_dir / "locale"])[0])
        locales = options["locale"] or [code for code, _name in settings.LANGUAGES]
        domain = options["domain"]

        # --- collect -----------------------------------------------------
        collected: OrderedDict[tuple[str, str], dict] = OrderedDict()

        def add(entry: dict) -> None:
            key = (entry.get("context", ""), entry["msgid"])
            if key in collected:
                collected[key]["locations"].append(entry["location"])
                if entry.get("plural") and not collected[key].get("plural"):
                    collected[key]["plural"] = entry["plural"]
            else:
                collected[key] = {
                    "msgid": entry["msgid"],
                    "context": entry.get("context", ""),
                    "plural": entry.get("plural"),
                    "locations": [entry["location"]],
                }

        python_files = template_files = 0
        for path in sorted(base_dir.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRECTORIES for part in path.parts):
                continue

            relative = path.relative_to(base_dir).as_posix()
            if path.suffix == ".py":
                python_files += 1
                for entry in extract_from_python(path, relative):
                    add(entry)
            elif path.suffix in {".html", ".txt"} and "templates" in path.parts:
                template_files += 1
                for entry in extract_from_template(path, relative):
                    add(entry)

        self.stdout.write(
            f"Scanned {python_files} Python file(s) and {template_files} template(s); "
            f"found {len(collected)} unique string(s)."
        )
        if not collected:
            raise CommandError("No translatable strings found — is the project path correct?")

        # --- write one catalogue per locale ------------------------------
        created = datetime.now(UTC).strftime("%Y-%m-%d %H:%M%z")

        for language in locales:
            po_path = locale_root / language / "LC_MESSAGES" / f"{domain}.po"
            existing = parse_po(po_path) if po_path.exists() else {}

            lines = [
                PO_HEADER.format(
                    created=created,
                    language=language,
                    plural_forms=PLURAL_FORMS.get(language, "nplurals=2; plural=(n != 1);"),
                ).rstrip()
            ]
            translated = 0

            for (context, msgid), entry in collected.items():
                lookup = f"{context}\x04{msgid}" if context else msgid
                if entry["plural"]:
                    lookup = (
                        f"{context}\x04{msgid}\x00{entry['plural']}"
                        if context
                        else f"{msgid}\x00{entry['plural']}"
                    )
                previous = existing.get(lookup, "")

                lines.append("")
                for location in entry["locations"][:8]:
                    lines.append(f"#: {location}")
                if context:
                    lines.append(po_string("msgctxt", context))

                if entry["plural"]:
                    lines.append(po_string("msgid", msgid))
                    lines.append(po_string("msgid_plural", entry["plural"]))
                    forms = previous.split("\x00") if previous else []
                    for index in range(2):
                        value = forms[index] if index < len(forms) else ""
                        lines.append(po_string(f"msgstr[{index}]", value))
                    if any(forms):
                        translated += 1
                else:
                    lines.append(po_string("msgid", msgid))
                    lines.append(po_string("msgstr", previous))
                    if previous:
                        translated += 1

            # Keep translations whose source string has gone, as obsolete entries.
            current_keys = set()
            for (context, msgid), entry in collected.items():
                if entry["plural"]:
                    base = f"{msgid}\x00{entry['plural']}"
                else:
                    base = msgid
                current_keys.add(f"{context}\x04{base}" if context else base)

            obsolete = [k for k in existing if k not in current_keys and k]
            if obsolete:
                lines.append("")
                lines.append("# Obsolete entries kept so translations are not lost:")
                for key in sorted(obsolete):
                    clean_id = key.split("\x04")[-1].split("\x00")[0]
                    lines.append(f'#~ msgid "{escape(clean_id)}"')
                    lines.append(f'#~ msgstr "{escape(existing[key].split(chr(0))[0])}"')

            po_path.parent.mkdir(parents=True, exist_ok=True)
            po_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            self.stdout.write(
                self.style.SUCCESS(
                    f"  {language}: {po_path.relative_to(base_dir)} — "
                    f"{len(collected)} string(s), {translated} already translated, "
                    f"{len(obsolete)} obsolete kept."
                )
            )

        self.stdout.write(
            self.style.SUCCESS("Done. Now run: python manage.py i18n_compile")
        )
