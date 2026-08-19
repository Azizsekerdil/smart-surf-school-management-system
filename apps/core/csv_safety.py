"""Neutralise spreadsheet formula injection in exported CSV.

A CSV file is data. A spreadsheet treats a cell beginning with ``=``, ``+``,
``-``, ``@`` or a leading tab/carriage return as a **formula** and evaluates it
when the file is opened. That turns any free-text field a customer can influence
— a name, a note, a supplier, an equipment label — into code that runs on the
machine of whoever opens the export.

This product exports customer, student, equipment, camp and financial data to
CSV specifically so it can be opened in Excel, which is exactly the environment
where this matters. Every writer therefore passes its values through
:func:`csv_safe`.

The mitigation is the conventional one: prefix an apostrophe, which Excel and
LibreOffice treat as "the rest of this cell is text" and do not display. It
costs nothing for the overwhelming majority of cells, which do not start with a
formula character at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

#: Characters that make a spreadsheet treat a cell as a formula.
FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> object:
    """Return *value* with any leading formula character neutralised.

    Non-string values are returned unchanged: a ``Decimal`` or a ``date`` cannot
    carry a formula, and quoting them as text would break the numeric columns
    the export exists to provide.

    A negative number that arrives as a *string* is the one awkward case. It is
    prefixed like anything else, because the alternative — guessing which
    hyphen-leading strings are safe — is how this class of bug survives.
    Numeric columns should pass a number, not a string.
    """
    if not isinstance(value, str):
        return value
    if value[:1] in FORMULA_PREFIXES:
        return "'" + value
    return value


def csv_safe_row(row: Sequence[object] | Iterable[object]) -> list[object]:
    """Apply :func:`csv_safe` to every cell of *row*."""
    return [csv_safe(cell) for cell in row]


class SafeCsvWriter:
    """A ``csv.writer`` that sanitises every cell on the way out.

    Wrapping the writer rather than each call site means a new column, or a new
    export added next year, is protected without anybody remembering to call the
    helper.
    """

    __slots__ = ("_writer",)

    def __init__(self, *args, **kwargs):
        import csv

        self._writer = csv.writer(*args, **kwargs)

    def writerow(self, row):
        return self._writer.writerow(csv_safe_row(row))

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)

    def __getattr__(self, name):
        return getattr(self._writer, name)


def safe_csv_writer(*args, **kwargs) -> SafeCsvWriter:
    """Drop-in replacement for :func:`csv.writer` with formula neutralisation."""
    return SafeCsvWriter(*args, **kwargs)
