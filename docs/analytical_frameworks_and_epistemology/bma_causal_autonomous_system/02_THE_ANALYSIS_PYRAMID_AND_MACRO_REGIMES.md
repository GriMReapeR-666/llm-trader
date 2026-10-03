# The Analysis Pyramid & Macroeconomic Regime Framework

## Executive Summary

Trading signals cannot be evaluated in a vacuum. A technical setup that delivers positive expectancy during a **Goldilocks expansion** regime will produce devastating drawdowns during a **Stagflationary liquidity contraction**. 

This document details the **7-Tier Analysis Pyramid** and the **Multi-Factor Macro Regime Classification Engine** that anchors all quantitative decisions to underlying macroeconomic reality.

---

## 1. The Analysis Pyramid (From Epistemology to Execution)

```mermaid
graph TD
    P1["1. Philosophy / Epistemology (What is true? Map vs Territory)"] --> P2["2. Formal Sciences (Mathematics, Probability, Logic, Systems)"]
    P2 --> P3["3. Domain Sciences (Macroeconomics, Psychology, Sociology)"]
    P3 --> P4["4. Macro-Fundamental Analysis (Pillars & Regime Scoring)"]
    P4 --> P5["5. Analytical Approaches (Top-Down, Bottom-Up, Quantamental)"]
    P5 --> P6["6. Causal Discovery & BMA Architecture Pool"]
    P6 --> P7["7. Execution & Risk Management (Deterministic Gates, Sizing)"]
```

---

## 2. The Three Pillars of Macro-Fundamental Analysis

Economic reality is continuously characterized across three fundamental pillars:

```mermaid
flowchart TD
    subgraph P1 ["Pillar 1: Real Activity (Growth)"]
        G1[Real GDP Growth & Nowcasts]
        G2[Industrial Production & Capacity Utilization]
        G3[Non-Farm Payrolls & Unemployment Rate]
        G4[ISM Manufacturing & Services PMIs]
        G5[Retail Sales & Real Personal Income]
    end

    subgraph P2 ["Pillar 2: Inflation & Purchasing Power"]
        I1[Headline CPI & Core CPI YoY]
        I2[Producer Price Index PPI]
        I3[Core PCE Deflator]
        I4[Employment Cost Index & Unit Labor Costs]
        I5[5-Year / 10-Year Breakeven Inflation Expectations]
    end

    subgraph P3 ["Pillar 3: Monetary Policy & Liquidity"]
        M1[Policy Rates: Fed Funds, ECB Deposit, BoJ Rate]
        M2[Central Bank Balance Sheets & QT/QE Pace]
        M3[Real Yield Curve Slope: 10Y-2Y, 10Y-3M]
        M4[High Yield & Investment Grade Credit Spreads]
        M5[Reverse Repo Facility RRP & Bank Reserves]
    end
```

---

## 3. The Three-Tools Framework & Synthesis

The macro environment is synthesized across three orthogonal axes:

```mermaid
flowchart TD
    Axis1["Axis 1: Real Economic Activity (PMI, Output, Payrolls)"]
    Axis2["Axis 2: Financial Conditions (Real Rates, Spreads, DXY)"]
    Axis3["Axis 3: Market Fragility & Sentiment (VIX, Positioning, COT)"]

    Axis1 --> Synthesis["Macro Regime Synthesis Engine"]
    Axis2 --> Synthesis
    Axis3 --> Synthesis

    Synthesis --> R1["Growth Regime: Expansion vs Contraction"]
    Synthesis --> R2["Inflation Regime: Reflation vs Disinflation"]
    Synthesis --> R3["Liquidity Regime: Easy vs Tight / Crisis"]
```

---

## 4. The Four Global Macro Regimes & Asset Class Biases

```mermaid
quadrantChart
    title Global Macro Regime Matrix
    x-axis Low Growth --> High Growth
    y-axis Low Inflation --> High Inflation
    quadrant-1 Reflation (High Growth, High Inflation)
    quadrant-2 Stagflation (Low Growth, High Inflation)
    quadrant-3 Deflation (Low Growth, Low Inflation)
    quadrant-4 Goldilocks (High Growth, Low Inflation)
```

### Regime Characteristics & Portfolio Dynamics

| Macro Regime | Economic Dynamics | Outperforming Assets | Underperforming Assets | Quantitative Trading Bias |
| :--- | :--- | :--- | :--- | :--- |
| **Goldilocks** | Strong growth, low/falling inflation, stable liquidity | Equities (Tech/Growth), High Yield Credit, Carry Trades | Sovereign Bonds, Gold, Cash | **Risk-ON**: Trend-following long equities, short volatility |
| **Reflation** | Accelerating growth, rising inflation, commodity demand | Broad Commodities (Energy, Metals), Value/Cyclical Equities, Short-duration debt | Long-duration Sovereign Bonds, Non-profitable Tech | **Cyclical Momentum**: Long commodities, curve steepeners |
| **Stagflation** | Stagnant/contracting growth, stubborn high inflation | Gold, Energy, TIPS (Inflation-Protected), Cash | Nominal Bonds, High-Multiple Equities, Real Estate | **Defensive / Asymmetric**: Long Gold/TIPS, short high-duration growth |
| **Deflation** | Severe growth contraction, falling prices, liquidity crunch | Long-Duration Sovereign Debt (US 10Y/30Y), Quality Cash, USD | Equities, High-Yield Credit, Industrial Commodities | **Risk-OFF**: Long Treasuries/USD, long volatility, short cyclicals |

---

## 5. Analytical Methodologies: Top-Down vs Bottom-Up vs Quantamental

```mermaid
flowchart TD
    subgraph TD ["1. Top-Down Macro Workflow"]
        TD1[Global Macro Regimes & Central Banks] --> TD2[Regional / Currency Selection]
        TD2 --> TD3[Sector Sensitivity Allocation]
        TD3 --> TD4[Individual Asset Sizing]
    end

    subgraph BU ["2. Bottom-Up Fundamental Workflow"]
        BU1[Company Financials / Balance Sheet] --> BU2[Competitive Moat & Management Quality]
        BU2 --> BU3[Valuation: DCF, EV/EBITDA]
        BU3 --> BU4[Micro Asset Selection]
    end

    subgraph QM ["3. Quantamental Bridge (The Institutional Standard)"]
        QM1[Point-in-Time Fundamental & Macro Data] --> QM2[Systematic Feature & Factor Engineering]
        QM2 --> QM3[Causal Discovery Filtering]
        QM3 --> QM4[BMA Optimization & Execution Algorithms]
    end
```

### The Quantamental Principle:
Every fundamental and macroeconomic data point must be strictly timestamped **Point-in-Time (PIT)**. Models are never allowed to train on revised data or forward-looking disclosures, eliminating **lookahead bias**.
