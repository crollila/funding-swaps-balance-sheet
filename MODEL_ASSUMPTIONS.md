# Model assumptions

Everything a reviewer should know before trusting a number from this repository. All parameter tables below are rendered from `src/fsbs/config.py`, so they always match the code.

## Data provenance

- **All market data is synthetic.** SOFR quotes, funding spreads, repo spreads, haircuts, volatilities, equity levels and dividends are illustrative values with realistic shape and magnitude. They are not observations and not any institution's parameters.
- **The book is simulated.** Counterparties are fictional archetypes ("multi-strategy hedge fund A"); trades, client requests and proposals are generated in `src/fsbs/book.py` (requests with a fixed seed). No real client, deal or position is represented.

## Conventions

- Valuation date 2026-10-01; weekends-only calendar (no holidays); modified-following adjustment.
- Money-market accrual ACT/360; curve time ACT/365F. Annual dollar figures multiply ACT/360 rates by 365/360.
- Desk perspective throughout: positive value / P&L is good for the desk. `reverse` repo = desk lends cash; `repo` = desk borrows cash.

## Curves

- SOFR OIS quotes: ON 3.85%, 1M 3.83%, 3M 3.76%, 6M 3.64%, 9M 3.54%, 1Y 3.46%, 2Y 3.34%, 3Y 3.32%, 4Y 3.36%, 5Y 3.41%, 7Y 3.52%, 10Y 3.66%, 15Y 3.81%, 20Y 3.87%, 30Y 3.79%.
- Spot-starting (no T+2 lag), no payment lag, annual fixed payments beyond one year, single period up to one year.
- Single-curve: SOFR projects and discounts. Log-linear interpolation on discount factors (flat forwards), flat-forward extrapolation.
- Unsecured FTP spread over SOFR by term: ON 12bp, 1M 30bp, 3M 40bp, 6M 50bp, 1Y 62bp, 2Y 80bp, 3Y 95bp, 5Y 112bp, 10Y 135bp; linear interpolation, flat beyond 10Y.
- Secured repo rate = SOFR term rate + a spread per collateral class (below).

## Collateral

| Class | Street haircut | Min client haircut | Supervisory haircut | Street spread | Typical client spread | Vol | Liquidity tier | Runoff weight | Liquidation cost |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| US Treasuries | 2% | 1% | 2% | 0bp | 12bp | 5% | L1 | 0.00 | 0.1% |
| Agency MBS | 3% | 2% | 4% | 8bp | 22bp | 6% | L2A | 0.15 | 0.3% |
| IG corporate bonds | 6% | 5% | 4% | 30bp | 65bp | 8% | L2B | 0.15 | 1.0% |
| HY corporate bonds | 12% | 10% | 25% | 55bp | 150bp | 15% | NONE | 0.40 | 3.0% |
| Main-index equities | 12% | 10% | 20% | 25bp | 95bp | 20% | L2B | 0.15 | 0.5% |
| Single-name equities | 18% | 15% | 30% | 45bp | 140bp | 35% | NONE | 0.40 | 1.5% |

- Street haircut: what lenders apply when the desk re-finances collateral. Minimum client haircut: the eligibility floor (requests below it are ineligible). Supervisory haircut: simplified comprehensive-approach haircut used for SFT exposure. Runoff weight: share of secured funding counted as liquidity usage (LCR-inspired, simplified).
- Margin is assumed current at the valuation date (collateral value = required collateral) for book trades; the worked example simulates daily margining explicitly.

## Counterparties

| Counterparty (fictional archetype) | Type | PD (1y) | LGD | Risk weight | CSA | EAD limit | NAV / max leverage |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Multi-strategy hedge fund A | hedge_fund | 1.00% | 60% | 100% | yes | $1,400mm | $9.0bn / 4x |
| Equity long/short fund B | hedge_fund | 1.50% | 60% | 100% | yes | $450mm | $0.6bn / 3x |
| Fixed-income relative-value fund C | hedge_fund | 1.20% | 60% | 100% | yes | $400mm | $1.0bn / 12x |
| Global macro fund D | hedge_fund | 1.00% | 60% | 100% | yes | $500mm | $4.0bn / 4x |
| Government money-market fund E | mmf | 0.03% | 45% | 50% | yes | $400mm | - |
| Public pension plan F | pension | 0.10% | 45% | 50% | yes | $500mm | - |
| Life insurer G | insurer | 0.20% | 45% | 50% | yes | $400mm | - |
| BBB industrial corporate H | corporate | 0.40% | 60% | 75% | no | $250mm | - |
| A-rated dealer I | dealer | 0.08% | 45% | 30% | yes | $700mm | - |
| Qualifying central counterparty J | ccp | 0.01% | 45% | 2% | yes | $5,000mm | - |

PDs, LGDs and risk weights are illustrative standardised-approach-style values. "Max leverage" bounds each fund's gross financed position as a multiple of its NAV.

## Desk charges and limits

| Charge | Value |
| :--- | ---: |
| Balance-sheet charge (on leverage exposure) | 0.12% per year |
| Capital ratio x hurdle (on RWA) | 11% x 10% = 1.10% per year |
| Liquidity charge (on liquidity usage) | 0.50% per year |
| Normal rate vol for EPE / IM proxies | 85bp per year |
| MPOR: repo / bilateral derivatives / CCP | 5 / 10 / 5 days |
| Unsecured recovery at close-out | 40% |
| PD multiplier in market-stress credit losses | 5x |

