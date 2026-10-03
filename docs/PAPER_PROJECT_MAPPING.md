# Research Paper → Project Integration Analysis

## Executive Summary

This document maps **35+ research papers** to the trading project's current state and provides a prioritized integration roadmap.

> **STATUS NOTE (2026-09-20):** the original "12 remaining gaps" list below has
> been substantially closed by the September integration merges (plugin
> profiles, DSR gate, meta-loop ingestion, trial ledger, learning-loop wiring).
> Treat the gap list as historical context — verify any specific item against
> the current tree before acting on it. Current authoritative gap tracking
> lives in `docs/TRADING_SYSTEM_MASTER_TODO.md`.
1. **Relevance** — Does it solve a real problem in the project?
2. **Implementability** — Can it be integrated without massive infrastructure?
3. **Impact** — How much does it improve the system?

---

## Part 1: What's Already Built (Project State)

| # | Module | Paper Inspiration | Status |
|---|--------|------------------|--------|
| 1 | Alpha Discovery Engine | FactorMiner Ralph Loop | ✅ Built & Wired |
| 2 | Meta-Adaptive Regime Engine | MAML / ReCAP | ✅ Built & Wired |
| 3 | Recursive Self-Improvement | AQuA sealed sandbox | ✅ Built & Wired |
| 4 | Evolutionary Optimizer | MadEvolve | ✅ Built & Wired |
| 5 | Cross-Market Benchmarking | AI-Trader | ✅ Built & Wired |
| 6 | Multimodal Analysis | FinAgent | ✅ Built & Wired |
| 7 | Alpha Ensemble (IC-weighted) | AlphaGen | ✅ Built |
| 8 | Alpha Decay Monitor | AlphaAgent | ✅ Built |
| 9 | Alpha Rotation Engine | FactorMiner | ✅ Built |
| 10 | CPCV Backtester | Author-C | ✅ Built |
| 11 | Risk Management (6-layer) | MARS Safety-Critic | ✅ Built |
| 12 | Multi-tier Memory (L0-L3) | FinMem | ✅ Built |
| 13 | Regime Detection (BOCPD+HMM) | Regime shift models | ✅ Built |
| 14 | RL Trainer (Q-learning) | DRL for Trading | ✅ Built |
| 15 | Auto-Trigger Tools (11 tools) | RD-Agent(Q) | ✅ Built |
| 16 | LLM Orchestrator (40+ tools) | AI-Trader / FinAgent | ✅ Built |

---

## Part 2: What's Missing — Gap Analysis

### GAP 1: Causal RL for Robust Generalization
**Problem:** Current RL uses correlation-based features. Spurious correlations cause overfitting to historical patterns.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **CRL-ABSO** | 2026 | Causal RL with online causal graph learning. Improves CNR by 22.3%, SSR by 50.9% over non-causal baselines. | **HIGH** — Directly prevents overfitting in RL agent |
| **CLEAR** | 2025 | Causal RL + environment abstraction + reward calibration. Identifies causally relevant state features. | **HIGH** — Cleaner state representation for RL |
| **Causal RL Survey (IEEE TNNLS)** | 2025 | Comprehensive framework combining structural invariances of causal inference with RL. | **MEDIUM** — Theoretical foundation |

**Implementation Plan:**
- Add causal graph inference to the RL state builder (`_build_rl_state`)
- Use DoWhy or CausalNex for causal discovery on feature set
- Filter features through causal relevance before feeding to RL
- **Complexity:** MEDIUM | **Impact:** HIGH | **Priority:** 1

---

### GAP 2: Extreme Volatility / Crash Protection
**Problem:** No mechanism to detect and respond to flash crashes or extreme volatility spikes. Current risk management is reactive, not predictive.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **Logic-Q** | 2025 | Neuro-symbolic trend analysis. Program sketch identifies rapid descend trends. -43.3% max DD vs -90.4% buy-and-hold during 2020 crash. | **CRITICAL** — Crash protection |
| **ArchetypeTrader** | 2026 | RL-based archetype selection. Distills expert demonstrations into reusable trading archetypes. Regret-aware adapter for fine-grained refinement. | **HIGH** — Dynamic strategy switching |
| **MARS Safety-Critic** | 2025 | Heterogeneous agent ensemble with Safety-Critic networks enforcing risk-tolerance thresholds. | **HIGH** — Risk-aware ensemble |

