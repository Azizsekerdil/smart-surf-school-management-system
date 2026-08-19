"""Factories for saved report configurations and archived exports."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.reporting.models import (
    GeneratedReport,
    ReportDefinition,
    ReportFormat,
    ReportStatus,
)


class ReportDefinitionFactory(DjangoModelFactory):
    class Meta:
        model = ReportDefinition
        django_get_or_create = ("code",)

    name = factory.Sequence(lambda n: f"Saved report {n}")
    code = factory.Sequence(lambda n: f"saved-report-{n}")
    report_key = "equipment_inventory"
    description = "Weekly asset register for the equipment manager."
    default_format = ReportFormat.EXCEL
    default_filters = factory.LazyFunction(dict)
    required_capability = ""
    is_scheduled = False
    schedule_cron = ""
    recipients = factory.LazyFunction(list)
    is_active = True


class ScheduledDefinitionFactory(ReportDefinitionFactory):
    """A definition that the scheduler will pick up."""

    is_scheduled = True
    schedule_cron = "0 7 * * 1"
    recipients = factory.LazyFunction(lambda: ["ops@surfschool.test"])


class GeneratedReportFactory(DjangoModelFactory):
    class Meta:
        model = GeneratedReport

    report_key = "equipment_inventory"
    title = "Equipment inventory"
    format = ReportFormat.PDF
    filters_used = factory.LazyFunction(dict)
    file_size_bytes = 1024
    row_count = 10
    generation_ms = 120
    status = ReportStatus.COMPLETED
    error_message = ""
