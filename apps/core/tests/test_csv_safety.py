"""CSV formula injection must not survive an export.

Every CSV this product produces is meant to be opened in Excel — that is why the
exporters write a BOM and a semicolon delimiter. It is also why a cell beginning
with ``=``, ``+``, ``-`` or ``@`` is dangerous: the spreadsheet evaluates it.
Free-text fields that a customer can influence (a name, a note, a supplier) all
reach these exports.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest

from apps.core.csv_safety import FORMULA_PREFIXES, csv_safe, csv_safe_row, safe_csv_writer


@pytest.mark.security
@pytest.mark.parametrize("prefix", FORMULA_PREFIXES)
def test_a_formula_leading_cell_is_neutralised(prefix):
    value = f"{prefix}HYPERLINK(\"http://example.test\",\"click\")"

    result = csv_safe(value)

    assert result.startswith("'")
    assert result[1:] == value


def test_ordinary_text_is_untouched():
    for value in ["Deniz", "board 6'2", "note: fine", "", "0", "a=b"]:
        assert csv_safe(value) == value


def test_numbers_and_dates_pass_through_unchanged():
    """Quoting them as text would break the numeric columns the export exists for."""
    for value in [Decimal("-12.50"), -3, 0, 4.5, None, True]:
        assert csv_safe(value) is value


def test_a_whole_row_is_sanitised():
    row = ["=cmd|' /C calc'!A0", "safe", Decimal("10.00")]

    out = csv_safe_row(row)

    assert out[0].startswith("'=")
    assert out[1] == "safe"
    assert out[2] == Decimal("10.00")


@pytest.mark.security
def test_the_writer_sanitises_without_the_caller_remembering():
    """The wrapper is the point: a new export is protected by construction."""
    buffer = io.StringIO(newline="")
    writer = safe_csv_writer(buffer, lineterminator="\n")

    writer.writerow(["=1+1", "ok"])
    writer.writerows([["@SUM(A1:A9)", "ok"]])

    rows = list(csv.reader(io.StringIO(buffer.getvalue())))
    assert rows[0][0] == "'=1+1"
    assert rows[1][0] == "'@SUM(A1:A9)"


@pytest.mark.security
def test_every_shipped_csv_export_uses_the_safe_writer():
    """A structural guard: `csv.writer` must not reappear in an export path."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    offenders = []
    for path in (root / "apps").rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == "csv_safety.py":
            continue  # the wrapper itself is the one legitimate caller
        text = path.read_text(encoding="utf-8", errors="replace")
        if "csv.writer(" in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == [], (
        "these modules build a CSV writer directly; use "
        f"apps.core.csv_safety.safe_csv_writer instead: {offenders}"
    )
