"""Small, fully specified worked examples used by the README and the tests.

Each is a controlled fixture: inputs are fixed numbers (no randomness except a
seeded price path), so every output can be checked by hand.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .curves import bootstrap_sofr_ois, bump_quotes
from .dates import add_tenor, is_business_day, year_fraction
from .irs import IRS
from .market import Market
from .repo import Repo
from .trs import Dividend, EquityTRS


# ----------------------------------------------------------------------------- repo
def repo_example(mkt: Market) -> dict:
    """$100mm of Treasuries financed for 30 days at a 2% haircut, with daily margining.

    The collateral price follows a gentle path with a 2% drop on day 12 and a
    partial recovery, so the lifecycle shows both a margin call and a return
    of excess collateral.
    """
    start = mkt.val
    end = start + timedelta(days=30)
    while not is_business_day(end):
        end += timedelta(days=1)
    rate = mkt.sofr.term_rate(end) + 0.0012
    r = Repo("EX-REPO", "HF_RV_C", "reverse", "UST", 100e6, 0.02, rate, start, end, mta=100_000.0)
    rng = np.random.default_rng(11)
    prices, p, d, k = {}, 100.0, start, 0
    while d <= end:
        if is_business_day(d):
            if d > start:
                p *= 1.0 + rng.normal(0.0, 0.0025)
                if k == 8:
                    p *= 0.98
                if k == 14:
                    p *= 1.012
            prices[d] = p
            k += 1
        d += timedelta(days=1)
    rows = r.lifecycle(prices)
    return {"trade": r, "prices": prices, "rows": rows,
            "purchase_price": r.purchase_price, "repurchase_price": r.repurchase_price,
            "interest": r.repurchase_price - r.purchase_price, "initial_margin": r.initial_margin,
            "days": (end - start).days, "rate": rate,
            "margin_calls": [(row["date"], row["margin_call"]) for row in rows if row["margin_call"] != 0.0]}


# ----------------------------------------------------------------------------- IRS
def irs_example(mkt: Market, tenor: str = "5Y", notional: float = 100e6) -> dict:
    end = add_tenor(mkt.val, tenor)
    proto = IRS("EX-IRS", "CCP_J", notional, 0.0, mkt.val, end, pay_fixed=True)
    par = proto.par_rate(mkt.sofr)
    s = proto.with_fixed_rate(par)
    fixed, flt = s.fixed_leg(mkt.sofr), s.float_leg(mkt.sofr)
    dv01_fd = s.dv01(mkt.sofr)
    dv01_an = s.dv01_analytic(mkt.sofr)
    up = bootstrap_sofr_ois(mkt.val, bump_quotes(mkt.quotes, 1.0))
    dn = bootstrap_sofr_ois(mkt.val, bump_quotes(mkt.quotes, -1.0))
    par_dv01 = (s.pv(up) - s.pv(dn)) / 2.0
    krd = {}
    for q in mkt.quotes:
        c_up = bootstrap_sofr_ois(mkt.val, bump_quotes(mkt.quotes, 1.0, only=q.tenor))
        krd[q.tenor] = s.pv(c_up) - s.pv(mkt.sofr)
    return {"swap": s, "par_rate": par, "fixed_leg": fixed, "float_leg": flt,
            "pv_fixed": s.pv_fixed(mkt.sofr), "pv_float": s.pv_float(mkt.sofr), "pv": s.pv(mkt.sofr),
            "annuity": s.annuity(mkt.sofr), "dv01_fd": dv01_fd, "dv01_analytic": dv01_an,
            "par_dv01": par_dv01, "key_rate_dv01": krd,
            "pv_if_rates_up_100": s.pv(mkt.sofr.shifted(100.0))}


# ----------------------------------------------------------------------------- TRS
def trs_example(mkt: Market, notional: float = 100e6, spread_bp: float = 45.0,
                hedge_spread_bp: float = 30.0, seed: int = 1) -> dict:
    """One-year index TRS, quarterly resets, along a seeded risk-neutral price path."""
    from . import config
    u = config.UNDERLYINGS["SIM_IDX"]
    end = add_tenor(mkt.val, "1Y")
    t = EquityTRS("EX-TRS", "HF_MULTI_A", "SIM_IDX", notional / u.spot, u.spot, mkt.val, end,
                  spread_bp * 1e-4, reset_months=3, im_pct=0.12)
    rd = t.reset_dates()
    rng = np.random.default_rng(seed)
    prices = {rd[0]: u.spot}
    fixings = {}
    divs = mkt.dividends["SIM_IDX"]
    for a, b in zip(rd[:-1], rd[1:]):
        dt = year_fraction(a, b, "ACT/365F")
        fwd = mkt.sofr.forward_rate(a, b)
        fixings[b] = fwd
        d_in = sum(dv.amount for dv in divs if a < dv.ex_date <= b)
        drift = np.log(1.0 + fwd * year_fraction(a, b)) - 0.5 * u.vol ** 2 * dt
        prices[b] = (prices[a] * np.exp(drift + u.vol * np.sqrt(dt) * rng.normal()) - d_in)
    rows = t.simulate(prices, divs, fixings, hedge_spread_bp * 1e-4)
    return {"trade": t, "prices": prices, "rows": rows, "dividends": divs,
            "client_total": sum(r["client_net"] for r in rows),
            "desk_total": sum(r["desk_net"] for r in rows),
            "mtm_at_inception_client": t.mtm_client(mkt.sofr, u.spot, divs)}


def flat_curve_trs_fixture() -> dict:
    """Hand-checkable TRS: flat 4% curve, two quarterly periods, one dividend."""
    import math

    from .curves import DiscountCurve
    val = date(2026, 1, 2)
    nodes = [date(2026, 7, 2), date(2027, 1, 4), date(2031, 1, 2)]
    curve = DiscountCurve(val, nodes, [math.exp(-0.04 * year_fraction(val, d, "ACT/365F")) for d in nodes])
    t = EquityTRS("FX-TRS", "HF_MULTI_A", "SIM_IDX", 1000.0, 100.0, val, date(2026, 7, 2), 0.005,
                  reset_months=3, im_pct=0.12)
    divs = [Dividend(date(2026, 2, 13), 0.50)]
    return {"curve": curve, "trs": t, "divs": divs}
