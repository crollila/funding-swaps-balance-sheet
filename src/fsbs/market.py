"""Market environment: curves, collateral tables, equity spots/dividends, stress overrides."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from . import config
from .curves import DiscountCurve, FundingCurve, OISQuote, bootstrap_sofr_ois, bump_quotes
from .dates import add_months
from .trs import Dividend


def make_dividends(val: date) -> dict[str, list[Dividend]]:
    """Synthetic quarterly dividends (ex-dates 15 Feb/May/Aug/Nov), amount = spot * yield / 4."""
    out = {}
    for key, u in config.UNDERLYINGS.items():
        divs = []
        d = date(val.year - 1, 2, 15)
        while d.year <= val.year + 8:
            divs.append(Dividend(d, u.spot * u.div_yield / 4.0))
            d = add_months(d, 3)
        out[key] = divs
    return out


@dataclass
class Market:
    val: date
    quotes: list[OISQuote]
    sofr: DiscountCurve
    funding: FundingCurve
    spots: dict[str, float]
    dividends: dict[str, list[Dividend]]
    collateral_mv_factor: dict[str, float] = field(default_factory=dict)  # stress: MV multiplier by class
    street_haircut_mult: float = 1.0  # stress: street haircut multiplier
    street_haircut_add: float = 0.0  # stress: absolute add-on
    liquidity_mult: float = 1.0  # stress: liquidity charge multiplier
    name: str = "base"

    # -- collateral ---------------------------------------------------------------
    def coll(self, key: str) -> config.CollateralClass:
        return config.COLLATERAL[key]

    def street_haircut(self, key: str) -> float:
        h = config.COLLATERAL[key].street_haircut * self.street_haircut_mult + self.street_haircut_add
        return min(max(h, 0.0), 0.95)

    def mv_factor(self, key: str) -> float:
        return self.collateral_mv_factor.get(key, 1.0)

    def street_rate(self, key: str, end: date) -> float:
        return self.sofr.term_rate(end) + config.COLLATERAL[key].street_spread_bp * 1e-4

    def ftp(self, end: date) -> float:
        return self.funding.rate(end)

    def funding_spread(self, end: date) -> float:
        return self.funding.spread(max(self.sofr.t(end), 1 / 365))

    def spot(self, underlying: str) -> float:
        return self.spots[underlying]

    # -- variants -----------------------------------------------------------------
    def with_quotes_bump(self, bp: float, only: str | None = None, name: str | None = None) -> "Market":
        q = bump_quotes(self.quotes, bp, only)
        sofr = bootstrap_sofr_ois(self.val, q)
        return replace(self, quotes=q, sofr=sofr, funding=replace(self.funding, sofr=sofr), name=name or self.name)


def build_market(val: date = config.VALUATION_DATE) -> Market:
    quotes = [OISQuote(t, r) for t, r in config.SOFR_QUOTES]
    sofr = bootstrap_sofr_ois(val, quotes)
    funding = FundingCurve(sofr, config.FUNDING_SPREAD_TENORS, config.FUNDING_SPREAD_BPS)
    spots = {k: u.spot for k, u in config.UNDERLYINGS.items()}
    return Market(val, quotes, sofr, funding, spots, make_dividends(val))
