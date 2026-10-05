"""Counterparty exposure: current exposure with netting, SFT exposure, SA-CCR-style EAD.

Current exposure
    Every trade i with a counterparty contributes a value-to-desk V_i and the
    collateral attributed to it C_i (>0 held by desk, <0 posted by desk).
    gross (pre-netting, pre-collateral)      = sum_i max(V_i, 0)
    net (post-netting, pre-collateral)       = max(sum_i V_i, 0)          if netting enforceable
    gross after collateral (trade by trade)  = sum_i max(V_i - C_i, 0)
    net after collateral                     = max(sum_i (V_i - C_i), 0)  if netting enforceable
    Without an enforceable netting agreement "net" falls back to the trade-
    by-trade figure. max(sum, 0) <= sum(max(., 0)) guarantees netting can
    never *increase* exposure; tests check this on random portfolios.

SFT exposure (comprehensive-approach style, simplified)
    reverse repo: E*_i = loan - MV * (1 - H)      repo: E*_i = MV * (1 + H) - loan
    netting set:  E* = max(0, sum_i E*_i)

Derivative EAD (SA-CCR, simplified to one currency and IR + equity classes)
    EAD = alpha * (RC + multiplier * AddOn)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SF_IR = 0.005
SF_EQ_SINGLE, SF_EQ_INDEX = 0.32, 0.20
RHO_EQ_SINGLE, RHO_EQ_INDEX = 0.50, 0.80


# --------------------------------------------------------------------------- current exposure
def exposure_summary(values: list[float], collateral: list[float], netting: bool) -> dict:
    if len(values) != len(collateral):
        raise ValueError("values/collateral length mismatch")
    gross = sum(max(v, 0.0) for v in values)
    gross_after = sum(max(v - c, 0.0) for v, c in zip(values, collateral))
    if netting:
        net = max(sum(values), 0.0)
        net_after = max(sum(values) - sum(collateral), 0.0)
    else:
        net, net_after = gross, gross_after
    return {"gross": gross, "net": net, "gross_after_collateral": gross_after, "net_after_collateral": net_after}


# --------------------------------------------------------------------------- SFT
def sft_contribution(direction: str, loan: float, mv: float, supervisory_haircut: float) -> float:
    if direction == "reverse":
        return loan - mv * (1.0 - supervisory_haircut)
    if direction == "repo":
        return mv * (1.0 + supervisory_haircut) - loan
    raise ValueError(direction)


def sft_exposure(contributions: list[float], netting: bool = True) -> float:
    if netting:
        return max(sum(contributions), 0.0)
    return sum(max(c, 0.0) for c in contributions)


# --------------------------------------------------------------------------- SA-CCR
@dataclass(frozen=True)
class DerivTrade:
    trade_id: str
    asset_class: str  # "IR" | "EQ"
    delta: float  # +1 long the risk factor, -1 short
    notional: float  # positive: swap notional (IR) or equity notional (EQ)
    start_years: float  # years to start (0 if started)
    end_years: float  # years to maturity
    mtm: float  # value to desk
    entity: str = ""  # equity reference entity
    is_index: bool = False


def supervisory_duration(s: float, e: float) -> float:
    s = max(s, 0.0)
    e = max(e, 10.0 / 250.0)
    return (math.exp(-0.05 * s) - math.exp(-0.05 * e)) / 0.05


def maturity_factor(margined: bool, mpor_days: int, maturity_years: float) -> float:
    if margined:
        return 1.5 * math.sqrt(mpor_days / 250.0)
    return math.sqrt(min(max(maturity_years, 10.0 / 250.0), 1.0))


def addon_ir(trades: list[DerivTrade], margined: bool, mpor_days: int) -> float:
    d = [0.0, 0.0, 0.0]
    for t in trades:
        if t.asset_class != "IR":
            continue
        eff = t.delta * t.notional * supervisory_duration(t.start_years, t.end_years) * \
            maturity_factor(margined, mpor_days, t.end_years)
        b = 0 if t.end_years < 1.0 else (1 if t.end_years <= 5.0 else 2)
        d[b] += eff
    d1, d2, d3 = d
    en2 = d1 * d1 + d2 * d2 + d3 * d3 + 1.4 * d1 * d2 + 1.4 * d2 * d3 + 0.6 * d1 * d3
    return SF_IR * math.sqrt(max(en2, 0.0))


def addon_eq(trades: list[DerivTrade], margined: bool, mpor_days: int) -> float:
    by_entity: dict[str, list[DerivTrade]] = {}
    for t in trades:
        if t.asset_class == "EQ":
            by_entity.setdefault(t.entity, []).append(t)
    systematic, idio = 0.0, 0.0
    for ts in by_entity.values():
        is_index = ts[0].is_index
        sf = SF_EQ_INDEX if is_index else SF_EQ_SINGLE
        rho = RHO_EQ_INDEX if is_index else RHO_EQ_SINGLE
        a = sf * sum(t.delta * t.notional * maturity_factor(margined, mpor_days, t.end_years) for t in ts)
        systematic += rho * a
        idio += (1.0 - rho * rho) * a * a
    return math.sqrt(systematic * systematic + idio)


def sa_ccr(trades: list[DerivTrade], margined: bool, mpor_days: int, vm_held: float = 0.0,
           im_held: float = 0.0, mta: float = 0.0, threshold: float = 0.0, alpha: float = 1.4) -> dict:
    """EAD for one netting set. ``vm_held``/``im_held`` are net collateral held (posted = negative)."""
    v = sum(t.mtm for t in trades)
    c = vm_held + im_held
    if margined:
        rc = max(v - c, threshold + mta - im_held, 0.0)
    else:
        rc = max(v - c, 0.0)
    addon = addon_ir(trades, margined, mpor_days) + addon_eq(trades, margined, mpor_days)
    if addon > 0:
        mult = min(1.0, 0.05 + 0.95 * math.exp(min((v - c) / (2.0 * 0.95 * addon), 0.0)))
    else:
        mult = 1.0
    pfe = mult * addon
    return {"V": v, "C": c, "rc": rc, "addon": addon, "multiplier": mult, "pfe": pfe, "ead": alpha * (rc + pfe)}
