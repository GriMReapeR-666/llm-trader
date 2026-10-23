# Task 2 Brief: Advanced Regime-Switching Monte Carlo Engine

**Files:**
- Modify: `core/growth_and_ruin_engine.py`
- Modify/Test: `tests/test_growth_and_ruin_engine.py`

**Requirements:**
1. Implement `MonteCarloRuinResult` dataclass:
   - `starting_capital`, `num_simulations`, `num_trades`, `median_terminal_equity`, `p5_terminal_equity`, `p95_terminal_equity`, `median_max_dd`, `probability_of_ruin`, `probability_dd_over_20`, `confidence_target_flip`, `execution_authority = False`, `to_dict()`.
2. Implement `AdvancedMonteCarloEngine`:
   - `__init__(self, seed: Optional[int] = 42)`
   - `run_simulation(self, trades_by_regime: Dict[str, np.ndarray], starting_capital: float = 1000.0, n_trades: int = 100, n_simulations: int = 1000, ruin_dd_threshold: float = 0.50, target_multiplier: float = 2.0, transition_matrix: Optional[np.ndarray] = None) -> MonteCarloRuinResult`
   - Simulates Markov regime switching between regimes and computes ruin, deep drawdown, and flip target confidence.
3. Add `test_advanced_monte_carlo_engine` in `tests/test_growth_and_ruin_engine.py`.
4. Run tests with `rtk pytest tests/test_growth_and_ruin_engine.py -k test_advanced_monte_carlo_engine -o addopts="" -v`.
5. Commit with `rtk git commit -m "feat(monte_carlo): implement AdvancedMonteCarloEngine with regime switching and ruin bounds"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-23-small-capital-growth-monte-carlo/task-2-report.md`.
