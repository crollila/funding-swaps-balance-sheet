"""Optional cross-check against QuantLib (skipped if QuantLib is not installed).

QuantLib is used only as an independent reference here; none of the library's
pricing depends on it. Conventions are aligned: weekends-only calendar, no
settlement or payment lag, annual ACT/360 fixed vs compounded SOFR.
"""
import pytest

from fsbs.dates import add_tenor

ql = pytest.importorskip("QuantLib")


def _qd(d):
    return ql.Date(d.day, d.month, d.year)


@pytest.fixture(scope="module")
def ql_curve(mkt):
    ql.Settings.instance().evaluationDate = _qd(mkt.val)
    cal = ql.WeekendsOnly()
    index = ql.Sofr()
    helpers = []
    for q in mkt.quotes:
        if q.tenor == "ON":
            helpers.append(ql.DepositRateHelper(ql.QuoteHandle(ql.SimpleQuote(q.rate)), ql.Period(1, ql.Days), 0,
                                                cal, ql.Following, False, ql.Actual360()))
        else:
            helpers.append(ql.OISRateHelper(0, ql.Period(q.tenor), ql.QuoteHandle(ql.SimpleQuote(q.rate)), index,
                                            ql.YieldTermStructureHandle(), False, 0, ql.ModifiedFollowing,
                                            ql.Annual, cal))
    c = ql.PiecewiseLogLinearDiscount(_qd(mkt.val), helpers, ql.Actual365Fixed())
    c.enableExtrapolation()
    return c


@pytest.mark.parametrize("tenor", ["1M", "6M", "1Y", "2Y", "5Y", "10Y"])
def test_bootstrapped_discount_factors_match_quantlib(mkt, ql_curve, tenor):
    d = add_tenor(mkt.val, tenor)
    assert mkt.sofr.df(d) == pytest.approx(ql_curve.discount(_qd(d)), abs=2e-6)


def test_5y_swap_npv_and_fair_rate_match_quantlib(mkt, ql_curve):
    from fsbs.irs import IRS

    end = add_tenor(mkt.val, "5Y")
    mine = IRS("X", "CCP_J", 100e6, 0.035, mkt.val, end, pay_fixed=True)
    h = ql.YieldTermStructureHandle(ql_curve)
    index = ql.Sofr(h)
    sched = ql.MakeSchedule(_qd(mkt.val), _qd(end), ql.Period(ql.Annual), calendar=ql.WeekendsOnly(),
                            convention=ql.ModifiedFollowing)
    swap = ql.OvernightIndexedSwap(ql.OvernightIndexedSwap.Payer, 100e6, sched, 0.035, ql.Actual360(), index)
    swap.setPricingEngine(ql.DiscountingSwapEngine(h))
    assert mine.par_rate(mkt.sofr) == pytest.approx(swap.fairRate(), abs=5e-6)
    assert mine.pv(mkt.sofr) == pytest.approx(swap.NPV(), abs=2e3)  # $2k on $100mm (curve convention noise)
