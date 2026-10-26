"""
Month keys for the production forecast.

A month is written "YYYY-MM" everywhere (Python, the Google Sheet, the Apps Script):
the text sorts in calendar order and reads the same in every language.

Example: add("2026-11", 3) == "2027-02", span("2026-11", "2027-01") == ["2026-11", "2026-12", "2027-01"].
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Union

MONTH_LABELS_FR = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]

DateLike = Union[date, datetime, str, None]


def key(year: int, month: int) -> str:
    """Month key from a year and a month number (1 to 12)."""
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return f"{year:04d}-{month:02d}"


def parse(month_key: str) -> tuple:
    """(year, month) of a month key. Raises ValueError on anything else than YYYY-MM."""
    text = str(month_key).strip()
    if len(text) != 7 or text[4] != "-":
        raise ValueError(f"not a month key: {month_key!r}")
    year, month = int(text[:4]), int(text[5:])
    if not 1 <= month <= 12:
        raise ValueError(f"not a month key: {month_key!r}")
    return year, month


def is_key(value: object) -> bool:
    try:
        parse(str(value))
        return True
    except ValueError:
        return False


def index(month_key: str) -> int:
    """Months since year 0: lets two keys be subtracted."""
    year, month = parse(month_key)
    return year * 12 + (month - 1)


def from_index(i: int) -> str:
    return key(i // 12, i % 12 + 1)


def add(month_key: str, n: int) -> str:
    return from_index(index(month_key) + n)


def diff(a: str, b: str) -> int:
    """Number of months from a to b (b - a)."""
    return index(b) - index(a)


def span(start: str, end: str) -> List[str]:
    """Every month from start to end, both included. Empty when end is before start."""
    first, last = index(start), index(end)
    return [from_index(i) for i in range(first, last + 1)]


def of(value: DateLike) -> Optional[str]:
    """Month key of a date, a datetime or an ISO text ("2026-10-08", "2026-10"). None when empty."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return key(value.year, value.month)
    text = str(value).strip()
    if not text or text.lower() in ("nat", "nan", "none"):
        return None
    if len(text) >= 7 and text[4] == "-":
        return key(int(text[:4]), int(text[5:7]))
    raise ValueError(f"cannot read a month from {value!r}")


def label(month_key: str) -> str:
    """French label, e.g. "oct. 2026"."""
    year, month = parse(month_key)
    return f"{MONTH_LABELS_FR[month - 1]} {year}"


def year_of(month_key: str) -> int:
    return parse(month_key)[0]
