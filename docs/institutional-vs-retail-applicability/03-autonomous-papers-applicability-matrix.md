# 03 — Autonomous Quant Papers: Applicability Matrix

> Row-by-row grading of every paper/framework Kimi listed in `docs/quant papers autonomous quant trading systems .txt`.  
> **Relevance** = solves a real, currently-bleeding problem in *this* retail MT5 codebase (0=none, 5=P0).

---

## How to read

- **Grade:** Institutional / Retail / Hybrid (per `01-taxonomy`).
- **Relevance:** 5 = fix today, 0 = do not build.
- **Unverified:** arXiv link not confirmed or claim (e.g., Sharpe 3.11) flagged as implausible without sealed sandbox audit.

---

## Tier A — Autonomous Alpha Generation

| Paper / Framework | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **101 Formulaic Alphas (Kakushadze)** | 2016 | Hybrid | **4** | Already partially ported: `core/alpha_factors.py:1` implements 23 time-series-ified alphas with primitives (`ts_rank`, `scale`). **Retail-relevant as feature library**, but decay premise is correct — static alphas need lifecycle management (`core/alpha_decay_monitor.py:1` exists but threshold is naive). Not a paper to "implement" — it's a dataset to rotate through. |
| **Beyond Prompting (Agentic AI factor investing)** | 2026 | Institutional | 2 | Claims Sharpe 3.11, 59% return — *unverified, no arXiv found*. Concept (LLM as self-directed researcher) is institutional (cross-sectional). Retail takeaway: iterative hypothesis→test loop is valid, but don't wire an LLM to generate alphas without AQuA-style sandbox. |
| **AgonAlpha (prompt engineering alpha discovery)** | 2026 | Institutional | 1 | Time-series adaptation possible but unverified. Lower priority than AQuA/FactorMiner due to weaker regularization story. |
| **MadEvolve / OpenEvolve** | 2026 | Hybrid | 2 | "Evolved Bitcoin Sharpe -2→3.99" — unverified. Evolutionary optimization *concept* is零售-compatible (mutate `core/alpha_factors.py` primitives) but paper is crypto-specific and overfit-prone without parameter budgeting. |
| **FactorMiner (Ralph Loop)** | 2026 | Hybrid | **4** | **Retail-applicable.** Experience memory (accumulated validated evidence → future hypothesis) is exactly what `core/alpha_decay_monitor.py:34` + `alpha_decay_state.json` lack: today state stores IC history but not *why* an alpha decayed. Port the memory idea, not the cross-sectional mining. |
| **AlphaAgent (AST + hypothesis alignment)** | 2025 | Hybrid | **4** | **Retail-applicable.** Three regularizations map retail-wise: originality via AST similarity on factor expressions (`scale(rank(...))` trees), complexity cap (15–20 params — currently uncapped), and hypothesis-factor alignment via `economic_dynamics_catalog.py:134` dynamics tags. |
| **AlphaGen (PPO synergistic collection)** | 2023 | Institutional | 2 | RL over cross-sectional factor collections — not over single-asset ensemble weights. Retail analogue is `core/alpha_ensemble.py:329` weight optimization, but PPO is overkill for 23 weights; Bayesian search suffices. |
| **RD-Agent(Q) (Microsoft)** | 2024–25 | Institutional | 2 | Research Agent + Trading Agent dual loop on Qlib (cross-sectional). Retail port: dual-velocity lanes already exist (`operations/dual_velocity.py`) — the idea is validated, the code is not drop-in. |
| **AlphaEvolve (DeepMind)** | 2025 | Institutional | 1 | General NAS/coding agent — institutional HFT focus, not alpha discovery. |

---

