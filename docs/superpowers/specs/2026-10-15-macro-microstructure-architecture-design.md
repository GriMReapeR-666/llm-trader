# Institutional Macro-Microstructure Trading Architecture Design

**Date**: 2026-10-15  
**Status**: Approved for Implementation  
**Doctrine**: Fail-closed, advisory-first (`execution_authority: False`), zero unnecessary dependencies, pure stdlib/NumPy/Pandas math, 100% open-source telemetry, bi-temporal PIT hygiene.

---

## 1. System Overview & End-to-End Flow

The architecture unifies macroeconomic forecasting with execution market microstructure across multi-horizon temporal cycles:

```
[ 100% OPEN-SOURCE INGESTION ]
FRED / ALFRED • DBnomics • BIS • GDELT 2.0 • ACLED • CFTC COT • OpenBB / Yahoo
                       │
                       ▼
[ MULTI-DOMAIN QUANTITATIVE SCORING (S_{i,t}) ]
S_{i,t} = 0.35 M_{i,t} + 0.30 C_{i,t} + 0.20 G_{i,t} + 0.15 P_{i,t} in [-100, +100]
                       │
                       ▼
[ CONVICTION & DE-CORRELATION SELECTION FUNNEL ]
1. Hurdle Rate: |S_{i,t}| >= 45
2. Cross-Asset Alignment (2Y spreads, TIPS, CDX)
3. Factor Neutrality: Trailing 60-day |r| < 0.35
==> Isolates Top 2 Trades/Week (Alpha Trend + Beta Catalyst)
                       │
                       ▼
[ MULTI-HORIZON TEMPORAL PLANNING ENGINE ]
Tier 1: Annual Seasonality (Q1 Capital Deploy, Q2 Tax Drain, Q3 Jackson Hole, Q4 Turn)
Tier 2: Monthly 4-Week Cycle (W1 NFP/PMI, W2 CPI/Auctions, W3 CBs/OPEX, W4 Rebalancing)
Tier 3: Day-of-Week Templates (Mon Range, Tue Judas Sweep, Wed/Thu Expansion, Fri De-risk)
Tier 4: Intraday Killzones (Asian 00-06, London 07-10, NY 12-16, London Fix 15-16 UTC)
                       │
                       ▼
[ REAL-TIME MICROSTRUCTURE & IMPACT CANDLE ENGINE ]
State 1: HTF Range Framing (PWH/PWL, PDH/PDL, ASH/ASL, Premium > 50% / Discount < 50%)
State 2: Session Killzone Sweep of Opposing Liquidity
State 3: Cumulative Volume Delta (CVD) Absorption & Divergence Verification (Dual-Mode)
State 4: Impact Candle (ICS_t: Efficiency >= 0.72, Z_vol >= 2.0, FVG created, MSS break)
State 5: Limit Entry at FVG/Order Block (with 1.5 pip queue penetration buffer)
Stop: 0.8-1.2x ATR behind sweep wick | TP1: 1.5x ATR (bank 50% + BE) | TP2: 2.5-3.5x ATR runner
                       │
                       ▼
[ BI-TEMPORAL PURGED & EMBARGOED WALK-FORWARD VALIDATOR ]
• BiTemporalMacroStore: Strict vintage indexing (t_ref, t_pub)
• MicrostructureSweepSimulator: Queue penetration, news spread expansion, intra-bar SL/TP
• Purged WFO Folds: 24M In-Sample, 3M Out-of-Sample, 5-Day Purge & 5-Day Embargo
• Acceptance Gates: WFE >= 0.55, DSR >= 0.95, Leave-One-Out >= +0.35R, Monte Carlo DD < 10%
```

---

## 2. Component Specifications

### 2.1 `data/open_intelligence_feed.py`
Provides lightweight, reliable REST clients for public feeds:
- `GDELTFeed`: Fetches 15-minute event updates, parses actor-action-target triplets, Goldstein conflict scores ($-10.0$ to $+10.0$), and quad-class categorization.
- `ACLEDFeed`: Tracks geocoded conflict events for critical transit chokepoints (Strait of Hormuz, Bab el-Mandeb, Malacca, major oil/gas pipelines).
- `DBnomicsFeed`: Unified open REST wrapper for Eurostat, OECD, and Cabinet Office Japan indicators.
- `BISFeed`: REST SDMX client for global liquidity, credit-to-GDP gaps, and FX effective exchange rates (EER).
- All feeds feature offline fallback mechanisms, returning historical snapshots or neutral default vectors if network is unavailable.

