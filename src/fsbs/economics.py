"""Per-trade desk economics and resource usage.

For every position this computes, per year:

    net income = client revenue
                 - funding cost
                 - balance-sheet charge   (on leverage exposure)
                 - capital charge         (capital ratio x hurdle x RWA)
                 - liquidity charge       (on liquidity usage)
                 - expected counterparty cost (PD x LGD x EAD)

and the resources it consumes (balance sheet, RWA, EAD per counterparty,
liquidity, non-HQLA financing, inventory, DV01, client leverage). All of these
scale linearly with trade size, which is what lets the optimizer treat each
client request as a fill fraction x in [0, 1].

Rates are ACT/360; ``ANN`` converts a money-market rate times principal into
dollars per 365-day year.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.stats import norm

from . import config
from .exposure import DerivTrade, sa_ccr, sft_contribution
from .irs import IRS
from .market import Market
from .positions import Position

ANN = 365.0 / 360.0


@dataclass
class TradeMetrics:
    trade_id: str
    product: str
    cpty: str
    desc: str
    end: date
    tenor_years: float
    size: float  # cash for repo, notional for TRS/IRS
    revenue: float
    funding_cost: float
    bs_charge: float
    capital_charge: float
    liquidity_charge: float
    expected_cp_cost: float
    le_onbalance: float  # on-balance-sheet asset (repo receivable, TRS hedge stock)
    le_ccr: float  # counterparty-exposure add-on to leverage exposure
    rwa: float
    ead_by_cp: dict[str, float]
    liquidity_usage: float
    unsecured_funding: float
    non_hqla_financing: float
    client_gross: float  # financed gross position for client leverage limits
    inventory_use: dict[str, float]
    dv01: float
    mtm: float
    conc_class: str | None
    sft_key: tuple | None = None  # (cpty, end) for on-balance-sheet repo netting
    sft_signed_loan: float = 0.0  # +loan for reverse repo, -loan for repo
    haircut_ok: bool = True
    haircut_note: str = ""
    stress_loss: float = 0.0
    detail: dict = field(default_factory=dict)

    @property
    def le_total(self) -> float:
        return self.le_onbalance + self.le_ccr

    @property
    def charges_total(self) -> float:
        return self.bs_charge + self.capital_charge

    @property
    def net_income(self) -> float:
        return (self.revenue - self.funding_cost - self.bs_charge - self.capital_charge
                - self.liquidity_charge - self.expected_cp_cost)

    @property
    def robs(self) -> float:
        return self.net_income / self.le_total if self.le_total > 0 else float("inf")

    @property
    def ead(self) -> float:
        return sum(self.ead_by_cp.values())


def _cp(key: str) -> config.Counterparty:
    return config.COUNTERPARTIES[key]


def _finish(m: TradeMetrics, ch: config.DeskCharges, mkt: Market) -> TradeMetrics:
    m.bs_charge = ch.balance_sheet_charge * m.le_total
    m.capital_charge = ch.capital_ratio * ch.hurdle_rate * m.rwa
    m.liquidity_charge = ch.liquidity_charge * mkt.liquidity_mult * m.liquidity_usage
    m.expected_cp_cost = sum(_cp(k).pd * _cp(k).lgd * v for k, v in m.ead_by_cp.items())
    return m


def repo_market_rate(mkt: Market, collateral: str, end: date) -> float:
    """Today's term rate for a client repo of this collateral (used to mark fixed-rate term repos)."""
    return mkt.sofr.term_rate(end) + config.COLLATERAL[collateral].client_spread_bp * 1e-4


def analyze(pos: Position, mkt: Market, ch: config.DeskCharges = config.CHARGES) -> TradeMetrics:
    if pos.product in ("reverse_repo", "repo"):
        return _finish(_analyze_repo(pos, mkt), ch, mkt)
    if pos.product == "trs":
        return _finish(_analyze_trs(pos, mkt, ch), ch, mkt)
    return _finish(_analyze_irs(pos, mkt, ch), ch, mkt)


def _zero(**kw) -> dict:
    base = dict(bs_charge=0.0, capital_charge=0.0, liquidity_charge=0.0, expected_cp_cost=0.0)
    base.update(kw)
    return base


