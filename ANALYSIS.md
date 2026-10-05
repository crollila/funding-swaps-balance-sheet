# Analysis

All figures come from one run of `python -m fsbs` on the **simulated** book at valuation date 2026-10-01, with synthetic market data. They show how the model behaves and what drives the decisions; they are not estimates of any real desk's economics.

## 1. Curve

The SOFR OIS curve is bootstrapped from 15 synthetic par quotes (ON 3.85%, 1M 3.83%, 3M 3.76%, 6M 3.64%, 9M 3.54%, 1Y 3.46%, 2Y 3.34%, 3Y 3.32%, 4Y 3.36%, 5Y 3.41%, 7Y 3.52%, 10Y 3.66%, 15Y 3.81%, 20Y 3.87%, 30Y 3.79%). Each pillar is solved so its own swap prices at par, given every earlier pillar.

| Tenor | Maturity | Par quote (input) | Discount factor | Zero rate (cc) | Re-pricing error (bp) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| ON | 2026-10-02 | 3.850% | 0.999893 | 3.903% | 2.8e-10 |
| 1M | 2026-11-02 | 3.830% | 0.996607 | 3.877% | 4.9e-12 |
| 3M | 2027-01-01 | 3.760% | 0.990483 | 3.794% | 1.9e-12 |
| 6M | 2027-04-01 | 3.640% | 0.981930 | 3.657% | 1.4e-13 |
| 9M | 2027-07-01 | 3.540% | 0.973857 | 3.542% | 1.2e-12 |
| 1Y | 2027-10-01 | 3.460% | 0.966108 | 3.448% | 7.6e-13 |
| 2Y | 2028-10-02 | 3.340% | 0.935433 | 3.328% | 4.9e-13 |
| 3Y | 2029-10-01 | 3.320% | 0.905425 | 3.309% | 3.5e-13 |
| 4Y | 2030-10-01 | 3.360% | 0.874495 | 3.350% | 6.9e-14 |
| 5Y | 2031-10-01 | 3.410% | 0.843465 | 3.403% | 1.4e-13 |
| 7Y | 2033-10-03 | 3.520% | 0.781325 | 3.520% | 0.0e+00 |
| 10Y | 2036-10-01 | 3.660% | 0.692408 | 3.673% | 0.0e+00 |
| 15Y | 2041-10-01 | 3.810% | 0.561585 | 3.844% | 3.5e-13 |
| 20Y | 2046-10-01 | 3.870% | 0.457090 | 3.912% | 6.9e-14 |
| 30Y | 2056-10-02 | 3.790% | 0.322451 | 3.770% | 6.9e-14 |

Points worth noting:

- **Exact re-pricing.** The largest re-pricing error is 2.8e-14, i.e. floating-point noise. The optional QuantLib test bootstraps the same quotes with `OISRateHelper` and `PiecewiseLogLinearDiscount` and agrees on discount factors to 2e-6 (the residual is calendar convention: QuantLib's SOFR helper and this library's weekends-only calendar can roll a date differently).
- **Forward staircase.** Log-linear interpolation of discount factors means flat instantaneous forwards between pillars, so the forward curve is a staircase that jumps at each pillar (most visibly between 15Y and 30Y, where the inverted long end produces a lower forward). Smoother schemes (monotone convex, tension splines) are a deliberate non-goal: log-linear is local, so a bump to one quote only moves forwards next to it, which is what key-rate DV01 needs.
- **Funding curve.** The desk's unsecured funds-transfer-pricing (FTP) curve is SOFR plus a term spread (ON 12bp, 1M 30bp, 3M 40bp, 6M 50bp, 1Y 62bp, 2Y 80bp, 3Y 95bp, 5Y 112bp, 10Y 135bp). Secured repo rates sit between SOFR and FTP for good collateral and can exceed short-dated FTP for poor collateral; liquidity charges (section 3) are what make secured funding worth paying for.

## 2. Product economics

Revenue ($1.4bn) and funding cost ($1.3bn) are both large because they include the SOFR base on billions of cash lent and borrowed; the business is the difference, $73.0mm of net interest before charges. After balance-sheet and capital charges ($31.2mm), the liquidity charge ($7.4mm) and expected counterparty cost ($3.6mm), the optimised book earns **$30.8mm a year on $19.8bn of balance sheet: 15.5bp**.

