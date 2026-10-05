from datetime import date

import pytest

from fsbs.curves import bootstrap_sofr_ois, bump_quotes
from fsbs.dates import add_tenor, year_fraction
from fsbs.examples import irs_example
from fsbs.irs import IRS


def _swap(mkt, tenor="5Y", k=None, pay_fixed=True, n=100e6, start=None):
    start = start or mkt.val
    s = IRS("S", "CCP_J", n, 0.0, start, add_tenor(mkt.val, tenor), pay_fixed)
    return s.with_fixed_rate(s.par_rate(mkt.sofr) if k is None else k)


@pytest.mark.parametrize("tenor", ["1Y", "2Y", "5Y", "10Y", "30Y"])
def test_par_swap_npv_is_zero(mkt, tenor):
    s = _swap(mkt, tenor)
    assert abs(s.pv(mkt.sofr)) < 1e-6 * s.notional
    # spot-starting annual swap: par rate equals the bootstrapped OIS quote at a pillar
    q = {x.tenor: x.rate for x in mkt.quotes}
    assert s.fixed_rate == pytest.approx(q[tenor], abs=1e-12)


def test_legs_reconcile_independently(mkt):
    s = _swap(mkt, "7Y", k=0.037)
    c = mkt.sofr
    # fixed leg = K * annuity, annuity rebuilt by hand
    ann = sum(year_fraction(r["start"], r["end"]) * c.df(r["end"]) for r in s.fixed_leg(c)) * s.notional
    assert s.annuity(c) == pytest.approx(ann, rel=1e-14)
    assert s.pv_fixed(c) == pytest.approx(0.037 * ann, rel=1e-14)
    # floating leg telescopes to N * (DF(start) - DF(end)) when projection = discounting
    assert s.pv_float(c) == pytest.approx(s.notional * (c.df(s.start) - c.df(s.end)), rel=1e-12)
    # each projected coupon is the curve forward
    for r in s.float_leg(c):
        assert r["rate"] == pytest.approx(c.forward_rate(r["start"], r["end"]), rel=1e-13)


def test_payer_and_receiver_are_opposite(mkt):
    p, r = _swap(mkt, "5Y", 0.036, True), _swap(mkt, "5Y", 0.036, False)
    assert p.pv(mkt.sofr) == pytest.approx(-r.pv(mkt.sofr))
    assert p.dv01(mkt.sofr) == pytest.approx(-r.dv01(mkt.sofr))


def test_off_market_pv_equals_rate_difference_times_annuity(mkt):
    par = _swap(mkt, "5Y")
    off = par.with_fixed_rate(par.fixed_rate + 0.001)
    assert off.pv(mkt.sofr) == pytest.approx(-0.001 * par.annuity(mkt.sofr), rel=1e-12)


@pytest.mark.parametrize("tenor", ["2Y", "5Y", "10Y", "30Y"])
def test_dv01_finite_difference_matches_analytic(mkt, tenor):
    s = _swap(mkt, tenor, k=0.035)
    fd_central = s.dv01(mkt.sofr)
    analytic = s.dv01_analytic(mkt.sofr)
    fd_one_sided = s.pv(mkt.sofr.shifted(0.01)) / 0.01 - s.pv(mkt.sofr) / 0.01
    assert fd_central == pytest.approx(analytic, rel=1e-5)
    # central-difference error is O(h^2): a 0.1bp bump is ~100x closer than a 1bp bump
    err_1 = abs(fd_central - analytic)
    err_01 = abs(s.dv01(mkt.sofr, bump_bp=0.1) - analytic)
    assert err_01 <= err_1 / 20 + 1e-6 * abs(analytic)
    assert fd_one_sided == pytest.approx(analytic, rel=1e-4)
    assert analytic > 0  # payer gains when rates rise


def test_dv01_close_to_annuity_rule_of_thumb(mkt):
    s = _swap(mkt, "5Y")
    # for a par swap, DV01 ~ annuity * 1bp (slightly less because of discounting of the float leg)
    assert s.dv01(mkt.sofr) == pytest.approx(s.annuity(mkt.sofr) * 1e-4, rel=0.05)


def test_key_rate_dv01s_sum_to_parallel_par_dv01(mkt):
    ex = irs_example(mkt, "5Y")
    assert sum(ex["key_rate_dv01"].values()) == pytest.approx(ex["par_dv01"], rel=2e-3)
    # a spot 5Y swap has no risk to pillars beyond 5Y
    for t in ("7Y", "10Y", "30Y"):
        assert abs(ex["key_rate_dv01"][t]) < 1e-6


def test_seasoned_swap_uses_past_fixing(mkt):
    s = IRS("S", "CORP_H", 100e6, 0.04, date(2025, 6, 16), date(2028, 6, 16), True, past_fixing=0.039)
    rows = s.float_leg(mkt.sofr)
    assert rows[0]["seasoned"] and rows[0]["start"] < mkt.val
    tau_past = year_fraction(rows[0]["start"], mkt.val)
    expected = s.notional * ((1 + 0.039 * tau_past) - mkt.sofr.df(rows[0]["end"]))
    assert rows[0]["pv"] == pytest.approx(expected, rel=1e-12)
    assert s.dv01(mkt.sofr) == pytest.approx(s.dv01_analytic(mkt.sofr), rel=1e-6)
    with pytest.raises(ValueError):
        IRS("S", "X", 1.0, 0.04, date(2025, 6, 16), date(2028, 6, 16), True).pv(mkt.sofr)


def test_rate_shock_pnl_matches_dv01_with_convexity(mkt):
    s = _swap(mkt, "10Y")
    up = bootstrap_sofr_ois(mkt.val, bump_quotes(mkt.quotes, 100.0))
    pnl = s.pv(up) - s.pv(mkt.sofr)
    linear = s.dv01(mkt.sofr) * 100
    assert pnl < linear  # payer swap: negative convexity in rates, P&L below the linear estimate
    assert pnl == pytest.approx(linear, rel=0.08)