def _analyze_repo(pos: Position, mkt: Market) -> TradeMetrics:
    r = pos.inst
    cls = mkt.coll(r.collateral)
    cp = _cp(r.cpty)
    val = mkt.val
    loan = r.loan_balance(val)
    mv_now = r.required_collateral(val) * mkt.mv_factor(r.collateral)  # margin current as of val
    years = r.years_to_maturity(val)
    days = (r.end - val).days
    mkt_rate = repo_market_rate(mkt, r.collateral, r.end)
    tau_rem = (r.end - val).days / 360.0
    ddr = r.repurchase_price * tau_rem / (1.0 + mkt_rate * tau_rem) ** 2  # -dPV/dr
    if r.direction == "reverse":
        street_cash = mv_now * (1.0 - mkt.street_haircut(r.collateral))
        secured = min(loan, street_cash)
        gap = loan - secured
        revenue = loan * r.rate * ANN
        funding = (secured * mkt.street_rate(r.collateral, r.end) + gap * mkt.ftp(r.end)) * ANN
        ead = max(sft_contribution("reverse", loan, mv_now, cls.supervisory_haircut), 0.0)
        liq = gap + cls.runoff * secured * (1.0 if days > 30 else 0.25)
        ok = r.haircut >= cls.internal_min_haircut - 1e-12
        return TradeMetrics(
            r.trade_id, pos.product, r.cpty, pos.desc, r.end, years, loan, revenue, funding,
            le_onbalance=loan, le_ccr=ead, rwa=ead * cp.risk_weight, ead_by_cp={r.cpty: ead},
            liquidity_usage=liq, unsecured_funding=gap,
            non_hqla_financing=loan if cls.hqla == "NONE" else 0.0, client_gross=mv_now, inventory_use={},
            dv01=-ddr * 1e-4, mtm=r.mtm(val, mkt_rate), conc_class=r.collateral,
            sft_key=(r.cpty, r.end), sft_signed_loan=loan, haircut_ok=ok,
            haircut_note="" if ok else f"client haircut {r.haircut:.1%} < internal minimum {cls.internal_min_haircut:.1%}",
            detail={"collateral_mv": mv_now, "secured_funding": secured, "street_cash": street_cash,
                    "initial_margin": mv_now - loan},
            **_zero())
    # desk borrows cash against its own inventory
    # value of the cash raised = what the desk would otherwise pay to finance this collateral in the street
    revenue = loan * mkt.street_rate(r.collateral, r.end) * ANN
    funding = loan * r.rate * ANN
    ead = max(sft_contribution("repo", loan, mv_now, cls.supervisory_haircut), 0.0)
    return TradeMetrics(
        r.trade_id, pos.product, r.cpty, pos.desc, r.end, years, loan, revenue, funding,
        le_onbalance=0.0, le_ccr=ead, rwa=ead * cp.risk_weight, ead_by_cp={r.cpty: ead},
        liquidity_usage=0.0, unsecured_funding=0.0, non_hqla_financing=0.0, client_gross=0.0,
        inventory_use={r.collateral: mv_now}, dv01=ddr * 1e-4, mtm=r.mtm(val, mkt_rate), conc_class=None,
        sft_key=(r.cpty, r.end), sft_signed_loan=-loan,
        detail={"collateral_mv": mv_now, "initial_margin": mv_now - loan}, **_zero())


def trs_hedge_funding(n: float, im: float, collateral: str, end: date, mkt: Market) -> dict:
    """Fund the stock hedge: client cash IM first (rebated at SOFR), then the street repo
    (up to N * (1 - street haircut)), and unsecured FTP for whatever is left."""
    h = mkt.street_haircut(collateral)
    im_used = min(im, n)
    unsecured = max(n * h - im_used, 0.0)
    secured = n - im_used - unsecured
    cost = (im_used * mkt.sofr.term_rate(end) + secured * mkt.street_rate(collateral, end)
            + unsecured * mkt.ftp(end)) * ANN
    return {"secured": secured, "unsecured": unsecured, "im_used": im_used, "cost": cost, "street_haircut": h}


def _analyze_trs(pos: Position, mkt: Market, ch: config.DeskCharges) -> TradeMetrics:
    t = pos.inst
    u = config.UNDERLYINGS[t.underlying]
    cls = mkt.coll(u.collateral)
    cp = _cp(t.cpty)
    s = mkt.spot(t.underlying)
    n = t.shares * s
    im = t.im_pct * t.shares * config.UNDERLYINGS[t.underlying].spot  # IM sized on base spot, fixed in $
    years = t.years_to_maturity(mkt.val)
    revenue = n * (mkt.sofr.term_rate(t.end) + t.spread) * ANN
    hf = trs_hedge_funding(n, im, u.collateral, t.end, mkt)
    mtm = t.mtm(mkt.sofr, s, mkt.dividends[t.underlying])
    dt = DerivTrade(t.trade_id, "EQ", -1.0, n, 0.0, years, mtm, t.underlying, u.is_index)
    sa = sa_ccr([dt], margined=cp.csa, mpor_days=ch.bilateral_mpor_days,
                vm_held=mtm if cp.csa else 0.0, im_held=im, alpha=ch.sacc_alpha)
    ok = t.im_pct >= cls.internal_min_haircut - 1e-12
    return TradeMetrics(
        t.trade_id, pos.product, t.cpty, pos.desc, t.end, years, n, revenue, hf["cost"],
        le_onbalance=n, le_ccr=sa["ead"], rwa=sa["ead"] * cp.risk_weight, ead_by_cp={t.cpty: sa["ead"]},
        liquidity_usage=hf["unsecured"] + cls.runoff * hf["secured"], unsecured_funding=hf["unsecured"],
        non_hqla_financing=n if cls.hqla == "NONE" else 0.0, client_gross=n, inventory_use={},
        dv01=0.0, mtm=mtm, conc_class=u.collateral, haircut_ok=ok,
        haircut_note="" if ok else f"client IM {t.im_pct:.1%} < internal minimum {cls.internal_min_haircut:.1%}",
        detail={"im": im, "sa_ccr": sa, **hf}, **_zero())


