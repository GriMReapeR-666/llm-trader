# Part 8: Constrained Reinforcement Learning (RL) Policy Engine — Implementation Plan

**Goal:** Implement the institutional constrained RL policy engine (`core/constrained_rl_policy.py`) featuring an 8-factor state space, 6 discrete constrained risk-management actions, hard safety guardrails (DD $\le 10\%$, Exposure $\le 1.0$), risk-adjusted reward shaping, and comparison against deterministic baselines.

---

## Task 1: 8-Factor State Feature Extraction & Constrained Action Space (TDD)
- **Files to create/modify:**
  - `core/constrained_rl_policy.py`
  - `tests/test_constrained_rl_policy.py`
- **Steps:**
  1. Write failing tests in `tests/test_constrained_rl_policy.py` for `RLStateFeatureExtractor` and `SafetyInvariantsGuard`.
  2. Implement `RLStateFeatureExtractor` (computing volatility, trend, momentum, spread, macro, liquidity, order book imbalance, regime flag).
  3. Implement `SafetyInvariantsGuard` (clamping exposure in $[-1.0, 1.0]$, forcing `FLATTEN` if drawdown $\ge 10\%$).
  4. Verify all tests pass (GREEN).

## Task 2: Policy Architecture, Risk-Adjusted Reward Shaping & Baseline Comparator (TDD)
- **Files to create/modify:**
  - `core/constrained_rl_policy.py`
  - `tests/test_constrained_rl_policy.py`
- **Steps:**
  1. Write failing tests for `ConstrainedPolicyAgent`, `compute_risk_adjusted_reward()`, and deterministic baseline simulation.
  2. Implement `ConstrainedPolicyAgent` (lightweight actor-critic / linear softmax policy with state discretization or continuous linear head).
  3. Implement reward shaping ($R_t = r_t - \lambda_{dd}\Delta DD_t - \lambda_{turnover}\text{Cost} - \lambda_{vol}\sigma$).
  4. Implement `DeterministicBaselineStrategy`.
  5. Verify all tests pass (GREEN).

## Task 3: End-to-End Pipeline, Gate Verification & CLI Integration
- **Files to create/modify:**
  - `core/constrained_rl_policy.py`
  - `mt5_trader.py`
  - `tests/test_constrained_rl_policy.py`
- **Steps:**
  1. Implement `ConstrainedRLPipeline.evaluate_policy(dataset)` producing `ConstrainedRLReport`.
  2. Add formatted ASCII table comparing RL policy vs Deterministic Baseline.
  3. Add CLI subcommand in `mt5_trader.py`: `quant rl-evaluate --symbol <SYM> --timeframe <TF> --bars <N>`.
  4. Verify complete test suite and CLI execution.
