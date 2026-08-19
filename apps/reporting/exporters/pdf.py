"""PDF export built on ReportLab's platypus API.

Why ReportLab and not an HTML-to-PDF engine
-------------------------------------------
WeasyPrint needs GTK, which is not installable as a plain wheel on Windows, and
this system has to run natively on a Windows 11 desk in a surf school office.
ReportLab is pure Python with a BSD licence and ships in ``requirements.txt``.

What the document guarantees
----------------------------
* The school name, the report title, the generation timestamp and the applied
  filters appear on the page — a printed report can never be mistaken for a
  different period's numbers.
* The header row repeats on every page and rows are zebra-striped, so a
  20-page equipment inventory stays readable on paper.
* ``Page N of M`` in the footer, which needs the total page count and is
  therefore drawn in a second pass (see :class:`_NumberedCanvas`).
* Turkish characters render correctly: a Unicode TrueType font is registered
  (Bitstream Vera ships inside ReportLab and covers ğ, ı, İ, ş, ç, ö, ü).
"""

from __future__ import annotations

import functools
import io
import logging
import os
from pathlib import Path

import reportlab
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .base import (
    LANDSCAPE,
    NUMERIC_KINDS,
    BaseExporter,
    ColumnKind,
    ReportData,
    display_value,
    school_name,
)

logger = logging.getLogger(__name__)

# --- brand ----------------------------------------------------------------
BRAND = colors.HexColor("#0083ce")
BRAND_DARK = colors.HexColor("#075985")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
HAIRLINE = colors.HexColor("#cbd5e1")
STRIPE = colors.HexColor("#f1f5f9")
PANEL = colors.HexColor("#f8fafc")

#: Above this many columns the page is turned on its side automatically.
WIDE_COLUMN_THRESHOLD = 6
#: Beyond this many rows the splitting-friendly LongTable is used.
LONG_TABLE_ROWS = 150
#: Rows sampled when guessing column widths — a full scan of a huge export is
#: pure waste, and the first few hundred rows describe the shape well enough.
WIDTH_SAMPLE_ROWS = 300


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
def _font_candidates() -> list[tuple[str, str, str]]:
    """``(family, regular_path, bold_path)`` in order of preference."""
    bundled = Path(reportlab.__file__).parent / "fonts"
    candidates = [
        # Ships inside ReportLab, so it is always present and the export never
        # depends on what fonts the host machine happens to have installed.
        ("SurfSans", str(bundled / "Vera.ttf"), str(bundled / "VeraBd.ttf")),
        # Nicer typography where the OS provides it.
        ("SurfSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("SurfSans", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("SurfSans", r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    ]
    return candidates


@functools.lru_cache(maxsize=1)
def resolve_fonts() -> tuple[str, str]:
    """Register and return ``(regular, bold)`` font names.

    Falls back to Helvetica when no TrueType font can be registered. That
    fallback uses WinAnsi encoding, which has no glyphs for ``ğ ı İ ş`` — the
    Turkish-specific letters would be dropped. It exists only so a broken font
    installation degrades the typography instead of failing the export.
    """
    for family, regular, bold in _font_candidates():
        if not (os.path.exists(regular) and os.path.exists(bold)):
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, regular))
            pdfmetrics.registerFont(TTFont(f"{family}-Bold", bold))
        except Exception:  # noqa: BLE001 - a corrupt font must not break exports
            logger.warning("Could not register PDF font %s", regular, exc_info=True)
            continue
        pdfmetrics.registerFontFamily(family, normal=family, bold=f"{family}-Bold")
        return family, f"{family}-Bold"

    logger.warning(
        "No Unicode TTF available for PDF export; falling back to Helvetica. "
        "Turkish characters will not render."
    )
    return "Helvetica", "Helvetica-Bold"