def irs_epe(mtm: float, dv01_abs: float, years: float, vol_bp: float, n: int = 48) -> float:
    """Expected positive exposure of an uncollateralised swap (normal-rate approximation).

    Value at t ~ Normal(mu(t), sd(t)^2) with sd(t) = vol_bp * sqrt(t) * DV01 * (T - t)/T
    (remaining DV01 amortises roughly linearly) and mu(t) = mtm * (T - t)/T.
    """
    if years <= 0:
        return max(mtm, 0.0)
    ts = (np.arange(n) + 0.5) / n * years
    rem = (years - ts) / years
    sd = vol_bp * np.sqrt(ts) * dv01_abs * rem
    mu = mtm * rem
    z = np.where(sd > 0, mu / np.maximum(sd, 1e-12), 0.0)
    ee = mu * norm.cdf(z) + sd * norm.pdf(z)
    return float(ee.mean())


def irs_ccp_im(dv01_abs: float, ch: config.DeskCharges) -> float:
    return ch.ccp_im_conf_z * ch.irs_vol_bp * math.sqrt(ch.ccp_mpor_days / 252.0) * dv01_abs


def irs_deriv_trade(s: IRS, mkt: Market) -> DerivTrade:
    return DerivTrade(s.trade_id, "IR", 1.0 if s.pay_fixed else -1.0, s.notional,
                      max(mkt.sofr.t(s.start), 0.0), s.years_to_maturity(mkt.val), s.pv(mkt.sofr))


def _analyze_irs(pos: Position, mkt: Market, ch: config.DeskCharges) -> TradeMetrics:
    s = pos.inst
    cp = _cp(s.cpty)
    years = s.years_to_maturity(mkt.val)
    mtm_c = s.pv(mkt.sofr)
    dv01_c = s.dv01_analytic(mkt.sofr)
    legs = [s] + ([pos.hedge] if pos.hedge is not None else [])
    dv01 = sum(x.dv01_analytic(mkt.sofr) for x in legs)
    fs = mkt.funding_spread(s.end)  # FVA on uncollateralised exposure: term funding spread
    fs_im = mkt.funding.spread(1.0)  # MVA proxy: IM funded at the 1Y unsecured spread
    revenue = s.notional * pos.sales_spread_bp * 1e-4
    ead_by_cp: dict[str, float] = {}
    im_total = 0.0
    sa_detail = {}
    for leg in legs:
        lcp = _cp(leg.cpty)
        dt = irs_deriv_trade(leg, mkt)
        if lcp.kind == "ccp":
            im = irs_ccp_im(abs(leg.dv01_analytic(mkt.sofr)), ch)
            im_total += im
            # IM posted to the CCP is treated as bankruptcy-remote (segregated), so it is not
            # added to replacement cost; its cost shows up as funding (MVA) and liquidity usage.
            sa = sa_ccr([dt], margined=True, mpor_days=ch.ccp_mpor_days, vm_held=dt.mtm, im_held=0.0,
                        alpha=ch.sacc_alpha)
        else:
            sa = sa_ccr([dt], margined=lcp.csa, mpor_days=ch.bilateral_mpor_days,
                        vm_held=dt.mtm if lcp.csa else 0.0, alpha=ch.sacc_alpha)
        ead_by_cp[leg.cpty] = ead_by_cp.get(leg.cpty, 0.0) + sa["ead"]
        sa_detail[leg.trade_id] = sa
    epe = 0.0 if cp.csa or cp.kind == "ccp" else irs_epe(mtm_c, abs(dv01_c), years, ch.irs_vol_bp)
    funding = im_total * fs_im + epe * fs
    liq = im_total + (0.0 if cp.csa or cp.kind == "ccp" else 50.0 * abs(dv01_c))
    rwa = sum(v * _cp(k).risk_weight for k, v in ead_by_cp.items())
    return TradeMetrics(
        s.trade_id, pos.product, s.cpty, pos.desc, s.end, years, s.notional, revenue, funding,
        le_onbalance=0.0, le_ccr=sum(ead_by_cp.values()), rwa=rwa, ead_by_cp=ead_by_cp,
        liquidity_usage=liq, unsecured_funding=im_total, non_hqla_financing=0.0, client_gross=0.0,
        inventory_use={}, dv01=dv01, mtm=mtm_c + (pos.hedge.pv(mkt.sofr) if pos.hedge is not None else 0.0),
        conc_class=None, detail={"epe": epe, "ccp_im": im_total, "client_dv01": dv01_c, "sa_ccr": sa_detail},
        **_zero())
