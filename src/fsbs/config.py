"""Every market input and desk assumption in one place.

ALL MARKET DATA HERE IS SYNTHETIC. The SOFR curve, spreads, haircuts,
volatilities, equity prices and counterparties are illustrative values chosen
to have realistic shape and magnitude; they are not market observations and
not any institution's actual parameters. See MODEL_ASSUMPTIONS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

VALUATION_DATE = date(2026, 10, 1)  # a Thursday

# SOFR OIS par quotes (synthetic): mildly inverted front end, upward-sloping belly.
SOFR_QUOTES: list[tuple[str, float]] = [
    ("ON", 0.0385), ("1M", 0.0383), ("3M", 0.0376), ("6M", 0.0364), ("9M", 0.0354),
    ("1Y", 0.0346), ("2Y", 0.0334), ("3Y", 0.0332), ("4Y", 0.0336), ("5Y", 0.0341),
    ("7Y", 0.0352), ("10Y", 0.0366), ("15Y", 0.0381), ("20Y", 0.0387), ("30Y", 0.0379),
]

# Unsecured funding (FTP) spread over SOFR, by term in years (synthetic).
FUNDING_SPREAD_TENORS = [1 / 365, 1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
FUNDING_SPREAD_BPS = [12.0, 30.0, 40.0, 50.0, 62.0, 80.0, 95.0, 112.0, 135.0]


@dataclass(frozen=True)
class CollateralClass:
    key: str
    label: str
    street_haircut: float  # haircut the desk receives when it re-finances this collateral
    internal_min_haircut: float  # minimum client haircut the desk will accept
    supervisory_haircut: float  # comprehensive-approach style haircut for EAD (simplified)
    street_spread_bp: float  # desk's secured funding cost over SOFR (repo-ing it out)
    client_spread_bp: float  # typical client financing spread over SOFR
    annual_vol: float  # price volatility used for margin-period-of-risk moves
    hqla: str  # L1 / L2A / L2B / NONE (LCR-style liquidity tier, simplified)
    runoff: float  # liquidity weight on secured funding against this collateral
    liquidation_cost: float  # cost to liquidate in a close-out, fraction of MV
    stress_move: float  # collateral price move in the "collateral -15%" family of shocks


COLLATERAL: dict[str, CollateralClass] = {c.key: c for c in [
    CollateralClass("UST", "US Treasuries", 0.02, 0.01, 0.02, 0.0, 12.0, 0.05, "L1", 0.00, 0.001, -0.03),
    CollateralClass("AGY_MBS", "Agency MBS", 0.03, 0.02, 0.04, 8.0, 22.0, 0.06, "L2A", 0.15, 0.003, -0.15),
    CollateralClass("IG_CORP", "IG corporate bonds", 0.06, 0.05, 0.04, 30.0, 65.0, 0.08, "L2B", 0.15, 0.010, -0.15),
    CollateralClass("HY_CORP", "HY corporate bonds", 0.12, 0.10, 0.25, 55.0, 150.0, 0.15, "NONE", 0.40, 0.030, -0.15),
    CollateralClass("EQ_INDEX", "Main-index equities", 0.12, 0.10, 0.20, 25.0, 95.0, 0.20, "L2B", 0.15, 0.005, -0.20),
    CollateralClass("EQ_SINGLE", "Single-name equities", 0.18, 0.15, 0.30, 45.0, 140.0, 0.35, "NONE", 0.40, 0.015, -0.20),
]}


@dataclass(frozen=True)
class Underlying:
    key: str
    label: str
    spot: float
    vol: float
    div_yield: float
    collateral: str  # which collateral class the stock/index finances as
    is_index: bool


UNDERLYINGS: dict[str, Underlying] = {u.key: u for u in [
    Underlying("SIM_IDX", "Synthetic large-cap index", 5800.0, 0.18, 0.014, "EQ_INDEX", True),
    Underlying("SIM_TECH", "Synthetic tech single name", 240.0, 0.38, 0.004, "EQ_SINGLE", False),
    Underlying("SIM_BANK", "Synthetic bank single name", 62.0, 0.30, 0.031, "EQ_SINGLE", False),
    Underlying("SIM_ENERGY", "Synthetic energy single name", 88.0, 0.32, 0.038, "EQ_SINGLE", False),
    Underlying("SIM_PHARMA", "Synthetic pharma single name", 145.0, 0.26, 0.022, "EQ_SINGLE", False),
]}


@dataclass(frozen=True)
class Counterparty:
    key: str
    label: str
    kind: str  # hedge_fund | mmf | pension | insurer | corporate | dealer | ccp
    rating: str
    pd: float  # 1y probability of default
    lgd: float  # loss given default on unsecured shortfall
    risk_weight: float  # standardised-approach-style counterparty risk weight
    netting: bool  # enforceable close-out netting (master agreement + legal opinion)
    csa: bool  # derivatives margined with daily VM
    ead_limit: float  # credit limit on exposure-at-default
    nav: float | None = None  # client equity, for leverage limits (funds only)
    max_leverage: float | None = None  # gross financed exposure / NAV


COUNTERPARTIES: dict[str, Counterparty] = {c.key: c for c in [
    Counterparty("HF_MULTI_A", "Multi-strategy hedge fund A", "hedge_fund", "NR", 0.010, 0.60, 1.00, True, True, 1.40e9, 9.0e9, 4.0),
    Counterparty("HF_EQLS_B", "Equity long/short fund B", "hedge_fund", "NR", 0.015, 0.60, 1.00, True, True, 0.45e9, 0.6e9, 3.0),
    Counterparty("HF_RV_C", "Fixed-income relative-value fund C", "hedge_fund", "NR", 0.012, 0.60, 1.00, True, True, 0.40e9, 1.0e9, 12.0),
    Counterparty("HF_MACRO_D", "Global macro fund D", "hedge_fund", "NR", 0.010, 0.60, 1.00, True, True, 0.50e9, 4.0e9, 4.0),
    Counterparty("MMF_GOVT_E", "Government money-market fund E", "mmf", "AAA", 0.0003, 0.45, 0.50, True, True, 0.40e9),
    Counterparty("PENSION_F", "Public pension plan F", "pension", "AA", 0.0010, 0.45, 0.50, True, True, 0.50e9),
    Counterparty("INSURER_G", "Life insurer G", "insurer", "A", 0.0020, 0.45, 0.50, True, True, 0.40e9),
    Counterparty("CORP_H", "BBB industrial corporate H", "corporate", "BBB", 0.0040, 0.60, 0.75, True, False, 0.25e9),
    Counterparty("DEALER_I", "A-rated dealer I", "dealer", "A", 0.0008, 0.45, 0.30, True, True, 0.70e9),
    Counterparty("CCP_J", "Qualifying central counterparty J", "ccp", "AA", 0.0001, 0.45, 0.02, True, True, 5.00e9),
]}


@dataclass(frozen=True)
class DeskCharges:
    """Internal transfer prices applied to every trade (annual rates)."""

    balance_sheet_charge: float = 0.0012  # on leverage exposure (12bp/yr)
    capital_ratio: float = 0.11  # capital held against RWA
    hurdle_rate: float = 0.10  # cost of that capital
    liquidity_charge: float = 0.0050  # on liquidity usage (cost of carrying HQLA, 50bp/yr)
    sacc_alpha: float = 1.4  # SA-CCR alpha
    irs_vol_bp: float = 85.0  # normal rate vol (bp/yr) for EPE/IM proxies
    ccp_im_conf_z: float = 2.326  # 99% one-sided
    ccp_mpor_days: int = 5
    bilateral_mpor_days: int = 10
    repo_mpor_days: int = 5
    unsecured_recovery: float = 0.40  # recovery on unsecured shortfall at close-out
    stress_pd_multiplier: float = 5.0  # conditional PD in market-stress credit losses


@dataclass(frozen=True)
class Limits:
    balance_sheet: float = 22.0e9  # total leverage exposure
    single_cpty_bs: float = 9.0e9  # max balance sheet attributable to one counterparty
    collateral_class_cap: dict = field(default_factory=lambda: {  # gross on-balance exposure by class
        "AGY_MBS": 7.0e9, "IG_CORP": 5.0e9, "HY_CORP": 2.0e9, "EQ_INDEX": 5.0e9, "EQ_SINGLE": 3.0e9})
    flow_reserve: float = 0.10  # share of balance sheet and liquidity capacity the optimizer leaves for flow
    hqla_pool: float = 3.5e9  # HQLA buffer held by the desk
    min_liquidity_buffer: float = 1.5e9  # buffer that must remain after liquidity usage
    non_hqla_financing: float = 4.0e9  # cash lent against HY / single-name collateral (incl. TRS hedges)
    term_balance_sheet: float = 9.0e9  # balance sheet in trades with > 1y remaining
    max_wam_years: float = 1.6  # balance-sheet-weighted average maturity
    dv01_limit: float = 400_000.0  # |desk DV01| in $ per bp
    stress_loss_limit: float = 1.5e9  # sum of standalone combined-stress losses
    repo_inventory: dict = field(default_factory=lambda: {  # desk inventory available to repo out (MV)
        "UST": 9.0e9, "AGY_MBS": 3.0e9, "IG_CORP": 0.8e9})


@dataclass(frozen=True)
class GateThresholds:
    min_net_income: float = 0.0  # $ per year after all charges
    min_robs_bp: float = 5.0  # net income / balance sheet
    min_rorwa: float = 0.01  # net income / RWA, on top of the capital charge already at hurdle
    max_single_stress_share: float = 0.15  # one trade's standalone stress loss / desk stress limit


CHARGES = DeskCharges()
LIMITS = Limits()
GATE = GateThresholds()