**Implementation Plan:**
- Build `CrashDetector` module: neuro-symbolic trend analysis using program sketches
- Implement volatility spike detection (ATR expansion, VIX analog)
- Add automatic position reduction when extreme volatility detected
- Wire into `_validate_proposal` as a HARD gate
- **Complexity:** MEDIUM | **Impact:** CRITICAL | **Priority:** 2

---

### GAP 3: LLM Hallucination Prevention in Trading Decisions
**Problem:** LLM can generate plausible but false financial reasoning. No grounding mechanism exists.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **RAG-Augmented Multi-Agent** | 2025 | Three-stage hallucination suppression: pre-retrieval matching → confidence scoring → automated fact-checking. 51.2% win rate vs 48.3% baseline. | **HIGH** — Grounds LLM in reality |
| **Dnalyaw TensorLogic** | 2026 | Dual-mode reasoning: Boolean mode for hard constraints (provably correct), Continuous mode for learning. "When system says 'violates dollar neutrality', it's provably correct." | **CRITICAL** — Enforces risk constraints |
| **FinAgent** | 2024 | Tool augmentation + expert knowledge integration. Dual-level reflection module. | **MEDIUM** — Already partially implemented |

**Implementation Plan:**
- Build `TradingFactChecker` that validates LLM reasoning against deterministic rules
- Add confidence scoring for LLM outputs (cross-check with indicators)
- Implement Boolean constraint layer for risk limits (position size, drawdown, exposure)
- Wire into `_validate_proposal` as a reasoning validator
- **Complexity:** HIGH | **Impact:** HIGH | **Priority:** 3

---

### GAP 4: Zero-Shot Regime Adaptation
**Problem:** Current regime detection requires 3-5 days to adapt. MAML-based adaptation is still slow.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **FinPFN** | 2025-2026 | Transformer-based Bayesian predictor. Conditions on recent feature-return relationships. Zero-shot regime adaptation. Validated on A-shares. | **HIGH** — Faster regime switching |
| **ReCAP** | 2026 | Continual learning with policy library + regime-gate module. Instantaneous regime switching. Only updates regime-gate and current policy vector. | **HIGH** — Near-zero adaptation lag |
| **Meta-LMPS-Online** | 2025 | MAML + mixture policies for online portfolio selection. Decomposes long-term into short-term segments. | **MEDIUM** — Complements existing meta-adaptive |

**Implementation Plan:**
- Enhance `RegimeTransitionDetector` with feature-return conditioning
- Add policy library to `MetaAdaptiveEngine` (store best parameters per regime)
- Implement regime-gate module for instant switching
- **Complexity:** MEDIUM | **Impact:** HIGH | **Priority:** 4

---

### GAP 5: Multi-Agent Ensemble with Heterogeneous Risk Profiles
**Problem:** Current system uses a single LLM agent. No ensemble of agents with different risk profiles.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **MARS** | 2025 | Heterogeneous Agent Ensemble where each agent has unique risk profile. Meta-Adaptive Controller generates agent weights. Safety-Critic enforces risk tolerance. | **HIGH** — Diversified decision making |
| **DeepFund** | 2025 | Multi-agent framework: LLMs as both analysts and managers. Forward-testing methodology. | **MEDIUM** — Research-trading separation |
| **QuantEvolve** | 2025 | Multi-agent evolutionary framework. Feature map aligned with investor preferences. Quality-diversity optimization. | **MEDIUM** — Strategy diversity |

**Implementation Plan:**
- Build `AgentEnsemble` with 3-5 agents (conservative, balanced, aggressive, momentum, mean-reversion)
- Each agent has its own risk profile and decision threshold
- Meta-Adaptive Controller weights agents based on regime
- Safety-Critic validates each agent's output
- **Complexity:** HIGH | **Impact:** HIGH | **Priority:** 5