- **Treasury financing** is the largest user of balance sheet and the thinnest earner. A 1-year Treasury reverse repo clears $5.47mm a year over funding but costs $3.60mm in balance-sheet charge. Most of it is only worth doing because it nets against repo funding from the same client (see section 4).
- **Credit and equity financing** (IG, HY, single names) earns 10-50bp on balance sheet: wider client spreads, haircuts above the street's, but higher liquidity, capital and concentration usage.
- **Equity TRS** earn the client spread minus the cost of funding the hedge. Client cash initial margin funds part of the hedge at SOFR, the street funds most of the rest against the shares, and the street haircut gap is unsecured. Index TRS are thin (and below ~45bp lose money once the hedge stock's balance-sheet charge is counted); single names pay more.
- **Interest-rate swaps** use little balance sheet but carry capital, FVA (uncollateralised clients), MVA (initial margin on the CCP hedge) and liquidity costs. A corporate payer at $450k a year of sales spread keeps only $37k after those costs. Long-dated LDI swaps at a few bp running often fail the return-on-RWA hurdle even when net income is positive.

## 3. Optimizer

| Allocation | Requests filled | Balance sheet | Netting benefit | Revenue | Net income | RoBS |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing book only | 0 | $17.3bn | $2.2bn | $1.1bn | $16.3mm | 9.4bp |
| Naive: biggest revenue first | 7 | $19.8bn | $3.0bn | $1.3bn | $17.1mm | 8.6bp |
| Greedy by return on balance sheet | 9 | $19.8bn | $2.2bn | $1.2bn | $28.9mm | 14.6bp |
| LP optimizer | 13 | $19.8bn | $4.8bn | $1.4bn | $30.8mm | 15.5bp |

- **Naive (biggest revenue first)** fills large, low-margin Treasury and interdealer requests first. It uses all the allowed balance sheet and earns $17.1mm, and 3 of the requests it fills lose money outright.
- **Greedy by return on balance sheet** is a strong heuristic: $28.9mm. It fails in exactly one way: it evaluates each request alone.
- **The LP** earns $30.8mm: **+$13.7mm (+80%) over naive and +$1.9mm (+7%) over greedy**. The gain over greedy comes from requests that are unprofitable or marginal standalone but net on balance sheet with other trades: C13 (1M UST, standalone net -$0.52mm/yr), C31 (1M UST, standalone net -$0.95mm/yr), C35 (1M UST, standalone net -$0.68mm/yr), C41 (1M UST (matched-book leg A), standalone net $0.40mm/yr), C43 (1M interdealer cash in (nets C09), standalone net -$0.04mm/yr). The LP's netting variables price that complementarity directly.

**Shadow prices** (marginal net income per unit of each binding limit):

| Binding constraint | Shadow price (marginal net income) |
| :--- | ---: |
| `balance_sheet` | 28.85bp per $ of limit |
| 11 repo netting keys | value of 1$ of offsetting repo at that (counterparty, date) |

The balance-sheet shadow price of 28.85bp a year is the right hurdle for an incremental trade at this book: a new trade that earns less than that per dollar of *incremental* balance sheet should be declined or re-priced, whatever its average-cost return looks like. The frontier figure shows the shadow price falling as the limit grows: average RoBS barely moves while the marginal return collapses, a classic sign that the last requests in the queue are the cheap ones.

**Caveats.** Allocations are fill fractions of client requests; real desks negotiate size and price rather than filling 37% of an order. Limits use the sum of *standalone* EADs (conservative; netting-set EAD is lower, $804.7mm vs $902.2mm), which keeps the problem linear. Netting is credited only for identical maturity dates with the same counterparty.

## 4. Pre-trade gate

| ID | Proposal | Counterparty | Decision | Incr. net income/yr | Incr. balance sheet | Incr. RoBS | Stress loss | Reason |
| :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | :--- |
| P01 | 1M UST financing, macro fund | Global macro fund D | **ACCEPT** | $1.24mm | $1.2bn | 10.3bp | $21mm | all checks passed |
| P02 | single-name financing at 12% haircut | Equity long/short fund B | **REJECT** | $0.60mm | $361.4mm | 16.5bp | $59mm | client haircut 12.0% < internal minimum 15.0%; return on RWA 0.97% < 1.00% hurdle |
| P03 | large synthetic long tech | Equity long/short fund B | **REJECT** | $0.55mm | $944.4mm | 5.9bp | $85mm | client_leverage:HF_EQLS_B: $2,271mm > limit $1,800mm |
| P04 | 10Y corporate payer, no CSA | BBB industrial corporate H | **REJECT** | -$0.82mm | $76.2mm | -107.1bp | $1mm | net income -$0.82mm/yr < floor; return on balance sheet -107.1bp < 5bp hurdle; return on RWA -1.68% < 1.00% hurdle |
| P05 | 2Y term UST financing | Fixed-income relative-value fund C | **REJECT** | $1.10mm | $5.0bn | 2.2bp | $190mm | balance_sheet: $24,800mm > limit $22,000mm; cpty_balance_sheet:HF_RV_C: $13,511mm > limit $9,000mm; dv01: |-$1,088k/bp| > limit $400k/bp; client_leverage:HF_RV_C: $16,508mm > limit $12,000mm; return on balance sheet 2.2bp < 5bp hurdle |
| P06 | MMF cash, 1M (no UST inventory left) | Government money-market fund E | **REJECT** | -$0.76mm | $81.6mm | -93.2bp | $7mm | inventory:UST: $10,911mm > limit $9,000mm; net income -$0.76mm/yr < floor; return on balance sheet -93.2bp < 5bp hurdle; return on RWA -1.86% < 1.00% hurdle |
| P07 | index delta-one at 65bp | Multi-strategy hedge fund A | **ACCEPT** | $0.57mm | $309.6mm | 18.6bp | $28mm | all checks passed |
| P08 | large HY financing | Multi-strategy hedge fund A | **ACCEPT** | $4.77mm | $1.0bn | 47.5bp | $46mm | all checks passed |
| P09 | IG repo priced below street | Life insurer G | **REJECT** | -$1.19mm | $400.0mm | -29.6bp | $32mm | net income -$1.19mm/yr < floor; return on balance sheet -29.6bp < 5bp hurdle |
| P10 | 30Y LDI receiver, left unhedged | Public pension plan F | **REJECT** | -$0.04mm | $16.3mm | -25.5bp | $0mm | dv01: |$772k/bp| > limit $400k/bp; net income -$0.04mm/yr < floor; return on balance sheet -25.5bp < 5bp hurdle; return on RWA -0.51% < 1.00% hurdle |
| P11 | index TRS at 18bp | Global macro fund D | **REJECT** | -$0.71mm | $258.0mm | -27.6bp | $23mm | net income -$0.71mm/yr < floor; return on balance sheet -27.6bp < 5bp hurdle; return on RWA -8.89% < 1.00% hurdle |
| P12 | 1M interdealer UST | A-rated dealer I | **REJECT** | -$0.82mm | $1.0bn | -8.2bp | $23mm | net income -$0.82mm/yr < floor; return on balance sheet -8.2bp < 5bp hurdle; return on RWA -27.18% < 1.00% hurdle |
| P13 | single-name TRS at 85bp | Equity long/short fund B | **ACCEPT** | $0.09mm | $105.4mm | 8.4bp | $8mm | all checks passed |
| P14 | 15Y insurer receiver, CCP-hedged | Life insurer G | **REJECT** | $0.01mm | $9.5mm | 12.2bp | $22mm | return on RWA 0.40% < 1.00% hurdle |

Rejections fall into four families, which is roughly how a real desk thinks about them:

1. **Not allowed:** collateral below the internal minimum haircut (P02), a trade that would breach a fund's leverage limit (P03), or exhaust repo inventory (P06).
2. **Too big for the book:** the 2Y Treasury term trade (P05) breaches total and counterparty balance sheet, client leverage and DV01 at once, and returns 2bp on balance sheet anyway.
3. **Not paid enough:** below-street pricing (P09), an 18bp index TRS (P11), interdealer Treasury lending (P12), and an uncollateralised 10Y corporate swap (P04) all lose money after charges.
4. **Risk the desk should not warehouse:** an unhedged 30Y receiver (P10) adds far more DV01 than the limit allows.

P14 is the instructive near-miss: positive net income and 12bp on balance sheet, but the return on the RWA it consumes is below hurdle. Gate decisions are evaluated independently against the same post-optimisation book; accepting several would consume shared headroom, so a production gate would re-evaluate sequentially.

## 5. Stress

| Scenario | Mark-to-market | 1y net income | Credit / close-out | Total | Extra liquidity used | Defaulted counterparty |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| SOFR +100bp | -$14.7mm | $0.0mm | -$0.2mm | **-$14.9mm** | $0.0 |  |
| Funding spread +100bp | $0.0mm | -$0.5mm | $0.0mm | **-$0.5mm** | $0.0 |  |
| Equity -20% | -$2.7mm | -$1.1mm | -$2.9mm | **-$6.7mm** | $232.9mm |  |
| Collateral -15% | $0.0mm | -$4.9mm | -$14.4mm | **-$19.3mm** | $1.2bn |  |
| Street haircuts x2 (+1pt) | $0.0mm | -$3.0mm | $0.0mm | **-$3.0mm** | $1.1bn |  |
| Worst counterparty default | $0.0mm | $0.0mm | -$114.8mm | **-$114.8mm** | $0.0 | Government money-market fund E |
| Liquidity charge doubles | $0.0mm | -$2.9mm | $0.0mm | **-$2.9mm** | $0.0 |  |
| Combined | -$17.4mm | -$19.5mm | -$573.8mm | **-$610.6mm** | $2.4bn | Multi-strategy hedge fund A |

- **Market risk is small by construction.** TRS are hedged with stock, client swaps with the CCP. SOFR +100bp costs -$14.7mm, almost all from fixed-rate term reverse repos (the desk lent at fixed rates for months and funds overnight) partly offset by a pay-fixed hedge. The book's DV01 is -$139k per bp; IRS alone are $172k. Key-rate DV01s show the hedge is not tenor-matched:

| Pillar | ON | 1M | 3M | 6M | 9M | 1Y | 2Y | 3Y | 4Y | 5Y | 7Y | 10Y | 15Y | 20Y | 30Y |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DV01 ($/bp) | -0k | -22k | -110k | -179k | -0k | -0k | -0k | 171k | -0k | -0k | -335k | 336k | -0k | -0k | 0k |

- **Credit dominates.** Any single default today costs at most $114.8mm (Government money-market fund E). That the worst single default is the *safest* counterparty is not a bug: with the money-market fund the desk is the cash borrower and has given away haircuts on billions of collateral; if the fund failed, the desk would have to replace those securities after a price move. In the combined scenario, collateral values have already fallen 15-20%, so a hedge fund's default leaves haircuts insufficient: -$573.8mm.

| Counterparty defaults | Repo-set shortfall | Derivative-set shortfall | Loss after recovery |
| :--- | ---: | ---: | ---: |
| Government money-market fund E | $191.3mm | $0.0mm | $114.8mm |
| Fixed-income relative-value fund C | $76.1mm | $0.0mm | $45.6mm |
| A-rated dealer I | $72.0mm | $0.0mm | $43.2mm |
| Public pension plan F | $38.3mm | $13.4mm | $31.0mm |
| Qualifying central counterparty J | $0.0mm | $40.4mm | $24.2mm |
| BBB industrial corporate H | $0.0mm | $16.8mm | $10.1mm |
| Life insurer G | $0.0mm | $13.9mm | $8.3mm |
| Equity long/short fund B | $0.0mm | $0.0mm | $0.0mm |
| Global macro fund D | $0.0mm | $0.0mm | $0.0mm |
| Multi-strategy hedge fund A | $0.0mm | $0.0mm | $0.0mm |

- **Liquidity is the binding stress.** Collateral -15% raises liquidity usage by $1.2bn and doubled street haircuts by $1.1bn, each against $2.0bn of capacity with $1.5bn already used. The haircut sweep shows the breach point:

| Street haircut multiplier | Unsecured funding needed | Liquidity usage | Change in net income/yr |
| :--- | ---: | ---: | ---: |
| 1x | $63.4mm | $1.5bn | $0.00mm |
| 1.25x | $273.9mm | $1.7bn | -$1.38mm |
| 1.5x | $561.3mm | $1.9bn | -$2.98mm |
| 2x | $1.2bn | $2.4bn | -$6.32mm |
| 2.5x | $1.8bn | $2.9bn | -$9.65mm |
| 3x | $2.4bn | $3.4bn | -$12.99mm |
| 4x | $3.6bn | $4.4bn | -$19.66mm |

The income cost of haircut stress is small; the liquidity cost is not. A desk that only watched P&L would miss the risk entirely.

## 6. What would change with real data

- Real SOFR, repo (tri-party / GCF / bilateral by collateral) and FTP curves, with holiday calendars and payment lags.
- Haircuts and client spreads per security and per client agreement, not per class.
- Full SA-CCR (hedging sets per currency, basis, volatility) and the full Basel comprehensive-approach SFT formula with per-security net exposures.
- Simulation-based exposure (EPE/PFE) and CVA/FVA rather than closed-form proxies; stochastic rates for TRS financing legs.
- Multi-period allocation (trades roll off; requests arrive over time) and integer constraints (minimum ticket sizes), which would make this a MILP.

See [MODEL_ASSUMPTIONS.md](MODEL_ASSUMPTIONS.md) for every simplification.
