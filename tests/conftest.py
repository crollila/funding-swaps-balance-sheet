import pytest

from fsbs import config
from fsbs.book import candidate_pipeline, existing_book
from fsbs.economics import analyze
from fsbs.market import build_market
from fsbs.optimizer import greedy, optimize
from fsbs.stress import standalone_stress_loss


@pytest.fixture(scope="session")
def mkt():
    return build_market()


def _metrics(positions, mkt):
    out = []
    for p in positions:
        m = analyze(p, mkt)
        m.stress_loss = standalone_stress_loss(p, mkt)
        out.append(m)
    return out


@pytest.fixture(scope="session")
def book(mkt):
    eb, cp = existing_book(mkt), candidate_pipeline(mkt)
    return {"existing_pos": eb, "cand_pos": cp, "existing": _metrics(eb, mkt), "cands": _metrics(cp, mkt)}


@pytest.fixture(scope="session")
def allocations(book):
    r = config.LIMITS.flow_reserve
    ex, cd = book["existing"], book["cands"]
    return {"optimizer": optimize(ex, cd, reserve=r), "naive": greedy(ex, cd, "revenue", reserve=r),
            "greedy_robs": greedy(ex, cd, "robs", reserve=r)}