# ---------------------------------------------------------------------------
# Canvas with a two-pass page counter
# ---------------------------------------------------------------------------
class _NumberedCanvas(rl_canvas.Canvas):
    """Buffers pages so the footer can print ``Page N of M``.

    ReportLab draws pages as it lays them out, at which point the total is not
    known yet. Every page state is kept and replayed once the document is
    complete.
    """

    def __init__(self, *args, footer_left: str = "", font_name: str = "Helvetica", **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: list[dict] = []
        self._footer_left = footer_left
        self._footer_font = font_name

    def showPage(self):  # noqa: N802 - ReportLab API
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):  # noqa: D102 - ReportLab API
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        # ``_pageNumber`` is part of the state restored above, so it always
        # matches the page currently being replayed.
        width, _height = self._pagesize
        self.saveState()
        self.setStrokeColor(HAIRLINE)
        self.setLineWidth(0.5)
        self.line(14 * mm, 12 * mm, width - 14 * mm, 12 * mm)
        self.setFont(self._footer_font, 7.5)
        self.setFillColor(MUTED)
        self.drawString(14 * mm, 8 * mm, self._footer_left[:120])
        self.drawRightString(
            width - 14 * mm,
            8 * mm,
            _("Page %(page)s of %(total)s") % {"page": self._pageNumber, "total": total},
        )
        self.restoreState()


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------
class PdfExporter(BaseExporter):
    """Branded, paginated PDF."""

    content_type = "application/pdf"
    file_extension = "pdf"
    label = "PDF"

    def render(self, data: ReportData) -> bytes:
        regular, bold = resolve_fonts()
        styles = self._styles(regular, bold)

        wide = data.effective_orientation == LANDSCAPE or len(data.columns) > WIDE_COLUMN_THRESHOLD
        pagesize = landscape(A4) if wide else A4

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=pagesize,
            leftMargin=14 * mm,
            rightMargin=14 * mm,
            topMargin=30 * mm,
            bottomMargin=16 * mm,
            title=str(data.title),
            author=school_name(),
            subject=str(data.subtitle or data.title),
            creator="Smart Surf School Management System",
        )

        story: list = []
        story.extend(self._filters_block(data, styles))
        story.extend(self._table_block(data, styles, doc.width))
        story.extend(self._summary_block(data, styles, doc.width))

        header = functools.partial(self._draw_header, data=data, regular=regular, bold=bold)
        canvasmaker = functools.partial(
            _NumberedCanvas,
            footer_left=f"{school_name()} · {data.title}",
            font_name=regular,
        )
        doc.build(story, onFirstPage=header, onLaterPages=header, canvasmaker=canvasmaker)
        return buffer.getvalue()

    # --- styles -----------------------------------------------------------
    def _styles(self, regular: str, bold: str) -> dict:
        return {
            "body": ParagraphStyle(
                "body", fontName=regular, fontSize=9, leading=12, textColor=INK
            ),
            "muted": ParagraphStyle(
                "muted", fontName=regular, fontSize=8, leading=11, textColor=MUTED
            ),
            "section": ParagraphStyle(
                "section",
                fontName=bold,
                fontSize=10,
                leading=13,
                textColor=BRAND_DARK,
                spaceAfter=4,
            ),
            "cell": ParagraphStyle(
                "cell", fontName=regular, fontSize=8, leading=10, textColor=INK
            ),
            "cellHeader": ParagraphStyle(
                "cellHeader",
                fontName=bold,
                fontSize=8,
                leading=10,
                textColor=colors.white,
                alignment=TA_LEFT,
            ),
            "cellHeaderRight": ParagraphStyle(
                "cellHeaderRight",
                fontName=bold,
                fontSize=8,
                leading=10,
                textColor=colors.white,
                alignment=TA_RIGHT,
            ),
            "notice": ParagraphStyle(
                "notice",
                fontName=regular,
                fontSize=9.5,
                leading=13,
                textColor=BRAND_DARK,
                backColor=PANEL,
                borderColor=HAIRLINE,
                borderWidth=0.5,
                borderPadding=8,
            ),
            "regular_font": regular,
            "bold_font": bold,
        }

    # --- page furniture ---------------------------------------------------
    def _draw_header(self, canvas, doc, *, data: ReportData, regular: str, bold: str) -> None:
        width, height = doc.pagesize
        canvas.saveState()

        canvas.setFillColor(BRAND)
        canvas.rect(0, height - 6 * mm, width, 6 * mm, stroke=0, fill=1)

        canvas.setFont(bold, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(14 * mm, height - 13 * mm, school_name().upper())

        canvas.setFont(bold, 15)
        canvas.setFillColor(INK)
        canvas.drawString(14 * mm, height - 21 * mm, str(data.title)[:90])

        if data.subtitle:
            canvas.setFont(regular, 8.5)
            canvas.setFillColor(MUTED)
            canvas.drawString(14 * mm, height - 26 * mm, str(data.subtitle)[:130])

        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(
            width - 14 * mm,
            height - 13 * mm,
            _("Generated %(when)s") % {"when": display_value(data.generated_at)},
        )
        canvas.drawRightString(
            width - 14 * mm,
            height - 18 * mm,
            _("%(count)s rows") % {"count": display_value(data.row_count, ColumnKind.NUMBER)},
        )

        canvas.setStrokeColor(HAIRLINE)
        canvas.setLineWidth(0.5)
        canvas.line(14 * mm, height - 29 * mm, width - 14 * mm, height - 29 * mm)
        canvas.restoreState()

    # --- content blocks ---------------------------------------------------
    def _filters_block(self, data: ReportData, styles: dict) -> list:
        items = data.filter_items()
        if not items:
            return []
        text = "   ·   ".join(f"<b>{_escape(label)}:</b> {_escape(value)}" for label, value in items)
        return [
            Paragraph(_("Applied filters"), styles["section"]),
            Paragraph(text, styles["muted"]),
            Spacer(1, 6 * mm),
        ]

    def _table_block(self, data: ReportData, styles: dict, available: float) -> list:
        if data.is_empty:
            message = data.message or _("No records matched the selected filters.")
            return [Paragraph(_escape(message), styles["notice"]), Spacer(1, 6 * mm)]

        font_size = _table_font_size(len(data.columns))
        cell_style = ParagraphStyle(
            "cellSized", parent=styles["cell"], fontSize=font_size, leading=font_size + 2
        )
        header_style = ParagraphStyle(
            "headSized", parent=styles["cellHeader"], fontSize=font_size, leading=font_size + 2
        )
        header_right = ParagraphStyle(
            "headSizedRight",
            parent=styles["cellHeaderRight"],
            fontSize=font_size,
            leading=font_size + 2,
        )

        numeric = [i for i in range(len(data.columns)) if data.kind(i) in NUMERIC_KINDS]

        table_data: list[list] = [
            [
                Paragraph(_escape(str(header)), header_right if i in numeric else header_style)
                for i, header in enumerate(data.columns)
            ]
        ]
        for row in data.rows:
            rendered: list = []
            for index, value in enumerate(row):
                text = display_value(value, data.kind(index), data.currency)
                if index in numeric or len(text) <= 18:
                    rendered.append(text)
                else:
                    rendered.append(Paragraph(_escape(text), cell_style))
            table_data.append(rendered)

        widths = _column_widths(data, available, font_size)
        table_class = LongTable if len(data.rows) > LONG_TABLE_ROWS else Table
        table = table_class(table_data, colWidths=widths, repeatRows=1, hAlign="LEFT")

        style = [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, 1), (-1, -1), styles["regular_font"]),
            ("FONTSIZE", (0, 1), (-1, -1), font_size),
            ("LEADING", (0, 1), (-1, -1), font_size + 2),
            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, HAIRLINE),
            ("BOX", (0, 0), (-1, -1), 0.5, HAIRLINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        for index in numeric:
            style.append(("ALIGN", (index, 0), (index, -1), "RIGHT"))
        table.setStyle(TableStyle(style))

        block: list = [table]
        if data.truncated_at:
            block.append(Spacer(1, 3 * mm))
            block.append(
                Paragraph(
                    _escape(
                        _("Only the first %(limit)s rows are included. Narrow the filters "
                          "to export the rest.")
                        % {"limit": display_value(data.truncated_at, ColumnKind.NUMBER)}
                    ),
                    styles["notice"],
                )
            )
        block.append(Spacer(1, 6 * mm))
        return block

    def _summary_block(self, data: ReportData, styles: dict, available: float) -> list:
        items = data.summary_items()
        if not items:
            return []

        rows = [[Paragraph(f"<b>{_escape(label)}</b>", styles["body"]), value] for label, value in items]
        width = min(available, 110 * mm)
        table = Table(rows, colWidths=[width * 0.62, width * 0.38], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                    ("BOX", (0, 0), (-1, -1), 0.5, HAIRLINE),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, HAIRLINE),
                    ("FONTNAME", (1, 0), (1, -1), styles["bold_font"]),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TEXTCOLOR", (1, 0), (1, -1), BRAND_DARK),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        return [KeepTogether([Paragraph(_("Summary"), styles["section"]), table])]


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def _table_font_size(column_count: int) -> float:
    """Shrink the type as the table gets wider, rather than clipping columns."""
    if column_count <= 6:
        return 8.5
    if column_count <= 9:
        return 7.5
    if column_count <= 12:
        return 6.8
    return 6.0


def _column_widths(data: ReportData, available: float, font_size: float) -> list[float]:
    """Distribute the printable width in proportion to the content length."""
    count = len(data.columns)
    if count == 0:
        return []

    weights: list[float] = []
    sample = data.rows[:WIDTH_SAMPLE_ROWS]
    for index, header in enumerate(data.columns):
        longest = len(str(header))
        for row in sample:
            if index < len(row):
                # Cap the influence of one very long note field.
                longest = max(longest, min(len(display_value(row[index], data.kind(index), data.currency)), 42))
        weights.append(max(float(longest), 6.0))

    total = sum(weights)
    widths = [available * weight / total for weight in weights]

    # Nothing narrower than a two-digit number, unless the page cannot afford it.
    minimum = min(12 * mm, available / count)
    widths = [max(width, minimum) for width in widths]
    overflow = sum(widths) - available
    if overflow > 0:
        scale = available / sum(widths)
        widths = [width * scale for width in widths]
    return widths


def _escape(value: object) -> str:
    """Escape for ReportLab's mini-HTML paragraph markup."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


__all__ = ["PdfExporter", "resolve_fonts"]
