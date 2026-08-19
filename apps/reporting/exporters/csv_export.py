"""CSV export tuned for the machine it will actually be opened on.

Two deliberate choices
----------------------
**UTF-8 with a BOM.** Excel on a Turkish Windows install assumes the legacy
Windows-1254 code page for a plain UTF-8 file, which turns every ``ş`` and ``ğ``
into mojibake. The byte-order mark is what makes Excel switch to UTF-8.

**Semicolon delimiter.** On a Turkish locale the list separator is ``;`` and the
decimal separator is ``,``. A comma-delimited file would split ``1.250,00``
across two columns. Numbers are formatted through Django's active locale, so
the delimiter and the decimals always agree with each other.
"""

from __future__ import annotations

import csv
import io

from django.utils.translation import gettext as _

from apps.core.csv_safety import safe_csv_writer

from .base import BaseExporter, ColumnKind, ReportData, display_value

DELIMITER = ";"
#: CRLF is what Excel expects; text editors cope with either.
LINE_TERMINATOR = "\r\n"
#: "utf-8-sig" writes the byte-order mark that makes Excel read UTF-8.
ENCODING = "utf-8-sig"


class CsvExporter(BaseExporter):
    """Flat CSV: header, rows, then the provenance block."""

    content_type = "text/csv; charset=utf-8"
    file_extension = "csv"
    label = "CSV"

    def __init__(self, include_context: bool = True):
        #: The filters and totals are appended *after* the data, separated by a
        #: blank line, so a naive reader that stops at the first blank line still
        #: gets a clean rectangle.
        self.include_context = include_context

    def render(self, data: ReportData) -> bytes:
        buffer = io.StringIO(newline="")
        writer = safe_csv_writer(
            buffer,
            delimiter=DELIMITER,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator=LINE_TERMINATOR,
        )

        if data.columns:
            writer.writerow([str(column) for column in data.columns])
            for row in data.rows:
                writer.writerow(data.display_row(row))

        if data.is_empty and data.message:
            writer.writerow([])
            writer.writerow([str(data.message)])

        if self.include_context:
            self._write_context(writer, data)

        return buffer.getvalue().encode(ENCODING)

    def _write_context(self, writer, data: ReportData) -> None:
        writer.writerow([])
        writer.writerow([str(_("Report")), str(data.title)])
        if data.subtitle:
            writer.writerow([str(_("Scope")), str(data.subtitle)])
        writer.writerow([str(_("Generated at")), display_value(data.generated_at)])
        writer.writerow([str(_("Rows")), display_value(data.row_count, ColumnKind.NUMBER)])
        if data.truncated_at:
            writer.writerow(
                [
                    str(_("Row limit reached")),
                    display_value(data.truncated_at, ColumnKind.NUMBER),
                ]
            )

        filters = data.filter_items()
        if filters:
            writer.writerow([])
            writer.writerow([str(_("Applied filters"))])
            for label, value in filters:
                writer.writerow([label, value])

        summary = data.summary_items()
        if summary:
            writer.writerow([])
            writer.writerow([str(_("Summary"))])
            for label, value in summary:
                writer.writerow([label, value])


__all__ = ["CsvExporter"]