---

### GAP 6: Transaction Cost Optimization / Smart Order Routing
**Problem:** Current TCA is basic. No intelligent order routing or market impact modeling.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **BestEx Research TCA Model** | 2025 | Novel transaction cost model for futures. Cost optimization framework. | **MEDIUM** — Better cost estimates |
| **LA-NAS-Lock** | 2025 | Intelligent lock-up strategy reduces transaction costs by 38.2%. Dynamic precision allocation. | **MEDIUM** — Cost reduction |
| **Almgren-Chriss Model** | Classic | Optimal execution with market impact. TWAP/VWAP algorithms. | **MEDIUM** — Foundation for SOR |

**Implementation Plan:**
- Enhance existing TCA with Almgren-Chriss market impact model
- Add TWAP/VWAP execution algorithms
- Implement spread-aware entry timing
- Wire into `_build_execution_plan`
- **Complexity:** LOW | **Impact:** MEDIUM | **Priority:** 6

---

### GAP 7: Catastrophic Forgetting Prevention in Online Learning
**Problem:** RL agent and self-improvement loop may forget previously learned patterns when adapting to new regimes.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **Elastic Weight Consolidation (EWC)** | 2017 | Prevents catastrophic forgetting by penalizing changes to important weights. | **HIGH** — Preserves learned knowledge |
| **Progressive Neural Networks** | 2016 | Adds new capacity for new tasks while freezing old capacity. | **MEDIUM** — Architecture-based solution |
| **Experience Replay with Priority** | 2015 | Prioritized experience replay prevents overwriting important memories. | **HIGH** — Already partially implemented |

**Implementation Plan:**
- Add EWC penalty to RL trainer updates
- Implement priority-based experience replay for trading memory
- Add "knowledge preservation" score to self-improvement proposals
- **Complexity:** MEDIUM | **Impact:** HIGH | **Priority:** 7

---

### GAP 8: Walk-Forward Validation with Decay Detection
**Problem:** CPCV exists but no continuous walk-forward validation with real-time decay detection.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **Advances in Financial ML (de Prado)** | 2018 | Walk-forward analysis, purged cross-validation, meta-labeling. | **HIGH** — Foundation |
| **FactorMiner** | 2026 | Experience memory accumulates decay evidence across sessions. | **HIGH** — Decay tracking |
| **AlphaAgent** | 2025 | Originality enforcement + hypothesis-factor alignment for decay resistance. | **MEDIUM** — Already implemented |

**Implementation Plan:**
- Add walk-forward validation to CPCV backtester
- Implement rolling IC tracking with decay alerts
- Add "knowledge half-life" metric to alpha experience memory
- **Complexity:** LOW | **Impact:** MEDIUM | **Priority:** 8

---

### GAP 9: Personalized Strategy Generation
**Problem:** One-size-fits-all strategy. No adaptation to investor risk preferences.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **QuantEvolve** | 2025 | Feature map: risk profile, turnover, return characteristics. Quality-diversity optimization retains best per niche. | **MEDIUM** — Personalization |
| **Meta-LMPS-Online** | 2025 | MAML for online portfolio selection with investor constraints. | **LOW** — Not core priority |

**Implementation Plan:**
- Add investor profile configuration (risk tolerance, return target, max DD)
- Map strategy parameters to investor preferences
- Use quality-diversity optimization to find strategies per profile
- **Complexity:** LOW | **Impact:** LOW | **Priority:** 9

---

### GAP 10: Reproducibility & Audit Trail
**Problem:** No standardized audit trail for reproducibility.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **PLUTUS** | 2025 | 9-step development process. Compliance Score. Standardized README. | **MEDIUM** — Audit trail |
| **AMA** | 2025-2026 | Transparent leaderboard + live dashboard. | **LOW** — Benchmarking |