## Tier B — Deep RL for Trading

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **Deep RL Ensemble (PPO+A2C+DDPG) for Stock Trading** | 2025 | Institutional | 2 | Multi-stock portfolio env, daily. Retail FX `M15` with 1R stops is different MDP (action=lot size, not simplex weights). Ensemble idea ports but not the env. |
| **Practical DRL Approach (Xiong et al. 2018)** | 2018 | Institutional | 1 | Foundational but dated; superseded by FinRL. |
| **DRL for Crypto Trading (Lucarelli & Borrotti)** | 2019 | Institutional | 0 | Crypto-specific, not retail FX. |
| **Adaptive Quantitative Trading (Imitative RL)** | 2020 | Hybrid | 3 | Imitation + RL is retail-relevant for *advisory* (learn from human/trend filter), but current `research/ppo_trainer.py` already covers this lane — don't add a second RL stack. |
| **Portfolio Optimization via DRL (DQN + replay)** | — | Institutional | 1 | DQN for simplex allocation — N=1 retail has no allocation to allocate. |
| **FinRL (library)** | 2020 | Institutional | 2 | Stock portfolio gym. Useful as *reference* for reward shaping, but `research/ppo_trainer.py` + `meta_rl_trainer.py` already exist — evaluate, don't adopt wholesale. |
| **Bridging Markowitz & DRL (Benhamou)** | 2020 | Institutional | 2 | Markowitz+DRL bridge — again N≫1; retail no Markowitz. |
| **Moody 1998 (Recurrent RL)** | 1998 | Hybrid | 3 | Original RRL for trading *is* single-asset — rare retail-native RL paper. Worth reading over PPO ensemble for M15 execution. |

---

## Tier C — Meta-Learning & Fast Adaptation

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **Meta-Learning Optimal Mixture (MAML for portfolio)** | 2025 | Institutional | 2 | Mixture of strategies meta-initialization — strong but N≫1. Retail regime router (`core/hierarchical_portfolio.py:148`) could use MAML to meta-learn regime-weight initialization, but current router is rule-based (Hurst+ADX+ATR). Low effort to retrofit. |
| **Dual-Head PPO (XGBoost↔ARIMA switching)** | 2025 | Hybrid | 3 | Adaptive model selection (vol→XGBoost, calm→ARIMA) is **directly portable** to retail: volatility regime already computed (`hierarchical_portfolio.py:78`). Current router does fixed multipliers (`alpha_ensemble.py:90`); could learn them. |
| **MARS (Meta-Adaptive RL + Safety-Critic)** | 2025 | Hybrid | **5** | **P0 — solves a bleeding problem.** Heterogeneous ensemble + Safety-Critic heterogeneity is the *only* paper that explicitly enforces per-agent risk profiles while meta-adapting weights. This codebase's router uses static `DEFAULT_REGIME_WEIGHTS` (`hierarchical_portfolio.py:180`) with no learned adaptation and no safety critic — MARS fills both. |
| **Meta-RL for Trading (2023, intraday)** | 2023 | Hybrid | 3 | Intraday MAML — retail-relevant timeframe (M15), but crypto-retail specifics. |
| **Meta-learning for Crypto (MAML + Prototypical, 81% regime acc)** | 2026 | Institutional | 1 | Crypto few-shot regime classification <50ms — FX regime calc is already <1ms (`hierarchical_portfolio.py:74`); don't need few-shot Prototypical for 4 regimes. |
| **FinPFN (meta-learned predictor)** | 2025–26 | Institutional | 1 | A-share zero-shot — not FX. |
| **ReCAP (continual adaptive portfolio)** | 2026 | Institutional | 1 | Portfolio policy library — institutional. |

---

## Tier D — Causal RL

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **CRL-ABSO (Causal RL)** | 2026 | Hybrid | **4** | **High retail relevance.** Online causal graph learning for regime shifts — maps onto `intelligence/autonomous_causal_engine.py` + `macro_graph.py` already in this codebase. Paper's reported +22% CNR / +50% SSR is unverified but the *method* (causal feature selection before RL) is sound and reduces spurious alphas. Recommend piloting on top of existing causal DAG. |
| **CLEAR (Causal + Env Abstraction)** | 2025 | Hybrid | **4** | Same — identifies causally relevant state features to prune `feature_builder.py` inputs. Directly reduces overfitting where `core/alpha_ensemble.py:390` currently IC-weights all 23 alphas without causal pre-filter. |

---

