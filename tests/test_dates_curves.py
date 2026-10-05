import math
from datetime import date

import pytest

from fsbs.curves import DiscountCurve, OISQuote, bootstrap_sofr_ois, bump_quotes
from fsbs.dates import add_tenor, adjust, schedule, year_fraction


def test_modified_following_stays_in_month():
    # 31 Oct 2026 is a Saturday; following would roll into November, so MF goes back to Friday 30th.
    assert adjust(date(2026, 10, 31), "MF") == date(2026, 10, 30)
    assert adjust(date(2026, 10, 31), "F") == date(2026, 11, 2)
    assert adjust(date(2026, 10, 3), "MF") == date(2026, 10, 5)


def test_day_counts():
    d1, d2 = date(2026, 1, 31), date(2026, 7, 31)
    assert year_fraction(d1, d2, "ACT/360") == pytest.approx(181 / 360)
    assert year_fraction(d1, d2, "ACT/365F") == pytest.approx(181 / 365)
    assert year_fraction(d1, d2, "30/360") == pytest.approx(0.5)


def test_schedule_rolls_from_start_without_drift():
    s = schedule(date(2026, 1, 30), date(2027, 1, 29), 3)
    assert s[0] == date(2026, 1, 30) and s[-1] == date(2027, 1, 29)
    assert len(s) == 5
    assert all(b > a for a, b in zip(s, s[1:]))


def test_bootstrap_reprices_every_quote_exactly(mkt):
    for q in mkt.quotes:
        if q.tenor == "ON":
            assert mkt.sofr.term_rate(add_tenor(mkt.val, "ON")) == pytest.approx(q.rate, abs=1e-13)
        else:
            assert mkt.sofr.par_ois_rate(q.tenor) == pytest.approx(q.rate, abs=1e-12)


def test_discount_factors_decrease_and_are_positive(mkt):
    ts = [0.1 * i for i in range(1, 300)]
    dfs = [mkt.sofr.df_t(t) for t in ts]
    assert all(0 < d < 1 for d in dfs)
    assert all(b < a for a, b in zip(dfs, dfs[1:]))  # positive rates -> strictly decreasing


def test_flat_curve_recovers_flat_par_rates():
    val = date(2026, 1, 2)
    quotes = [OISQuote(t, 0.04) for t in ("1M", "6M", "1Y", "2Y", "5Y", "10Y")]
    c = bootstrap_sofr_ois(val, quotes)
    for t in ("2Y", "5Y", "10Y"):
        assert c.par_ois_rate(t) == pytest.approx(0.04, abs=1e-12)


def test_parallel_zero_shift_is_exact():
    val = date(2026, 1, 2)
    c = DiscountCurve(val, [date(2027, 1, 2), date(2031, 1, 2)], [0.96, 0.82])
    s = c.shifted(25.0)
    for d in (date(2026, 6, 1), date(2028, 3, 3), date(2035, 1, 1)):
        assert s.zero_rate(d) - c.zero_rate(d) == pytest.approx(0.0025, abs=1e-12)


def test_forward_rate_consistency(mkt):
    a, b, c = add_tenor(mkt.val, "1Y"), add_tenor(mkt.val, "2Y"), add_tenor(mkt.val, "3Y")
    g = (1 + mkt.sofr.forward_rate(a, b) * year_fraction(a, b)) * (1 + mkt.sofr.forward_rate(b, c) * year_fraction(b, c))
    assert g == pytest.approx(mkt.sofr.df(a) / mkt.sofr.df(c), rel=1e-13)


def test_quote_bump_moves_only_that_pillar_region(mkt):
    up = bootstrap_sofr_ois(mkt.val, bump_quotes(mkt.quotes, 1.0, only="5Y"))
    assert up.df(add_tenor(mkt.val, "3Y")) == pytest.approx(mkt.sofr.df(add_tenor(mkt.val, "3Y")), rel=1e-14)
    assert up.df(add_tenor(mkt.val, "5Y")) < mkt.sofr.df(add_tenor(mkt.val, "5Y"))
    assert math.isfinite(up.zero_rate(add_tenor(mkt.val, "30Y")))
