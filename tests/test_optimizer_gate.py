from dataclasses import replace

import numpy as np
import pytest

from fsbs import config
from fsbs.book import proposed_trades
from fsbs.gate import run_gate
from fsbs.optimizer import evaluate, frontier, optimize, planning_limits
from fsbs.portfolio import aggregate, check_limits


def test_existing_book_is_feasible(book):
    allm = book["existing"]
    assert all(c.ok for c in check_limits(aggregate(allm), allm, None, config.LIMITS))


def test_optimizer_solution_is_feasible_under_independent_checks(book, allocations):
    opt = allocations["optimizer"]
    assert np.all(opt.x >= -1e-9) and np.all(opt.x <= 1 + 1e-9)
    plan = planning_limits(config.LIMITS, config.LIMITS.flow_reserve)
    re = evaluate(book["existing"], book["cands"], opt.x, "check", plan, tol=1e-7)
    bad = [c for c in re.checks if not c.ok]
    assert not bad, bad
    # ineligible collateral never gets balance sheet
    for m, x in zip(book["cands"], opt.x):
        if not m.haircut_ok:
            assert x == 0.0


def test_optimizer_beats_baselines(allocations):
    o, n, g = (allocations[k] for k in ("optimizer", "naive", "greedy_robs"))
    assert n.feasible and g.feasible
    assert o.net_income >= g.net_income - 1.0
    assert o.net_income >= n.net_income - 1.0


def test_optimizer_is_optimal_against_random_feasible_allocations(book, allocations):
    """No random feasible fill should beat the LP (sanity check of optimality)."""
    rng = np.random.default_rng(1)
    plan = planning_limits(config.LIMITS, config.LIMITS.flow_reserve)
    best = allocations["optimizer"].net_income
    tried = 0
    for _ in range(300):
        x = rng.random(len(book["cands"])) * (rng.random(len(book["cands"])) < 0.25)
        a = evaluate(book["existing"], book["cands"], x, "rand", plan, tol=0.0)
        if a.feasible:
            tried += 1
            assert a.net_income <= best + 1.0
    assert tried > 10


def test_lp_objective_equals_independent_net_income(book, allocations):
    """The LP's netting variables and charge add-backs reproduce aggregate() exactly."""
    opt = allocations["optimizer"]
    agg = aggregate(book["existing"] + book["cands"], [1.0] * len(book["existing"]) + list(opt.x))
    assert opt.net_income == pytest.approx(agg["net_income"], rel=1e-12)


def test_frontier_is_monotone(book):
    lim = planning_limits(config.LIMITS, config.LIMITS.flow_reserve)
    f = frontier(book["existing"], book["cands"], [17.6e9, 18.5e9, 19.5e9, 21e9], lim)
    ni = [p["net_income"] for p in f]
    assert all(b >= a - 1.0 for a, b in zip(ni, ni[1:]))
    sh = [p["shadow_bs_bp"] for p in f]
    assert all(b <= a + 1e-9 for a, b in zip(sh, sh[1:]))  # diminishing marginal value of balance sheet


def test_tighter_limit_cannot_improve(book):
    lim = config.LIMITS
    loose = optimize(book["existing"], book["cands"], lim)
    tight = optimize(book["existing"], book["cands"], replace(lim, balance_sheet=18.5e9))
    assert tight.net_income <= loose.net_income + 1.0


@pytest.fixture(scope="module")
def gate_decisions(mkt, book, allocations):
    opt = allocations["optimizer"]
    bm = book["existing"] + book["cands"]
    bx = [1.0] * len(book["existing"]) + list(opt.x)
    return bm, bx, run_gate(proposed_trades(mkt), mkt, bm, bx)


def test_gate_has_both_outcomes(gate_decisions):
    _, _, ds = gate_decisions
    assert any(d.accepted for d in ds) and any(not d.accepted for d in ds)


def test_rejected_trades_actually_violate_reported_constraints(gate_decisions):
    """Recompute every reported failure independently from the post-trade book."""
    bm, bx, ds = gate_decisions
    lim, thr = config.LIMITS, config.GATE
    for d in ds:
        after = aggregate(bm + [d.metrics], bx + [1.0])
        before = aggregate(bm, bx)
        checks = {c.name: c for c in check_limits(after, bm + [d.metrics], bx + [1.0], lim)}
        for f in d.failures:
            if f.check in checks:
                c = checks[f.check]
                v = abs(c.value) if c.kind == "abs" else c.value
                assert not c.ok and v > c.limit, (d.trade_id, f.check)
            elif f.check == "net_income":
                assert after["net_income"] - before["net_income"] < thr.min_net_income
            elif f.check == "robs":
                inc_bs = after["balance_sheet"] - before["balance_sheet"]
                assert inc_bs > 0 and (after["net_income"] - before["net_income"]) / inc_bs < thr.min_robs_bp * 1e-4
            elif f.check == "rorwa":
                inc_rwa = after["rwa"] - before["rwa"]
                assert inc_rwa > 0 and (after["net_income"] - before["net_income"]) / inc_rwa < thr.min_rorwa
            elif f.check == "single_trade_stress":
                assert d.metrics.stress_loss > thr.max_single_stress_share * lim.stress_loss_limit
            else:
                raise AssertionError(f"unknown check {f.check}")
        assert d.accepted == (not d.failures)


def test_accepted_trades_pass_every_limit(gate_decisions):
    bm, bx, ds = gate_decisions
    for d in ds:
        if d.accepted:
            after_m, after_x = bm + [d.metrics], bx + [1.0]
            assert all(c.ok for c in check_limits(aggregate(after_m, after_x), after_m, after_x))
            assert d.incremental["net_income"] >= 0


def test_ineligible_collateral_is_rejected(gate_decisions):
    _, _, ds = gate_decisions
    p02 = next(d for d in ds if d.trade_id == "P02")
    assert not p02.accepted and "collateral_eligibility" in {f.check for f in p02.failures}
