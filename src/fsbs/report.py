"""Render README.md, ANALYSIS.md and RESUME_BULLETS.md from templates and the run's results.

Templates live in docs/templates/*.md.tmpl and use {{key}} placeholders. Every
number in the rendered documents comes from ``summary`` (the run), so the docs
cannot drift from the code. Rendering fails loudly on any unresolved key.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config

TEMPLATES = {"README.md.tmpl": "README.md", "ANALYSIS.md.tmpl": "ANALYSIS.md",
             "RESUME_BULLETS.md.tmpl": "RESUME_BULLETS.md", "MODEL_ASSUMPTIONS.md.tmpl": "MODEL_ASSUMPTIONS.md"}


# ----------------------------------------------------------------------------- formatting
def usd(v: float, unit: str | None = None, dp: int = 1) -> str:
    sign = "-" if v < 0 else ""
    a = abs(v)
    if unit is None:
        unit = "bn" if a >= 1e9 else "mm" if a >= 1e6 else "k" if a >= 1e3 else ""
    div = {"bn": 1e9, "mm": 1e6, "k": 1e3, "": 1.0}[unit]
    return f"{sign}${a / div:,.{dp}f}{unit}"


def bp(v: float, dp: int = 1) -> str:
    return f"{v:,.{dp}f}bp"


def pct(v: float, dp: int = 1) -> str:
    return f"{v * 100:,.{dp}f}%"


def table(headers: list[str], rows: list[list], align: str | None = None) -> str:
    align = align or "l" + "r" * (len(headers) - 1)
    sep = ["---:" if a == "r" else ":---" for a in align]
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(sep) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ----------------------------------------------------------------------------- context
def build_context(s: dict) -> dict:
    h = s["headline"]
    ctx: dict[str, str] = {}
    lab = {k: c.label for k, c in config.COUNTERPARTIES.items()}

    # headline
    ctx |= {
        "val_date": h["valuation_date"],
        "bs": usd(h["balance_sheet"]), "bs_limit": usd(h["balance_sheet_limit"]),
        "bs_gross": usd(h["balance_sheet_gross_pre_netting"]), "netting_benefit": usd(h["netting_benefit"]),
        "revenue": usd(h["revenue"]), "funding_cost": usd(h["funding_cost"]), "net_interest": usd(h["net_interest"]),
        "charges": usd(h["bs_and_capital_charges"]), "liq_charge": usd(h["liquidity_charge"]),
        "cp_cost": usd(h["expected_cp_cost"]), "net_income": usd(h["net_income"]), "robs": bp(h["robs_bp"]),
        "rwa": usd(h["rwa"]),
        "gross_exposure": usd(h["gross_cpty_exposure"]), "net_exposure_pre": usd(h["net_cpty_exposure_pre_collateral"]),
        "net_exposure": usd(h["net_cpty_exposure_after_collateral"]), "ead_netted": usd(h["ead_netting_sets"]),
        "ead_standalone": usd(h["ead_standalone_sum"]),
        "desk_dv01": usd(h["desk_dv01"], "k", 0), "desk_par_dv01": usd(h["desk_par_dv01"], "k", 0),
        "irs_dv01": usd(h["irs_dv01"], "k", 0),
        "coll_held": usd(h["client_collateral_held"]), "haircut_cushion": usd(h["client_haircut_cushion"]),
        "trs_im": usd(h["trs_im_held"]), "coll_posted": usd(h["collateral_posted_in_repo"]),
        "overcoll": usd(h["overcollateralisation_given"]), "ccp_im": usd(h["ccp_im_posted"]),
        "unsecured": usd(h["unsecured_funding_need"]),
        "naive_ni": usd(h["naive_net_income"]), "greedy_ni": usd(h["greedy_robs_net_income"]),
        "opt_ni": usd(h["optimizer_net_income"]), "existing_ni": usd(h["existing_net_income"]),
        "opt_vs_naive": usd(h["optimizer_vs_naive"]), "opt_vs_naive_pct": pct(h["optimizer_vs_naive_pct"], 0),
        "opt_vs_greedy": usd(h["optimizer_vs_greedy"]),
        "opt_vs_greedy_pct": pct(h["optimizer_vs_greedy"] / abs(h["greedy_robs_net_income"]), 0),
        "n_acc": str(h["gate_accepted"]), "n_rej": str(h["gate_rejected"]),
        "n_prop": str(h["gate_accepted"] + h["gate_rejected"]),
        "combined": usd(h["combined_stress_total"]), "worst_cp": lab[h["worst_default_cpty"]],
        "worst_cp_key": h["worst_default_cpty"], "worst_loss": usd(h["worst_default_loss"]),
        "liq_usage": usd(h["liquidity_usage"]), "liq_cap": usd(h["liquidity_capacity"]),
        "hc_breach": (f"{h['haircut_multiplier_breaching_liquidity']:g}x" if
                      h["haircut_multiplier_breaching_liquidity"] else "not within the tested range"),
        "reprice_err": f"{h['curve_max_reprice_error']:.1e}",
        "n_existing": str(h["n_existing"]), "n_requests": str(h["n_requests"]), "n_filled": str(h["n_filled"]),
        "reserve": pct(config.LIMITS.flow_reserve, 0),
    }

    # headline table
    rows = [
        ["Simulated balance sheet used (leverage exposure)", f"{ctx['bs']} of {ctx['bs_limit']} limit"],
        ["Repo netting benefit (same counterparty, same maturity)", ctx["netting_benefit"]],
        ["Annual revenue (gross interest + spread income)", ctx["revenue"]],
        ["Annual funding cost", ctx["funding_cost"]],
        ["Balance-sheet + capital charges / liquidity charge / expected CP cost",
         f"{ctx['charges']} / {ctx['liq_charge']} / {ctx['cp_cost']}"],
        ["**Net income after all charges**", f"**{ctx['net_income']}/yr**"],
        ["**Return on balance sheet**", f"**{ctx['robs']}**"],
        ["Gross counterparty exposure (no netting, no collateral)", ctx["gross_exposure"]],
        ["Net counterparty exposure (after netting and collateral)", ctx["net_exposure"]],
        ["Exposure-at-default, netting sets (vs. sum of standalone)", f"{ctx['ead_netted']} (vs. {ctx['ead_standalone']})"],
        ["Desk DV01 (all products) / IRS-only DV01", f"{ctx['desk_dv01']} / {ctx['irs_dv01']} per bp"],
        ["Client collateral held / haircut cushion in it", f"{ctx['coll_held']} / {ctx['haircut_cushion']}"],
        ["TRS initial margin held / CCP initial margin posted", f"{ctx['trs_im']} / {ctx['ccp_im']}"],
        ["Unsecured funding needed for haircut gaps and IM", ctx["unsecured"]],
        ["**Optimizer vs. naive allocation**", f"**+{ctx['opt_vs_naive']}/yr (+{ctx['opt_vs_naive_pct']})**"],
        ["Optimizer vs. greedy return-on-balance-sheet heuristic", f"+{ctx['opt_vs_greedy']}/yr (+{ctx['opt_vs_greedy_pct']})"],
        ["Pre-trade gate: proposed trades accepted / rejected", f"{ctx['n_acc']} / {ctx['n_rej']}"],
        ["Combined stress, 1-year P&L impact", ctx["combined"]],
    ]
    ctx["headline_table"] = table(["Metric (simulated book, valuation date " + ctx["val_date"] + ")", "Value"], rows)

    # worked examples
    rx, ix, tx = s["repo_example"], s["irs_example"], s["trs_example"]
    ctx |= {
        "rx_mv": usd(rx["collateral_mv"], "mm", 0), "rx_h": pct(rx["haircut"], 0), "rx_pp": usd(rx["purchase_price"], "mm", 3),
        "rx_rate": pct(rx["rate"], 3), "rx_days": str(rx["days"]), "rx_rp": usd(rx["repurchase_price"], "mm", 3),
        "rx_int": usd(rx["interest"], "k", 1), "rx_im": usd(rx["initial_margin"], "mm", 1),
        "rx_ncalls": str(len(rx["margin_calls"])), "rx_mta": usd(rx["mta"], "k", 0),
        "rx_biggest_call": usd(max((c for _, c in rx["margin_calls"]), key=abs), "mm", 2),
        "rx_start": rx["start"], "rx_end": rx["end"],
        "ix_par": pct(ix["par_rate"], 3), "ix_pvfix": usd(ix["pv_fixed"], "mm", 3), "ix_pvflt": usd(ix["pv_float"], "mm", 3),
        "ix_ann": usd(ix["annuity"], "mm", 1), "ix_dv01": usd(ix["dv01_fd"], "", 0),
        "ix_dv01_an": usd(ix["dv01_analytic"], "", 0),
        "ix_par_dv01": usd(ix["par_dv01"], "", 0), "ix_up100": usd(ix["pv_if_rates_up_100"], "mm", 2),
        "ix_lin100": usd(ix["dv01_fd"] * 100, "mm", 2),
        "tx_notional": usd(tx["notional"], "mm", 0), "tx_spread": bp(tx["spread"] * 1e4, 0),
        "tx_client": usd(tx["client_total"], "mm", 2), "tx_desk": usd(tx["desk_total"], "k", 0),
        "tx_mtm0": usd(-tx["mtm_at_inception_client"], "k", 0),
    }
    leg_rows = []
    for f, fl in zip(ix["fixed_leg"], ix["float_leg"]):
        leg_rows.append([f["end"], f"{f['tau']:.4f}", f"{f['df']:.6f}", pct(fl["rate"], 3),
                         usd(fl["amount"], "mm", 3), usd(-f["amount"], "mm", 3), usd(fl["pv"] - f["pv"], "k", 1)])
    ctx["irs_leg_table"] = table(["Payment date", "Accrual (ACT/360)", "Discount factor", "SOFR forward",
                                  "Float received", "Fixed paid", "PV of net"], leg_rows)
    trs_rows = []
    for r in tx["rows"]:
        trs_rows.append([r["end"], f"{r['s_prev']:,.1f} -> {r['s_now']:,.1f}", usd(r["notional"], "mm", 1),
                         pct(r["sofr"], 3), usd(r["equity_leg"], "mm", 2), usd(-r["financing_leg"], "mm", 2),
                         usd(r["client_net"], "mm", 2), usd(r["desk_net"], "k", 1)])
    ctx["trs_table"] = table(["Reset", "Index level", "Notional", "SOFR (period)", "Equity leg (client receives)",
                              "Financing (client pays)", "Client net", "Desk net (hedged)"], trs_rows)

    pill = [[p["tenor"], p["maturity"], pct(p["par_quote"], 3), f"{p['df']:.6f}", pct(p["zero_cc"], 3),
             f"{abs(p['repriced_par'] - p['par_quote']) * 1e4:.1e}"] for p in s["pillars"]]
    ctx["pillar_table"] = table(["Tenor", "Maturity", "Par quote (input)", "Discount factor", "Zero rate (cc)",
                                 "Re-pricing error (bp)"], pill)

    # allocation table
    names = {"existing book only": "Existing book only", "naive": "Naive: biggest revenue first",
             "greedy_robs": "Greedy by return on balance sheet", "optimizer": "LP optimizer"}
    ctx["alloc_table"] = table(
        ["Allocation", "Requests filled", "Balance sheet", "Netting benefit", "Revenue", "Net income", "RoBS"],
        [[names[a["allocation"]], a["requests_filled"], usd(a["balance_sheet"]), usd(a["netting_benefit"]),
          usd(a["revenue"]), usd(a["net_income"]), bp(a["robs_bp"])] for a in s["allocations"]])
    sh = s["shadow"]
    sh_rows = []
    for k, v in sorted(sh.items(), key=lambda kv: -abs(kv[1])):
        if k.startswith("netting:"):
            continue
        sh_rows.append([f"`{k}`", f"{v * 1e4:,.2f}bp per $ of limit" if "dv01" not in k else f"${v:,.2f} per $ of DV01"])
    n_net = sum(1 for k in sh if k.startswith("netting:"))
    sh_rows.append([f"{n_net} repo netting keys", "value of 1$ of offsetting repo at that (counterparty, date)"])
    ctx["shadow_table"] = table(["Binding constraint", "Shadow price (marginal net income)"], sh_rows)
    ctx["shadow_bs"] = bp(sh.get("balance_sheet", 0.0) * 1e4, 2)

    # which candidates
    fills = s["fills"]
    lp_only = [f for f in fills if f["lp"] > 0.01 and f["greedy"] < 0.01]
    ctx["lp_only_list"] = ", ".join(f"{f['trade_id']} ({f['desc']}, standalone net "
                                    f"{usd(f['net_income'], 'mm', 2)}/yr)" for f in lp_only) or "none"
    naive_neg = [f for f in fills if f["naive"] > 0.01 and f["net_income"] < 0]
    ctx["naive_neg_count"] = str(len(naive_neg))

    # gate
    g_rows = []
    for g in s["gate"]:
        g_rows.append([g["trade_id"], g["desc"], lab.get(g["cpty"], g["cpty"]), f"**{g['decision']}**",
                       usd(g["incr_net_income"], "mm", 2), usd(g["incr_balance_sheet"]),
                       "n/a" if g["incr_robs_bp"] is None else bp(g["incr_robs_bp"]), usd(g["stress_loss"], "mm", 0),
                       g["reason"]])
    ctx["gate_table"] = table(["ID", "Proposal", "Counterparty", "Decision", "Incr. net income/yr", "Incr. balance sheet",
                               "Incr. RoBS", "Stress loss", "Reason"], g_rows, "lllcrrrrl")

    # stress
    st_rows = [[r["label"], usd(r["mtm_pnl"], "mm"), usd(r["nii_1y"], "mm"), usd(r["credit"], "mm"),
                f"**{usd(r['total'], 'mm')}**", usd(r["liquidity_usage_change"]),
                lab.get(r["defaulted"], "") if r["defaulted"] else ""] for r in s["stress"]]
    ctx["stress_table"] = table(["Scenario", "Mark-to-market", "1y net income", "Credit / close-out", "Total",
                                 "Extra liquidity used", "Defaulted counterparty"], st_rows)
    st = {r["scenario"]: r for r in s["stress"]}
    ctx |= {f"st_{k}": usd(v["total"], "mm") for k, v in st.items()}
    ctx["st_combined_credit"] = usd(st["combined"]["credit"], "mm")
    ctx["st_combined_def"] = lab.get(st["combined"]["defaulted"], "")
    ctx["st_sofr_mtm"] = usd(st["sofr_up_100"]["mtm_pnl"], "mm")
    ctx["st_coll_liq"] = usd(st["collateral_down_15"]["liquidity_usage_change"])
    ctx["st_hc_liq"] = usd(st["haircuts_up"]["liquidity_usage_change"])
    d_rows = [[lab[d["cpty"]], usd(d["sft_shortfall"], "mm"), usd(d["deriv_shortfall"], "mm"), usd(d["loss"], "mm")]
              for d in sorted(s["default_by_cpty"], key=lambda d: -d["loss"])]
    ctx["default_table"] = table(["Counterparty defaults", "Repo-set shortfall", "Derivative-set shortfall",
                                  "Loss after recovery"], d_rows)
    hc_rows = [[f"{r['mult']:g}x", usd(r["unsecured_funding"]), usd(r["liquidity_usage"]),
                usd(r["net_income_change"], "mm", 2)] for r in s["haircut_stress"]]
    ctx["haircut_table"] = table(["Street haircut multiplier", "Unsecured funding needed", "Liquidity usage",
                                  "Change in net income/yr"], hc_rows)
    krd = s["key_rate_dv01"]
    ctx["krd_table"] = table(["Pillar"] + list(krd), [["DV01 ($/bp)"] + [f"{v / 1e3:,.0f}k" for v in krd.values()]])
    big = sorted(krd.items(), key=lambda kv: -abs(kv[1]))[:4]
    ctx["krd_top"] = ", ".join(f"{k} {usd(v, 'k', 0)}" for k, v in big)

    # exposure table
    e_rows = []
    for cp, e in sorted(s["exposure"].items(), key=lambda kv: -kv[1]["gross"]):
        e_rows.append([lab[cp], usd(e["gross"]), usd(e["net"]), usd(e["net_after_collateral"], "mm"),
                       usd(e["ead"], "mm"), usd(e["ead_limit"], "mm", 0), pct(e["ead"] / e["ead_limit"], 0)])
    ctx["exposure_table"] = table(["Counterparty", "Gross", "Net (netting)", "Net (netting + collateral)",
                                   "EAD (netting sets)", "EAD limit", "Used"], e_rows)
    lim_rows = [[f"`{x['name']}`", _fmt_limit(x["name"], x["value"]), _fmt_limit(x["name"], x["limit"]),
                 pct(x["utilisation"], 0)] for x in s["limits"]
                if x["name"] != "collateral_eligibility" and x["utilisation"] >= 0.5]
    ctx["limit_table"] = table(["Limit", "Usage", "Limit", "Utilisation"], lim_rows)

    # existing examples for waterfall commentary
    ex = {e["trade_id"]: e for e in s["existing"]}
    ctx["rr10_rev_less_fund"] = usd(ex["RR10"]["revenue"] - ex["RR10"]["funding_cost"], "mm", 2)
    ctx["rr10_bs"] = usd(ex["RR10"]["bs_charge"], "mm", 2)
    ctx["irs01_ni"] = usd(ex["IRS01"]["net_income"], "k", 0)
    ctx["irs01_rev"] = usd(ex["IRS01"]["revenue"], "k", 0)

    # assumptions tables
    ctx["collateral_table"] = table(
        ["Class", "Street haircut", "Min client haircut", "Supervisory haircut", "Street spread", "Typical client spread",
         "Vol", "Liquidity tier", "Runoff weight", "Liquidation cost"],
        [[c.label, pct(c.street_haircut, 0), pct(c.internal_min_haircut, 0), pct(c.supervisory_haircut, 0),
          bp(c.street_spread_bp, 0), bp(c.client_spread_bp, 0), pct(c.annual_vol, 0), c.hqla, f"{c.runoff:.2f}",
          pct(c.liquidation_cost, 1)] for c in config.COLLATERAL.values()])
    ctx["cpty_table"] = table(
        ["Counterparty (fictional archetype)", "Type", "PD (1y)", "LGD", "Risk weight", "CSA", "EAD limit", "NAV / max leverage"],
        [[c.label, c.kind, pct(c.pd, 2), pct(c.lgd, 0), pct(c.risk_weight, 0), "yes" if c.csa else "no",
          usd(c.ead_limit, "mm", 0), f"{usd(c.nav, 'bn', 1)} / {c.max_leverage:g}x" if c.nav else "-"]
         for c in config.COUNTERPARTIES.values()])
    ch, lm, gt = config.CHARGES, config.LIMITS, config.GATE
    ctx["charges_table"] = table(["Charge", "Value"], [
        ["Balance-sheet charge (on leverage exposure)", pct(ch.balance_sheet_charge, 2) + " per year"],
        ["Capital ratio x hurdle (on RWA)", f"{pct(ch.capital_ratio, 0)} x {pct(ch.hurdle_rate, 0)} = "
                                            f"{pct(ch.capital_ratio * ch.hurdle_rate, 2)} per year"],
        ["Liquidity charge (on liquidity usage)", pct(ch.liquidity_charge, 2) + " per year"],
        ["Normal rate vol for EPE / IM proxies", bp(ch.irs_vol_bp, 0) + " per year"],
        ["MPOR: repo / bilateral derivatives / CCP", f"{ch.repo_mpor_days} / {ch.bilateral_mpor_days} / {ch.ccp_mpor_days} days"],
        ["Unsecured recovery at close-out", pct(ch.unsecured_recovery, 0)],
        ["PD multiplier in market-stress credit losses", f"{ch.stress_pd_multiplier:g}x"],
    ])
    ctx["limits_cfg_table"] = table(["Limit", "Value"], [
        ["Total balance sheet (leverage exposure)", usd(lm.balance_sheet)],
        ["Flow reserve kept out of the optimizer", pct(lm.flow_reserve, 0)],
        ["Balance sheet per counterparty", usd(lm.single_cpty_bs)],
        ["Gross exposure per collateral class", ", ".join(f"{k} {usd(v)}" for k, v in lm.collateral_class_cap.items())],
        ["HQLA pool / minimum buffer", f"{usd(lm.hqla_pool)} / {usd(lm.min_liquidity_buffer)}"],
        ["Financing against non-HQLA collateral", usd(lm.non_hqla_financing)],
        ["Balance sheet in trades > 1 year", usd(lm.term_balance_sheet)],
        ["Weighted-average maturity", f"{lm.max_wam_years:g} years"],
        ["DV01", usd(lm.dv01_limit, "k", 0) + " per bp"],
        ["Sum of standalone combined-stress losses", usd(lm.stress_loss_limit)],
        ["Repo inventory available", ", ".join(f"{k} {usd(v)}" for k, v in lm.repo_inventory.items())],
        ["Gate: min net income / min RoBS / min return on RWA", f"$0 / {gt.min_robs_bp:g}bp / {pct(gt.min_rorwa, 1)}"],
        ["Gate: single-trade stress loss cap", pct(gt.max_single_stress_share, 0) + " of the desk stress limit"],
    ])
    root = Path(__file__).resolve().parents[2]
    n_tests = sum(len(re.findall(r"^def test_", f.read_text(encoding="utf-8"), re.M))
                  for f in (root / "tests").glob("test_*.py"))
    ctx["n_tests"] = str(n_tests)
    ctx["sofr_quotes"] = ", ".join(f"{t} {r * 100:.2f}%" for t, r in config.SOFR_QUOTES)
    ctx["ftp_spreads"] = ", ".join(f"{_tenor_label(t)} {b:g}bp" for t, b in
                                   zip(config.FUNDING_SPREAD_TENORS, config.FUNDING_SPREAD_BPS))
    return ctx


def _tenor_label(t: float) -> str:
    if t < 1 / 52:
        return "ON"
    if t < 1:
        return f"{round(t * 12)}M"
    return f"{t:g}Y"


def _fmt_limit(name: str, v: float) -> str:
    if name.startswith("wam"):
        return f"{v:.2f}y"
    if name.startswith("dv01"):
        return usd(v, "k", 0)
    return usd(v)


def render(template: str, ctx: dict) -> str:
    def sub(m):
        k = m.group(1)
        if k not in ctx:
            raise KeyError(f"unresolved template key {{{{{k}}}}}")
        return ctx[k]
    return re.sub(r"\{\{([a-zA-Z0-9_]+)\}\}", sub, template)


def render_all(root: Path, summary: dict) -> None:
    ctx = build_context(summary)
    tdir = root / "docs" / "templates"
    for src, dst in TEMPLATES.items():
        p = tdir / src
        if p.exists():
            text = render(p.read_text(encoding="utf-8"), ctx)
            (root / dst).write_text(text, encoding="utf-8", newline="\n")
