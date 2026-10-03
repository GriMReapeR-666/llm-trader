# Institutional vs Retail Grade — What Applies to This Project

**Folder:** `docs/institutional-vs-retail-applicability/`  
**Companion folders:** `docs/author-p-author-k-portfolio-optimization-series/` | `docs/quant papers autonomous quant trading systems .txt`  
**Last updated:** 2026-08-28  
**Purpose:** Extract the Kimi paper dump and the Author-P–Author-K series, classify each by *institutional* vs *retail* grade, and map only what solves **real problems in this codebase** — not theoretical ornamentation.

---

## TL;DR — One-page verdict

| Question | Answer |
|---|---|
| **Will this autonomous system ever need *just* the 101 Alphas?** | No. `core/alpha_factors.py:4` already adapts 23 alphas to single-asset FX time-series, and `core/alpha_ensemble.py:1` combines them via IC-weighted ensemble. That is only Layer 2 of a 6-layer autonomous stack. |
| **Does it need *everything* Kimi listed (2025–2026 agentic RL/NAS)?** | No — 60–70% of that list is institutional-grade (multi-asset, HFT latency, A-share T+1) and fires blanks at a retail MT5 system trading `M15` on `EURUSD` with max 2 positions (`core/risk_manager.py:510`). |
| **What solves a real, currently-bleeding problem?** | 5 papers/concepts: **AQuA sealed sandbox**, **Author-P–Author-K r-budget**, **FactorMiner experience memory**, **Causal RL (CRL-ABSO/CLEAR)**, **MARS Safety-Critic**. Everything else is P2/P3. |
| **What is the Author-P–Author-K grade?** | Purely **institutional** (N=100–400 covariance optimization). Zero direct use today (system trades 1 symbol), but becomes P0 the day you add a portfolio sleeve (`core/hierarchical_portfolio.py:148` already assumes one). |

---

## Folder map

| File | Content |
|---|---|
| `README.md` (this file) | Verdict + navigation |
| `01-taxonomy-institutional-vs-retail.md` | Definitions, 10 discriminants, how to label any paper in 30 sec |
| `02-author-p-author-k-applicability.md` | Deep dive: why the 7-paper series is institutional-only today, and the 4 tripwires that make it retail-critical tomorrow |
| `03-autonomous-papers-applicability-matrix.md` | Row-by-row grade of every Kimi-listed paper (30+ entries) — Institutional / Retail / Hybrid + relevance 0–5 |
| `04-real-problems-gap-analysis.md` | Maps 9 *actual* problems in this codebase (with file:line evidence) to papers that fix them — and papers that don't |
| `05-priority-roadmap.md` | P0/P1/P2 build order, estimated effort, and what *not* to build |

---

## How to use this folder

1. **New engineer:** Read `01-taxonomy` then `04-gap-analysis` (15 min total).
2. **Researcher proposing a paper:** Look it up in `03-matrix`; if it scores ≤2/5, expect a hard challenge.
3. **PM prioritizing:** `05-roadmap` is the build sequence — do not invert it.

---

## Source of truth note

Claims below are evidence-backed against `core/*.py:line` and `README.md:1`. Kimi's 2025–2026 paper summaries were not re-verified against arXiv; where a paper could not be located or its Sharpe/R² claim is implausible on inspection, the matrix flags it as **unverified**.
