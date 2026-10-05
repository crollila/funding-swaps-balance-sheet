"""The SIMULATED book, client-demand pipeline and pre-trade proposals.

Nothing here is a real trade, client or deal. Counterparty names are generic
archetypes; sizes are chosen to resemble a mid-sized dealer's financing desk.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from . import config
from .dates import add_tenor
from .irs import IRS
from .market import Market
from .positions import Position
from .repo import Repo
from .trs import EquityTRS

D = date


def _repo(mkt: Market, tid: str, cp: str, coll: str, mv: float, h: float, spread_bp: float, start: date,
          end: date, direction: str = "reverse", desc: str = "") -> Position:
    rate = mkt.sofr.term_rate(end) + spread_bp * 1e-4
    r = Repo(tid, cp, direction, coll, mv, h, rate, start, end)
    return Position(r, "reverse_repo" if direction == "reverse" else "repo", desc=desc)


def _trs(mkt: Market, tid: str, cp: str, und: str, notional: float, spread_bp: float, start: date, end: date,
         im: float, reset_months: int = 3, last_reset_price: float | None = None,
         past_fixing: float | None = None, desc: str = "") -> Position:
    u = config.UNDERLYINGS[und]
    s0 = u.spot if last_reset_price is None else last_reset_price
    t = EquityTRS(tid, cp, und, notional / u.spot, s0, start, end, spread_bp * 1e-4, reset_months, im,
                  last_reset_price=last_reset_price, past_fixing=past_fixing)
    return Position(t, "trs", desc=desc)


def _irs(mkt: Market, tid: str, cp: str, notional: float, start: date, end: date, desk_pays_fixed: bool,
         sales_bp: float, hedge: str = "exact", hedge_end: date | None = None, past_fixing: float | None = None,
         fixed_rate: float | None = None, desc: str = "") -> Position:
    """Client swap at mid -/+ sales spread, optionally hedged at the CCP.

    hedge="exact": opposite swap at mid, same schedule.
    hedge="dv01":  opposite swap at mid on ``hedge_end`` with DV01-matched notional (leaves curve risk).
    hedge="none":  desk position, no hedge.
    """
    sign = 1 if desk_pays_fixed else -1
    proto = IRS(tid, cp, notional, 0.0, start, end, desk_pays_fixed, past_fixing=past_fixing)
    mid = proto.par_rate(mkt.sofr)
    k = fixed_rate if fixed_rate is not None else mid - sign * sales_bp * 1e-4
    client = proto.with_fixed_rate(k)
    h = None
    if hedge == "exact":
        h = IRS(tid + "-H", "CCP_J", notional, mid, start, end, not desk_pays_fixed, past_fixing=past_fixing)
    elif hedge == "dv01":
        he = hedge_end or end
        unit = IRS(tid + "-H", "CCP_J", 1.0, 0.0, mkt.val, he, not desk_pays_fixed)
        unit = unit.with_fixed_rate(unit.par_rate(mkt.sofr))
        n_h = abs(client.dv01_analytic(mkt.sofr) / unit.dv01_analytic(mkt.sofr))
        h = IRS(tid + "-H", "CCP_J", n_h, unit.fixed_rate, mkt.val, he, not desk_pays_fixed)
    return Position(client, "irs", hedge=h, sales_spread_bp=sales_bp, desc=desc)


def existing_book(mkt: Market) -> list[Position]:
    v = mkt.val
    P = []
    # -- reverse repos: desk lends cash to clients against collateral --------------------------
    P += [
        _repo(mkt, "RR01", "HF_RV_C", "UST", 4.0e9, 0.02, 12, D(2026, 9, 24), D(2026, 10, 8), desc="RV fund UST basis financing"),
        _repo(mkt, "RR02", "HF_RV_C", "UST", 2.5e9, 0.02, 16, D(2026, 9, 1), D(2026, 12, 1), desc="3M term UST"),
        _repo(mkt, "RR03", "HF_MULTI_A", "AGY_MBS", 1.8e9, 0.04, 26, D(2026, 9, 15), D(2026, 10, 15), desc="MBS financing"),
        _repo(mkt, "RR04", "HF_MULTI_A", "IG_CORP", 0.9e9, 0.08, 75, D(2026, 8, 17), D(2026, 11, 17), desc="IG credit financing"),
        _repo(mkt, "RR05", "HF_MACRO_D", "UST", 2.0e9, 0.02, 17, D(2026, 7, 1), D(2027, 1, 4), desc="6M term UST"),
        _repo(mkt, "RR06", "HF_EQLS_B", "EQ_SINGLE", 0.4e9, 0.20, 160, D(2026, 9, 21), D(2026, 10, 21), desc="single-name margin financing"),
        _repo(mkt, "RR07", "HF_MULTI_A", "EQ_INDEX", 0.6e9, 0.12, 110, D(2026, 9, 10), D(2026, 12, 10), desc="index basket financing"),
        _repo(mkt, "RR08", "HF_MULTI_A", "HY_CORP", 0.35e9, 0.15, 190, D(2026, 9, 1), D(2026, 11, 2), desc="HY financing"),
        _repo(mkt, "RR09", "INSURER_G", "IG_CORP", 0.5e9, 0.06, 65, D(2026, 9, 15), D(2026, 12, 15), desc="insurer IG repo"),
        _repo(mkt, "RR10", "HF_RV_C", "UST", 3.0e9, 0.02, 18, D(2026, 4, 1), D(2027, 4, 1), desc="1Y term UST"),
        _repo(mkt, "RR11", "DEALER_I", "UST", 1.5e9, 0.01, 7, D(2026, 9, 30), D(2026, 10, 2), desc="interdealer overnight"),
    ]
    # -- repos: desk borrows cash against its inventory ------------------------------------------
    P += [
        _repo(mkt, "RP01", "MMF_GOVT_E", "UST", 3.0e9, 0.02, -2, D(2026, 9, 30), D(2026, 10, 8), "repo", desc="MMF cash, 1w"),
        _repo(mkt, "RP02", "DEALER_I", "UST", 1.2e9, 0.01, -1, D(2026, 9, 30), D(2026, 10, 2), "repo", desc="interdealer overnight (nets RR11)"),
        _repo(mkt, "RP03", "MMF_GOVT_E", "AGY_MBS", 1.5e9, 0.03, 6, D(2026, 9, 30), D(2026, 10, 30), "repo", desc="MMF cash, 1M MBS"),
        _repo(mkt, "RP04", "PENSION_F", "UST", 1.0e9, 0.02, -3, D(2026, 9, 1), D(2026, 12, 1), "repo", desc="pension cash reinvestment"),
        _repo(mkt, "RP05", "HF_RV_C", "UST", 1.0e9, 0.02, -1, D(2026, 9, 24), D(2026, 10, 8), "repo", desc="RV fund cash (nets RR01)"),
    ]
    # -- equity TRS: desk pays total return, client pays SOFR + spread ----------------------------
    P += [
        _trs(mkt, "TRS01", "HF_EQLS_B", "SIM_TECH", 200e6, 75, D(2026, 8, 14), D(2027, 8, 16), 0.20, past_fixing=0.0386, desc="synthetic long tech"),
        _trs(mkt, "TRS02", "HF_MULTI_A", "SIM_IDX", 750e6, 55, D(2026, 9, 15), D(2028, 9, 15), 0.12, reset_months=1, past_fixing=0.0385, desc="index delta-one"),
        _trs(mkt, "TRS03", "HF_EQLS_B", "SIM_BANK", 150e6, 85, D(2026, 9, 1), D(2027, 9, 1), 0.20, past_fixing=0.0386, desc="synthetic long bank"),
        _trs(mkt, "TRS04", "HF_MACRO_D", "SIM_IDX", 400e6, 50, D(2026, 9, 22), D(2027, 9, 22), 0.12, past_fixing=0.0385, desc="macro index overlay"),
        _trs(mkt, "TRS05", "HF_MULTI_A", "SIM_ENERGY", 180e6, 80, D(2026, 9, 8), D(2027, 9, 8), 0.18, past_fixing=0.0386, desc="synthetic long energy"),
        _trs(mkt, "TRS06", "HF_EQLS_B", "SIM_PHARMA", 120e6, 70, D(2026, 9, 29), D(2027, 9, 29), 0.18, past_fixing=0.0385, desc="synthetic long pharma"),
    ]
    # -- interest-rate swaps ---------------------------------------------------------------------
    P += [
        _irs(mkt, "IRS01", "CORP_H", 500e6, v, add_tenor(v, "5Y"), False, 9.0, desc="corporate pays fixed, no CSA; CCP hedge"),
        _irs(mkt, "IRS02", "PENSION_F", 400e6, v, add_tenor(v, "10Y"), True, 3.5, hedge="dv01",
             hedge_end=add_tenor(v, "7Y"), desc="LDI receiver; hedged with DV01-matched 7Y"),
        _irs(mkt, "IRS03", "INSURER_G", 250e6, v, add_tenor(v, "20Y"), True, 4.0, desc="insurer receiver 20Y; CCP hedge"),
        _irs(mkt, "IRS04", "HF_MACRO_D", 1.5e9, v, add_tenor(v, "2Y"), False, 1.0, desc="macro fund pays 2Y"),
        _irs(mkt, "IRS05", "CCP_J", 600e6, v, add_tenor(v, "3Y"), True, 0.0, hedge="none",
             fixed_rate=mkt.sofr.par_ois_rate("3Y"), desc="desk pay-fixed: hedges fixed-rate term reverse repo"),
        _irs(mkt, "IRS06", "CORP_H", 300e6, D(2025, 6, 16), D(2028, 6, 16), False, 0.0, past_fixing=0.0391,
             fixed_rate=0.0405, desc="seasoned corporate swap (fixed 4.05%); CCP hedge at inception mid"),
    ]
    return P


_TEMPLATES = [
    # product, cpty, class/underlying, size range ($), tenors, spread range (bp), haircut/IM
    ("reverse_repo", "HF_RV_C", "UST", (1.0e9, 3.0e9), ["1M", "3M", "6M"], (10, 20), 0.02),
    ("reverse_repo", "HF_MACRO_D", "UST", (0.5e9, 2.0e9), ["1M", "3M"], (11, 20), 0.02),
    ("reverse_repo", "HF_MULTI_A", "AGY_MBS", (0.5e9, 1.5e9), ["1M", "3M"], (20, 32), 0.04),
    ("reverse_repo", "HF_MULTI_A", "IG_CORP", (0.2e9, 0.8e9), ["1M", "3M", "6M"], (60, 95), 0.08),
    ("reverse_repo", "HF_MULTI_A", "HY_CORP", (0.1e9, 0.4e9), ["1M", "3M"], (160, 220), 0.15),
    ("reverse_repo", "HF_EQLS_B", "EQ_SINGLE", (0.1e9, 0.4e9), ["1M", "3M"], (135, 195), 0.20),
    ("reverse_repo", "HF_MULTI_A", "EQ_INDEX", (0.2e9, 0.8e9), ["1M", "3M"], (90, 130), 0.12),
    ("reverse_repo", "INSURER_G", "IG_CORP", (0.2e9, 0.6e9), ["3M", "6M"], (50, 78), 0.06),
    ("reverse_repo", "DEALER_I", "UST", (0.5e9, 2.0e9), ["1M"], (4, 10), 0.01),
    ("repo", "MMF_GOVT_E", "UST", (1.0e9, 2.5e9), ["1M", "3M"], (-4, 1), 0.02),
    ("repo", "MMF_GOVT_E", "AGY_MBS", (0.5e9, 1.0e9), ["1M"], (3, 9), 0.03),
    ("repo", "PENSION_F", "UST", (0.5e9, 1.5e9), ["3M", "6M"], (-5, 0), 0.02),
    ("repo", "HF_RV_C", "UST", (0.5e9, 1.5e9), ["1M", "3M"], (-4, 1), 0.02),
    ("trs", "HF_EQLS_B", "SIM_TECH", (80e6, 250e6), ["1Y"], (60, 110), 0.20),
    ("trs", "HF_MULTI_A", "SIM_IDX", (200e6, 600e6), ["1Y", "2Y"], (45, 70), 0.12),
    ("trs", "HF_MACRO_D", "SIM_IDX", (150e6, 500e6), ["1Y"], (40, 65), 0.12),
    ("trs", "HF_MULTI_A", "SIM_BANK", (60e6, 180e6), ["1Y"], (65, 110), 0.18),
    ("trs", "HF_EQLS_B", "SIM_PHARMA", (60e6, 160e6), ["1Y"], (60, 100), 0.18),
    ("irs", "CORP_H", None, (150e6, 500e6), ["3Y", "5Y", "7Y"], (8.0, 15.0), None),
    ("irs", "PENSION_F", None, (200e6, 600e6), ["10Y", "20Y", "30Y"], (2.5, 5.0), None),
    ("irs", "INSURER_G", None, (100e6, 400e6), ["10Y", "15Y"], (2.5, 5.0), None),
    ("irs", "HF_MACRO_D", None, (0.5e9, 2.0e9), ["2Y", "5Y"], (0.6, 1.4), None),
]


def candidate_pipeline(mkt: Market, n: int = 40, seed: int = 7) -> list[Position]:
    """Simulated client demand for the period: each is a request the desk may fill 0-100%."""
    rng = np.random.default_rng(seed)
    v = mkt.val
    out = []
    for i in range(n):
        prod, cp, under, (lo, hi), tenors, (s_lo, s_hi), h = _TEMPLATES[i % len(_TEMPLATES)]
        size = float(np.round(rng.uniform(lo, hi) / 1e7) * 1e7)
        tenor = tenors[int(rng.integers(len(tenors)))]
        spread = float(np.round(rng.uniform(s_lo, s_hi), 2))
        end = add_tenor(v, tenor)
        tid = f"C{i + 1:02d}"
        if prod in ("reverse_repo", "repo"):
            mv = size / (1.0 - h)
            out.append(_repo(mkt, tid, cp, under, mv, h, spread, v, end,
                             "reverse" if prod == "reverse_repo" else "repo", desc=f"{tenor} {under}"))
        elif prod == "trs":
            out.append(_trs(mkt, tid, cp, under, size, spread, v, end, h, desc=f"{tenor} {under} TRS"))
        else:
            pays = cp in ("PENSION_F", "INSURER_G")  # LDI clients receive fixed -> desk pays fixed
            out.append(_irs(mkt, tid, cp, size, v, end, pays, spread, desc=f"{tenor} client swap, CCP-hedged"))
    # Matched-book requests: the same client both borrows and lends cash to the same date. Each leg looks
    # poor standalone; together they net on balance sheet (same counterparty, same maturity).
    one_m = add_tenor(v, "1M")
    out += [
        _repo(mkt, f"C{n + 1:02d}", "HF_RV_C", "UST", 1.8e9 / 0.98, 0.02, 14, v, one_m, desc="1M UST (matched-book leg A)"),
        _repo(mkt, f"C{n + 2:02d}", "HF_RV_C", "UST", 1.5e9 / 0.98, 0.02, -1, v, one_m, "repo", desc="1M UST cash in (matched-book leg B)"),
        _repo(mkt, f"C{n + 3:02d}", "DEALER_I", "UST", 0.8e9 / 0.99, 0.01, -1, v, one_m, "repo", desc="1M interdealer cash in (nets C09)"),
    ]
    return out


def proposed_trades(mkt: Market) -> list[Position]:
    """Hand-built proposals run through the pre-trade gate after the book is optimised."""
    v = mkt.val
    m = lambda t: add_tenor(v, t)  # noqa: E731
    return [
        _repo(mkt, "P01", "HF_MACRO_D", "UST", 1.2e9 / 0.98, 0.02, 22, v, m("1M"), desc="1M UST financing, macro fund"),
        _repo(mkt, "P02", "HF_EQLS_B", "EQ_SINGLE", 0.3e9 / 0.88, 0.12, 140, v, m("1M"), desc="single-name financing at 12% haircut"),
        _trs(mkt, "P03", "HF_EQLS_B", "SIM_TECH", 900e6, 80, v, m("1Y"), 0.20, desc="large synthetic long tech"),
        _irs(mkt, "P04", "CORP_H", 1.0e9, v, m("10Y"), False, 8.0, desc="10Y corporate payer, no CSA"),
        _repo(mkt, "P05", "HF_RV_C", "UST", 5.0e9 / 0.98, 0.02, 14, v, m("2Y"), desc="2Y term UST financing"),
        _repo(mkt, "P06", "MMF_GOVT_E", "UST", 2.0e9 / 0.98, 0.02, 1, v, m("1M"), "repo", desc="MMF cash, 1M (no UST inventory left)"),
        _trs(mkt, "P13", "HF_EQLS_B", "SIM_PHARMA", 100e6, 85, v, m("1Y"), 0.18, desc="single-name TRS at 85bp"),
        _irs(mkt, "P14", "INSURER_G", 250e6, v, m("15Y"), True, 6.0, desc="15Y insurer receiver, CCP-hedged"),
        _trs(mkt, "P07", "HF_MULTI_A", "SIM_IDX", 300e6, 65, v, m("1Y"), 0.12, desc="index delta-one at 65bp"),
        _repo(mkt, "P08", "HF_MULTI_A", "HY_CORP", 0.9e9 / 0.85, 0.15, 160, v, m("3M"), desc="large HY financing"),
        _repo(mkt, "P09", "INSURER_G", "IG_CORP", 0.4e9 / 0.94, 0.06, 20, v, m("3M"), desc="IG repo priced below street"),
        _irs(mkt, "P10", "PENSION_F", 500e6, v, m("30Y"), True, 1.5, hedge="none", desc="30Y LDI receiver, left unhedged"),
        _trs(mkt, "P11", "HF_MACRO_D", "SIM_IDX", 250e6, 18, v, m("1Y"), 0.12, desc="index TRS at 18bp"),
        _repo(mkt, "P12", "DEALER_I", "UST", 1.0e9 / 0.99, 0.01, 5, v, m("1M"), desc="1M interdealer UST"),
    ]
