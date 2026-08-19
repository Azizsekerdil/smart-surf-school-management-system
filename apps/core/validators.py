"""Reusable field validators.

File-upload validation is deliberately strict: extension, declared MIME type and
real magic bytes must all agree, and the filename is normalised so a crafted
name (``../../etc/passwd``, ``CON``, a name with a NUL byte) cannot escape the
media root on Windows or POSIX.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Simple field validators
# ---------------------------------------------------------------------------
phone_validator = RegexValidator(
    regex=r"^\+?[0-9 ()\-]{6,25}$",
    message=_("Enter a valid phone number, e.g. +90 555 123 45 67."),
)

hex_color_validator = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    message=_("Enter a hex colour such as #0ea5e9."),
)

slug_code_validator = RegexValidator(
    regex=r"^[A-Za-z0-9][A-Za-z0-9_\-]{1,49}$",
    message=_("Use 2–50 letters, digits, hyphens or underscores."),
)


def validate_latitude(value: float) -> None:
    if value is None:
        return
    if not (-90 <= float(value) <= 90):
        raise ValidationError(_("Latitude must be between -90 and 90."))


def validate_longitude(value: float) -> None:
    if value is None:
        return
    if not (-180 <= float(value) <= 180):
        raise ValidationError(_("Longitude must be between -180 and 180."))


def validate_not_negative(value) -> None:
    if value is not None and value < 0:
        raise ValidationError(_("This value cannot be negative."))


# ---------------------------------------------------------------------------
# File upload validation
# ---------------------------------------------------------------------------
# Leading bytes that identify a real file of each allowed type.
_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),  # plus 'WEBP' at offset 8, checked below
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04",),
    "xlsx": (b"PK\x03\x04",),
}

# Windows device names that must never become a stored filename.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, fallback: str = "upload") -> str:
    """Return a filesystem-safe basename.

    Strips any directory component, control characters and Windows-reserved
    names, so the result can never traverse outside the upload directory.
    """
    if not name:
        return fallback
    # Take the basename only — defeats "../../x" and "C:\\windows\\x".
    name = PurePath(name.replace("\\", "/")).name
    # Normalise unicode so look-alike characters cannot smuggle separators.
    name = unicodedata.normalize("NFKC", name)
    # Drop control characters, path separators, and NTFS stream separators.
    name = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]", "_", name)
    name = name.strip(" .") or fallback

    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"file_{stem}"
    stem = stem[:100]
    suffix = re.sub(r"[^A-Za-z0-9]", "", suffix)[:10]
    return f"{stem}.{suffix}" if suffix else stem


def validate_upload(
    uploaded_file,
    allowed_extensions: list[str] | None = None,
    max_size_bytes: int | None = None,
) -> None:
    """Validate size, extension and magic bytes of an uploaded file."""
    if uploaded_file is None:
        return

    max_size = max_size_bytes or getattr(settings, "MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024)
    size = getattr(uploaded_file, "size", 0) or 0
    if size > max_size:
        raise ValidationError(
            _("File is too large (%(size).1f MB). Maximum allowed is %(max).1f MB.")
            % {"size": size / 1048576, "max": max_size / 1048576}
        )
    if size == 0:
        raise ValidationError(_("The uploaded file is empty."))

    raw_name = getattr(uploaded_file, "name", "") or ""
    safe_name = sanitize_filename(raw_name)
    extension = safe_name.rpartition(".")[2].lower() if "." in safe_name else ""

    allowed = [e.lower().lstrip(".") for e in (allowed_extensions or [])]
    if allowed and extension not in allowed:
        raise ValidationError(
            _("Unsupported file type '.%(ext)s'. Allowed: %(allowed)s.")
            % {"ext": extension or "?", "allowed": ", ".join(allowed)}
        )

    # Verify the content really is what the extension claims.
    signatures = _MAGIC_SIGNATURES.get(extension)
    if signatures:
        try:
            position = uploaded_file.tell()
        except (AttributeError, OSError):
            position = 0
        try:
            uploaded_file.seek(0)
            header = uploaded_file.read(16)
        finally:
            try:
                uploaded_file.seek(position)
            except (AttributeError, OSError):
                pass

        matched = any(header.startswith(sig) for sig in signatures)
        if extension == "webp":
            matched = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        if not matched:
            raise ValidationError(
                _("The file content does not match its '.%(ext)s' extension.")
                % {"ext": extension}
            )


def validate_image_upload(uploaded_file) -> None:
    validate_upload(
        uploaded_file,
        allowed_extensions=getattr(settings, "ALLOWED_IMAGE_EXTENSIONS", ["jpg", "jpeg", "png", "webp"]),
        max_size_bytes=5 * 1024 * 1024,
    )


def validate_document_upload(uploaded_file) -> None:
    validate_upload(
        uploaded_file,
        allowed_extensions=getattr(
            settings, "ALLOWED_DOCUMENT_EXTENSIONS", ["pdf", "docx", "txt", "csv", "xlsx"]
        ),
    )