**Implementation Plan:**
- Add versioned experiment tracking (already partially implemented)
- Implement PLUTUS compliance scoring
- Add automated performance reporting
- **Complexity:** LOW | **Impact:** LOW | **Priority:** 10

---

### GAP 11: Multi-Day Holding Period Optimization
**Problem:** Current system is optimized for intraday/scalp. No multi-day strategy.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **Deep Learning Multi-Day Turnover** | 2025 | Five-module system: cross-sectional prediction + opening signal + position sizing + grid-search + market timing. 15.2% annualized, Sharpe 1.87. | **MEDIUM** — Swing trading |
| **FinPFN** | 2025-2026 | Meta-learning for daily return prediction. | **MEDIUM** — Multi-timeframe |

**Implementation Plan:**
- Add swing trading strategy profile to `MetaAdaptiveEngine`
- Implement multi-day holding period logic
- Add overnight risk management
- **Complexity:** LOW | **Impact:** MEDIUM | **Priority:** 11

---

### GAP 12: Hardware-Aware Execution (Latency)
**Problem:** Not relevant for current M15/H1 timeframes. Relevant only for HFT.

| Paper | Year | Key Contribution | Relevance |
|-------|------|-----------------|-----------|
| **LA-NAS-Lock** | 2025 | 425μs inference latency. Dynamic precision allocation. | **LOW** — HFT only |
| **vLLM PagedAttention** | 2025-2026 | KV cache management. | **LOW** — LLM inference only |

**Implementation Plan:** NOT RELEVANT for current system. Skip.

---

## Part 3: Prioritized Integration Roadmap

### Tier 1: Critical (Implement First)
| # | Gap | Paper | Complexity | Impact | Priority |
|---|-----|-------|------------|--------|----------|
| 1 | Crash Protection | Logic-Q | MEDIUM | CRITICAL | P0 |
| 2 | Causal RL | CRL-ABSO / CLEAR | MEDIUM | HIGH | P0 |
| 3 | Hallucination Prevention | TensorLogic / RAG Multi-Agent | HIGH | HIGH | P0 |

### Tier 2: High Value (Implement Second)
| # | Gap | Paper | Complexity | Impact | Priority |
|---|-----|-------|------------|--------|----------|
| 4 | Zero-Shot Regime Adaptation | FinPFN / ReCAP | MEDIUM | HIGH | P1 |
| 5 | Multi-Agent Ensemble | MARS | HIGH | HIGH | P1 |
| 6 | Catastrophic Forgetting | EWC / Priority Replay | MEDIUM | HIGH | P1 |

### Tier 3: Medium Value (Implement Third)
| # | Gap | Paper | Complexity | Impact | Priority |
|---|-----|-------|------------|--------|----------|
| 7 | Transaction Cost Optimization | Almgren-Chriss / LA-NAS | LOW | MEDIUM | P2 |
| 8 | Walk-Forward Validation | de Prado | LOW | MEDIUM | P2 |
| 9 | Multi-Day Holding | Multi-Day Turnover | LOW | MEDIUM | P2 |

### Tier 4: Nice-to-Have (Implement Last)
| # | Gap | Paper | Complexity | Impact | Priority |
|---|-----|-------|------------|--------|----------|
| 10 | Personalized Strategies | QuantEvolve | LOW | LOW | P3 |
| 11 | Reproducibility | PLUTUS | LOW | LOW | P3 |
| 12 | HFT Latency | LA-NAS-Lock | HIGH | LOW | SKIP |

---

## Part 4: Paper Quick Reference

### Papers Already Integrated (via inspiration)
| Paper | Module | How It's Used |
|-------|--------|--------------|
| FactorMiner (2026) | Alpha Discovery | Ralph Loop: Retrieve→Generate→Evaluate→Distill |
| AlphaAgent (2025) | Alpha Decay Monitor | Originality enforcement, decay detection |
| AlphaGen (2023) | Alpha Ensemble | IC-weighted combination |
| AQuA (2026) | Self-Improvement | Sealed sandbox, dual research loops |
| MadEvolve (2026) | Evolutionary Optimizer | Population, mutation, crossover, parameter budget |
| AI-Trader (2025) | Cross-Market Benchmark | Multi-instrument validation |
| FinAgent (2024) | Multimodal Analysis | Chart patterns, trend, S/R |
| MARS (2025) | Risk Management | 6-layer defense system |
| FinMem (2023) | Trading Memory | L0-L3 tiered memory |
| RD-Agent(Q) (2024) | Auto-Trigger Tools | 11 auto-trigger tools |
| Author-C (2018) | CPCV Backtester | Purged cross-validation |

