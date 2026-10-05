"""Repo and reverse repo.

Perspective is always the desk (the bank):

* ``direction="reverse"``: the desk *lends cash* and receives collateral
  (financing a client's long securities position; a "reverse repo" on the
  desk's books). The desk is exposed if collateral falls below the loan.
* ``direction="repo"``: the desk *borrows cash* and delivers collateral from
  its inventory (funding the desk). The desk is exposed to the over-
  collateralisation (the haircut) if the cash lender fails.

Cash amounts follow the haircut convention: purchase price = MV * (1 - h).
Interest is simple ACT/360: repurchase price = PP * (1 + r * days/360).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from .dates import DAY, is_business_day, year_fraction
from .margin import cash_against_collateral, collateral_required, margin_call


@dataclass(frozen=True)
class Repo:
    trade_id: str
    cpty: str
    direction: str  # "reverse" | "repo"
    collateral: str  # collateral class key (see config.COLLATERAL)
    collateral_mv: float  # collateral market value at start
    haircut: float
    rate: float  # fixed repo rate, ACT/360
    start: date
    end: date
    mta: float = 250_000.0
    day_count: str = "ACT/360"

    def __post_init__(self):
        if self.direction not in ("reverse", "repo"):
            raise ValueError("direction must be 'reverse' or 'repo'")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.collateral_mv <= 0:
            raise ValueError("collateral_mv must be positive")

    # -- static economics -------------------------------------------------------
    @property
    def sign(self) -> int:
        """+1 when the desk is the cash lender (reverse repo), -1 when borrower."""
        return 1 if self.direction == "reverse" else -1

    @property
    def purchase_price(self) -> float:
        return cash_against_collateral(self.collateral_mv, self.haircut)

    @property
    def cash(self) -> float:
        return self.purchase_price

    @property
    def tau(self) -> float:
        return year_fraction(self.start, self.end, self.day_count)

    @property
    def repurchase_price(self) -> float:
        return self.purchase_price * (1.0 + self.rate * self.tau)

    @property
    def initial_margin(self) -> float:
        """Over-collateralisation delivered by the cash borrower at inception."""
        return self.collateral_mv - self.purchase_price

    def accrued(self, d: date) -> float:
        d = min(max(d, self.start), self.end)
        return self.purchase_price * self.rate * year_fraction(self.start, d, self.day_count)

    def loan_balance(self, d: date) -> float:
        return self.purchase_price + self.accrued(d)

    def required_collateral(self, d: date) -> float:
        return collateral_required(self.loan_balance(d), self.haircut)

    def exposure(self, d: date, collateral_mv_now: float) -> float:
        """Desk's current credit exposure (negative = desk owes more than it is owed)."""
        return self.sign * (self.loan_balance(d) - collateral_mv_now)

    def cash_flows(self) -> list[tuple[date, float]]:
        """Desk cash flows: reverse repo pays PP out and receives RP back."""
        return [(self.start, -self.sign * self.purchase_price), (self.end, self.sign * self.repurchase_price)]

    def years_to_maturity(self, val: date) -> float:
        return max(year_fraction(val, self.end, "ACT/365F"), 0.0)

    def mtm(self, val: date, market_rate: float) -> float:
        """Value to the desk vs. carrying value (loan balance) at ``val``.

        The fixed repurchase price is discounted at today's term repo rate for
        the same collateral/tenor. A term reverse repo lent at 4.0% loses value
        when market term rates rise.
        """
        if val >= self.end:
            return 0.0
        tau_rem = year_fraction(val, self.end, self.day_count)
        pv = self.repurchase_price / (1.0 + market_rate * tau_rem)
        return self.sign * (pv - self.loan_balance(val))

    def scaled(self, x: float, trade_id: str | None = None) -> "Repo":
        return replace(self, collateral_mv=self.collateral_mv * x, trade_id=trade_id or self.trade_id)

    # -- lifecycle ----------------------------------------------------------------
    def lifecycle(self, price: dict[date, float]) -> list[dict]:
        """Daily margining over the life of the trade.

        ``price`` maps business days to the collateral's clean price per unit;
        the desk/counterparty hold ``units`` of it. Margin is settled in
        collateral units at the day's price (variation margin in securities).
        Returns one row per business day with the call made that day.
        """
        p0 = price[self.start]
        units = self.collateral_mv / p0
        rows = []
        d = self.start
        while d <= self.end:
            if is_business_day(d):
                if d not in price:
                    raise KeyError(f"missing collateral price for {d}")
                p = price[d]
                mv_before = units * p
                loan = self.loan_balance(d)
                req = self.required_collateral(d)
                call = 0.0
                if self.start < d < self.end:
                    call = margin_call(req, mv_before, mta=self.mta)
                    units += call / p
                mv_after = units * p
                rows.append({
                    "date": d,
                    "price": p,
                    "units": units,
                    "collateral_mv_before_call": mv_before,
                    "loan_balance": loan,
                    "accrued_interest": loan - self.purchase_price,
                    "required_collateral": req,
                    "margin_call": call,  # >0: cash borrower delivers more collateral
                    "collateral_mv": mv_after,
                    "exposure_before_call": self.sign * (loan - mv_before),
                    "exposure_after_call": self.sign * (loan - mv_after),
                })
            d += DAY
        return rows

    # -- default ------------------------------------------------------------------
    def close_out(self, default_date: date, collateral_mv_at_default: float, adverse_move: float,
                  liquidation_cost: float, recovery: float) -> dict:
        """Counterparty default and close-out over the margin period of risk.

        reverse (client defaults): desk sells the collateral, which has moved
        *down* by ``adverse_move`` and costs ``liquidation_cost`` to sell; any
        shortfall vs. the loan balance is an unsecured claim recovering
        ``recovery``.
        repo (cash lender defaults): desk keeps the cash, loses its securities
        and must buy them back after an *upward* adverse move.
        """
        loan = self.loan_balance(default_date)
        if self.direction == "reverse":
            proceeds = collateral_mv_at_default * (1.0 - adverse_move) * (1.0 - liquidation_cost)
            shortfall = max(loan - proceeds, 0.0)
        else:
            replacement = collateral_mv_at_default * (1.0 + adverse_move) * (1.0 + liquidation_cost)
            proceeds = replacement
            shortfall = max(replacement - loan, 0.0)
        return {
            "trade_id": self.trade_id,
            "claim_or_cash_held": loan,
            "collateral_value_realised": proceeds,
            "shortfall": shortfall,
            "loss": shortfall * (1.0 - recovery),
        }
