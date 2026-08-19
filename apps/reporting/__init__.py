"""Reporting: turn the operational database into documents people can hand over.

Three layers, deliberately separated:

``exporters/``
    Format engines. They know nothing about surfing — they take a
    :class:`~apps.reporting.exporters.base.ReportData` table and return bytes.
``reports.py``
    The catalogue. Each entry is a builder function ``(user, filters)`` that
    reads one module's data and returns ``ReportData``. Builders resolve models
    lazily, so a report for a module that is not installed degrades to an empty
    document with an explanation instead of a 500.
``services.py``
    Orchestration: capability check, timing, file storage, audit entry.
"""
