# Task 1 Brief: 9-Tier Quant Gate Stack & 7-Factor Composite Ranking Engine

**Files:**
- Create: `core/quant_gates_and_pipeline.py`
- Test: `tests/test_quant_gates_and_pipeline.py`

**Requirements:**
1. Implement `QuantGate(str, Enum)` with 9 gates:
   `DATA_QUALITY_GATE`, `SIGNAL_ROBUSTNESS_GATE`, `RISK_GATE`, `EXECUTION_GATE`, `PORTFOLIO_FIT_GATE`, `STABILITY_GATE`, `MONTE_CARLO_GATE`, `STRESS_TEST_GATE`, `LIVE_SHADOW_GATE`.
2. Implement `calculate_composite_score(metrics: Dict[str, float]) -> float`:
   25% Return + 20% DD Control + 15% Regime + 15% Exec + 10% MC + 10% Div + 5% Capacity.
3. Implement `QuantGateVerdict` dataclass:
   `passed: bool`, `passed_gates: List[str]`, `failed_gates: List[str]`, `gate_scores: Dict[str, float]`, `composite_score: float`, `execution_authority: bool = False`, `to_dict()`.
4. Implement `QuantGateStack`:
   `evaluate_candidate(metrics: Dict[str, Any]) -> QuantGateVerdict`.
5. In `tests/test_quant_gates_and_pipeline.py`, write tests for `QuantGate` taxonomy, `calculate_composite_score`, and `QuantGateStack` passing and failing conditions.
6. Run tests with `rtk pytest tests/test_quant_gates_and_pipeline.py -o addopts="" -v`.
7. Commit with `rtk git commit -m "feat(gates): implement 9-tier QuantGateStack and calculate_composite_score"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-23-quant-gates-and-deployment-pipeline/task-1-report.md`.