### 2.2 `core/macro_conviction_funnel.py`
- Implements the 4-domain scoring engine:
  - $M_{i,t}$: Macro surprise & sticky component momentum index ($\tanh$).
  - $C_{i,t}$: Central bank divergence (OIS 12M, hawkish/dovish NLP, net liquidity $\Delta (\text{Assets} - [\text{TGA} + \text{RRP}]))$.
  - $G_{i,t}$: Geopolitical & terms-of-trade elasticity index ($\text{Elasticity}_i \times \frac{\text{Goldstein}}{10} \times \Delta \text{ToT}_i$).
  - $P_{i,t}$: Contrarian CFTC 3-year positioning Z-score modifier ($Z > 2.0 \implies -1.0$; $Z < -2.0 \implies +1.0$).
- Enforces the 2-trade-per-week selection funnel:
  - Step 1: Filter $\|S_{i,t}\| \ge 45$.
  - Step 2: Cross-asset verification (FX yield spread expansion; Equity real yields / CDX).
  - Step 3: Trailing 60-day pairwise correlation $\|r\| < 0.35$.
  - Assigns candidates to 5 Master Playbooks (`CB-DIV`, `STAG-SHOCK`, `SUPPLY-DISRUPT`, `LIQUIDITY-SQUEEZE`, `CARRY-UNWIND`).

### 2.3 `core/temporal_planning_engine.py`
- Evaluates temporal cycles for any timestamp:
  - Yearly Seasonality: Q1, Q2, Q3, Q4 regime classification.
  - Monthly Cycle: Week 1 (PMI/NFP), Week 2 (CPI/Auctions), Week 3 (CBs/OPEX), Week 4 (Rebalancing/Fix).
  - Day of Week: Monday (Range build), Tuesday (Judas sweep / Trade 1), Wednesday/Thursday (Expansion / Trade 2), Friday (De-risking).
  - Intraday Killzones: Asian (00:00–06:00 UTC), London (07:00–10:00 UTC), NY (12:00–16:00 UTC), London Fix (15:00–16:00 UTC).

### 2.4 `core/microstructure_impact_engine.py`
- Interacts with `core/smc_structure.py` and `research/alpha_miner/fvg_detector.py`.
- Evaluates:
  - Dealing ranges and liquidity pools ($\text{PWH}/\text{PWL}, \text{PDH}/\text{PDL}, \text{ASH}/\text{ASL}, \text{EQH}/\text{EQL}$, Premium/Discount).
  - Sweeps of pools opposing the macro conviction bias.
  - Dual-mode Cumulative Volume Delta (CVD) divergence (broker real ticks or CME futures proxy).
  - Impact Candle Score: $\text{Efficiency}_t = \frac{|C_t - O_t|}{H_t - L_t} \ge 0.72$, $Z_{\text{vol}} \ge 2.0$, FVG created ($L_{t+1} > H_{t-1}$), MSS swing break.
  - 5-step state machine (`HTF_FRAMED` $\to$ `SWEEP_DETECTED` $\to$ `CVD_ABSORBED` $\to$ `IMPACT_CONFIRMED` $\to$ `LIMIT_ARMED`).
  - A+ Confluence Matrix ($\ge 85/100$).
  - Position sizing: 1.0% NAV risk per trade, max 2.0% open risk, ATR-based TP1/TP2.

### 2.5 `core/bitemporal_purged_wfo.py`
- `BiTemporalMacroStore`: Indexes data strictly by $(t_{\text{ref}}, t_{\text{pub}})$, preventing revision and publication lookahead.
- `MicrostructureSweepSimulator`: Simulates $M15$ execution with queue penetration ($1.5\text{ pips}$), news spread expansion, and pessimistic intra-bar execution resolution.
- `generate_purged_wfo_folds`: Generates rolling In-Sample (24M) and Out-of-Sample (3M) folds separated by 5-day Purge and 5-day Embargo buffers.
- Statistical Acceptance Gates:
  - $\text{WFE} \ge 0.55$.
  - $\text{DSR} \ge 0.95$.
  - Leave-One-Out Expectancy $> +0.35\text{R}$.
  - Monte Carlo Perturbation 95th percentile MaxDD $< 10\%$.

---

## 3. Testing & Verification Plan

1. Unit tests for `data/open_intelligence_feed.py` validating GDELT, ACLED, DBnomics, and BIS data schemas and offline fallback.
2. Unit tests for `core/macro_conviction_funnel.py` validating $S_{i,t}$, selection funnel, de-correlation matrix, and 5 playbooks.
3. Unit tests for `core/temporal_planning_engine.py` validating all 4 temporal tiers and killzone boundaries.
4. Unit tests for `core/microstructure_impact_engine.py` validating sweep detection, CVD divergence, Impact Candle Score, and A+ matrix grading.
5. Unit tests for `core/bitemporal_purged_wfo.py` validating point-in-time querying, queue penetration limit execution, purged/embargoed fold generation, and acceptance metrics.
6. End-to-end integration tests verifying autonomous loop cadence wiring and zero-lookahead invariants.
