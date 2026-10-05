"""Discount curves built from first principles.

* ``DiscountCurve`` stores discount factors at node dates and interpolates
  log-linearly in time (equivalently: piecewise-constant instantaneous forwards).
* ``bootstrap_sofr_ois`` turns SOFR OIS par quotes into a discount curve by
  solving each pillar's par condition in turn, re-pricing every earlier quote
  exactly.
* ``FundingCurve`` is the desk's unsecured funds-transfer-pricing (FTP) curve:
  SOFR plus a term funding spread.

Curve time is ACT/365F from the valuation date. Accrual on instruments is
ACT/360 (USD money-market convention).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy.optimize import brentq

from .dates import add_tenor, schedule, year_fraction


class DiscountCurve:
    def __init__(self, ref_date: date, node_dates: list[date], dfs: list[float]):
        if len(node_dates) != len(dfs):
            raise ValueError("node_dates and dfs length mismatch")
        pairs = sorted(zip(node_dates, dfs))
        self.ref_date = ref_date
        self.node_dates = [d for d, _ in pairs]
        self.times = np.array([0.0] + [self.t(d) for d, _ in pairs])
        self.log_dfs = np.array([0.0] + [math.log(v) for _, v in pairs])
        if np.any(np.diff(self.times) <= 0):
            raise ValueError("curve nodes must be strictly after the reference date and distinct")

    # -- time & interpolation -------------------------------------------------
    def t(self, d: date) -> float:
        return year_fraction(self.ref_date, d, "ACT/365F")

    def df_t(self, t: float) -> float:
        ts, ls = self.times, self.log_dfs
        if t <= 0.0:
            return 1.0
        if t >= ts[-1]:
            slope = (ls[-1] - ls[-2]) / (ts[-1] - ts[-2])  # flat-forward extrapolation
            return math.exp(ls[-1] + slope * (t - ts[-1]))
        return math.exp(float(np.interp(t, ts, ls)))

    def df(self, d: date) -> float:
        return self.df_t(self.t(d))

    def df_between(self, d1: date, d2: date) -> float:
        return self.df(d2) / self.df(d1)

    # -- rates ----------------------------------------------------------------
    def zero_rate(self, d: date) -> float:
        """Continuously-compounded zero rate, ACT/365F."""
        t = self.t(d)
        return -math.log(self.df(d)) / t

    def forward_rate(self, d1: date, d2: date, basis: str = "ACT/360") -> float:
        """Simple forward rate for [d1, d2]; for SOFR this is the expected
        compounded-in-arrears rate of the period."""
        tau = year_fraction(d1, d2, basis)
        return (self.df(d1) / self.df(d2) - 1.0) / tau

    def term_rate(self, end: date) -> float:
        """Simple ACT/360 rate from the reference date to ``end``."""
        return self.forward_rate(self.ref_date, end)

    def par_ois_rate(self, tenor: str) -> float:
        """Annual-pay fixed-vs-SOFR par rate for a spot-starting swap."""
        end = add_tenor(self.ref_date, tenor)
        dates = _ois_fixed_dates(self.ref_date, end)
        annuity = sum(year_fraction(a, b) * self.df(b) for a, b in zip(dates[:-1], dates[1:]))
        return (1.0 - self.df(end)) / annuity

    # -- shocks ---------------------------------------------------------------
    def shifted(self, bp: float | None = None, shift_fn=None) -> "DiscountCurve":
        """Zero-rate shift at the nodes: parallel ``bp`` or ``shift_fn(t) -> bp``.

        Because log-DFs are linear in time between nodes, a parallel zero
        shift applied at the nodes is an exact parallel shift everywhere.
        """
        if (bp is None) == (shift_fn is None):
            raise ValueError("give exactly one of bp or shift_fn")
        fn = shift_fn if shift_fn is not None else (lambda _t: bp)
        dfs = [math.exp(lg - fn(t) * 1e-4 * t) for t, lg in zip(self.times[1:], self.log_dfs[1:])]
        return DiscountCurve(self.ref_date, list(self.node_dates), dfs)


def _ois_fixed_dates(start: date, end: date) -> list[date]:
    """SOFR OIS: single period up to one year, annual periods beyond."""
    if year_fraction(start, end, "ACT/365F") <= 1.0 + 1e-9:
        return [start, end]
    return schedule(start, end, 12)


@dataclass(frozen=True)
class OISQuote:
    tenor: str
    rate: float  # decimal, annual ACT/360


def bootstrap_sofr_ois(ref_date: date, quotes: list[OISQuote]) -> DiscountCurve:
    """Sequential bootstrap of SOFR OIS par quotes.

    Each spot-starting OIS pays fixed annually (single period if <= 1Y) against
    compounded SOFR. With projection and discounting on the same curve the
    floating leg telescopes to 1 - DF(T), so the par condition is

        S * sum_i tau_i * DF(t_i)  =  1 - DF(T).

    Payment dates between the previous pillar and the new one depend on the
    unknown DF(T) through interpolation, so each pillar is solved with Brent.
    """
    qs = sorted(quotes, key=lambda q: add_tenor(ref_date, q.tenor))
    node_dates: list[date] = []
    node_dfs: list[float] = []
    for q in qs:
        end = add_tenor(ref_date, q.tenor)
        dates = _ois_fixed_dates(ref_date, end)

        def residual(df_end: float, end=end, dates=dates, q=q) -> float:
            c = DiscountCurve(ref_date, node_dates + [end], node_dfs + [df_end])
            annuity = sum(year_fraction(a, b) * c.df(b) for a, b in zip(dates[:-1], dates[1:]))
            return q.rate * annuity - (1.0 - df_end)

        df_end = brentq(residual, 1e-6, 1.5, xtol=1e-15, rtol=1e-15, maxiter=200)
        node_dates.append(end)
        node_dfs.append(df_end)
    return DiscountCurve(ref_date, node_dates, node_dfs)


def bump_quotes(quotes: list[OISQuote], bp: float, only: str | None = None) -> list[OISQuote]:
    """Parallel par-quote bump (or a single pillar if ``only`` names its tenor)."""
    return [OISQuote(q.tenor, q.rate + (bp * 1e-4 if only in (None, q.tenor) else 0.0)) for q in quotes]


@dataclass
class FundingCurve:
    """Unsecured funds-transfer-pricing curve: SOFR term rate + term funding spread.

    ``spread_tenors_years`` / ``spread_bps`` define the bank's unsecured term
    spread over SOFR (linear interpolation, flat extrapolation).
    """

    sofr: DiscountCurve
    spread_tenors_years: list[float]
    spread_bps: list[float]
    shift_bp: float = 0.0  # stress add-on

    def spread(self, years: float) -> float:
        return (float(np.interp(years, self.spread_tenors_years, self.spread_bps)) + self.shift_bp) * 1e-4

    def rate(self, end: date) -> float:
        """Annualised simple ACT/360 unsecured rate to ``end``."""
        years = max(self.sofr.t(end), 1.0 / 365.0)
        return self.sofr.term_rate(end) + self.spread(years)

    def with_shift(self, bp: float) -> "FundingCurve":
        return FundingCurve(self.sofr, self.spread_tenors_years, self.spread_bps, self.shift_bp + bp)
