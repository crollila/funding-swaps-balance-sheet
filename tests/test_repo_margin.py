from datetime import date, timedelta

import pytest

from fsbs.examples import repo_example
from fsbs.margin import (
    cash_against_collateral,
    collateral_required,
    haircut_to_margin_ratio,
    margin_call,
    margin_ratio_to_haircut,
)
from fsbs.repo import Repo

START, END = date(2026, 10, 1), date(2026, 10, 31 - 1)


def _repo(h=0.02, rate=0.04, direction="reverse", mv=100e6):
    return Repo("T", "HF_RV_C", direction, "UST", mv, h, rate, START, END, mta=0.0)


# ------------------------------------------------------------------ margin arithmetic
def test_haircut_and_margin_ratio_are_inverse():
    for h in (0.0, 0.02, 0.15, 0.5):
        assert margin_ratio_to_haircut(haircut_to_margin_ratio(h)) == pytest.approx(h)
    assert haircut_to_margin_ratio(0.02) == pytest.approx(1 / 0.98 - 1)


def test_cash_and_required_collateral_round_trip():
    assert cash_against_collateral(100.0, 0.10) == pytest.approx(90.0)
    assert collateral_required(90.0, 0.10) == pytest.approx(100.0)


def test_margin_call_mta_threshold_and_rounding():
    assert margin_call(100.0, 99.5, mta=1.0) == 0.0  # below MTA
    assert margin_call(100.0, 98.0, mta=1.0) == pytest.approx(2.0)
    assert margin_call(100.0, 103.0, mta=1.0) == pytest.approx(-3.0)  # return excess
    assert margin_call(100.0, 90.0, threshold=4.0) == pytest.approx(6.0)
    assert margin_call(100.0, 98.2, rounding=0.5) == pytest.approx(2.0)  # deliveries round up
    assert margin_call(100.0, 101.7, rounding=0.5) == pytest.approx(-1.5)  # returns round down
    with pytest.raises(ValueError):
        cash_against_collateral(100.0, 1.0)


# ------------------------------------------------------------------ repo cash flows
def test_repo_cash_flows_reconcile():
    r = _repo()
    days = (END - START).days
    assert r.purchase_price == pytest.approx(98e6)
    assert r.repurchase_price == pytest.approx(98e6 * (1 + 0.04 * days / 360))
    (d0, out), (d1, back) = r.cash_flows()
    assert (d0, d1) == (START, END)
    assert out + back == pytest.approx(r.repurchase_price - r.purchase_price)  # desk P&L = interest
    # accrued financing builds linearly to exactly the interest at maturity
    assert r.accrued(END) == pytest.approx(r.repurchase_price - r.purchase_price)
    assert r.accrued(START + timedelta(days=10)) == pytest.approx(98e6 * 0.04 * 10 / 360)
    assert r.initial_margin == pytest.approx(2e6)


def test_repo_and_reverse_are_mirror_images():
    rr, rp = _repo(direction="reverse"), _repo(direction="repo")
    for (_, a), (_, b) in zip(rr.cash_flows(), rp.cash_flows()):
        assert a == pytest.approx(-b)
    assert rr.exposure(END, 99e6) == pytest.approx(-rp.exposure(END, 99e6))
    assert rr.mtm(START, 0.05) == pytest.approx(-rp.mtm(START, 0.05))


def test_zero_haircut_behaviour():
    r = _repo(h=0.0)
    assert r.purchase_price == pytest.approx(r.collateral_mv)
    assert r.initial_margin == 0.0
    assert r.exposure(START, r.collateral_mv) == pytest.approx(0.0)
    assert r.required_collateral(END) == pytest.approx(r.repurchase_price)
    # any price fall is immediately uncollateralised exposure
    assert r.exposure(START, 99e6) == pytest.approx(1e6)


def test_fixed_rate_term_repo_loses_value_when_rates_rise():
    r = _repo()
    assert r.mtm(START, 0.04) == pytest.approx(0.0, abs=1e-6)
    assert r.mtm(START, 0.05) < 0 < r.mtm(START, 0.03)


def test_lifecycle_margin_restores_coverage(mkt):
    ex = repo_example(mkt)
    r, rows = ex["trade"], ex["rows"]
    units0 = r.collateral_mv / ex["prices"][r.start]
    total_called_units = sum(row["margin_call"] / row["price"] for row in rows)
    assert rows[-1]["units"] == pytest.approx(units0 + total_called_units)  # collateral bookkeeping reconciles
    for row in rows[1:-1]:
        if row["margin_call"] != 0.0:
            assert row["collateral_mv"] == pytest.approx(row["required_collateral"], rel=1e-12)
        else:  # no call -> deficit was within MTA
            assert abs(row["required_collateral"] - row["collateral_mv_before_call"]) < r.mta
    assert any(c > 0 for _, c in ex["margin_calls"]) and any(c < 0 for _, c in ex["margin_calls"])
    assert ex["interest"] == pytest.approx(r.purchase_price * r.rate * ex["days"] / 360)


def test_close_out_shortfall_and_recovery():
    r = _repo(h=0.05)
    out = r.close_out(START, 100e6, adverse_move=0.10, liquidation_cost=0.01, recovery=0.4)
    proceeds = 100e6 * 0.9 * 0.99
    assert out["shortfall"] == pytest.approx(95e6 - proceeds)
    assert out["loss"] == pytest.approx((95e6 - proceeds) * 0.6)
    # no loss when the haircut covers the move
    assert r.close_out(START, 100e6, 0.02, 0.0, 0.4)["loss"] == 0.0
    # desk as cash borrower loses its over-collateralisation if the lender fails
    rp = _repo(h=0.05, direction="repo")
    assert rp.close_out(START, 100e6, 0.0, 0.0, 0.0)["loss"] == pytest.approx(5e6)
