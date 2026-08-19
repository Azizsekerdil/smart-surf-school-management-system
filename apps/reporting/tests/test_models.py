from __future__ import annotations

import pytest

from apps.reporting.models import GeneratedReport, ReportFormat, ReportStatus

from .factories import GeneratedReportFactory, ReportDefinitionFactory

pytestmark = pytest.mark.django_db


def test_definition_str_is_its_name():
    definition = ReportDefinitionFactory(name="Monday ops sheet")
    assert str(definition) == "Monday ops sheet"


def test_generated_report_str_names_the_format():
    report = GeneratedReportFactory(title="Revenue", format=ReportFormat.EXCEL)
    assert "Revenue" in str(report)
    assert str(ReportFormat.EXCEL.label) in str(report)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["a@example.com", " b@example.com "], ["a@example.com", "b@example.com"]),
        ("a@example.com, b@example.com", ["a@example.com", "b@example.com"]),
        ("a@example.com; b@example.com", ["a@example.com", "b@example.com"]),
        ({}, []),
        ([], []),
    ],
)
def test_recipient_list_normalises_whatever_json_holds(raw, expected):
    definition = ReportDefinitionFactory(recipients=raw)
    assert definition.recipient_list == expected


def test_filter_dict_never_returns_a_non_dict():
    definition = ReportDefinitionFactory(default_filters=["not", "a", "dict"])
    assert definition.filter_dict == {}


def test_file_size_display_is_human_readable():
    assert GeneratedReportFactory(file_size_bytes=512).file_size_display == "512 B"
    assert GeneratedReportFactory(file_size_bytes=2048).file_size_display == "2.0 KB"


def test_duration_display_switches_to_seconds():
    assert GeneratedReportFactory(generation_ms=250).duration_display == "250 ms"
    assert GeneratedReportFactory(generation_ms=2500).duration_display == "2.5 s"


def test_download_filename_carries_the_export_date():
    from django.utils import timezone

    report = GeneratedReportFactory(title="Günlük Operasyon", format=ReportFormat.CSV)
    name = report.download_filename()
    assert name.endswith(".csv")
    # The date is the school's local date, not UTC — an 01:00 export in Istanbul
    # must not be filed under the previous day.
    assert timezone.localtime(report.created_at).strftime("%Y-%m-%d") in name
    # Slugified: a filename must survive any filesystem.
    assert name.isascii()


def test_a_failed_report_is_not_downloadable():
    report = GeneratedReportFactory(status=ReportStatus.FAILED, error_message="boom")
    assert report.is_downloadable is False


def test_soft_delete_hides_the_definition_but_keeps_the_row():
    definition = ReportDefinitionFactory()
    definition.delete()
    assert definition.__class__.objects.filter(pk=definition.pk).count() == 0
    assert definition.__class__.all_objects.filter(pk=definition.pk).count() == 1


def test_generated_reports_are_newest_first():
    first = GeneratedReportFactory(title="First")
    second = GeneratedReportFactory(title="Second")
    assert list(GeneratedReport.objects.values_list("title", flat=True))[:2] == [
        second.title,
        first.title,
    ]
