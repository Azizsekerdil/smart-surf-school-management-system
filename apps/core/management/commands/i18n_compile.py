"""Compile ``.po`` catalogues to ``.mo`` in pure Python.

Django's ``compilemessages`` shells out to GNU ``msgfmt``, which is not present
on a standard Windows install (verified: ``msgfmt``, ``xgettext`` and
``msgmerge`` are all absent on the target machine). Rather than making every
developer install the GNU gettext tools, this command writes the binary MO
format directly.

The MO format is small and stable (GNU gettext manual, "The Format of GNU MO
Files"):

    uint32  magic 0x950412de
    uint32  revision
    uint32  number of strings
    uint32  offset of the original-string table
    uint32  offset of the translation table
    uint32  hash table size
    uint32  hash table offset          (0 = no hash table; gettext copes)
    ...     the two offset/length tables, then the string data

Entries must be sorted by the original string, which is what the runtime binary
search relies on.
"""

from __future__ import annotations

import array
import re
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

MO_MAGIC = 0x950412DE

# Lines of a .po file we care about.
_RE_COMMENT = re.compile(r"^\s*#")
_RE_MSGCTXT = re.compile(r'^\s*msgctxt\s+"(.*)"\s*$')
_RE_MSGID = re.compile(r'^\s*msgid\s+"(.*)"\s*$')
_RE_MSGID_PLURAL = re.compile(r'^\s*msgid_plural\s+"(.*)"\s*$')
_RE_MSGSTR = re.compile(r'^\s*msgstr\s+"(.*)"\s*$')
_RE_MSGSTR_N = re.compile(r'^\s*msgstr\[(\d+)\]\s+"(.*)"\s*$')
_RE_CONTINUATION = re.compile(r'^\s*"(.*)"\s*$')


def unescape(value: str) -> str:
    """Decode the C-style escapes a .po file uses."""
    return (
        value.replace(r"\\n", "\n")
        .replace(r"\n", "\n")
        .replace(r"\t", "\t")
        .replace(r"\r", "\r")
        .replace(r"\"", '"')
        .replace(r"\\", "\\")
    )


def parse_po(path: Path) -> dict[str, str]:
    """Return ``{key: translation}`` for every translated entry in *path*.

    Keys use the gettext conventions: ``ctxt\\x04msgid`` for a context, and
    ``msgid\\x00msgid_plural`` for plurals (with translations joined by NUL).
    Untranslated and fuzzy entries are skipped, exactly as ``msgfmt`` does.
    """
    catalogue: dict[str, str] = {}

    context = msgid = msgid_plural = None
    plurals: dict[int, str] = {}
    msgstr = None
    current: str | None = None  # which field a continuation line extends
    is_fuzzy = False
    pending_fuzzy = False

    def flush() -> None:
        nonlocal context, msgid, msgid_plural, msgstr, plurals, is_fuzzy
        if msgid is not None and not is_fuzzy:
            if msgid_plural is not None:
                translations = [plurals[i] for i in sorted(plurals)] if plurals else []
                if any(translations):
                    key = f"{msgid}\x00{msgid_plural}"
                    if context:
                        key = f"{context}\x04{key}"
                    catalogue[key] = "\x00".join(translations)
            elif msgstr:
                key = f"{context}\x04{msgid}" if context else msgid
                catalogue[key] = msgstr
        context = msgid = msgid_plural = msgstr = None
        plurals = {}
        is_fuzzy = False

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")

            if _RE_COMMENT.match(line):
                if line.startswith("#,") and "fuzzy" in line:
                    pending_fuzzy = True
                continue

            if not line.strip():
                flush()
                current = None
                continue

            match = _RE_MSGCTXT.match(line)
            if match:
                flush()
                is_fuzzy = pending_fuzzy
                pending_fuzzy = False
                context = unescape(match.group(1))
                current = "ctxt"
                continue

            match = _RE_MSGID.match(line)
            if match:
                if msgid is not None and current in {"str", "plural"}:
                    flush()
                if context is None:
                    is_fuzzy = pending_fuzzy
                    pending_fuzzy = False
                msgid = unescape(match.group(1))
                current = "id"
                continue

            match = _RE_MSGID_PLURAL.match(line)
            if match:
                msgid_plural = unescape(match.group(1))
                current = "id_plural"
                continue

            match = _RE_MSGSTR_N.match(line)
            if match:
                index = int(match.group(1))
                plurals[index] = unescape(match.group(2))
                current = f"plural:{index}"
                continue

            match = _RE_MSGSTR.match(line)
            if match:
                msgstr = unescape(match.group(1))
                current = "str"
                continue

            match = _RE_CONTINUATION.match(line)
            if match and current:
                extra = unescape(match.group(1))
                if current == "ctxt":
                    context = (context or "") + extra
                elif current == "id":
                    msgid = (msgid or "") + extra
                elif current == "id_plural":
                    msgid_plural = (msgid_plural or "") + extra
                elif current == "str":
                    msgstr = (msgstr or "") + extra
                elif current.startswith("plural:"):
                    index = int(current.split(":", 1)[1])
                    plurals[index] = plurals.get(index, "") + extra
                continue

    flush()
    return catalogue


