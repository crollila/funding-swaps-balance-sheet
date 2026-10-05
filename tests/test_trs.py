import math
from datetime import date

import pytest

from fsbs.curves import DiscountCurve
from fsbs.dates import year_fraction
from fsbs.examples import trs_example
from fsbs.trs import Dividend, EquityTRS

R = 0.04


def flat(ref: date) -> DiscountCurve:
    nodes = [date(ref.year + k, ref.month, 1) for k in (1, 2, 6)]
    return DiscountCurve(ref, nodes, [math.exp(-R * year_fraction(ref, d, "ACT/365F")) for d in nodes])


def trs(spread=0.0, start=date(2026, 1, 2), end=date(2026, 7, 2), **kw):
    return EquityTRS("T", "HF_MULTI_A", "SIM_IDX", 1000.0, 100.0, start, end, spread, reset_months=3, **kw)


def test_zero_spread_no_dividend_trs_is_worth_zero_at_inception():
    t = trs()
    assert t.mtm_client(flat(t.start), 100.0, []) == pytest.approx(0.0, abs=1e-9)


def test_inception_value_is_minus_pv_of_spread_annuity():
    """Controlled fixture: closed-form value of a resetting TRS with spread s and no dividends.

    Period 1 financing: q S0 s tau1 DF1. Period 2: q S tau2 s DF2/DF1 (notional resets to S at t1,
    whose PV paid at t2 is S * DF2/DF1).
    """
    s = 0.005
    t = trs(spread=s)
    c = flat(t.start)
    r0, r1, r2 = t.reset_dates()
    tau1, tau2 = year_fraction(r0, r1), year_fraction(r1, r2)
    expected = -1000 * 100 * s * (tau1 * c.df(r1) + tau2 * c.df(r2) / c.df(r1))
    assert t.mtm_client(c, 100.0, []) == pytest.approx(expected, rel=1e-12)
    assert t.mtm(c, 100.0, []) == pytest.approx(-expected, rel=1e-12)


def test_mid_period_mark_to_market_identity():
    """Mid-period: value = q(S_t - S_k DF1) - q S_k (g_past - DF1) - spread terms (no dividends)."""
    s = 0.003
    t = trs(spread=s, past_fixing=0.041)
    val = date(2026, 2, 17)
    c = flat(val)
    r0, r1, r2 = t.reset_dates()
    st = 104.0
    g = 1 + 0.041 * year_fraction(r0, val)
    df1, df2 = c.df(r1), c.df(r2)
    q, sk = 1000.0, 100.0
    current = q * (st - sk * df1) - q * sk * ((g - df1) + s * year_fraction(r0, r1) * df1)
    future = -q * st * s * year_fraction(r1, r2) * df2 / df1
    assert t.mtm_client(c, st, []) == pytest.approx(current + future, rel=1e-12)
    # delta: +q shares, less the spread paid on the higher future notional
    bump = t.mtm_client(c, st + 0.01, []) - t.mtm_client(c, st, [])
    assert bump == pytest.approx(q * 0.01 * (1 - s * year_fraction(r1, r2) * df2 / df1), rel=1e-9)


def test_dividend_handling_in_mark_to_market():
    """A known dividend going ex in period 1: client is owed it at t1; spot already reflects it."""
    t = trs()
    c = flat(t.start)
    r0, r1, _ = t.reset_dates()
    d = Dividend(date(2026, 2, 13), 0.50)
    v = t.mtm_client(c, 100.0, [d])
    # equity leg: q[(S - PV(D)) - S0 DF1] + q D DF1 ; financing q S0 (1 - DF1); future period: timing term only
    expected = 1000 * ((100 - 0.5 * c.df(d.ex_date)) - 100 * c.df(r1)) + 1000 * 0.5 * c.df(r1) - 1000 * 100 * (1 - c.df(r1))
    assert v == pytest.approx(expected, abs=1e-8)  # large offsetting terms: compare to absolute precision
    assert abs(v) < 1000 * 0.5 * 0.01  # only a small dividend-timing value remains


def test_realised_lifecycle_identities(mkt):
    ex = trs_example(mkt)
    t, rows, p = ex["trade"], ex["rows"], ex["prices"]
    rd = t.reset_dates()
    q = t.shares
    total_divs = sum(r["dividends"] for r in rows)
    total_fin = sum(r["financing_leg"] for r in rows)
    # equity legs telescope: sum of price returns = q (S_T - S_0)
    assert ex["client_total"] == pytest.approx(q * (p[rd[-1]] - p[rd[0]]) + q * total_divs - total_fin, rel=1e-12)
    # desk is hedged: each period nets to q S_{k-1} (spread - hedge spread) tau exactly
    for r in rows:
        assert r["desk_net"] == pytest.approx(q * r["s_prev"] * (t.spread - 0.0030) * r["tau"], rel=1e-10)
    assert ex["desk_total"] > 0  # spread 45bp > hedge funding 30bp
    # financing leg on resetting notional
    for r in rows:
        assert r["financing_leg"] == pytest.approx(q * r["s_prev"] * (r["sofr"] + t.spread) * r["tau"], rel=1e-12)


def test_scaling_is_linear():
    t = trs(spread=0.004)
    c = flat(t.start)
    assert t.scaled(2.5).mtm_client(c, 100.0, []) == pytest.approx(2.5 * t.mtm_client(c, 100.0, []), rel=1e-12)
