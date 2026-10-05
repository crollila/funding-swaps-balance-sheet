"""One-command reproduction: build curves, price the book, optimise, gate, stress, plot, report.

    python -m fsbs            # writes results/, figures/, README.md, ANALYSIS.md
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, plots
from .book import candidate_pipeline, existing_book, proposed_trades
from .dates import add_tenor
from .economics import analyze
from .examples import irs_example, repo_example, trs_example
from .gate import run_gate
from .market import build_market
from .optimizer import frontier, greedy, optimize, planning_limits
from .portfolio import aggregate, book_exposures, check_limits
from .stress import SCENARIOS, closeout_loss, position_mtm, run_scenario, standalone_stress_loss

ROOT = Path(__file__).resolve().parents[2]


def _metrics(positions, mkt):
    out = []
    for p in positions:
        m = analyze(p, mkt)
        m.stress_loss = standalone_stress_loss(p, mkt)
        out.append(m)
    return out


def _mrow(m, **extra) -> dict:
    return {"trade_id": m.trade_id, "product": m.product, "cpty": m.cpty, "desc": m.desc, "end": m.end.isoformat(),
            "tenor_years": m.tenor_years, "size": m.size, "revenue": m.revenue, "funding_cost": m.funding_cost,
            "bs_charge": m.bs_charge, "capital_charge": m.capital_charge, "liquidity_charge": m.liquidity_charge,
            "expected_cp_cost": m.expected_cp_cost, "net_income": m.net_income, "balance_sheet": m.le_total,
            "rwa": m.rwa, "ead": m.ead, "liquidity_usage": m.liquidity_usage, "dv01": m.dv01, "mtm": m.mtm,
            "stress_loss": m.stress_loss, "robs_bp": m.robs * 1e4 if np.isfinite(m.robs) else None,
            "haircut_ok": m.haircut_ok, **extra}


def run(root: Path = ROOT, render_docs: bool = True, verbose: bool = True) -> dict:
    t0 = time.time()
    res, fig = root / "results", root / "figures"
    res.mkdir(exist_ok=True)
    fig.mkdir(exist_ok=True)
    plots.style()
    log = print if verbose else (lambda *a, **k: None)
    mkt = build_market()
    lim, ch = config.LIMITS, config.CHARGES

    # ------------------------------------------------------------------ 1. curves
    pillars = []
    for q in mkt.quotes:
        end = add_tenor(mkt.val, q.tenor)
        pillars.append({"tenor": q.tenor, "maturity": end.isoformat(), "t": mkt.sofr.t(end), "par_quote": q.rate,
                        "df": mkt.sofr.df(end), "zero_cc": mkt.sofr.zero_rate(end),
                        "repriced_par": mkt.sofr.par_ois_rate(q.tenor) if q.tenor != "ON" else mkt.sofr.term_rate(end)})
    pd.DataFrame(pillars).to_csv(res / "curve_pillars.csv", index=False, lineterminator="\n")
    max_reprice_err = max(abs(p["repriced_par"] - p["par_quote"]) for p in pillars)
    grid = []
    for t in np.concatenate([np.linspace(0.02, 1, 25), np.linspace(1.1, 30, 120)]):
        d = mkt.val + pd.Timedelta(days=int(round(t * 365))).to_pytimedelta()
        d3 = d + pd.Timedelta(days=91).to_pytimedelta()
        grid.append({"t": t, "zero": mkt.sofr.zero_rate(d), "fwd_3m": mkt.sofr.forward_rate(d, d3),
                     "ftp": mkt.funding.rate(d),
                     "ftp_zero": mkt.sofr.zero_rate(d) + mkt.funding.spread(t), "term_sofr": mkt.sofr.term_rate(d)})
    pd.DataFrame(grid).to_csv(res / "curve_grid.csv", index=False, lineterminator="\n")
    repo_curve = []
    for key in ("UST", "IG_CORP", "EQ_SINGLE"):
        for tn in ("1W", "1M", "3M", "6M", "1Y", "2Y"):
            d = add_tenor(mkt.val, tn)
            repo_curve.append({"collateral": key, "tenor": tn, "t": mkt.sofr.t(d), "rate": mkt.street_rate(key, d)})
    plots.funding_curve(grid, repo_curve, [{"t": p["t"], "rate": p["par_quote"]} for p in pillars],
                        fig / "funding_curve.png")
    log(f"[curve] bootstrapped {len(pillars)} pillars, max par re-pricing error {max_reprice_err:.2e}")

    # ------------------------------------------------------------------ 2. worked examples
    rx = repo_example(mkt)
    pd.DataFrame(rx["rows"]).to_csv(res / "example_repo_lifecycle.csv", index=False, lineterminator="\n")
    plots.repo_lifecycle(rx["rows"], fig / "repo_lifecycle.png")
    ix = irs_example(mkt)
    pd.DataFrame(ix["fixed_leg"] + ix["float_leg"]).to_csv(res / "example_irs_legs.csv", index=False, lineterminator="\n")
    plots.irs_cashflows(ix, fig / "irs_cashflows.png")
    tx = trs_example(mkt)
    pd.DataFrame(tx["rows"]).to_csv(res / "example_trs_resets.csv", index=False, lineterminator="\n")
    plots.trs_lifecycle(tx, fig / "trs_lifecycle.png")
    log(f"[examples] 5Y par {ix['par_rate']:.4%}, DV01 fd {ix['dv01_fd']:,.0f} analytic {ix['dv01_analytic']:,.0f}")

    # ------------------------------------------------------------------ 3. book + pipeline
    eb, cp_pos = existing_book(mkt), candidate_pipeline(mkt)
    ex, cd = _metrics(eb, mkt), _metrics(cp_pos, mkt)
    base_agg = aggregate(ex)
    log(f"[book] {len(ex)} existing positions, {len(cd)} client requests")

    # ------------------------------------------------------------------ 4. allocation
    reserve = lim.flow_reserve
    opt = optimize(ex, cd, lim, ch, reserve=reserve)
    naive = greedy(ex, cd, "revenue", lim, ch, reserve=reserve)
    gro = greedy(ex, cd, "robs", lim, ch, reserve=reserve)
    allocs = {"naive": naive, "greedy_robs": gro, "optimizer": opt}
    for a in allocs.values():
        a.full_checks = check_limits(a.agg, ex + cd, [1.0] * len(ex) + list(a.x), lim)
    pd.DataFrame([_mrow(m, fill_naive=naive.x[i], fill_greedy_robs=gro.x[i], fill_optimizer=opt.x[i])
                  for i, m in enumerate(cd)]).to_csv(res / "client_requests.csv", index=False, lineterminator="\n")
    pd.DataFrame([_mrow(m) for m in ex]).to_csv(res / "existing_book.csv", index=False, lineterminator="\n")
    alloc_rows = []
    for name, a in [("existing book only", None)] + list(allocs.items()):
        g = base_agg if a is None else a.agg
        alloc_rows.append({"allocation": name, "revenue": g["revenue"], "funding_cost": g["funding_cost"],
                           "net_income": g["net_income"], "balance_sheet": g["balance_sheet"],
                           "robs_bp": g["robs"] * 1e4, "netting_benefit": g["netting_benefit"],
                           "liquidity_usage": g["liquidity_usage"], "feasible": True if a is None else a.feasible,
                           "requests_filled": 0 if a is None else int(np.sum(a.x > 1e-6))})
    pd.DataFrame(alloc_rows).to_csv(res / "allocation_comparison.csv", index=False, lineterminator="\n")
    shadow = {k: v for k, v in (opt.shadow or {}).items()}
    pd.DataFrame([{"constraint": k, "shadow_price_per_unit": v} for k, v in sorted(shadow.items())]).to_csv(
        res / "optimizer_shadow_prices.csv", index=False, lineterminator="\n")
    log(f"[optimizer] net income: naive {naive.net_income / 1e6:.2f}mm, greedy-RoBS {gro.net_income / 1e6:.2f}mm, "
        f"LP {opt.net_income / 1e6:.2f}mm")

    # final book = existing + optimiser fills
    fills = [(p, x) for p, x in zip(cp_pos, opt.x) if x > 1e-9]
    book_pos = eb + [p.scaled(x) for p, x in fills]
    book_m = ex + cd
    book_x = [1.0] * len(ex) + list(opt.x)
    agg = opt.agg
    limits_now = check_limits(agg, book_m, book_x, lim)
    pd.DataFrame([{"limit": c.name, "value": c.value, "limit_value": c.limit, "utilisation": c.utilisation(),
                   "ok": c.ok} for c in limits_now]).to_csv(res / "limit_utilisation.csv", index=False, lineterminator="\n")

    # ------------------------------------------------------------------ 5. exposures, collateral
    expo = book_exposures(book_pos, mkt)
    pd.DataFrame([{"cpty": k, **v} for k, v in expo.items()]).to_csv(
        res / "counterparty_exposure.csv", index=False, lineterminator="\n")
    labels = {k: c.label for k, c in config.COUNTERPARTIES.items()}
    plots.counterparty_exposure(expo, labels, fig / "counterparty_exposure.png")
    coll = _collateral_summary(book_pos, mkt, book_m, book_x)

    # ------------------------------------------------------------------ 6. stress
    base_metrics_book = [analyze(p, mkt) for p in book_pos]
    stress_rows = [run_scenario(book_pos, mkt, sc, base_metrics_book) for sc in SCENARIOS]
    pd.DataFrame([{k: v for k, v in r.items() if k != "default_detail"} for r in stress_rows]).to_csv(
        res / "stress.csv", index=False, lineterminator="\n")
    plots.stress_pnl(stress_rows, fig / "stress_pnl.png")
    cps_all = sorted({p.cpty for p in book_pos} | {p.hedge.cpty for p in book_pos if p.hedge is not None})
    dflt = [closeout_loss(book_pos, c, mkt, mkt) for c in cps_all]
    pd.DataFrame(dflt).to_csv(res / "default_by_counterparty.csv", index=False, lineterminator="\n")
    plots.default_losses(dflt, labels, fig / "default_losses.png")
    hc_rows = []
    base_ni = aggregate(base_metrics_book)["net_income"]
    for mult in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0):
        mh = replace(mkt, street_haircut_mult=mult)
        g = aggregate([analyze(p, mh) for p in book_pos])
        hc_rows.append({"mult": mult, "liquidity_usage": g["liquidity_usage"], "unsecured_funding": g["unsecured_funding"],
                        "net_income": g["net_income"], "net_income_change": g["net_income"] - base_ni})
    pd.DataFrame(hc_rows).to_csv(res / "haircut_stress.csv", index=False, lineterminator="\n")
    liq_cap = lim.hqla_pool - lim.min_liquidity_buffer
    plots.haircut_stress(hc_rows, liq_cap, fig / "haircut_stress.png")
    breach = next((r["mult"] for r in hc_rows if r["liquidity_usage"] > liq_cap), None)
    log(f"[stress] combined scenario total {stress_rows[-1]['total'] / 1e6:,.1f}mm")

    # ------------------------------------------------------------------ 7. risk: DV01
    base_v = sum(position_mtm(p, mkt) for p in book_pos)
    krd = {}
    for q in mkt.quotes:
        mb = mkt.with_quotes_bump(1.0, only=q.tenor)
        krd[q.tenor] = sum(position_mtm(p, mb) for p in book_pos) - base_v
    par_dv01 = sum(position_mtm(p, mkt.with_quotes_bump(1.0)) for p in book_pos) - base_v
    irs_dv01 = sum(m.dv01 * w for m, w in zip(book_m, book_x) if m.product == "irs")
    pd.DataFrame([{"tenor": k, "dv01": v} for k, v in krd.items()]).to_csv(
        res / "key_rate_dv01.csv", index=False, lineterminator="\n")
    plots.keyrate_dv01(krd, fig / "key_rate_dv01.png")

    # ------------------------------------------------------------------ 8. pre-trade gate
    decisions = run_gate(proposed_trades(mkt), mkt, book_m, book_x, lim)
    gate_rows = []
    for d in sorted(decisions, key=lambda d: d.trade_id):
        m = d.metrics
        gate_rows.append({"trade_id": d.trade_id, "product": d.product, "cpty": d.cpty, "desc": d.desc,
                          "decision": "ACCEPT" if d.accepted else "REJECT", "size": m.size,
                          "revenue": m.revenue, "funding_cost": m.funding_cost,
                          "capital_bs_charge": m.bs_charge + m.capital_charge,
                          "incr_net_income": d.incremental["net_income"],
                          "incr_balance_sheet": d.incremental["balance_sheet"],
                          "incr_robs_bp": d.incremental["robs"] * 1e4 if np.isfinite(d.incremental["robs"]) else None,
                          "stress_loss": m.stress_loss, "cpty_ead_after": d.incremental["cpty_ead_after"],
                          "cpty_ead_limit": d.incremental["cpty_ead_limit"],
                          "failed_checks": "|".join(f.check for f in d.failures), "reason": d.reason})
    pd.DataFrame(gate_rows).to_csv(res / "pretrade_gate.csv", index=False, lineterminator="\n")
    n_acc = sum(d.accepted for d in decisions)
    log(f"[gate] {n_acc} accepted, {len(decisions) - n_acc} rejected")

    # ------------------------------------------------------------------ 9. frontier
    # Frontier: vary the balance-sheet cap, keep the liquidity reserve, so the chosen point sits on it.
    grid_bs = [17.5e9, 18e9, 18.5e9, 19e9, 19.4e9, 19.8e9, 20.4e9, 21e9, 22e9, 23e9]
    lim_f = planning_limits(lim, reserve)
    front = frontier(ex, cd, grid_bs, lim_f, ch)
    g_pts, n_pts = [], []
    for L in grid_bs:
        lL = replace(lim_f, balance_sheet=L)
        for meth, store in (("robs", g_pts), ("revenue", n_pts)):
            a = greedy(ex, cd, meth, lL, ch)
            store.append({"bs_limit": L, "balance_sheet": a.agg["balance_sheet"], "net_income": a.net_income})
    pd.DataFrame([dict(f, greedy_net_income=g["net_income"], naive_net_income=n["net_income"])
                  for f, g, n in zip(front, g_pts, n_pts)]).to_csv(res / "frontier.csv", index=False, lineterminator="\n")
    plots.robs_frontier(front, g_pts, n_pts, {"balance_sheet": agg["balance_sheet"], "net_income": agg["net_income"]},
                        fig / "robs_frontier.png")

    # ------------------------------------------------------------------ 10. book figures
    stacks = {}
    for name, g in [("Existing book", base_agg), ("Naive fill", naive.agg), ("Greedy by RoBS", gro.agg),
                    ("LP optimizer", agg)]:
        stacks[name] = {"reverse_repo": g["sft_net_onbalance"], "trs": g["onbal_non_sft"], "ccr": g["le_ccr"],
                        "netting": g["netting_benefit"]}
    plots.balance_sheet_usage(stacks, lim.balance_sheet, planning_limits(lim, reserve).balance_sheet,
                              fig / "balance_sheet_usage.png")
    plots.allocation(cd, {"naive": naive.x, "greedy": gro.x, "lp": opt.x}, fig / "optimizer_allocation.png")
    by_id = {m.trade_id: m for m in ex}
    plots.economics_waterfall([("Reverse repo: IG credit (RR04)", by_id["RR04"]),
                               ("Equity TRS: tech (TRS01)", by_id["TRS01"]),
                               ("IRS: corporate, no CSA (IRS01)", by_id["IRS01"]),
                               ("Reverse repo: 1Y UST (RR10)", by_id["RR10"])], fig / "economics_waterfall.png")

    # ------------------------------------------------------------------ 11. headline + summary
    expo_tot = {k: sum(v[k] for v in expo.values()) for k in
                ("gross", "net", "gross_after_collateral", "net_after_collateral", "ead")}
    headline = {
        "valuation_date": mkt.val.isoformat(),
        "balance_sheet": agg["balance_sheet"], "balance_sheet_gross_pre_netting": agg["gross_le"],
        "balance_sheet_limit": lim.balance_sheet, "netting_benefit": agg["netting_benefit"],
        "revenue": agg["revenue"], "funding_cost": agg["funding_cost"],
        "net_interest": agg["revenue"] - agg["funding_cost"],
        "bs_and_capital_charges": agg["bs_charge"] + agg["capital_charge"],
        "liquidity_charge": agg["liquidity_charge"], "expected_cp_cost": agg["expected_cp_cost"],
        "net_income": agg["net_income"], "robs_bp": agg["robs"] * 1e4, "rwa": agg["rwa"],
        "gross_cpty_exposure": expo_tot["gross"], "net_cpty_exposure_pre_collateral": expo_tot["net"],
        "net_cpty_exposure_after_collateral": expo_tot["net_after_collateral"], "ead_netting_sets": expo_tot["ead"],
        "ead_standalone_sum": sum(agg["by_cp_ead"].values()),
        "desk_dv01": agg["dv01"], "desk_par_dv01": par_dv01, "irs_dv01": irs_dv01,
        "example_5y_irs_dv01": ix["dv01_fd"], **coll,
        "naive_net_income": naive.net_income, "greedy_robs_net_income": gro.net_income,
        "optimizer_net_income": opt.net_income, "existing_net_income": base_agg["net_income"],
        "optimizer_vs_naive": opt.net_income - naive.net_income,
        "optimizer_vs_naive_pct": (opt.net_income - naive.net_income) / abs(naive.net_income),
        "optimizer_vs_greedy": opt.net_income - gro.net_income,
        "gate_accepted": n_acc, "gate_rejected": len(decisions) - n_acc,
        "combined_stress_total": stress_rows[-1]["total"], "worst_default_cpty": stress_rows[5]["defaulted"],
        "worst_default_loss": -stress_rows[5]["credit"],
        "liquidity_usage": agg["liquidity_usage"], "liquidity_capacity": liq_cap,
        "haircut_multiplier_breaching_liquidity": breach,
        "curve_max_reprice_error": max_reprice_err,
        "n_existing": len(ex), "n_requests": len(cd), "n_filled": int(np.sum(opt.x > 1e-6)),
    }
    (res / "headline.json").write_text(json.dumps(headline, indent=2, default=str) + "\n", newline="\n")
    summary = {
        "headline": headline, "pillars": pillars, "repo_example": _ser_repo(rx), "irs_example": _ser_irs(ix),
        "trs_example": _ser_trs(tx), "allocations": alloc_rows, "stress": [
            {k: v for k, v in r.items() if k != "default_detail"} for r in stress_rows],
        "gate": gate_rows, "frontier": front, "shadow": shadow, "key_rate_dv01": krd, "exposure": expo,
        "default_by_cpty": dflt, "haircut_stress": hc_rows, "limits": [
            {"name": c.name, "value": c.value, "limit": c.limit, "utilisation": c.utilisation()} for c in limits_now],
        "fills": [{"trade_id": m.trade_id, "product": m.product, "cpty": m.cpty, "desc": m.desc,
                   "robs_bp": m.robs * 1e4, "net_income": m.net_income, "naive": float(naive.x[i]),
                   "greedy": float(gro.x[i]), "lp": float(opt.x[i])} for i, m in enumerate(cd)],
        "existing": [_mrow(m) for m in ex],
    }
    (res / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", newline="\n")
    if render_docs:
        from .report import render_all
        render_all(root, summary)
    log(f"[done] {time.time() - t0:.1f}s -> {res}, {fig}")
    return summary


def _collateral_summary(book_pos, mkt, book_m, book_x) -> dict:
    val = mkt.val
    held_mv = held_cushion = posted_mv = posted_over = trs_im = 0.0
    for p in book_pos:
        inst = p.inst
        if p.product == "reverse_repo":
            mv = inst.required_collateral(val)
            held_mv += mv
            held_cushion += mv - inst.loan_balance(val)
        elif p.product == "repo":
            mv = inst.required_collateral(val)
            posted_mv += mv
            posted_over += mv - inst.loan_balance(val)
        elif p.product == "trs":
            trs_im += inst.im_pct * inst.shares * config.UNDERLYINGS[inst.underlying].spot
    ccp_im = sum(m.detail.get("ccp_im", 0.0) * w for m, w in zip(book_m, book_x) if m.product == "irs")
    unsecured = sum(m.unsecured_funding * w for m, w in zip(book_m, book_x))
    return {"client_collateral_held": held_mv, "client_haircut_cushion": held_cushion, "trs_im_held": trs_im,
            "collateral_posted_in_repo": posted_mv, "overcollateralisation_given": posted_over,
            "ccp_im_posted": ccp_im, "unsecured_funding_need": unsecured}


def _ser_repo(rx):
    r = rx["trade"]
    return {"collateral_mv": r.collateral_mv, "haircut": r.haircut, "rate": rx["rate"], "start": r.start.isoformat(),
            "end": r.end.isoformat(), "days": rx["days"], "purchase_price": rx["purchase_price"],
            "repurchase_price": rx["repurchase_price"], "interest": rx["interest"],
            "initial_margin": rx["initial_margin"], "mta": r.mta,
            "margin_calls": [(d.isoformat(), c) for d, c in rx["margin_calls"]]}


def _ser_irs(ix):
    return {k: ix[k] for k in ("par_rate", "pv_fixed", "pv_float", "pv", "annuity", "dv01_fd", "dv01_analytic",
                               "par_dv01", "pv_if_rates_up_100")} | {
        "fixed_leg": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in ix["fixed_leg"]],
        "float_leg": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in ix["float_leg"]],
        "key_rate_dv01": ix["key_rate_dv01"]}


def _ser_trs(tx):
    t = tx["trade"]
    return {"notional": t.shares * t.initial_price, "spread": t.spread, "shares": t.shares,
            "client_total": tx["client_total"], "desk_total": tx["desk_total"],
            "mtm_at_inception_client": tx["mtm_at_inception_client"],
            "rows": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in tx["rows"]]}


def main():
    run()


if __name__ == "__main__":
    main()