## Tier E — Transformers & Time-Series

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **STL-Transformer (R²=0.972 on MSFT)** | 2025 | Institutional | 1 | R² 0.972 is a **red flag** (leakage / single-stock overfit). Retail FX close prediction with Transformer is tempting but M15 close is near-random walk — leakage-safe claim unverified. Do not chase. |
| **Transformer Comparative Study (encoder-only, walk-forward)** | 2026 | Hybrid | 2 | Leakage-safe walk-forward is correct, but FX M15 forecasting with Transformer underperforms simpler vol models at this horizon — evidence weak. |
| **Multivariate Transformer** | 2026 | Institutional | 1 | Multi-asset multivariate — N>1. |
| **Kronos (this project's actual model)** | — | Retail | 5 | Already in `research/kronos_bridge.py`, `kronos_mt5_adapter.py`, `models/`. Beats generic Transformer papers by being tokenization-native. Don't add a second TS forecaster. |

---

## Tier F — Regime Detection & Switching

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **Regime Shift Models (Markov Switching AR)** | 2019 | Hybrid | 3 | Markov switching is retail-applicable and arguably cleaner than Hurst thresholding (`hierarchical_portfolio.py:93` VIX≥32 hard cutoffs). But current 4-regime detector already works; MS-AR is a P1 swap, not P0. |
| **Evaluating Building Blocks (regime-specific asset classes)** | 2018 | Institutional | 1 | Multi-asset regime paper — not FX single-asset. |
| **Logic-Q (program sketch trend ID)** | 2025 | Hybrid | 2 | HFT-ish program sketch — over-engineered for 4-regime M15. |

---

## Tier G — Evolutionary / AutoML / Execution

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **LA-NAS-Lock (425µs, NAS+lockup)** | 2025 | Institutional | 0 | **Do not build.** M15 bar ~15 min; broker spread 0.5–1.5 pip dominates 425µs inference. Retail execution is `core/order_execution.py` + `execution_policy.py`, not GPU NAS. |
| **vLLM / TensorRT-LLM / Dynamo** | 2025–26 | Institutional | 0 | Infra for serving 1000 QPS LLM trading — this system polls MT5 at 1–2 sec (`realtime_news_ingest.py`). Not needed. |
| **AMA / AI-Trader / CTBench / PLUTUS** | 2025–26 | Institutional | 2 | Benchmarks — read for evaluation philosophy, but they benchmark cross-market multi-agent; retail FX benchmark is simpler: CPCV + broker replay (`research/broker_replay.py:1`). |

---

## Tier H — Foundational / Survey

| Paper | Year | Grade | Relevance | Verdict |
|---|---|---|---|---|
| **Advances in Financial ML (Author-C)** | 2018 | Hybrid | **5** | **P0 bible.** Purge/CPCV, triple-barrier labeling, meta-labeling (`core/meta_labeling.py` exists), backtest overfitting — already partially implemented but not fully audited. Re-read before any "new paper" work. |
| **Machine Learning for Asset Managers (Author-C)** | 2020 | Institutional | 3 | Portfolio construction (HRP etc.) — institutional; read only if tripwire #1 fires. |
| **MAML (Finn et al. 2017), PPO (Schulman 2017), SAC** | 2017–18 | Hybrid | 3 | Already in `research/ppo_trainer.py`, `meta_rl_trainer.py`. No separate implementation. |

---

## Summary — What is institutional theater vs retail cure

| Bucket | Do NOT build (theater) | DO build (cure) |
|---|---|---|
| **Alpha** | 101 Alphas as cross-sectional_rank; AgonAlpha/Beyond Prompting without sandbox | FactorMiner memory + AlphaAgent regularization ported to time-series factor lifecycle |
| **RL** | FinRL ensemble / DQN simplex / LA-NAS-Lock | Moody RRL (single-asset) + MARS Safety-Critic + CRL/CLEAR causal pre-filter |
| **Meta** | Crypto few-shot Prototypical, FinPFN A-share | MARS + Dual-Head switching idea (learn regime→alpha multipliers) |
| **Execution** | NAS / vLLM / Dynamo | `risk_manager.py` circuit breakers (already strong) + `cpcv_validation.py` audit |
| **Portfolio** | Author-P–Author-K direct RMT / ES_99 LP | Author-P–Author-K *discipline* (r-budget, measurement vs optimization) — cheap, no code until N>1 |

---

## Unverified claim log (treat as marketing until audited)

- Beyond Prompting Sharpe 3.11 / 59% — no arXiv, no code, no transaction costs disclosed.
- MadEvolve Sharpe -2→3.99 — crypto, likely in-sample evolved.
- STL-Transformer R² 0.972 — single-stock, likely leakage.
- Most 2026 papers (FactorMiner, AQuA, MARS) — no peer review; treat architecture ideas as inspi, not SOTA truth.

> **Rule:** Before citing a 2025–2026 Sharpe in a design doc, demand a CPCV curve and a transaction-cost table. `research/broker_replay.py` is the only Sharpe that counts here.
