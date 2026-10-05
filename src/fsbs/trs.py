"""Equity total-return swap with resetting notional (prime-brokerage "synthetic long").

The client receives the total return on ``shares`` units of an equity/index
and pays SOFR + spread on the reset notional. At each reset date t_k:

    equity leg (client receives)   = q * (S_k - S_{k-1}) + q * sum(dividends ex in (t_{k-1}, t_k])
    financing leg (client pays)    = q * S_{k-1} * (SOFR_k + spread) * tau_k
    then notional resets to q * S_k

(fixed share quantity, notional resets to market each period). Dividends are
passed through on the following reset date. The desk is the total-return
payer and hedges by holding the shares, funded in the equity-financing market.

Mark-to-market (``mtm_client``) is the risk-neutral PV under deterministic
rates where the equity forward is F(t,T) = (S_t - PV(divs in (t,T])) / DF(t,T)
(equity financed at SOFR for forward purposes). Future periods are then worth
almost exactly -spread * PV(notional) (plus a tiny dividend timing term), which
is why a TRS at zero spread is worth ~0 at every reset.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from .curves import DiscountCurve
from .dates import schedule, year_fraction


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    amount: float  # per share


@dataclass(frozen=True)
class EquityTRS:
    trade_id: str
    cpty: str
    underlying: str
    shares: float
    initial_price: float
    start: date
    end: date
    spread: float  # client pays SOFR + spread (decimal)
    reset_months: int = 3
    im_pct: float = 0.15  # client initial margin, fraction of notional
    last_reset_price: float | None = None  # S at the most recent reset if seasoned
    past_fixing: float | None = None  # realised compounded SOFR of current period to date
    day_count: str = "ACT/360"

    def scaled(self, x: float, trade_id: str | None = None) -> "EquityTRS":
        return replace(self, shares=self.shares * x, trade_id=trade_id or self.trade_id)

    def reset_dates(self) -> list[date]:
        return schedule(self.start, self.end, self.reset_months)

    def notional(self, price: float) -> float:
        return self.shares * price

    def years_to_maturity(self, val: date) -> float:
        return max(year_fraction(val, self.end, "ACT/365F"), 0.0)

    # -- realised cash flows ------------------------------------------------------
    def period_settlement(self, s_prev: float, s_now: float, divs: float, sofr: float, tau: float) -> dict:
        equity = self.shares * (s_now - s_prev) + self.shares * divs
        financing = self.shares * s_prev * (sofr + self.spread) * tau
        return {"equity_leg": equity, "financing_leg": financing, "client_net": equity - financing,
                "notional": self.shares * s_prev}

    def simulate(self, prices: dict[date, float], dividends: list[Dividend], sofr_fixings: dict[date, float],
                 hedge_funding_spread: float) -> list[dict]:
        """Realised lifecycle along a given price path.

        ``prices`` must contain every reset date; ``sofr_fixings[end]`` is the
        realised compounded SOFR for the period ending on ``end``. The desk
        hedges with q shares funded at SOFR + ``hedge_funding_spread`` on the
        reset notional; its net per period is q * S_{k-1} * (spread - hedge_funding_spread) * tau
        exactly, because equity and dividend legs offset the hedge.
        """
        rd = self.reset_dates()
        rows = []
        for a, b in zip(rd[:-1], rd[1:]):
            tau = year_fraction(a, b, self.day_count)
            divs = sum(dv.amount for dv in dividends if a < dv.ex_date <= b)
            st = self.period_settlement(prices[a], prices[b], divs, sofr_fixings[b], tau)
            hedge_pnl = self.shares * (prices[b] - prices[a]) + self.shares * divs
            hedge_funding = self.shares * prices[a] * (sofr_fixings[b] + hedge_funding_spread) * tau
            rows.append({
                "start": a, "end": b, "tau": tau, "s_prev": prices[a], "s_now": prices[b], "dividends": divs,
                "sofr": sofr_fixings[b], **st,
                "desk_swap_pnl": -st["client_net"],
                "desk_hedge_pnl": hedge_pnl - hedge_funding,
                "desk_net": -st["client_net"] + hedge_pnl - hedge_funding,
            })
        return rows

    # -- valuation ----------------------------------------------------------------
    def mtm_client(self, curve: DiscountCurve, spot: float, dividends: list[Dividend]) -> float:
        """PV to the client (total-return receiver) at ``curve.ref_date``."""
        val = curve.ref_date
        rd = self.reset_dates()
        if val >= rd[-1]:
            return 0.0
        if val < self.start:
            raise ValueError("valuation before trade start is not supported")
        q = self.shares

        def pv_div(a: date, b: date) -> float:  # PV at val of dividends going ex in (a, b], ex > val
            return sum(dv.amount * curve.df(dv.ex_date) for dv in dividends if a < dv.ex_date <= b and dv.ex_date > val)

        def divs_in(a: date, b: date) -> float:
            return sum(dv.amount for dv in dividends if a < dv.ex_date <= b)

        k = max(i for i, d in enumerate(rd[:-1]) if d <= val)
        tk, tk1 = rd[k], rd[k + 1]
        if tk == self.start:
            s_k = self.initial_price
        else:
            if self.last_reset_price is None:
                raise ValueError(f"{self.trade_id}: last_reset_price required for seasoned trade")
            s_k = self.last_reset_price
        if tk < val:
            if self.past_fixing is None:
                raise ValueError(f"{self.trade_id}: past_fixing required mid-period")
            growth_past = 1.0 + self.past_fixing * year_fraction(tk, val, self.day_count)
        else:
            growth_past = 1.0
        df1 = curve.df(tk1)
        tau_k = year_fraction(tk, tk1, self.day_count)
        equity = q * ((spot - pv_div(val, tk1)) - s_k * df1) + q * divs_in(tk, tk1) * df1
        financing = q * s_k * ((growth_past - df1) + self.spread * tau_k * df1)
        value = equity - financing

        for a, b in zip(rd[k + 1:-1], rd[k + 2:]):
            dfa, dfb = curve.df(a), curve.df(b)
            tau = year_fraction(a, b, self.day_count)
            x_a = spot - pv_div(val, a)
            x_b = spot - pv_div(val, b)
            eq = q * (x_b - x_a * dfb / dfa) + q * divs_in(a, b) * dfb
            fin = q * x_a * ((1.0 - dfb / dfa) + self.spread * tau * dfb / dfa)
            value += eq - fin
        return value

    def mtm(self, curve: DiscountCurve, spot: float, dividends: list[Dividend]) -> float:
        """PV to the desk (total-return payer)."""
        return -self.mtm_client(curve, spot, dividends)

    def equity_delta(self) -> float:
        """Desk's swap delta in shares (short the underlying via the swap)."""
        return -self.shares