| Limit | Value |
| :--- | ---: |
| Total balance sheet (leverage exposure) | $22.0bn |
| Flow reserve kept out of the optimizer | 10% |
| Balance sheet per counterparty | $9.0bn |
| Gross exposure per collateral class | AGY_MBS $7.0bn, IG_CORP $5.0bn, HY_CORP $2.0bn, EQ_INDEX $5.0bn, EQ_SINGLE $3.0bn |
| HQLA pool / minimum buffer | $3.5bn / $1.5bn |
| Financing against non-HQLA collateral | $4.0bn |
| Balance sheet in trades > 1 year | $9.0bn |
| Weighted-average maturity | 1.6 years |
| DV01 | $400k per bp |
| Sum of standalone combined-stress losses | $1.5bn |
| Repo inventory available | UST $9.0bn, AGY_MBS $3.0bn, IG_CORP $800.0mm |
| Gate: min net income / min RoBS / min return on RWA | $0 / 5bp / 1.0% |
| Gate: single-trade stress loss cap | 15% of the desk stress limit |

## Instrument modelling

- **Repo:** fixed rate, simple interest, haircut convention `cash = MV × (1 − h)`. Term repos are marked by discounting the repurchase price at today's term client rate for the collateral. Close-out: collateral sold after a 99% margin-period-of-risk move plus liquidation cost; unsecured shortfall recovers 40%.
- **IRS:** fixed vs compounded SOFR, both ACT/360 annual. A period that has started uses a synthetic realised fixing to date. Client swaps are hedged at the CCP at mid (same schedule, or DV01-matched on a different tenor where stated). Revenue is a running sales spread over mid.
- **TRS:** fixed share quantity, notional resets each period, dividends passed through at the next reset, client pays SOFR + spread. MTM uses deterministic rates and an equity forward financed at SOFR (no stock borrow cost or repo-SOFR basis). The desk hedges with stock funded by client IM (rebated at SOFR), street equity repo, then unsecured FTP.
- **Funding:** reverse repos are funded by re-pledging the collateral at the street haircut and spread; any haircut gap is funded unsecured at FTP. Repos are valued at the desk's alternative secured funding cost (street rate) for that collateral.

## Capital, exposure and liquidity

- **Leverage exposure** = on-balance-sheet assets (reverse-repo receivables, TRS hedge stock) + counterparty add-ons (SFT exposure, derivative EAD). Same-counterparty, same-maturity repo / reverse repo receivables net when a netting agreement exists.
- **SFT exposure** per netting set: `max(0, Σ loan − MV × (1 − H))` for cash lent and `max(0, Σ MV × (1 + H) − loan)` for cash borrowed, a simplification of the Basel comprehensive approach (no per-security net exposure term).
- **Derivative EAD**: SA-CCR structure (alpha 1.4, replacement cost with VM/IM, interest-rate add-on with supervisory duration and maturity buckets, equity add-on with single-name/index factors and correlations, PFE multiplier), single currency, no basis or volatility hedging sets. IM posted to the CCP is treated as bankruptcy-remote.
- **Limit usage** sums standalone EADs per counterparty (conservative, keeps the optimizer linear); netting-set EAD is reported separately and is always lower in the tests.
- **RWA** = EAD × counterparty risk weight; market-risk capital on hedged positions is ignored.
- **Expected counterparty cost** = PD × LGD × EAD over one year; no CVA term structure.
- **IRS FVA/MVA proxies:** uncollateralised clients carry an EPE from a normal-rate approximation (85bp/yr vol, linearly amortising DV01) funded at the term FTP spread; CCP initial margin ≈ 99% 5-day move × DV01 funded at the 1Y spread.
- **Liquidity usage** = unsecured funding + runoff weight × secured funding (×0.25 for trades of 30 days or less) + IM posted + stressed VM on hedges of uncollateralised swaps.

## Stress

- Rate and price shocks revalue positions (mark-to-market); spread, haircut and charge shocks re-run one year of economics on the base SOFR curve (1-year NII) so effects are not double counted.
- Market-shock credit cost = stressed PD (5 × the base PD) × LGD × the increase in uncollateralised exposure before margin calls.
- Default scenarios close out the defaulted counterparty's repo and derivative netting sets separately (no cross-product netting) and report the worst counterparty.
- Standalone stress loss per trade (used by the optimizer budget and the gate) = combined scenario applied to that trade alone, including its own counterparty's default.

## Optimizer and gate

- Linear programme over fill fractions in [0, 1]; economics and resource usage are linear in size (SA-CCR multipliers are evaluated at full request size).
- 10% of balance-sheet and liquidity capacity is reserved for flow; the gate checks against the full limits.
- Gate proposals are evaluated independently against the same book, not sequentially.

## Not modelled

Intraday liquidity and settlement fails; rehypothecation limits by client agreement; wrong-way risk; collateral substitution; tri-party mechanics; tax and dividend withholding on TRS; accounting (amortised cost vs fair value); stochastic rates and equity vol in valuation; multi-currency; holidays.
