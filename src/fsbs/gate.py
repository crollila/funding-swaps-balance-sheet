"""Pre-trade gate: accept or reject one proposed trade against the current book.

Every proposal is evaluated independently against the same post-optimisation
book. The gate reports the trade's standalone economics, its *incremental*
(netting-aware) effect on desk totals, and every check it fails. Two kinds of
check:

* hard limits - the full ``portfolio.check_limits`` set evaluated on the book
  *with* the trade. The book is feasible before, so any breach is caused by it.
* economics  - incremental net income >= floor, incremental return on balance
  sheet >= hurdle, return on RWA >= hurdle, and the trade's own combined-stress
  loss must not exceed a share of the desk stress limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .economics import TradeMetrics, analyze
from .market import Market
from .portfolio import aggregate, check_limits
from .positions import Position
from .stress import standalone_stress_loss


@dataclass(frozen=True)
class Failure:
    check: str
    value: float
    limit: float
    detail: str = ""


@dataclass
class GateDecision:
    trade_id: str
    product: str
    cpty: str
    desc: str
    accepted: bool
    failures: list[Failure]
    metrics: TradeMetrics
    incremental: dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        if self.accepted:
            return "all checks passed"
        return "; ".join(f.detail or f.check for f in self.failures)


def evaluate_trade(pos: Position, mkt: Market, book: list[TradeMetrics], book_x: list[float],
                   lim: config.Limits = config.LIMITS, thr: config.GateThresholds = config.GATE,
                   ch: config.DeskCharges = config.CHARGES) -> GateDecision:
    m = analyze(pos, mkt, ch)
    m.stress_loss = standalone_stress_loss(pos, mkt, ch=ch)
    before = aggregate(book, book_x, ch)
    after_m, after_x = book + [m], list(book_x) + [1.0]
    after = aggregate(after_m, after_x, ch)
    inc = {
        "net_income": after["net_income"] - before["net_income"],
        "balance_sheet": after["balance_sheet"] - before["balance_sheet"],
        "rwa": after["rwa"] - before["rwa"],
        "stress_loss": m.stress_loss,
        "ead_cpty": after["by_cp_ead"].get(m.cpty, 0.0) - before["by_cp_ead"].get(m.cpty, 0.0),
        "cpty_ead_after": after["by_cp_ead"].get(m.cpty, 0.0),
        "cpty_ead_limit": config.COUNTERPARTIES[m.cpty].ead_limit,
        "dv01": m.dv01,
        "liquidity_usage": m.liquidity_usage,
    }
    inc["robs"] = inc["net_income"] / inc["balance_sheet"] if inc["balance_sheet"] > 0 else float("inf")
    inc["rorwa"] = inc["net_income"] / inc["rwa"] if inc["rwa"] > 0 else float("inf")

    failures: list[Failure] = []
    before_bad = {c.name for c in check_limits(before, book, book_x, lim) if not c.ok}
    for c in check_limits(after, after_m, after_x, lim):
        if not c.ok and c.name not in before_bad:
            if c.name == "collateral_eligibility":
                failures.append(Failure(c.name, 1.0, 0.0, m.haircut_note))
            else:
                shown = f"|{_fmt(c.name, c.value)}|" if c.kind == "abs" else _fmt(c.name, c.value)
                failures.append(Failure(c.name, c.value, c.limit,
                                        f"{c.name}: {shown} > limit {_fmt(c.name, c.limit)}"))
    if inc["net_income"] < thr.min_net_income:
        ni = inc["net_income"]
        failures.append(Failure("net_income", ni, thr.min_net_income,
                                f"net income {'-' if ni < 0 else ''}${abs(ni) / 1e6:,.2f}mm/yr < floor"))
    if inc["robs"] < thr.min_robs_bp * 1e-4:
        failures.append(Failure("robs", inc["robs"], thr.min_robs_bp * 1e-4,
                                f"return on balance sheet {inc['robs'] * 1e4:.1f}bp < {thr.min_robs_bp:.0f}bp hurdle"))
    if inc["rorwa"] < thr.min_rorwa:
        failures.append(Failure("rorwa", inc["rorwa"], thr.min_rorwa,
                                f"return on RWA {inc['rorwa']:.2%} < {thr.min_rorwa:.2%} hurdle"))
    stress_cap = thr.max_single_stress_share * lim.stress_loss_limit
    if m.stress_loss > stress_cap:
        failures.append(Failure("single_trade_stress", m.stress_loss, stress_cap,
                                f"combined-stress loss ${m.stress_loss / 1e6:,.0f}mm > ${stress_cap / 1e6:,.0f}mm cap"))
    return GateDecision(m.trade_id, m.product, m.cpty, m.desc, not failures, failures, m, inc)


def _fmt(name: str, v: float) -> str:
    if name.startswith("wam"):
        return f"{v:.2f}y"
    sign = "-" if v < 0 else ""
    if name.startswith("dv01"):
        return f"{sign}${abs(v) / 1e3:,.0f}k/bp"
    return f"{sign}${abs(v) / 1e6:,.0f}mm"


def run_gate(proposals: list[Position], mkt: Market, book: list[TradeMetrics], book_x: list[float],
             lim: config.Limits = config.LIMITS, thr: config.GateThresholds = config.GATE) -> list[GateDecision]:
    return [evaluate_trade(p, mkt, book, book_x, lim, thr) for p in proposals]
