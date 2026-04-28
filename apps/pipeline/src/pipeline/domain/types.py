"""Enums and scalar types."""

from __future__ import annotations

from enum import StrEnum

# Sane bounds for any 4-digit year we see in filenames, link text, period
# headers, etc. — used to reject stray digit-runs that aren't fiscal years
# (publication codes, ISBNs, etc.). Bump MAX_YEAR when 2031 starts filing.
MIN_YEAR: int = 1990
MAX_YEAR: int = 2030
VALID_YEAR_RANGE: tuple[int, int] = (MIN_YEAR, MAX_YEAR)


def is_valid_year(year: int) -> bool:
    return MIN_YEAR <= year <= MAX_YEAR


class StatementKind(StrEnum):
    INCOME = "income"
    BALANCE = "balance"
    CASHFLOW = "cashflow"


class ReportKind(StrEnum):
    ANNUAL = "annual"
    INTERIM = "interim"
    QUARTERLY = "quarterly"
    SUSTAINABILITY = "sustainability"
    OTHER = "other"


class Currency(StrEnum):
    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    CHF = "CHF"


class Unit(StrEnum):
    """Scale factor for reported numbers. 1.0 in this unit = the unit itself."""

    UNITS = "units"
    THOUSANDS = "thousands"
    MILLIONS = "millions"
    BILLIONS = "billions"