### Papers NOT Yet Integrated (New)
| Paper | Year | What It Solves | Integration Difficulty |
|-------|------|---------------|----------------------|
| **CRL-ABSO** | 2026 | Causal RL for robust generalization | MEDIUM |
| **CLEAR** | 2025 | Causal RL + environment abstraction | MEDIUM |
| **Logic-Q** | 2025 | Neuro-symbolic crash protection | MEDIUM |
| **ArchetypeTrader** | 2026 | Dynamic archetype selection | HIGH |
| **TensorLogic** | 2025-2026 | Boolean constraint enforcement | HIGH |
| **RAG Multi-Agent** | 2025 | Hallucination suppression | HIGH |
| **FinPFN** | 2025-2026 | Zero-shot regime adaptation | MEDIUM |
| **ReCAP** | 2026 | Policy library + regime-gate | MEDIUM |
| **EWC** | 2017 | Catastrophic forgetting prevention | LOW |
| **Almgren-Chriss** | Classic | Market impact modeling | LOW |
| **QuantEvolve** | 2025 | Personalized strategy generation | LOW |
| **PLUTUS** | 2025 | Reproducibility standard | LOW |

---

## Part 5: Integration Effort Estimate

| Tier | Modules | Estimated Effort | Expected Sharpe Improvement |
|------|---------|-----------------|---------------------------|
| Tier 1 (P0) | CrashDetector + CausalRL + FactChecker | ~3 weeks | +0.3-0.5 |
| Tier 2 (P1) | RegimeAdaptation + AgentEnsemble + EWC | ~4 weeks | +0.2-0.4 |
| Tier 3 (P2) | TCA+ + WalkForward + MultiDay | ~2 weeks | +0.1-0.2 |
| Tier 4 (P3) | Personalization + Reproducibility | ~1 week | +0.05-0.1 |
| **Total** | | **~10 weeks** | **+0.65-1.2** |

---

## Part 6: Key Insights from Papers

### 1. Risk Management > Prediction Accuracy
> "General intelligence does not automatically translate to effective trading capability, with most agents exhibiting poor returns and weak risk management." — AI-Trader (2025)

**Implication:** Our 6-layer risk system is correct. Focus on enhancing it, not replacing it.

### 2. Sealed Sandbox > Raw Capability
> "The sealed sandbox principle ensures that any performance gains are grounded in genuine predictive power rather than technical artifacts." — AQuA (2026)

**Implication:** Our sealed sandbox in self-improvement is correct. Apply same principle to causal RL.

### 3. Parameter Budget Prevents Overfitting
> "Every additional free parameter expands the effective search space and inflates the gap between in-sample and out-of-sample performance." — MadEvolve (2026)

**Implication:** Our 20-param budget in evolutionary optimizer is correct. Maintain this constraint.

### 4. Structural Constraints > Model Capability
> "The most impactful finding: structural constraints (sealed sandboxes, parameter budgets, safety critics, hardware-aware search) are more important than raw model capability for achieving genuine autonomy." — Multiple papers

**Implication:** Our deterministic gates in `_validate_proposal` are the right architecture. Enhance them.

### 5. Execution Alpha = Transaction Cost Alpha
> "The 425μs latency sacrifice for incorporating intelligent lock-up decisions yields a 32% improvement in net returns." — LA-NAS-Lock (2025)

**Implication:** TCA enhancement is worth doing even though it's not glamorous.

---

*Generated: 2026-08-29*
*Source: docs/quant papers autonomous quant trading systems.txt + online research*