def write_mo(catalogue: dict[str, str], destination: Path) -> int:
    """Serialise *catalogue* to the binary MO format. Returns the entry count."""
    # The metadata entry (empty msgid) must be present for plural handling.
    entries = sorted(catalogue.items())

    keys = b""
    values = b""
    key_offsets: list[tuple[int, int]] = []
    value_offsets: list[tuple[int, int]] = []

    for key, value in entries:
        key_bytes = key.encode("utf-8")
        value_bytes = value.encode("utf-8")
        key_offsets.append((len(key_bytes), len(keys)))
        value_offsets.append((len(value_bytes), len(values)))
        keys += key_bytes + b"\x00"
        values += value_bytes + b"\x00"

    count = len(entries)
    header_size = 7 * 4
    key_table_offset = header_size
    value_table_offset = key_table_offset + count * 8
    keys_start = value_table_offset + count * 8
    values_start = keys_start + len(keys)

    output = array.array("B")
    output.frombytes(
        struct.pack(
            "<Iiiiiii",
            MO_MAGIC,
            0,                    # revision
            count,
            key_table_offset,
            value_table_offset,
            0,                    # hash table size (none)
            0,                    # hash table offset
        )
    )
    for length, offset in key_offsets:
        output.frombytes(struct.pack("<ii", length, keys_start + offset))
    for length, offset in value_offsets:
        output.frombytes(struct.pack("<ii", length, values_start + offset))
    output.frombytes(keys)
    output.frombytes(values)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output.tobytes())
    return count


class Command(BaseCommand):
    help = (
        "Compile .po files to .mo without GNU gettext. "
        "Use this instead of `compilemessages` on Windows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--locale", "-l", action="append", default=[],
            help="Only compile these locales (repeatable). Default: all.",
        )
        parser.add_argument(
            "--path", default=None,
            help="Locale directory. Default: the first entry of settings.LOCALE_PATHS.",
        )

    def handle(self, *args, **options):
        locale_paths = [Path(p) for p in (settings.LOCALE_PATHS or [])]
        if options["path"]:
            locale_paths = [Path(options["path"])]
        if not locale_paths:
            raise CommandError("No LOCALE_PATHS configured.")

        wanted = set(options["locale"]) or None
        total_files = total_entries = 0

        for root in locale_paths:
            if not root.is_dir():
                self.stdout.write(self.style.WARNING(f"Skipping missing directory: {root}"))
                continue

            for po_path in sorted(root.glob("*/LC_MESSAGES/*.po")):
                locale = po_path.parent.parent.name
                if wanted and locale not in wanted:
                    continue

                try:
                    catalogue = parse_po(po_path)
                except (OSError, UnicodeDecodeError) as exc:
                    raise CommandError(f"Could not read {po_path}: {exc}") from exc

                mo_path = po_path.with_suffix(".mo")
                entries = write_mo(catalogue, mo_path)
                total_files += 1
                total_entries += entries
                self.stdout.write(
                    f"  {locale}/{po_path.name} -> {mo_path.name}  "
                    f"({entries} translated entr{'y' if entries == 1 else 'ies'})"
                )

        if total_files == 0:
            self.stdout.write(self.style.WARNING("No .po files found."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Compiled {total_files} catalogue(s), {total_entries} entries total."
            )
        )
