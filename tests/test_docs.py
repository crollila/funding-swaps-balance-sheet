"""The documentation is generated from the run; these tests keep the prose honest."""
import json
from pathlib import Path

import pytest

from fsbs.report import TEMPLATES, build_context, render

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def summary():
    p = ROOT / "results" / "summary.json"
    if not p.exists():
        pytest.skip("run `python -m fsbs` first")
    return json.loads(p.read_text())


def test_committed_docs_match_committed_results(summary):
    ctx = build_context(summary)
    for src, dst in TEMPLATES.items():
        rendered = render((ROOT / "docs" / "templates" / src).read_text(encoding="utf-8"), ctx)
        assert "{{" not in rendered
        assert (ROOT / dst).read_text(encoding="utf-8") == rendered, f"{dst} is stale: re-run python -m fsbs"


def test_analysis_narrative_claims_hold(summary):
    """ANALYSIS.md names specific gate outcomes; check them against the results."""
    g = {r["trade_id"]: r for r in summary["gate"]}
    fails = {k: set(filter(None, v["failed_checks"].split("|"))) for k, v in g.items()}
    for tid in ("P01", "P07", "P08", "P13"):
        assert g[tid]["decision"] == "ACCEPT", tid
    assert "collateral_eligibility" in fails["P02"]
    assert any(f.startswith("client_leverage") for f in fails["P03"])
    assert any(f.startswith("inventory") for f in fails["P06"])
    assert {"balance_sheet", "dv01"} <= fails["P05"]
    assert "dv01" in fails["P10"]
    for tid in ("P04", "P09", "P11", "P12"):
        assert "net_income" in fails[tid], tid
    assert fails["P14"] == {"rorwa"} and g["P14"]["incr_net_income"] > 0
    h = summary["headline"]
    assert h["optimizer_net_income"] > h["greedy_robs_net_income"] > h["naive_net_income"]
    assert h["worst_default_cpty"] == "MMF_GOVT_E"
    st = {r["scenario"]: r for r in summary["stress"]}
    assert st["combined"]["credit"] < st["combined"]["mtm_pnl"] + st["combined"]["nii_1y"]  # credit dominates
    assert abs(st["equity_down_20"]["mtm_pnl"]) < abs(st["sofr_up_100"]["mtm_pnl"])
