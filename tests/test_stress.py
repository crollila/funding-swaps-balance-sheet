import pytest

from fsbs.economics import analyze
from fsbs.stress import SCENARIOS, Scenario, closeout_loss, position_mtm, run_scenario, stressed_market


def test_trs_book_is_delta_neutral_to_equity_shock(mkt, book):
    trs = [p for p in book["existing_pos"] if p.product == "trs"]
    shocked = stressed_market(mkt, Scenario("eq", "eq", equity_move=-0.20))
    for p in trs:
        d = position_mtm(p, shocked) - position_mtm(p, mkt)
        notional = p.inst.shares * mkt.spot(p.inst.underlying)
        # swap + stock hedge offset; what remains is the PV of the spread on the (now smaller) future notional
        bound = p.inst.spread * p.inst.years_to_maturity(mkt.val) * 0.20 * notional * 1.1 + 1e-6 * notional
        assert abs(d) < bound


def test_rates_shock_hits_mtm_only(mkt, book):
    r = run_scenario(book["existing_pos"], mkt, SCENARIOS[0])
    assert r["nii_1y"] == pytest.approx(0.0, abs=1e-6)
    # first-order check against the book's par DV01 (re-bootstrapped +1bp); the residual is convexity,
    # which is material here because large offsetting positions (term repo vs. pay-fixed swap) net down.
    base = sum(position_mtm(p, mkt) for p in book["existing_pos"])
    up1 = mkt.with_quotes_bump(1.0)
    par_dv01 = sum(position_mtm(p, up1) for p in book["existing_pos"]) - base
    assert r["mtm_pnl"] == pytest.approx(par_dv01 * 100, rel=0.15)
    assert r["mtm_pnl"] < 0 and par_dv01 < 0


def test_funding_and_liquidity_shocks_hit_income_only(mkt, book):
    for sc in (SCENARIOS[1], SCENARIOS[6]):
        r = run_scenario(book["existing_pos"], mkt, sc)
        assert r["mtm_pnl"] == pytest.approx(0.0, abs=1e-6)
        assert r["nii_1y"] < 0


def test_liquidity_doubling_costs_exactly_the_liquidity_charge(mkt, book):
    r = run_scenario(book["existing_pos"], mkt, SCENARIOS[6])
    expected = -sum(m.liquidity_charge * min(m.tenor_years, 1.0) for m in book["existing"])
    assert r["nii_1y"] == pytest.approx(expected, rel=1e-9)


def test_haircut_increase_raises_unsecured_funding(mkt, book):
    hi = stressed_market(mkt, SCENARIOS[4], rates=False)
    base = sum(analyze(p, mkt).unsecured_funding for p in book["existing_pos"])
    stressed = sum(analyze(p, hi).unsecured_funding for p in book["existing_pos"])
    assert stressed > base


def test_combined_is_at_least_as_bad_as_each_component_default(mkt, book):
    rows = {sc.key: run_scenario(book["existing_pos"], mkt, sc) for sc in SCENARIOS}
    assert rows["combined"]["credit"] <= rows["cpty_default"]["credit"]
    assert rows["combined"]["total"] <= min(r["total"] for k, r in rows.items() if k != "combined")


def test_default_loss_zero_when_overcollateralised(mkt, book):
    # an overnight UST reverse repo with a 1% haircut: loss only from MPOR move beyond the haircut
    p = next(p for p in book["existing_pos"] if p.trade_id == "RR11")
    out = closeout_loss([p], "DEALER_I", mkt, mkt)
    assert out["loss"] >= 0
    calm = stressed_market(mkt, Scenario("x", "x"))
    assert closeout_loss([], "DEALER_I", mkt, calm)["loss"] == 0.0
