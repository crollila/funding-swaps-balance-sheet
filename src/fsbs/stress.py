"""Stress scenarios, counterparty close-out and standalone stress loss.

Each scenario is decomposed into three P&L components so nothing is double
counted:

* MTM P&L   - instantaneous revaluation for *rate and price* moves
              (IRS, fixed-rate term repo, TRS swap + its stock hedge).
* 1y NII    - change in one year of net income from *spreads, haircuts and
              charges* (economics re-run on the stressed market with the base
              SOFR curve, weighted by min(remaining life, 1y)).
* Credit    - default scenarios: close-out loss of the defaulted counterparty
              (collateral liquidated after a 99% margin-period-of-risk move,
              liquidation cost, unsecured recovery). Market scenarios: stressed
              expected loss on exposure the shock leaves uncollateralised.

SFTs and derivatives with the same counterparty are separate netting sets
(repo master agreement vs. ISDA); there is no cross-product netting.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from . import config
from .economics import TradeMetrics, analyze
from .market import Market
from .positions import Position

Z99 = 2.326


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    sofr_bp: float = 0.0
    funding_bp: float = 0.0
    equity_move: float = 0.0
    collateral_move: float = 0.0  # applied to all non-UST collateral (UST gets 1/5 of it)
    haircut_mult: float = 1.0
    haircut_add: float = 0.0
    liquidity_mult: float = 1.0
    default: bool = False


SCENARIOS = [
    Scenario("sofr_up_100", "SOFR +100bp", sofr_bp=100),
    Scenario("funding_up_100", "Funding spread +100bp", funding_bp=100),
    Scenario("equity_down_20", "Equity -20%", equity_move=-0.20),
    Scenario("collateral_down_15", "Collateral -15%", collateral_move=-0.15),
    Scenario("haircuts_up", "Street haircuts x2 (+1pt)", haircut_mult=2.0, haircut_add=0.01),
    Scenario("cpty_default", "Worst counterparty default", default=True),
    Scenario("liquidity_x2", "Liquidity charge doubles", liquidity_mult=2.0),
    Scenario("combined", "Combined", sofr_bp=100, funding_bp=100, equity_move=-0.20, collateral_move=-0.15,
             haircut_mult=2.0, haircut_add=0.01, liquidity_mult=2.0, default=True),
]
COMBINED = SCENARIOS[-1]


def stressed_market(base: Market, sc: Scenario, rates: bool = True) -> Market:
    """Apply a scenario. ``rates=False`` keeps the base SOFR curve (used for the NII leg)."""
    m = base.with_quotes_bump(sc.sofr_bp, name=sc.key) if (rates and sc.sofr_bp) else replace(base, name=sc.key)
    if sc.funding_bp:
        m = replace(m, funding=m.funding.with_shift(sc.funding_bp))
    factors = dict(base.collateral_mv_factor)
    spots = dict(base.spots)
    if sc.collateral_move:
        for k in config.COLLATERAL:
            factors[k] = factors.get(k, 1.0) * (1.0 + (sc.collateral_move / 5.0 if k == "UST" else sc.collateral_move))
    if sc.equity_move:
        for k in config.UNDERLYINGS:
            spots[k] = spots[k] * (1.0 + sc.equity_move)
        for k in ("EQ_INDEX", "EQ_SINGLE"):
            factors[k] = factors.get(k, 1.0) * (1.0 + sc.equity_move)
    return replace(m, collateral_mv_factor=factors, spots=spots,
                   street_haircut_mult=base.street_haircut_mult * sc.haircut_mult,
                   street_haircut_add=base.street_haircut_add + sc.haircut_add,
                   liquidity_mult=base.liquidity_mult * sc.liquidity_mult)


# --------------------------------------------------------------------------- valuation pieces
def position_mtm(pos: Position, mkt: Market) -> float:
    """Desk value including hedges (TRS stock hedge valued at q * S)."""
    from .economics import repo_market_rate
    inst = pos.inst
    if pos.product in ("reverse_repo", "repo"):
        return inst.mtm(mkt.val, repo_market_rate(mkt, inst.collateral, inst.end))
    if pos.product == "trs":
        s = mkt.spot(inst.underlying)
        return inst.mtm(mkt.sofr, s, mkt.dividends[inst.underlying]) + inst.shares * s
    v = inst.pv(mkt.sofr)
    if pos.hedge is not None:
        v += pos.hedge.pv(mkt.sofr)
    return v


def closeout_claims(pos: Position, cp: str, base: Market, mkt: Market,
                    ch: config.DeskCharges = config.CHARGES) -> tuple[list[float], list[float]]:
    """Desk claims (>0 desk is owed after collateral) on ``cp`` if it defaults under ``mkt``.

    Returns (sft_claims, derivative_claims) so the caller can net within each set.
    """
    inst = pos.inst
    sft, der = [], []
    if pos.product in ("reverse_repo", "repo"):
        if inst.cpty != cp:
            return sft, der
        cls = config.COLLATERAL[inst.collateral]
        loan = inst.loan_balance(mkt.val)
        mv = inst.required_collateral(mkt.val) * mkt.mv_factor(inst.collateral)
        adverse = Z99 * cls.annual_vol * math.sqrt(ch.repo_mpor_days / 252.0)
        if inst.direction == "reverse":
            sft.append(loan - mv * (1 - adverse) * (1 - cls.liquidation_cost))
        else:
            sft.append(mv * (1 + adverse) * (1 + cls.liquidation_cost) - loan)
    elif pos.product == "trs":
        if inst.cpty != cp:
            return sft, der
        u = config.UNDERLYINGS[inst.underlying]
        cls = config.COLLATERAL[u.collateral]
        cpo = config.COUNTERPARTIES[cp]
        s1 = mkt.spot(inst.underlying)
        n1 = inst.shares * s1
        mtm1 = inst.mtm(mkt.sofr, s1, mkt.dividends[inst.underlying])
        vm = inst.mtm(base.sofr, base.spot(inst.underlying), base.dividends[inst.underlying]) if cpo.csa else 0.0
        im = inst.im_pct * inst.shares * u.spot
        adverse = Z99 * u.vol * math.sqrt(ch.bilateral_mpor_days / 252.0) * n1
        der.append(mtm1 - vm - im + adverse + cls.liquidation_cost * n1)
    else:
        for leg in [inst] + ([pos.hedge] if pos.hedge is not None else []):
            if leg.cpty != cp:
                continue
            cpo = config.COUNTERPARTIES[cp]
            mtm1 = leg.pv(mkt.sofr)
            vm = leg.pv(base.sofr) if cpo.csa else 0.0
            mpor = ch.ccp_mpor_days if cpo.kind == "ccp" else ch.bilateral_mpor_days
            adverse = Z99 * ch.irs_vol_bp * math.sqrt(mpor / 252.0) * abs(leg.dv01_analytic(mkt.sofr))
            der.append(mtm1 - vm + adverse)
    return sft, der


def closeout_loss(positions: list[Position], cp: str, base: Market, mkt: Market,
                  ch: config.DeskCharges = config.CHARGES) -> dict:
    sft, der = [], []
    for p in positions:
        a, b = closeout_claims(p, cp, base, mkt, ch)
        sft += a
        der += b
    netting = config.COUNTERPARTIES[cp].netting
    net = (lambda xs: max(sum(xs), 0.0)) if netting else (lambda xs: sum(max(v, 0.0) for v in xs))
    sft_short, der_short = net(sft), net(der)
    loss = (sft_short + der_short) * (1.0 - ch.unsecured_recovery)
    return {"cpty": cp, "sft_shortfall": sft_short, "deriv_shortfall": der_short, "loss": loss}


def uncollateralised_after_shock(positions: list[Position], cp: str, base: Market, mkt: Market) -> float:
    """Exposure left uncovered immediately after a price shock (no MPOR move, no liquidation cost)."""
    sft, der = [], []
    for p in positions:
        inst = p.inst
        if p.product in ("reverse_repo", "repo") and inst.cpty == cp:
            loan = inst.loan_balance(mkt.val)
            mv = inst.required_collateral(mkt.val) * mkt.mv_factor(inst.collateral)
            sft.append(inst.sign * (loan - mv))
        elif p.product == "trs" and inst.cpty == cp:
            u = config.UNDERLYINGS[inst.underlying]
            mtm1 = inst.mtm(mkt.sofr, mkt.spot(inst.underlying), mkt.dividends[inst.underlying])
            vm = inst.mtm(base.sofr, base.spot(inst.underlying), base.dividends[inst.underlying]) \
                if config.COUNTERPARTIES[cp].csa else 0.0
            der.append(mtm1 - vm - inst.im_pct * inst.shares * u.spot)
        elif p.product == "irs":
            for leg in [inst] + ([p.hedge] if p.hedge is not None else []):
                if leg.cpty == cp:
                    vm = leg.pv(base.sofr) if config.COUNTERPARTIES[cp].csa else 0.0
                    der.append(leg.pv(mkt.sofr) - vm)
    return max(sum(sft), 0.0) + max(sum(der), 0.0)


# --------------------------------------------------------------------------- portfolio stress
def run_scenario(positions: list[Position], base: Market, sc: Scenario,
                 base_metrics: list[TradeMetrics] | None = None,
                 ch: config.DeskCharges = config.CHARGES) -> dict:
    mkt = stressed_market(base, sc)
    mkt_nii = stressed_market(base, sc, rates=False)
    base_metrics = base_metrics or [analyze(p, base) for p in positions]
    mtm = sum(position_mtm(p, mkt) - position_mtm(p, base) for p in positions)
    nii = 0.0
    liq_delta = 0.0
    for p, bm in zip(positions, base_metrics):
        sm = analyze(p, mkt_nii, ch)
        nii += (sm.net_income - bm.net_income) * min(bm.tenor_years, 1.0)
        liq_delta += sm.liquidity_usage - bm.liquidity_usage
    # desk-side margin calls on repos where the desk delivered collateral (collateral up = more to post)
    cpties = sorted({p.cpty for p in positions} | {p.hedge.cpty for p in positions if p.hedge is not None})
    credit = 0.0
    defaulted = None
    if sc.default:
        losses = [closeout_loss(positions, cp, base, mkt, ch) for cp in cpties]
        worst = max(losses, key=lambda d: d["loss"])
        credit -= worst["loss"]
        defaulted = worst
    else:
        for cp in cpties:
            c = config.COUNTERPARTIES[cp]
            shock = uncollateralised_after_shock(positions, cp, base, mkt) \
                - uncollateralised_after_shock(positions, cp, base, base)
            credit -= min(c.pd * ch.stress_pd_multiplier, 1.0) * c.lgd * max(shock, 0.0)
    return {"scenario": sc.key, "label": sc.label, "mtm_pnl": mtm, "nii_1y": nii, "credit": credit,
            "total": mtm + nii + credit, "liquidity_usage_change": liq_delta,
            "defaulted": defaulted["cpty"] if defaulted else None,
            "default_detail": defaulted}


def standalone_stress_loss(pos: Position, base: Market, sc: Scenario = COMBINED,
                           ch: config.DeskCharges = config.CHARGES) -> float:
    """Loss of one position alone under ``sc``, including its own counterparty's default."""
    mkt = stressed_market(base, sc)
    mkt_nii = stressed_market(base, sc, rates=False)
    d_mtm = position_mtm(pos, mkt) - position_mtm(pos, base)
    bm, sm = analyze(pos, base, ch), analyze(pos, mkt_nii, ch)
    d_nii = (sm.net_income - bm.net_income) * min(bm.tenor_years, 1.0)
    default = closeout_loss([pos], pos.cpty, base, mkt, ch)["loss"] if sc.default else 0.0
    return max(-(d_mtm + d_nii) + default, 0.0)
