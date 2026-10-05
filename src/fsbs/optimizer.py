"""Balance-sheet allocation as a linear programme.

Decision variables
    x_i in [0, 1]  fill fraction of client request i (0 if its collateral is ineligible)
    u_k >= 0       net on-balance-sheet repo receivable of netting key k = (counterparty, maturity)

Objective (maximise, $/yr)
    sum_i x_i * (revenue - funding - capital - liquidity - expected CP cost - BS charge on non-nettable usage)_i
    - BS charge * sum_k u_k

Constraints (each mirrors one entry of ``portfolio.check_limits``)
    u_k >= E_k + sum_{i in k} signed_loan_i * x_i        (netting: repos offset reverse repos)
    total balance sheet <= L
    per-counterparty balance sheet <= cap
    per-collateral-class gross exposure <= cap_c
    per-counterparty EAD <= limit_cp
    liquidity usage <= HQLA pool - minimum buffer
    non-HQLA financing, term (>1y) balance sheet, weighted-average maturity
    |DV01| <= limit, sum of standalone stress losses <= limit
    client leverage: financed gross / NAV <= max leverage
    repo inventory per collateral class

Solved with HiGHS via ``scipy.optimize.linprog``. Duals give the shadow price
of balance sheet: the marginal net income from one more dollar of capacity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import linprog

from . import config
from .economics import TradeMetrics
from .portfolio import aggregate, check_limits, violations


@dataclass
class Allocation:
    method: str
    x: np.ndarray
    agg: dict
    checks: list
    status: str = "ok"
    shadow: dict | None = None

    @property
    def feasible(self) -> bool:
        return not violations(self.checks)

    @property
    def net_income(self) -> float:
        return self.agg["net_income"]


def planning_limits(lim: config.Limits, reserve: float) -> config.Limits:
    """Limits with ``reserve`` of balance-sheet and liquidity capacity held back."""
    if reserve == 0.0:
        return lim
    cap = (lim.hqla_pool - lim.min_liquidity_buffer) * (1.0 - reserve)
    return replace(lim, balance_sheet=lim.balance_sheet * (1.0 - reserve),
                   hqla_pool=lim.min_liquidity_buffer + cap)


def _nettable(m: TradeMetrics) -> bool:
    return m.sft_key is not None and config.COUNTERPARTIES[m.cpty].netting


def evaluate(existing: list[TradeMetrics], cands: list[TradeMetrics], x, method: str,
             lim: config.Limits = config.LIMITS, ch: config.DeskCharges = config.CHARGES, tol: float = 1e-6,
             **kw) -> Allocation:
    allm = existing + cands
    w = [1.0] * len(existing) + [float(v) for v in x]
    agg = aggregate(allm, w, ch)
    return Allocation(method, np.asarray(x, dtype=float), agg, check_limits(agg, allm, w, lim, tol), **kw)


def optimize(existing: list[TradeMetrics], cands: list[TradeMetrics], lim: config.Limits = config.LIMITS,
             ch: config.DeskCharges = config.CHARGES, reserve: float = 0.0) -> Allocation:
    """Solve the allocation LP. ``reserve`` keeps that share of balance-sheet and liquidity
    capacity unallocated (headroom for flow that arrives through the pre-trade gate)."""
    lim = planning_limits(lim, reserve)
    n = len(cands)
    keys = sorted({m.sft_key for m in cands if _nettable(m)}, key=str)
    kidx = {k: n + j for j, k in enumerate(keys)}
    nv = n + len(keys)
    rows, rhs, names = [], [], []

    def row(name: str, coef: np.ndarray, const: float, limit: float):
        """Add: coef . v + const <= limit."""
        rows.append(coef)
        rhs.append(limit - const)
        names.append(name)

    def vec():
        return np.zeros(nv)

    # existing-book constants -------------------------------------------------------------
    ex_key_sum: dict = {}
    for m in existing:
        if m.sft_key is not None:
            ex_key_sum[m.sft_key] = ex_key_sum.get(m.sft_key, 0.0) + m.sft_signed_loan

    def existing_onbal_by_cp(include_keys_in_lp: bool) -> dict:
        out: dict = {}
        for m in existing:
            if m.sft_key is None:
                out[m.cpty] = out.get(m.cpty, 0.0) + m.le_onbalance
        for k, s in ex_key_sum.items():
            if k in kidx and include_keys_in_lp:
                continue  # represented by u_k
            cp = k[0]
            if config.COUNTERPARTIES[cp].netting:
                v = max(s, 0.0)
            else:
                v = sum(max(m.sft_signed_loan, 0.0) for m in existing if m.sft_key == k)
            out[cp] = out.get(cp, 0.0) + v
        return out

    ex_onbal_cp = existing_onbal_by_cp(True)
    ex_ead_cp: dict = {}
    for m in existing:
        for k, v in m.ead_by_cp.items():
            ex_ead_cp[k] = ex_ead_cp.get(k, 0.0) + v

    # candidate coefficients --------------------------------------------------------------
    def cand_onbal_direct(m: TradeMetrics) -> float:
        """On-balance usage not routed through a netting variable."""
        if m.sft_key is None:
            return m.le_onbalance
        return 0.0 if _nettable(m) else max(m.sft_signed_loan, 0.0)

    # netting rows: E_k + sum s_i x_i - u_k <= 0
    for k in keys:
        a = vec()
        for i, m in enumerate(cands):
            if m.sft_key == k:
                a[i] = m.sft_signed_loan
        a[kidx[k]] = -1.0
        row(f"netting:{k[0]}:{k[1]}", a, ex_key_sum.get(k, 0.0), 0.0)

    # total balance sheet
    a = vec()
    for i, m in enumerate(cands):
        a[i] = cand_onbal_direct(m) + m.le_ccr
    for k in keys:
        a[kidx[k]] = 1.0
    bs_const = sum(ex_onbal_cp.values()) + sum(m.le_ccr for m in existing)
    row("balance_sheet", a, bs_const, lim.balance_sheet)

    # per-counterparty balance sheet
    cps = sorted(config.COUNTERPARTIES)
    for cp in cps:
        a = vec()
        for i, m in enumerate(cands):
            if m.cpty == cp:
                a[i] += cand_onbal_direct(m)
            a[i] += m.ead_by_cp.get(cp, 0.0)
        for k in keys:
            if k[0] == cp:
                a[kidx[k]] = 1.0
        row(f"cpty_balance_sheet:{cp}", a, ex_onbal_cp.get(cp, 0.0) + ex_ead_cp.get(cp, 0.0), lim.single_cpty_bs)

    # collateral-class concentration (gross on-balance exposure)
    for cls, cap in lim.collateral_class_cap.items():
        a = vec()
        for i, m in enumerate(cands):
            if m.conc_class == cls and m.le_onbalance > 0:
                a[i] = m.le_onbalance
        const = sum(m.le_onbalance for m in existing if m.conc_class == cls and m.le_onbalance > 0)
        row(f"concentration:{cls}", a, const, cap)

    # counterparty EAD limits
    for cp in cps:
        a = vec()
        for i, m in enumerate(cands):
            a[i] = m.ead_by_cp.get(cp, 0.0)
        row(f"cpty_ead:{cp}", a, ex_ead_cp.get(cp, 0.0), config.COUNTERPARTIES[cp].ead_limit)

    def simple(name: str, f, limit: float):
        a = vec()
        for i, m in enumerate(cands):
            a[i] = f(m)
        row(name, a, sum(f(m) for m in existing), limit)

    simple("liquidity_buffer", lambda m: m.liquidity_usage, lim.hqla_pool - lim.min_liquidity_buffer)
    simple("non_hqla_financing", lambda m: m.non_hqla_financing, lim.non_hqla_financing)
    simple("term_balance_sheet", lambda m: m.le_total if m.tenor_years > 1.0 else 0.0, lim.term_balance_sheet)
    simple("wam_years", lambda m: m.le_total * (m.tenor_years - lim.max_wam_years), 0.0)
    simple("dv01_upper", lambda m: m.dv01, lim.dv01_limit)
    simple("dv01_lower", lambda m: -m.dv01, lim.dv01_limit)
    simple("stress_loss", lambda m: m.stress_loss, lim.stress_loss_limit)
    for cp, c in config.COUNTERPARTIES.items():
        if c.max_leverage is not None:
            simple(f"client_leverage:{cp}", lambda m, cp=cp: m.client_gross if m.cpty == cp else 0.0,
                   c.max_leverage * c.nav)
    for cls, cap in lim.repo_inventory.items():
        simple(f"inventory:{cls}", lambda m, cls=cls: m.inventory_use.get(cls, 0.0), cap)

    # objective ---------------------------------------------------------------------------
    c = np.zeros(nv)
    for i, m in enumerate(cands):
        addback = ch.balance_sheet_charge * m.le_onbalance if _nettable(m) else 0.0
        c[i] = -(m.net_income + addback)
    for k in keys:
        c[kidx[k]] = ch.balance_sheet_charge

    bounds = [(0.0, 1.0 if m.haircut_ok else 0.0) for m in cands] + [(0.0, None)] * len(keys)
    res = linprog(c, A_ub=np.array(rows), b_ub=np.array(rhs), bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"LP failed: {res.message}")
    x = np.clip(res.x[:n], 0.0, 1.0)
    duals = -np.asarray(res.ineqlin.marginals)  # d(max net income)/d(limit)
    shadow = {nm: float(d) for nm, d in zip(names, duals) if abs(d) > 1e-12}
    alloc = evaluate(existing, cands, x, "optimizer", lim, ch, shadow=shadow, status=res.message)
    return alloc


def _max_fraction(existing, cands, x, i, lim, ch, iters: int = 30) -> float:
    def ok(t):
        y = x.copy()
        y[i] = t
        return evaluate(existing, cands, y, "probe", lim, ch, tol=0.0).feasible
    if ok(1.0):
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo if ok(lo) else 0.0


def greedy(existing: list[TradeMetrics], cands: list[TradeMetrics], rank: str = "revenue",
           lim: config.Limits = config.LIMITS, ch: config.DeskCharges = config.CHARGES,
           reserve: float = 0.0) -> Allocation:
    """Baselines that respect every limit but allocate greedily.

    rank="revenue": the naive desk - fill the biggest-revenue requests first, whatever they earn net.
    rank="robs":    a smarter heuristic - fill by standalone return on balance sheet, skip net losers.
    """
    if rank == "revenue":
        order = sorted(range(len(cands)), key=lambda i: -cands[i].revenue)
    elif rank == "robs":
        order = [i for i in sorted(range(len(cands)), key=lambda i: -cands[i].robs) if cands[i].net_income > 0]
    else:
        raise ValueError(rank)
    lim = planning_limits(lim, reserve)
    x = np.zeros(len(cands))
    for i in order:
        if not cands[i].haircut_ok:
            continue
        x[i] = _max_fraction(existing, cands, x, i, lim, ch)
    return evaluate(existing, cands, x, f"greedy_{rank}", lim, ch)


def frontier(existing: list[TradeMetrics], cands: list[TradeMetrics], bs_limits: list[float],
             lim: config.Limits = config.LIMITS, ch: config.DeskCharges = config.CHARGES) -> list[dict]:
    out = []
    for L in bs_limits:
        try:
            a = optimize(existing, cands, replace(lim, balance_sheet=L), ch)
        except RuntimeError:
            continue
        out.append({"bs_limit": L, "balance_sheet": a.agg["balance_sheet"], "net_income": a.net_income,
                    "robs_bp": a.agg["robs"] * 1e4,
                    "shadow_bs_bp": a.shadow.get("balance_sheet", 0.0) * 1e4 if a.shadow else 0.0,
                    "revenue": a.agg["revenue"]})
    return out
