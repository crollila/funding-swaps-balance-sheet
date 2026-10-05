"""Vanilla fixed-vs-SOFR interest-rate swap, priced leg by leg.

* Fixed leg: notional * K * tau_i paid at each fixed period end.
* Floating leg: compounded SOFR in arrears. With projection and discounting on
  the same OIS curve the expected period rate is the simple forward
  F_i = (DF(s_i)/DF(e_i) - 1) / tau_i. A period that has already started uses
  the realised compounded fixing to date (``past_fixing``) times the
  projected growth to period end.
* Both legs ACT/360, payment on period end (no payment lag; see MODEL_ASSUMPTIONS.md).

Sign: PV is from the desk's perspective. ``pay_fixed=True`` means the desk
pays fixed / receives SOFR, which gains when rates rise (positive DV01).
DV01 here = PV(rates + 1bp) - PV(rates), i.e. P&L per +1bp.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from .curves import DiscountCurve
from .dates import schedule, year_fraction


@dataclass(frozen=True)
class IRS:
    trade_id: str
    cpty: str
    notional: float
    fixed_rate: float
    start: date
    end: date
    pay_fixed: bool  # desk pays fixed
    fixed_months: int = 12
    float_months: int = 12
    past_fixing: float | None = None  # realised compounded SOFR of the current period to date (ACT/360)
    day_count: str = "ACT/360"

    @property
    def sign(self) -> int:
        return 1 if self.pay_fixed else -1

    def scaled(self, x: float, trade_id: str | None = None) -> "IRS":
        return replace(self, notional=self.notional * x, trade_id=trade_id or self.trade_id)

    def with_fixed_rate(self, k: float) -> "IRS":
        return replace(self, fixed_rate=k)

    def years_to_maturity(self, val: date) -> float:
        return max(year_fraction(val, self.end, "ACT/365F"), 0.0)

    def _periods(self, months: int, ref: date) -> list[tuple[date, date]]:
        dates = schedule(self.start, self.end, months)
        return [(a, b) for a, b in zip(dates[:-1], dates[1:]) if b > ref]

    # -- legs -----------------------------------------------------------------
    def fixed_leg(self, curve: DiscountCurve) -> list[dict]:
        rows = []
        for s, e in self._periods(self.fixed_months, curve.ref_date):
            tau = year_fraction(s, e, self.day_count)
            amt = self.notional * self.fixed_rate * tau
            df = curve.df(e)
            rows.append({"leg": "fixed", "start": s, "end": e, "pay": e, "tau": tau, "rate": self.fixed_rate,
                         "amount": amt, "df": df, "pv": amt * df, "t": curve.t(e)})
        return rows

    def float_leg(self, curve: DiscountCurve) -> list[dict]:
        ref = curve.ref_date
        rows = []
        for s, e in self._periods(self.float_months, ref):
            tau = year_fraction(s, e, self.day_count)
            df_e = curve.df(e)
            if s >= ref:
                rate = curve.forward_rate(s, e, self.day_count)
                seasoned = False
            else:
                if self.past_fixing is None:
                    raise ValueError(f"{self.trade_id}: period {s}..{e} has started; past_fixing required")
                growth_past = 1.0 + self.past_fixing * year_fraction(s, ref, self.day_count)
                rate = (growth_past / df_e - 1.0) / tau  # DF(ref) = 1
                seasoned = True
            amt = self.notional * rate * tau
            rows.append({"leg": "float", "start": s, "end": e, "pay": e, "tau": tau, "rate": rate,
                         "amount": amt, "df": df_e, "pv": amt * df_e, "t": curve.t(e), "seasoned": seasoned,
                         "t_start": curve.t(s) if s >= ref else 0.0, "df_start": curve.df(s) if s >= ref else 1.0})
        return rows

    def pv_fixed(self, curve: DiscountCurve) -> float:
        return sum(r["pv"] for r in self.fixed_leg(curve))

    def pv_float(self, curve: DiscountCurve) -> float:
        return sum(r["pv"] for r in self.float_leg(curve))

    def annuity(self, curve: DiscountCurve) -> float:
        """PV of 1 (unit rate) paid on the fixed schedule, times notional."""
        return sum(self.notional * r["tau"] * r["df"] for r in self.fixed_leg(curve))

    def pv(self, curve: DiscountCurve) -> float:
        return self.sign * (self.pv_float(curve) - self.pv_fixed(curve))

    def par_rate(self, curve: DiscountCurve) -> float:
        """Fixed rate that sets PV to zero given the remaining schedule."""
        return self.pv_float(curve) / self.annuity(curve)

    # -- risk -----------------------------------------------------------------
    def dv01(self, curve: DiscountCurve, bump_bp: float = 1.0) -> float:
        """Central finite difference on a parallel zero-rate shift, per +1bp."""
        up = self.pv(curve.shifted(bump_bp))
        dn = self.pv(curve.shifted(-bump_bp))
        return (up - dn) / (2.0 * bump_bp)

    def dv01_analytic(self, curve: DiscountCurve) -> float:
        """Exact derivative of PV w.r.t. a parallel continuously-compounded zero shift, per 1bp.

        dDF(t)/dz = -t * DF(t).  Fixed leg: sum(-t_i * CF_i * DF_i).
        Unstarted float period: PV = N (DF_s - DF_e)  ->  N (-t_s DF_s + t_e DF_e).
        Seasoned float period:  PV = N (g - DF_e)     ->  N t_e DF_e.
        """
        d_fixed = sum(-r["t"] * r["amount"] * r["df"] for r in self.fixed_leg(curve))
        d_float = 0.0
        for r in self.float_leg(curve):
            if r["seasoned"]:
                d_float += self.notional * r["t"] * r["df"]
            else:
                d_float += self.notional * (-r["t_start"] * r["df_start"] + r["t"] * r["df"])
        return self.sign * (d_float - d_fixed) * 1e-4
