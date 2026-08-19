"""Format registry: one lookup for the whole application.

Views, the REST API, the scheduler and the admin all resolve an exporter through
:func:`get_exporter`, so the set of supported formats is defined exactly once.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from .base import BaseExporter
from .csv_export import CsvExporter
from .excel import ExcelExporter
from .pdf import PdfExporter

PDF = "pdf"
EXCEL = "excel"
CSV = "csv"

#: format key -> exporter class
EXPORT_FORMATS: dict[str, type[BaseExporter]] = {
    PDF: PdfExporter,
    EXCEL: ExcelExporter,
    CSV: CsvExporter,
}

#: format key -> label for a select box
FORMAT_LABELS: dict[str, object] = {
    PDF: _("PDF document"),
    EXCEL: _("Excel workbook"),
    CSV: _("CSV file"),
}

#: format key -> icon name vendored in scripts/vendor_assets.js
FORMAT_ICONS: dict[str, str] = {
    PDF: "file-text",
    EXCEL: "file-spreadsheet",
    CSV: "file",
}


class UnknownExportFormat(ValueError):
    """Raised when an unsupported format key reaches the export engine."""


def get_exporter(fmt: str) -> BaseExporter:
    """Return a ready-to-use exporter for ``"pdf"``, ``"excel"`` or ``"csv"``."""
    key = (fmt or "").strip().lower()
    exporter_class = EXPORT_FORMATS.get(key)
    if exporter_class is None:
        raise UnknownExportFormat(
            _("Unsupported export format: %(format)s") % {"format": fmt}
        )
    return exporter_class()


def available_formats() -> list[tuple[str, object]]:
    """``[(key, label)]`` for form choices, in a stable order."""
    return [(key, FORMAT_LABELS[key]) for key in (PDF, EXCEL, CSV)]


def format_icon(fmt: str) -> str:
    return FORMAT_ICONS.get((fmt or "").lower(), "file")


__all__ = [
    "PDF",
    "EXCEL",
    "CSV",
    "EXPORT_FORMATS",
    "FORMAT_LABELS",
    "FORMAT_ICONS",
    "UnknownExportFormat",
    "get_exporter",
    "available_formats",
    "format_icon",
]
