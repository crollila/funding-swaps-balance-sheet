"""Calendar, day-count and schedule utilities.

Deliberately small and explicit: a weekends-only calendar (no holiday list),
modified-following adjustment, ACT/360, ACT/365F and 30/360 day counts, and
regular schedules rolled off the start date. These are the conventions every
instrument in the library is built on, so they are kept dependency-free and
fully tested.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

DAY = timedelta(days=1)


def is_business_day(d: date) -> bool:
    """Weekends-only calendar. Real desks use SIFMA/NY Fed calendars; see MODEL_ASSUMPTIONS.md."""
    return d.weekday() < 5


def adjust(d: date, convention: str = "MF") -> date:
    """Business-day adjustment: F (following), MF (modified following), P (preceding), NONE."""
    convention = convention.upper()
    if convention == "NONE" or is_business_day(d):
        return d
    if convention in ("F", "MF"):
        a = d
        while not is_business_day(a):
            a += DAY
        if convention == "MF" and a.month != d.month:
            a = d
            while not is_business_day(a):
                a -= DAY
        return a
    if convention == "P":
        a = d
        while not is_business_day(a):
            a -= DAY
        return a
    raise ValueError(f"unknown business-day convention {convention!r}")


def add_business_days(d: date, n: int) -> date:
    step = DAY if n >= 0 else -DAY
    a = d
    for _ in range(abs(n)):
        a += step
        while not is_business_day(a):
            a += step
    return a


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def add_tenor(d: date, tenor: str, convention: str = "MF") -> date:
    """Add a tenor string such as '1D', '2W', '3M', '5Y' and adjust.

    '1D' / 'ON' means one business day (overnight).
    """
    tenor = tenor.upper().strip()
    if tenor in ("ON", "O/N"):
        return add_business_days(d, 1)
    n, unit = int(tenor[:-1]), tenor[-1]
    if unit == "D":
        return add_business_days(d, n)
    if unit == "W":
        return adjust(d + timedelta(weeks=n), convention)
    if unit == "M":
        return adjust(add_months(d, n), convention)
    if unit == "Y":
        return adjust(add_months(d, 12 * n), convention)
    raise ValueError(f"bad tenor {tenor!r}")


def year_fraction(d1: date, d2: date, basis: str = "ACT/360") -> float:
    basis = basis.upper()
    if basis == "ACT/360":
        return (d2 - d1).days / 360.0
    if basis in ("ACT/365F", "ACT/365"):
        return (d2 - d1).days / 365.0
    if basis == "30/360":
        day1, day2 = min(d1.day, 30), d2.day
        if day1 == 30 and day2 == 31:
            day2 = 30
        return ((d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (day2 - day1)) / 360.0
    raise ValueError(f"unknown day count {basis!r}")


def schedule(start: date, end: date, months: int, convention: str = "MF") -> list[date]:
    """Regular schedule rolled forward from ``start`` every ``months`` months.

    Unadjusted roll dates are generated as start + k*months (so the day of month
    never drifts), then adjusted. The final date is ``end`` (adjusted); a short
    final stub is produced if the tenor is not a whole number of periods.
    Returns [start, d1, ..., end] with at least two elements.
    """
    if end <= start:
        raise ValueError("end must be after start")
    out = [start]
    k = 1
    while True:
        roll = add_months(start, k * months)
        if roll >= end:
            break
        out.append(adjust(roll, convention))
        k += 1
    out.append(adjust(end, convention))
    # Adjustment can in rare cases collapse a stub into the final date.
    dedup = [out[0]]
    for d in out[1:]:
        if d > dedup[-1]:
            dedup.append(d)
    return dedup
