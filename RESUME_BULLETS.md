# Resume bullets

Phrased for a personal project: every portfolio, client and trade is **simulated** on synthetic market data. Pick the ones that match the role.

**Project line**

- *Funding, Swaps & Balance Sheet* (Python; personal project): pricing, risk and balance-sheet optimisation library for repo, reverse repo, equity TRS and SOFR swaps on a simulated financing book. github.com/crollila/funding-swaps-balance-sheet

**Rates / quant**

- Bootstrapped a SOFR OIS discount curve from 15 par quotes with log-linear interpolation and Brent root-finding; quotes re-price to 2.8e-14, cross-checked against QuantLib to 2e-6 in discount factors.
- Priced fixed-vs-SOFR swaps leg by leg (projected forwards, annuity, par rate, PV) with analytic, finite-difference, par and key-rate DV01; tests prove par NPV ≈ 0, leg reconciliation and O(h²) finite-difference convergence.
- Built key-rate risk showing a DV01-matched hedge left 10Y $336k, 7Y -$335k, 6M -$179k, 3Y $171k of curve risk in a simulated book flat to parallel moves.

**Repo / prime finance / delta one**

- Modelled repo and reverse repo end to end: purchase/repurchase price, ACT/360 accrual, haircut and margin-ratio conventions, daily variation margin with minimum transfer amounts, and counterparty close-out over a margin period of risk.
- Implemented resetting equity total-return swaps with dividend pass-through, risk-neutral mark-to-market and a hedged desk P&L that isolates the financing spread (verified exactly on controlled fixtures).
- Modelled hedge funding stacks (client IM, street repo at collateral-specific haircuts and spreads, unsecured FTP for gaps) and their sensitivity to haircut shocks; a 2x street-haircut shock breaches the simulated liquidity limit.

**Balance sheet / structuring / optimisation**

- Formulated balance-sheet allocation as a linear programme (HiGHS) maximising revenue minus funding, balance-sheet, capital, liquidity and expected counterparty costs under 12 limit families and 40+ constraints (counterparty, concentration, liquidity buffer, collateral eligibility, tenor, client leverage, DV01, stress budget), with variables for same-counterparty repo netting.
- On a simulated $19.8bn book with 43 client requests, the optimizer earned $30.8mm/yr vs $17.1mm for revenue-first allocation (+80%) and +7% over a return-on-balance-sheet heuristic, by finding matched-book netting the heuristic cannot see; shadow prices give the marginal value of balance sheet (28.85bp/yr).
- Built a pre-trade gate returning accept/reject with the exact breached limit or hurdle (incremental net income, return on balance sheet and RWA, counterparty usage, stress loss); 4 of 14 simulated proposals accepted, and tests independently confirm every rejection.
- Stress-tested the book across SOFR +100bp, funding +100bp, equity -20%, collateral -15%, haircut shocks, counterparty default and combined scenarios, separating mark-to-market, 1-year income and close-out credit losses.

**Engineering**

- One-command reproducible pipeline (results, 13 figures and documentation regenerated from the run), 63 pytest checks, CI with lint, QuantLib cross-check and a reproducibility check of headline numbers.
