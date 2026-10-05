"""Figures. Static PNGs for the README; every number plotted comes from the run."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

# Validated categorical order (blue, orange, aqua, yellow, magenta, green, violet, red)
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#ffffff"
NEG, POS, NEUTRAL = "#e34948", "#2a78d6", "#b8b7b0"

PRODUCT_COLOR = {"reverse_repo": C[0], "repo": C[2], "trs": C[1], "irs": C[6]}
PRODUCT_LABEL = {"reverse_repo": "Reverse repo", "repo": "Repo", "trs": "Equity TRS", "irs": "IRS"}


def style():
    plt.rcParams.update({
        "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
        "axes.edgecolor": GRID, "axes.linewidth": 0.8, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 10,
        "axes.labelsize": 10, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "xtick.major.size": 0, "ytick.major.size": 0, "legend.frameon": False, "legend.fontsize": 9,
        "font.family": "DejaVu Sans", "lines.linewidth": 2.0, "figure.dpi": 110, "savefig.dpi": 150,
        "axes.axisbelow": True,
    })
    if "text.parse_math" in plt.rcParams:
        plt.rcParams["text.parse_math"] = False  # "$" in labels is currency, not mathtext


def _save(fig, path: Path, note: str = "Simulated book · synthetic market data"):
    fig.text(0.995, 0.005, note, ha="right", va="bottom", fontsize=7.5, color=MUTED)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


bn = FuncFormatter(lambda v, _: f"${v / 1e9:,.0f}bn" if v == 0 or abs(v / 1e9 - round(v / 1e9)) < 1e-9
                   else f"${v / 1e9:,.1f}bn")


def money(v: float) -> str:
    return ("-" if v < 0 else "") + f"${abs(v):,.1f}mm"
mm = FuncFormatter(lambda v, _: f"${v / 1e6:,.0f}mm")
pct = FuncFormatter(lambda v, _: f"{v:.2f}%")


# ----------------------------------------------------------------------------- curves
def funding_curve(curve_rows: list[dict], repo_rows: list[dict], quotes: list[dict], path: Path):
    fig, ax = plt.subplots(figsize=(9, 4.8))
    t = [r["t"] for r in curve_rows]
    ax.plot(t, [r["zero"] * 100 for r in curve_rows], color=C[0], label="SOFR zero rate (cont.)")
    ax.plot(t, [r["fwd_3m"] * 100 for r in curve_rows], color=C[0], alpha=0.45, lw=1.5,
            label="SOFR 3M forward")
    ax.plot(t, [r["ftp_zero"] * 100 for r in curve_rows], color=C[1], label="Unsecured funding (SOFR zero + FTP spread)")
    ax.scatter([q["t"] for q in quotes], [q["rate"] * 100 for q in quotes], s=28, color=C[0], zorder=5,
               edgecolor=SURF, linewidth=1.5, label="SOFR OIS par quotes (inputs)")
    for i, (key, lab) in enumerate([("UST", "Repo: Treasuries"), ("IG_CORP", "Repo: IG credit"),
                                    ("EQ_SINGLE", "Repo: single-name equity")]):
        rr = [r for r in repo_rows if r["collateral"] == key]
        ax.plot([r["t"] for r in rr], [r["rate"] * 100 for r in rr], color=C[[2, 3, 4][i]], lw=1.5,
                label=lab)
    ax.set_xscale("symlog", linthresh=1.0)
    ax.set_xticks([0, 0.25, 0.5, 1, 2, 5, 10, 30])
    ax.set_xticklabels(["0", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "30Y"])
    ax.yaxis.set_major_formatter(pct)
    ax.set_xlabel("Maturity")
    ax.set_title("Funding curves: what it costs the desk to borrow, by tenor and collateral")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3)
    _save(fig, path)


# ----------------------------------------------------------------------------- teaching figures
def repo_lifecycle(rows: list[dict], path: Path):
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    d = [r["date"] for r in rows]
    ax.plot(d, [r["collateral_mv_before_call"] / 1e6 for r in rows], color=C[0], lw=1.4, alpha=0.5,
            label="Collateral value before margin call")
    ax.plot(d, [r["collateral_mv"] / 1e6 for r in rows], color=C[0], label="Collateral value held (after calls)")
    ax.plot(d, [r["required_collateral"] / 1e6 for r in rows], color=C[1], lw=1.5,
            label="Required = (cash + accrued interest) / (1 - haircut)")
    ax.plot(d, [r["loan_balance"] / 1e6 for r in rows], color=C[6], label="Cash lent + accrued interest")
    ax.set_ylabel("$mm")
    ax.set_title("Repo lifecycle: $100mm Treasuries, 2% haircut, daily margin")
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.11))
    calls = [r["margin_call"] / 1e6 for r in rows]
    ax2.bar(d, calls, color=[POS if c > 0 else NEG for c in calls], width=0.8)
    ax2.axhline(0, color=INK2, lw=0.8)
    ax2.set_ylabel("Margin call, $mm")
    ax2.text(0.01, 0.92, "Blue: client delivers collateral · Red: excess returned to client", transform=ax2.transAxes,
             fontsize=8, color=INK2, va="top")
    import matplotlib.dates as mdates
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    _save(fig, path)


def irs_cashflows(ex: dict, path: Path):
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2), gridspec_kw={"width_ratios": [1.6, 1]})
    fl, fx = ex["float_leg"], ex["fixed_leg"]
    yrs = np.arange(1, len(fx) + 1)
    w = 0.38
    ax.bar(yrs - w / 2, [r["amount"] / 1e6 for r in fl], w, color=C[0], label="Floating (SOFR) leg: receive")
    ax.bar(yrs + w / 2, [-r["amount"] / 1e6 for r in fx], w, color=C[1], label="Fixed leg: pay")
    ax.axhline(0, color=INK2, lw=0.8)
    for y, r in zip(yrs, fl):
        ax.text(y - w / 2, r["amount"] / 1e6 + 0.05, f"{r['rate'] * 100:.2f}%", ha="center", fontsize=7.5, color=INK2)
    ax.set_xticks(yrs)
    ax.set_xticklabels([f"Y{y}" for y in yrs])
    ax.set_ylabel("Cash flow, $mm")
    ax.set_title(f"5Y payer swap at par ({ex['par_rate'] * 100:.3f}%): projected cash flows")
    ax.legend(loc="lower left")
    ax2.plot([r["t"] for r in fx], [r["df"] for r in fx], color=C[0], marker="o", ms=5)
    ax2.set_ylim(min(r["df"] for r in fx) - 0.02, 1.0)
    ax2.set_title("Discount factors at payment dates")
    ax2.set_xlabel("Years")
    _save(fig, path)


def trs_lifecycle(ex: dict, path: Path):
    rows = ex["rows"]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    labels = [r["end"].strftime("%b-%y") for r in rows]
    x = np.arange(len(rows))
    eq = np.array([r["equity_leg"] - r["dividends"] * ex["trade"].shares for r in rows]) / 1e6
    dv = np.array([r["dividends"] * ex["trade"].shares for r in rows]) / 1e6
    fin = -np.array([r["financing_leg"] for r in rows]) / 1e6
    for vals, col, lab, off in [(eq, C[0], "Price return", -0.27), (dv, C[2], "Dividends", 0.0),
                                (fin, C[1], "Financing (SOFR + spread)", 0.27)]:
        ax.bar(x + off, vals, 0.25, color=col, label=lab)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("$mm per reset (client view)")
    ax.set_title("Client cash flows per quarterly reset")
    ax.legend(loc="best")
    desk_swap = np.cumsum([r["desk_swap_pnl"] for r in rows]) / 1e6
    desk_hedge = np.cumsum([r["desk_hedge_pnl"] for r in rows]) / 1e6
    desk_net = np.cumsum([r["desk_net"] for r in rows]) / 1e6
    ax2.plot(x, desk_swap, color=C[0], label="Swap leg (pays total return)")
    ax2.plot(x, desk_hedge, color=C[1], label="Stock hedge, net of funding")
    ax2.plot(x, desk_net, color=INK, lw=2.4, label="Desk net = spread - hedge funding")
    ax2.axhline(0, color=INK2, lw=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("Cumulative $mm (desk view)")
    ax2.set_title(f"Desk view: hedged, keeps ${desk_net[-1] * 1e3:,.0f}k")
    ax2.legend(loc="best")
    _save(fig, path)


def economics_waterfall(examples: list, path: Path):
    """examples: list of (title, TradeMetrics)."""
    fig, axes = plt.subplots(1, len(examples), figsize=(3.4 * len(examples), 4.0), sharey=False)
    steps = [("Revenue", "revenue", 1), ("Funding", "funding_cost", -1), ("BS charge", "bs_charge", -1),
             ("Capital", "capital_charge", -1), ("Liquidity", "liquidity_charge", -1),
             ("CP cost", "expected_cp_cost", -1)]
    for ax, (title, m) in zip(np.atleast_1d(axes), examples):
        # Revenue and funding are large and nearly offset; start from net interest to keep the scale readable.
        nim = m.revenue - m.funding_cost
        vals = [("Revenue -\nfunding", nim)] + [(lab, sgn * getattr(m, f)) for lab, f, sgn in steps[2:]]
        level = 0.0
        for i, (_lab, v) in enumerate(vals):
            bottom = level if v >= 0 else level + v
            ax.bar(i, abs(v) / 1e3, bottom=bottom / 1e3, color=POS if v >= 0 else NEG, width=0.7)
            level += v
        ax.bar(len(vals), m.net_income / 1e3, color=INK if m.net_income >= 0 else NEG, width=0.7)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.set_xticks(range(len(vals) + 1))
        ax.set_xticklabels([v[0] for v in vals] + ["Net"], rotation=45, ha="right", fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.set_ylabel("$k per year")
    fig.suptitle("Where a trade's money goes (one example of each product, $k/yr)", x=0.01, ha="left",
                 fontweight="bold")
    fig.tight_layout()
    _save(fig, path)


# ----------------------------------------------------------------------------- book
def balance_sheet_usage(stacks: dict, limit: float, planning: float, path: Path):
    """stacks: {label: {component: value}}"""
    comps = [("reverse_repo", "Reverse repo receivables (net)"), ("trs", "TRS hedge stock"),
             ("ccr", "Counterparty add-ons (SFT E*, SA-CCR)")]
    colors = [C[0], C[1], C[6]]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    labels = list(stacks)
    y = np.arange(len(labels))
    left = np.zeros(len(labels))
    for (k, lab), col in zip(comps, colors):
        v = np.array([stacks[s].get(k, 0.0) for s in labels])
        ax.barh(y, v, left=left, color=col, label=lab, height=0.55, edgecolor=SURF, linewidth=2)
        left += v
    for yi, s in zip(y, labels):
        ax.text(left[yi] + 1.5e8, yi, f"${left[yi] / 1e9:,.1f}bn" + (
            f"  (netting saved ${stacks[s]['netting'] / 1e9:,.1f}bn)" if stacks[s].get("netting") else ""),
            va="center", fontsize=8.5, color=INK2)
    ax.axvline(limit, color=NEG, lw=1.5)
    ax.text(limit, len(labels) - 0.45, " limit", color=NEG, fontsize=8.5, va="bottom")
    ax.axvline(planning, color=INK2, lw=1.0)
    ax.text(planning, len(labels) - 0.45, "allocation cap (10% flow reserve) ", color=INK2, fontsize=8.5,
            va="bottom", ha="right")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(bn)
    ax.set_xlim(0, limit * 1.32)
    ax.set_title("Balance-sheet usage (leverage exposure) by allocation")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3)
    _save(fig, path)


def counterparty_exposure(exp: dict, labels: dict, path: Path):
    cps = sorted(exp, key=lambda k: -exp[k]["gross"])
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.8), sharey=True)
    y = np.arange(len(cps))
    ax.barh(y - 0.2, [exp[c]["gross"] for c in cps], 0.38, color=C[0], label="Gross (no netting, no collateral)")
    ax.barh(y + 0.2, [exp[c]["net_after_collateral"] for c in cps], 0.38, color=C[1],
            label="Net of netting and collateral")
    ax.set_yticks(y)
    ax.set_yticklabels([labels.get(c, c) for c in cps], fontsize=8.5)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(bn)
    for yi, c in zip(y, cps):
        ax.text(max(exp[c]["net_after_collateral"], 0) + 1.5e8, yi + 0.2,
                f"${exp[c]['net_after_collateral'] / 1e6:,.0f}mm", va="center", fontsize=7.5, color=INK2)
    ax.set_title("Current exposure: why collateral matters")
    ax.legend(loc="lower right")
    util = [exp[c]["ead"] / exp[c]["ead_limit"] * 100 for c in cps]
    ax2.barh(y, util, 0.55, color=[NEG if u > 100 else C[6] for u in util])
    ax2.axvline(100, color=NEG, lw=1.2)
    for yi, c, u in zip(y, cps, util):
        ax2.text(u + 1, yi, f"${exp[c]['ead'] / 1e6:,.0f}mm of ${exp[c]['ead_limit'] / 1e6:,.0f}mm", va="center",
                 fontsize=7.5, color=INK2)
    ax2.set_xlim(0, max(110, max(util) * 1.45))
    ax2.set_xlabel("% of counterparty EAD limit")
    ax2.set_title("Exposure-at-default vs. credit limit")
    _save(fig, path)


def stress_pnl(rows: list[dict], path: Path):
    labels = [r["label"] for r in rows]
    comps = [("mtm_pnl", "Mark-to-market", C[0]), ("nii_1y", "1-year net income", C[1]),
             ("credit", "Credit / close-out", C[6])]
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    y = np.arange(len(rows))
    pos_left, neg_left = np.zeros(len(rows)), np.zeros(len(rows))
    for key, lab, col in comps:
        v = np.array([r[key] for r in rows]) / 1e6
        left = np.where(v >= 0, pos_left, neg_left)
        ax.barh(y, v, left=left, color=col, label=lab, height=0.6, edgecolor=SURF, linewidth=1.5)
        pos_left += np.where(v >= 0, v, 0)
        neg_left += np.where(v < 0, v, 0)
    for yi, r in zip(y, rows):
        tot = r["total"] / 1e6
        ax.text(min(neg_left[yi], 0) - 4, yi, money(tot), va="center", ha="right", fontsize=8.5, color=INK)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("P&L impact, $mm")
    ax.set_xlim(neg_left.min() * 1.25 - 20, max(pos_left.max() * 1.2, 15))
    ax.set_title("Stress P&L of the optimised book")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)
    _save(fig, path)


def default_losses(rows: list[dict], labels: dict, path: Path):
    rows = sorted(rows, key=lambda r: -r["loss"])
    fig, ax = plt.subplots(figsize=(9, 4.2))
    y = np.arange(len(rows))
    f = [r["loss"] / (r["sft_shortfall"] + r["deriv_shortfall"]) if r["loss"] > 0 else 0.0 for r in rows]
    sft = [r["sft_shortfall"] * k / 1e6 for r, k in zip(rows, f)]
    ax.barh(y, sft, 0.6, color=C[0], label="Repo netting set")
    ax.barh(y, [r["deriv_shortfall"] * k / 1e6 for r, k in zip(rows, f)], 0.6, left=sft, color=C[1],
            label="Derivative netting set")
    ax.set_yticks(y)
    ax.set_yticklabels([labels.get(r["cpty"], r["cpty"]) for r in rows], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("Loss after unsecured recovery, $mm")
    ax.set_title("Close-out loss if each counterparty defaulted today (base market)")
    ax.legend(loc="lower right")
    _save(fig, path)


def robs_frontier(front: list[dict], greedy_pts: list[dict], naive_pts: list[dict], current: dict, path: Path):
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    bs = [f["balance_sheet"] for f in front]
    ax.plot(bs, [f["net_income"] / 1e6 for f in front], color=C[0], marker="o", ms=4, label="LP optimum (frontier)")
    ax.plot([g["balance_sheet"] for g in greedy_pts], [g["net_income"] / 1e6 for g in greedy_pts], color=C[2],
            marker="s", ms=4, lw=1.5, label="Greedy by return on balance sheet")
    ax.plot([g["balance_sheet"] for g in naive_pts], [g["net_income"] / 1e6 for g in naive_pts], color=C[1],
            marker="^", ms=4, lw=1.5, label="Naive: biggest revenue first")
    ax.scatter([current["balance_sheet"]], [current["net_income"] / 1e6], s=70, color=INK, zorder=6,
               edgecolor=SURF, linewidth=2, label="Chosen allocation")
    ax.xaxis.set_major_formatter(bn)
    ax.set_xlabel("Balance sheet used")
    ax.set_ylabel("Desk net income, $mm/yr")
    ax.set_title("Net income vs. balance sheet")
    ax.legend(loc="upper left")
    ax2.plot(bs, [f["robs_bp"] for f in front], color=C[0], marker="o", ms=4, label="Average RoBS (book)")
    ax2.plot(bs, [f["shadow_bs_bp"] for f in front], color=C[6], marker="o", ms=4,
             label="Marginal value of $1 more balance sheet")
    ax2.set_xlabel("Balance sheet used")
    ax2.set_ylabel("basis points per year")
    ax2.xaxis.set_major_formatter(bn)
    ax2.set_title("Average vs. marginal return on balance sheet")
    ax2.legend(loc="upper right")
    _save(fig, path)


def haircut_stress(rows: list[dict], capacity: float, path: Path):
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    mult = [r["mult"] for r in rows]
    ax.plot(mult, [r["liquidity_usage"] for r in rows], color=C[0], marker="o", ms=4, label="Liquidity usage")
    ax.plot(mult, [r["unsecured_funding"] for r in rows], color=C[1], marker="o", ms=4,
            label="Unsecured funding needed")
    ax.axhline(capacity, color=NEG, lw=1.2)
    ax.text(mult[0], capacity, " liquidity capacity (HQLA pool - minimum buffer)", color=NEG, fontsize=8,
            va="bottom")
    ax.yaxis.set_major_formatter(bn)
    ax.set_xlabel("Street haircut multiplier (x base)")
    ax.set_title("Haircuts up: the desk funds the gap")
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.92))
    ax2.plot(mult, [r["net_income_change"] / 1e6 for r in rows], color=C[6], marker="o", ms=4)
    ax2.axhline(0, color=INK2, lw=0.8)
    ax2.set_xlabel("Street haircut multiplier (x base)")
    ax2.set_ylabel("$mm per year")
    ax2.set_title("Change in desk net income, $mm/yr")
    _save(fig, path)


def allocation(cands: list, allocs: dict, path: Path):
    order = sorted(range(len(cands)), key=lambda i: -cands[i].robs)
    names = [("Naive: biggest revenue first", C[1]), ("Greedy by return on balance sheet", C[2]),
             ("LP optimizer", C[0])]
    fig, axes = plt.subplots(3, 1, figsize=(11, 6.2), sharex=True)
    x = np.arange(len(order))
    for ax, (lab, col), xs in zip(axes, names, allocs.values()):
        v = [xs[i] * 100 for i in order]
        ax.bar(x, v, 0.7, color=col)
        ax.set_ylim(0, 105)
        ax.set_yticks([0, 50, 100])
        ax.set_ylabel("% filled", fontsize=8.5)
        ax.set_title(f"{lab}  ({sum(1 for a in v if a > 0.01)} requests filled)", fontsize=10)
    axes[-1].set_xticks(x)
    axes[-1].set_xticklabels([cands[i].trade_id for i in order], rotation=90, fontsize=7.5)
    axes[-1].set_xlabel("Client requests, sorted left to right by standalone return on balance sheet")
    fig.suptitle("Which client requests get balance sheet", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    _save(fig, path)


def keyrate_dv01(krd: dict, path: Path):
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ks = list(krd)
    v = [krd[k] / 1e3 for k in ks]
    ax.bar(ks, v, color=[POS if a >= 0 else NEG for a in v], width=0.6)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_ylabel("$k per +1bp")
    ax.set_title(f"Key-rate DV01 of the book (sum ${sum(v):,.0f}k per bp): where the curve risk sits")
    _save(fig, path)
