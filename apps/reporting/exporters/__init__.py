"""Export engines.

Every engine implements the same contract::

    exporter = get_exporter("pdf")
    payload: bytes = exporter.render(report_data)

so a new format is one module plus one registry line, and no caller changes.
"""

from .base import BaseExporter, ColumnKind, ReportData
from .registry import EXPORT_FORMATS, available_formats, get_exporter

__all__ = [
    "BaseExporter",
    "ColumnKind",
    "ReportData",
    "EXPORT_FORMATS",
    "available_formats",
    "get_exporter",
]
