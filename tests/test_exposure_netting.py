import numpy as np
import pytest

from fsbs import config
from fsbs.economics import analyze
from fsbs.exposure import DerivTrade, exposure_summary, sa_ccr, sft_contribution, sft_exposure
from fsbs.portfolio import aggregate, book_exposures


def test_netting_never_increases_exposure_random_portfolios():
    rng = np.random.default_rng(0)
    for _ in range(2000):
        n = int(rng.integers(1, 12))
        v = rng.normal(0, 1e7, n)
        c = np.where(rng.random(n) < 0.7, np.abs(rng.normal(0, 5e6, n)), 0.0)  # collateral held >= 0
        s = exposure_summary(list(v), list(c), netting=True)
        assert s["net"] <= s["gross"] + 1e-6
        assert s["net_after_collateral"] <= s["gross_after_collateral"] + 1e-6
        assert s["gross_after_collateral"] <= s["gross"] + 1e-6  # collateral held only reduces exposure
        # gross figure does not depend on the netting flag
        assert exposure_summary(list(v), list(c), netting=False)["gross"] == pytest.approx(s["gross"])


def test_no_netting_agreement_falls_back_to_gross():
    s = exposure_summary([10.0, -8.0], [0.0, 0.0], netting=False)
    assert s["net"] == s["gross"] == 10.0
    assert exposure_summary([10.0, -8.0], [0.0, 0.0], netting=True)["net"] == 2.0


def test_sft_exposure_comprehensive_approach():
    # reverse repo: 98 lent against 100 of collateral with 2% supervisory haircut -> E* = 0
    assert sft_contribution("reverse", 98.0, 100.0, 0.02) == pytest.approx(0.0)
    assert sft_contribution("repo", 98.0, 100.0, 0.02) == pytest.approx(4.0)  # over-collateralisation + haircut
    assert sft_exposure([5.0, -3.0]) == 2.0
    assert sft_exposure([5.0, -3.0], netting=False) == 5.0


def test_sa_ccr_offsetting_trades_and_collateral():
    a = DerivTrade("a", "IR", 1.0, 100e6, 0.0, 5.0, 1e6)
    b = DerivTrade("b", "IR", -1.0, 100e6, 0.0, 5.0, -1e6)
    alone = sa_ccr([a], margined=False, mpor_days=10)["ead"]
    both = sa_ccr([a, b], margined=False, mpor_days=10)
    assert both["addon"] == pytest.approx(0.0, abs=1e-6)  # perfect hedge in the same bucket
    assert both["ead"] <= alone
    # collateral reduces RC and the PFE multiplier, never below 5% of the add-on
    unc = sa_ccr([a], margined=True, mpor_days=10)
    col = sa_ccr([a], margined=True, mpor_days=10, vm_held=1e6, im_held=50e6)
    assert col["rc"] == 0.0 and col["ead"] < unc["ead"]
    assert col["multiplier"] >= 0.05


def test_book_netting_set_ead_not_above_standalone_sum(mkt, book):
    pos = book["existing_pos"]
    expo = book_exposures(pos, mkt)
    standalone = aggregate(book["existing"])["by_cp_ead"]
    for cp, e in expo.items():
        assert e["ead"] <= standalone.get(cp, 0.0) + 1.0, cp
        assert e["net_after_collateral"] <= e["gross_after_collateral"] + 1e-6


def test_repo_on_balance_sheet_netting_rules(mkt, book):
    ms = {m.trade_id: m for m in book["existing"]}
    # RR11 (desk lends 1.485bn to DEALER_I) and RP02 (desk borrows 1.188bn from DEALER_I) mature the same day
    pair = [ms["RR11"], ms["RP02"]]
    agg = aggregate(pair)
    assert ms["RR11"].sft_key == ms["RP02"].sft_key
    assert agg["sft_net_onbalance"] == pytest.approx(ms["RR11"].le_onbalance - ms["RP02"].size)
    assert agg["netting_benefit"] == pytest.approx(ms["RP02"].size)
    # different maturity -> no offset
    rr10 = ms["RR10"]
    agg2 = aggregate([rr10, ms["RP05"]])
    assert agg2["sft_net_onbalance"] == pytest.approx(rr10.le_onbalance)
    # netting never makes balance sheet exceed the gross figure
    full = aggregate(book["existing"])
    assert full["balance_sheet"] <= full["gross_le"] + 1e-6


def test_economics_scale_linearly(mkt, book):
    p = book["cand_pos"][0]
    m1, m2 = analyze(p, mkt), analyze(p.scaled(0.5), mkt)
    for f in ("revenue", "funding_cost", "le_total", "rwa", "liquidity_usage", "net_income"):
        assert getattr(m2, f) == pytest.approx(0.5 * getattr(m1, f), rel=1e-9), f


def test_counterparty_reference_data_is_consistent():
    for c in config.COUNTERPARTIES.values():
        assert 0 <= c.pd < 1 and 0 < c.lgd <= 1 and c.ead_limit > 0
        if c.kind == "hedge_fund":
            assert c.nav and c.max_leverage
