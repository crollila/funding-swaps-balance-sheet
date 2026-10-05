"""Desk-level aggregation and limit checks.

``aggregate`` turns per-trade metrics and fill fractions into desk totals. It
applies on-balance-sheet repo netting (same counterparty, same maturity date,
enforceable netting: reverse-repo receivables offset repo payables, in the
spirit of ASC 210-20-45-11 / FIN 41) and recomputes the balance-sheet charge on
the netted figure. Counterparty-limit usage is measured conservatively as the
sum of standalone EADs; netting-set EADs are reported separately by
``book_exposures``.

``check_limits`` is the single source of truth for every hard limit. The
optimizer's LP is built separately and verified against this function in tests.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from . import config
from .economics import TradeMetrics


@dataclass(frozen=True)
class LimitCheck:
    name: str
    value: float
    limit: float
    ok: bool
    kind: str = "max"  # "max": value <= limit ; "abs": |value| <= limit

    def utilisation(self) -> float:
        return abs(self.value) / self.limit if self.limit else float("inf")


def aggregate(metrics: list[TradeMetrics], x: list[float] | None = None,
              ch: config.DeskCharges = config.CHARGES) -> dict:
    x = [1.0] * len(metrics) if x is None else list(x)
    if len(x) != len(metrics):
        raise ValueError("x / metrics length mismatch")
    tot = defaultdict(float)
    by_cp_onbal = defaultdict(float)
    by_cp_ead = defaultdict(float)
    by_cp_gross_client = defaultdict(float)
    by_class = defaultdict(float)
    by_product_le = defaultdict(float)
    inventory = defaultdict(float)
    sft_keys = defaultdict(float)
    sft_gross_onbal = 0.0
    wam_num = 0.0
    for m, w in zip(metrics, x):
        if w == 0.0:
            continue
        for f in ("revenue", "funding_cost", "capital_charge", "liquidity_charge", "expected_cp_cost",
                  "le_ccr", "rwa", "liquidity_usage", "unsecured_funding", "non_hqla_financing", "dv01",
                  "stress_loss", "mtm"):
            tot[f] += getattr(m, f) * w
        tot["gross_le"] += m.le_total * w
        tot["bs_charge_gross"] += m.bs_charge * w
        if m.tenor_years > 1.0:
            tot["term_le"] += m.le_total * w
        wam_num += m.le_total * m.tenor_years * w
        for k, v in m.ead_by_cp.items():
            by_cp_ead[k] += v * w
        by_cp_gross_client[m.cpty] += m.client_gross * w
        for k, v in m.inventory_use.items():
            inventory[k] += v * w
        if m.conc_class is not None and m.le_onbalance > 0:
            by_class[m.conc_class] += m.le_onbalance * w
        by_product_le[m.product] += m.le_total * w
        if m.sft_key is not None:
            sft_keys[m.sft_key] += m.sft_signed_loan * w
            sft_gross_onbal += max(m.sft_signed_loan, 0.0) * w
        else:
            by_cp_onbal[m.cpty] += m.le_onbalance * w
            tot["onbal_non_sft"] += m.le_onbalance * w
    sft_net_onbal = 0.0
    for (cp, _end), s in sft_keys.items():
        if config.COUNTERPARTIES[cp].netting:
            v = max(s, 0.0)
        else:  # no offset without enforceable netting: only receivables count
            v = sum(max(m.sft_signed_loan, 0.0) * w for m, w in zip(metrics, x) if m.sft_key == (cp, _end))
        sft_net_onbal += v
        by_cp_onbal[cp] += v
    balance_sheet = tot["onbal_non_sft"] + sft_net_onbal + tot["le_ccr"]
    bs_charge = ch.balance_sheet_charge * balance_sheet
    net_income = (tot["revenue"] - tot["funding_cost"] - bs_charge - tot["capital_charge"]
                  - tot["liquidity_charge"] - tot["expected_cp_cost"])
    by_cp_bs = {k: by_cp_onbal.get(k, 0.0) + by_cp_ead.get(k, 0.0)
                for k in set(by_cp_onbal) | set(by_cp_ead)}
    return {
        **dict(tot),
        "balance_sheet": balance_sheet,
        "sft_gross_onbalance": sft_gross_onbal,
        "sft_net_onbalance": sft_net_onbal,
        "netting_benefit": sft_gross_onbal - sft_net_onbal,
        "bs_charge": bs_charge,
        "net_income": net_income,
        "robs": net_income / balance_sheet if balance_sheet > 0 else 0.0,
        "rorwa": net_income / tot["rwa"] if tot["rwa"] > 0 else 0.0,
        "wam_years": wam_num / tot["gross_le"] if tot["gross_le"] > 0 else 0.0,
        "by_cp_ead": dict(by_cp_ead),
        "by_cp_bs": by_cp_bs,
        "by_cp_client_gross": dict(by_cp_gross_client),
        "by_class": dict(by_class),
        "by_product_le": dict(by_product_le),
        "inventory": dict(inventory),
    }


def check_limits(agg: dict, metrics: list[TradeMetrics] | None = None, x: list[float] | None = None,
                 lim: config.Limits = config.LIMITS, tol: float = 1e-6) -> list[LimitCheck]:
    """All hard limits. ``tol`` is a relative tolerance for LP round-off (0 = strict)."""
    out: list[LimitCheck] = []

    def add(name, value, limit, kind="max"):
        v = abs(value) if kind == "abs" else value
        out.append(LimitCheck(name, value, limit, v <= limit * (1 + tol) + (tol and 1e-6), kind))

    add("balance_sheet", agg["balance_sheet"], lim.balance_sheet)
    for cp, v in sorted(agg["by_cp_bs"].items()):
        add(f"cpty_balance_sheet:{cp}", v, lim.single_cpty_bs)
    for cls, cap in lim.collateral_class_cap.items():
        add(f"concentration:{cls}", agg["by_class"].get(cls, 0.0), cap)
    for cp, c in config.COUNTERPARTIES.items():
        add(f"cpty_ead:{cp}", agg["by_cp_ead"].get(cp, 0.0), c.ead_limit)
    add("liquidity_buffer", agg.get("liquidity_usage", 0.0), lim.hqla_pool - lim.min_liquidity_buffer)
    add("non_hqla_financing", agg.get("non_hqla_financing", 0.0), lim.non_hqla_financing)
    add("term_balance_sheet", agg.get("term_le", 0.0), lim.term_balance_sheet)
    add("wam_years", agg["wam_years"], lim.max_wam_years)
    add("dv01", agg.get("dv01", 0.0), lim.dv01_limit, "abs")
    add("stress_loss", agg.get("stress_loss", 0.0), lim.stress_loss_limit)
    for cp, c in config.COUNTERPARTIES.items():
        if c.max_leverage is not None:
            add(f"client_leverage:{cp}", agg["by_cp_client_gross"].get(cp, 0.0), c.max_leverage * c.nav)
    for cls, cap in lim.repo_inventory.items():
        add(f"inventory:{cls}", agg["inventory"].get(cls, 0.0), cap)
    if metrics is not None:
        xs = [1.0] * len(metrics) if x is None else x
        bad = [m.trade_id for m, w in zip(metrics, xs) if w > 0 and not m.haircut_ok]
        out.append(LimitCheck("collateral_eligibility", float(len(bad)), 0.0, not bad))
    return out


def violations(checks: list[LimitCheck]) -> list[LimitCheck]:
    return [c for c in checks if not c.ok]


def book_exposures(positions, mkt, ch: config.DeskCharges = config.CHARGES) -> dict:
    """Counterparty exposure on the actual netting sets of a book of positions.

    Per counterparty there are two netting sets (repo master agreement for SFTs,
    ISDA/CSA or CCP rulebook for derivatives). Returns current-exposure figures
    (gross / net, before / after collateral) and netting-set EAD (SFT E* plus
    SA-CCR), which is what a credit officer would see.
    """
    from .economics import irs_deriv_trade
    from .exposure import DerivTrade, exposure_summary, sa_ccr, sft_contribution, sft_exposure

    sets: dict = defaultdict(lambda: {"sft_v": [], "sft_c": [], "sft_e": [], "der": [], "der_v": [], "der_c": [],
                                      "vm": 0.0, "im": 0.0})
    val = mkt.val
    for p in positions:
        inst = p.inst
        if p.product in ("reverse_repo", "repo"):
            s = sets[inst.cpty]
            loan = inst.loan_balance(val)
            mv = inst.required_collateral(val) * mkt.mv_factor(inst.collateral)
            s["sft_v"].append(inst.sign * loan)
            s["sft_c"].append(inst.sign * mv)
            s["sft_e"].append(sft_contribution(inst.direction, loan, mv,
                                               config.COLLATERAL[inst.collateral].supervisory_haircut))
        elif p.product == "trs":
            s = sets[inst.cpty]
            u = config.UNDERLYINGS[inst.underlying]
            spot = mkt.spot(inst.underlying)
            mtm = inst.mtm(mkt.sofr, spot, mkt.dividends[inst.underlying])
            im = inst.im_pct * inst.shares * u.spot
            csa = config.COUNTERPARTIES[inst.cpty].csa
            s["der"].append(DerivTrade(inst.trade_id, "EQ", -1.0, inst.shares * spot, 0.0,
                                       inst.years_to_maturity(val), mtm, inst.underlying, u.is_index))
            s["der_v"].append(mtm)
            s["der_c"].append((mtm if csa else 0.0) + im)
            s["vm"] += mtm if csa else 0.0
            s["im"] += im
        else:
            for leg in [inst] + ([p.hedge] if p.hedge is not None else []):
                s = sets[leg.cpty]
                dt = irs_deriv_trade(leg, mkt)
                csa = config.COUNTERPARTIES[leg.cpty].csa
                s["der"].append(dt)
                s["der_v"].append(dt.mtm)
                s["der_c"].append(dt.mtm if csa else 0.0)
                s["vm"] += dt.mtm if csa else 0.0
    out = {}
    for cp, s in sorted(sets.items()):
        c = config.COUNTERPARTIES[cp]
        sft = exposure_summary(s["sft_v"], s["sft_c"], c.netting)
        der = exposure_summary(s["der_v"], s["der_c"], c.netting)
        ead_sft = sft_exposure(s["sft_e"], c.netting)
        if s["der"]:
            mpor = ch.ccp_mpor_days if c.kind == "ccp" else ch.bilateral_mpor_days
            sa = sa_ccr(s["der"], margined=c.csa, mpor_days=mpor, vm_held=s["vm"], im_held=s["im"],
                        alpha=ch.sacc_alpha)
            ead_der = sa["ead"]
        else:
            ead_der = 0.0
        out[cp] = {k: sft[k] + der[k] for k in sft} | {
            "ead_sft": ead_sft, "ead_derivatives": ead_der, "ead": ead_sft + ead_der, "ead_limit": c.ead_limit}
    return out
