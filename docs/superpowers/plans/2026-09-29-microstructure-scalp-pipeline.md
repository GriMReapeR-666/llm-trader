# Microstructure Scalp Trading Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the missing Hawkes Process and Avellaneda-Stoikov engines, wire OFI, VPIN, Roll Model, Phantom Liquidity, and OU Time Stops into the live scalp decision pipeline, and register the `momentum_cascade_v1` setup.

**Architecture:** Approach A (Unified Microstructure Layer) implemented across two distinct phases. Phase 1 integrates existing standalone engines (OFI, VPIN, Roll, OU) into feature extraction, safety shields, and position exits. Phase 2 implements Hawkes self-exciting point-process calibration and Avellaneda-Stoikov reservation pricing, registering `momentum_cascade_v1` with end-to-end regression validation.

**Tech Stack:** Python 3.10+, NumPy, SciPy (optional fallback), SQLite3 (`trading_state.db`), Pytest.

**Spec:** [docs/superpowers/specs/2026-09-29-microstructure-scalp-pipeline-design.md](file:///c:/Users/User/OneDrive/Desktop/selfmade%20game%20projects/llm%20trader%20-%20Checkpoint%20dicisipline%20soilder%20-%20used%20in%20antigravity/docs/superpowers/specs/2026-09-29-microstructure-scalp-pipeline-design.md)

---

## Global Constraints

- Never break existing journaled trade schemas or the 150 historical trades in `trading_state.db`.
- All newly added scalar features must be finite (`math.isfinite`) and validate cleanly in `intelligence/feature_schema.py`.
- No new heavy third-party dependencies; use standard library `math`, `datetime`, and already-installed `numpy` / `scipy`.
- Every task must follow strict Test-Driven Development (TDD): write the failing test first, verify failure, implement minimal code, verify pass, commit.

---

### Task 1: Build Hawkes Process Point-Process Engine

**Files:**
- Create: `core/hawkes_process.py`
- Test: `tests/test_hawkes_process.py`

**Interfaces:**
- Produces: `calibrate_hawkes_process(timestamps: List[float]) -> HawkesResult`
- Produces: `HawkesResult` dataclass with fields `mu`, `alpha`, `beta`, `branching_ratio`, `regime`, `is_cascade`

- [x] **Step 1: Write the failing unit tests for Hawkes Process**
  Create `tests/test_hawkes_process.py` covering:
  - Branching ratio calculation $n = \alpha / \beta$.
  - Regime detection: `n >= 0.85` $\implies$ `MOMENTUM_CASCADE`, `n < 0.30` $\implies$ `POISSON_BASELINE`, `n >= 1.0` $\implies$ `SUPER_CRITICAL`.
  - Calibration on synthetic clustered timestamps vs uniform timestamps.
  - Graceful fallback on insufficient timestamps ($N < 10$).

- [x] **Step 2: Run test to confirm it fails**
  Run `rtk pytest tests/test_hawkes_process.py` and verify `ModuleNotFoundError: No module named 'core.hawkes_process'`.

- [x] **Step 3: Implement `core/hawkes_process.py`**
  Implement:
  - `HawkesResult` dataclass.
  - Fast analytical log-likelihood computation:
    $$\ln L(\mu, \alpha, \beta) = \sum_{i=1}^M \ln \lambda(t_i) - \int_0^T \lambda(s) \, ds$$
    $$\int_0^T \lambda(s) \, ds = \mu T + \frac{\alpha}{\beta} \sum_{i=1}^M \left(1 - e^{-\beta (T - t_i)}\right)$$
  - Recursive intensity accumulation for $O(M)$ evaluation.
  - Safe bounded parameter optimizer.

- [x] **Step 4: Run test to confirm it passes**
  Run `rtk pytest tests/test_hawkes_process.py`.

- [x] **Step 5: Git commit Task 1**
  `rtk git add core/hawkes_process.py tests/test_hawkes_process.py`
  `rtk git commit -m "feat(quant): implement Hawkes point-process branching ratio calibration"`

---

### Task 2: Build Avellaneda-Stoikov Reservation Pricing & Spread Engine

**Files:**
- Create: `core/avellaneda_stoikov.py`
- Test: `tests/test_avellaneda_stoikov.py`

**Interfaces:**
- Produces: `calculate_reservation_price(mid_price: float, inventory_units: float, gamma: float, volatility: float, horizon_seconds: float) -> float`
- Produces: `calculate_optimal_spread(gamma: float, volatility: float, horizon_seconds: float, kappa: float) -> float`
- Produces: `AvellanedaStoikovEngine` class with `compute_quotes(mid_price, inventory_units, volatility, horizon_seconds)`

- [x] **Step 1: Write the failing unit tests for Avellaneda-Stoikov**
  Create `tests/test_avellaneda_stoikov.py` covering:
  - Reservation price discount when long ($q > 0 \implies r < s$).
  - Reservation price premium when short ($q < 0 \implies r > s$).
  - Optimal half-spread scaling with volatility $\sigma$ and risk aversion $\gamma$.
  - Bound checks ensuring finite prices and non-negative spreads.

- [x] **Step 2: Run test to confirm it fails**
  Run `rtk pytest tests/test_avellaneda_stoikov.py` and verify import failure.

- [x] **Step 3: Implement `core/avellaneda_stoikov.py`**
  Implement:
  - $r(s, q, t) = s - q \gamma \sigma^2 (T - t)$
  - $\delta^* = \frac{1}{2} \gamma \sigma^2 (T - t) + \frac{1}{\gamma} \ln(1 + \frac{\gamma}{\kappa})$
  - Optimal bid $r - \delta^*$ and ask $r + \delta^*$.
  - Micro-price limit price recommender for scalping orders.

- [x] **Step 4: Run test to confirm it passes**
  Run `rtk pytest tests/test_avellaneda_stoikov.py`.

- [x] **Step 5: Git commit Task 2**
  `rtk git add core/avellaneda_stoikov.py tests/test_avellaneda_stoikov.py`
  `rtk git commit -m "feat(quant): implement Avellaneda-Stoikov reservation pricing engine"`

---

### Task 3: Wire Microstructure Features into `feature_builder.py` and `feature_schema.py`

**Files:**
- Modify: `intelligence/feature_builder.py:195-245`
- Modify: `intelligence/feature_schema.py:40-100`
- Test: `tests/test_feature_builder.py`

**Interfaces:**
- Consumes: `core/hawkes_process.py`, `intelligence/macro_alpha_signals_and_microstructure.py` (VPIN), `core/urgency_sor.py` (OFI), `core/market_structure_edges.py` (Roll model)
- Produces: Enhanced `build_snapshot` returning `hawkes_branching_ratio`, `hawkes_cascade_active`, `vpin_toxicity`, `ofi_urgency_score`, `roll_implied_spread`, `phantom_liquidity_flag`, `ou_half_life_bars`.

- [x] **Step 1: Write tests for microstructure feature extraction**
  Update or add test in `tests/test_feature_builder.py` verifying that when microstructure context is provided or computed, the new scalar fields are correctly populated and validate against `feature_schema`.

- [x] **Step 2: Run test to verify failure / missing features**
  Run `rtk pytest tests/test_feature_builder.py`.

- [x] **Step 3: Update `feature_schema.py` & `feature_builder.py`**
  - Add new optional scalar definitions to `FEATURE_DEFINITIONS` in `feature_schema.py`.
  - In `build_snapshot`, integrate microstructure calculations safely with fallback defaults when raw tick streams are unavailable.

- [x] **Step 4: Run tests to confirm passing**
  Run `rtk pytest tests/test_feature_builder.py tests/test_feature_schema.py`.

- [x] **Step 5: Git commit Task 3**
  `rtk git add intelligence/feature_builder.py intelligence/feature_schema.py tests/test_feature_builder.py`
  `rtk git commit -m "feat(features): wire microstructure and point-process features into snapshot builder"`

---

### Task 4: Register `momentum_cascade_v1` in `setup_registry.py`

**Files:**
- Modify: `research/setup_registry.py:28-118`
- Test: `tests/test_setup_registry.py`

**Interfaces:**
- Produces: SetupSpec for `momentum_cascade_v1`.
- Setup classification logic in `classify(features, session, strategy)`.

- [x] **Step 1: Write failing test in `tests/test_setup_registry.py`**
  Test that features with `hawkes_cascade_active=True`, `adx >= 25`, `volume_confirmed=True`, `confirmed_candle=True`, and `vpin_toxicity < 0.70` classify as `momentum_cascade_v1` with `min_rr=2.0`.
  Ensure all legacy tests for `trend_pullback_v1`, `liquidity_sweep_reversal_v1`, `range_reversal_v1`, and `asian_range_breakout_v1` remain 100% green.

- [x] **Step 2: Run test to confirm it fails**
  Run `rtk pytest tests/test_setup_registry.py`.

- [x] **Step 3: Implement `momentum_cascade_v1` in `setup_registry.py`**
  Add setup specification:
  ```python
  "momentum_cascade_v1": SetupSpec(
      "momentum_cascade_v1", "1",
      "Hawkes self-exciting momentum cascade with volume and VPIN toxicity confirmation.",
      2.0, ("LONDON", "NY-LONDON", "NEW YORK"),
      "hawkes branching ratio >= 0.85 + volume surge + confirmed bar close",
      "close back through initiation bar or adverse VPIN spike >= 0.70",
      "beyond cascade initiation swing",
      "next liquidity pool / >=2R",
      "minutes-to-hours", True
  )
  ```
  Update `classify()` to evaluate `hawkes_cascade_active` and `vpin_toxicity`.

- [x] **Step 4: Run tests to verify all pass**
  Run `rtk pytest tests/test_setup_registry.py`.

- [x] **Step 5: Git commit Task 4**
  `rtk git add research/setup_registry.py tests/test_setup_registry.py`
  `rtk git commit -m "feat(registry): register momentum_cascade_v1 deterministic setup"`

---

### Task 5: Wire Phantom Liquidity & VPIN Adverse Selection Shields into `trading_safety.py`

**Files:**
- Modify: `core/trading_safety.py:950-1020`
- Test: `tests/test_trading_safety.py`

**Interfaces:**
- Consumes: `detect_phantom_liquidity` from `core/market_structure_edges.py`, `vpin_toxicity` from proposal features.
- Produces: Safety checks `phantom_liquidity_clear` and `vpin_toxicity_acceptable` during order validation.

- [x] **Step 1: Write failing test in `tests/test_trading_safety.py`**
  Add tests asserting that:
  - If `vpin_toxicity >= 0.75`, the trade is rejected with reason `"vpin toxicity too high"`.
  - If `phantom_liquidity_flag == True`, the trade is rejected with reason `"phantom liquidity / quote stuffing detected"`.

- [x] **Step 2: Run test to confirm it fails**
  Run `rtk pytest tests/test_trading_safety.py`.

- [x] **Step 3: Implement safety checks in `core/trading_safety.py`**
  In `TradingSafety.validate_order()` / proposal verification:
  - Check `vpin_toxicity <= 0.75`.
  - Check `phantom_liquidity_flag is False`.
  - Add corresponding validation flags to validation result dictionary.

- [x] **Step 4: Run tests to confirm passing**
  Run `rtk pytest tests/test_trading_safety.py`.

- [x] **Step 5: Git commit Task 5**
  `rtk git add core/trading_safety.py tests/test_trading_safety.py`
  `rtk git commit -m "feat(safety): add VPIN adverse selection and phantom liquidity pre-flight guards"`

---

### Task 6: Wire Ornstein-Uhlenbeck Time Stops into Trade Position Lifecycle

**Files:**
- Modify: `core/ou_time_stop.py`
- Modify: `core/bounded_autonomy_pipeline.py` / `operations/learning_loop.py`
- Test: `tests/test_ou_time_stop_lifecycle.py`

**Interfaces:**
- Consumes: `OUTimeStopEngine`
- Produces: Position stagnant exit signal when holding bars exceed $2.5 \times t_{1/2}$ for range reversals.

- [x] **Step 1: Write failing test for OU lifecycle evaluation**
  Create `tests/test_ou_time_stop_lifecycle.py` testing that when a range reversal scalp trade is held beyond $2.5 \times \text{half\_life}$, an exit recommendation `TIME_STOP_CLOSE` is triggered.

- [x] **Step 2: Run test to confirm failure**
  Run `rtk pytest tests/test_ou_time_stop_lifecycle.py`.

- [x] **Step 3: Implement OU time-stop evaluation helper**
  Provide helper in `core/ou_time_stop.py` linking directly to trade exit checking.

- [x] **Step 4: Run test to confirm pass**
  Run `rtk pytest tests/test_ou_time_stop_lifecycle.py`.

- [x] **Step 5: Git commit Task 6**
  `rtk git add core/ou_time_stop.py tests/test_ou_time_stop_lifecycle.py`
  `rtk git commit -m "feat(lifecycle): implement OU half-life dynamic time stop position exit"`

---

### Task 7: End-to-End Scalp Pipeline Integration & Backward Compatibility Verification

**Files:**
- Create: `tests/test_microstructure_scalp_pipeline_e2e.py`

**Interfaces:**
- Validates the entire flow: tick generation $\to$ Hawkes/Avellaneda/OFI/VPIN $\to$ feature snapshot $\to$ setup classification $\to$ safety validation $\to$ execution proposal.

- [x] **Step 1: Write the end-to-end integration test**
  Verify:
  1. A cascade tick sequence generates $n \ge 0.85$ and successfully classifies as `momentum_cascade_v1`.
  2. A toxic VPIN burst ($\ge 0.75$) successfully trips the pre-flight risk shield and prevents order dispatch.
  3. All 150 historical trades in `trading_state.db` under `trend_pullback_v1` continue to validate cleanly with zero regressions.

- [x] **Step 2: Run test suite**
  Run `rtk pytest tests/test_microstructure_scalp_pipeline_e2e.py`.

- [x] **Step 3: Run full regression suite**
  Run `rtk pytest tests/test_setup_registry.py tests/test_feature_builder.py tests/test_trading_safety.py`.

- [x] **Step 4: Git commit Task 7**
  `rtk git add tests/test_microstructure_scalp_pipeline_e2e.py`
  `rtk git commit -m "test(integration): verify end-to-end microstructure scalp pipeline and backward compatibility"`
