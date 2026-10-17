# Autonomous Quant Validation & Robustness Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the missing quant validation primitives and quality-diversity search components required for 100% compliance with institutional-grade autonomous trading standards: Time-Under-Water duration metrics, Top-5% trade removal break-it testing, automated null-hypothesis trade permutation tests, and MAP-Elites quality-diversity behavioral archiving.

**Architecture:** Extend existing decoupled modules (`core.metrics`, `research.research_stress`, `research.baseline_research`, and `core.evolutionary_optimizer`) following strict fail-closed, deterministic principles, complete with unit tests verifying mathematical guarantees.

**Tech Stack:** Python 3.10+, NumPy, SciPy, pytest.

**Spec:** In-session requirements from autonomous quant validation guidelines.

## Global Constraints

- Fail-closed execution: Any missing, non-finite, or malformed inputs must return an explicit error or safe default; never throw unhandled exceptions.
- Deterministic reproducibility: All random permutation, bootstrap, or simulation functions must accept an explicit `seed` parameter with sensible defaults.
- Zero live execution authority: All research and validation modules must declare or enforce `execution_authority = False`.
- Performance efficiency: Vectorized NumPy implementations preferred over raw Python loops.

---

### Task 1: Time-Under-Water Duration Metric

**Files:**
- Modify: `core/metrics.py`
- Test: `tests/test_robustness_gaps.py`

**Interfaces:**
- Produces: `calculate_time_under_water(equity_curve: Sequence[float]) -> Dict[str, Any]` returning `{"max_underwater_bars": int, "avg_underwater_bars": float, "current_underwater_bars": int, "underwater_periods": List[int]}`
- Consumes: Standard sequence of positive finite equity floats.

- [ ] **Step 1: Write the failing test for `calculate_time_under_water`**

```python
# In tests/test_robustness_gaps.py
from core.metrics import calculate_time_under_water

def test_calculate_time_under_water_known_path():
    # Peak at 100, drops for 3 bars (95, 90, 98), reaches new peak at 105 (recovery length = 3)
    # Drops for 2 bars (102, 101), end of series (current underwater = 2)
    equity = [100.0, 95.0, 90.0, 98.0, 105.0, 102.0, 101.0]
    result = calculate_time_under_water(equity)
    assert result["max_underwater_bars"] == 3
    assert result["current_underwater_bars"] == 2
    assert result["avg_underwater_bars"] == 3.0
    assert result["underwater_periods"] == [3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_calculate_time_under_water_known_path`
Expected: FAIL with `ImportError: cannot import name 'calculate_time_under_water'`

- [ ] **Step 3: Implement `calculate_time_under_water` in `core/metrics.py`**

```python
def calculate_time_under_water(
    equity_curve: Sequence[float],
) -> Dict[str, Any]:
    """Calculate time-under-water metrics (drawdown duration in bars).
    
    Tracks bars spent strictly below previous all-time-high equity.
    """
    arr = np.asarray(equity_curve, dtype=float)
    if len(arr) < 2 or not np.all(np.isfinite(arr)):
        return {
            "max_underwater_bars": 0,
            "avg_underwater_bars": 0.0,
            "current_underwater_bars": 0,
            "underwater_periods": [],
        }

    running_max = np.maximum.accumulate(arr)
    is_underwater = arr < running_max

    completed_periods = []
    current_run = 0

    for under in is_underwater:
        if under:
            current_run += 1
        else:
            if current_run > 0:
                completed_periods.append(current_run)
                current_run = 0

    max_period = max(completed_periods + [current_run]) if (completed_periods or current_run > 0) else 0
    avg_period = float(np.mean(completed_periods)) if completed_periods else 0.0

    return {
        "max_underwater_bars": int(max_period),
        "avg_underwater_bars": round(avg_period, 2),
        "current_underwater_bars": int(current_run),
        "underwater_periods": completed_periods,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_calculate_time_under_water_known_path`
Expected: PASS

---

### Task 2: Break-It Stress Test: Remove Top 5% Trades

**Files:**
- Modify: `research/research_stress.py`
- Test: `tests/test_robustness_gaps.py`

**Interfaces:**
- Produces: `stress_remove_best_trades(r_values: List[float], drop_pct: float = 0.05, minimum_trades: int = 20) -> Dict[str, Any]`
- Consumes: List of trade R-multiples.

- [ ] **Step 1: Write the failing test for `stress_remove_best_trades`**

```python
from research.research_stress import stress_remove_best_trades

def test_stress_remove_best_trades():
    # 20 trades: 1 outlier winner of 10.0 R, others modest winners/losers
    trades = [10.0] + [0.5] * 10 + [-1.0] * 9  # 20 trades
    # Top 5% = 1 trade (10.0 R). Remaining 19 trades: 10 * 0.5 - 9 * 1.0 = 5 - 9 = -4.0 (loss)
    res = stress_remove_best_trades(trades, drop_pct=0.05, minimum_trades=20)
    assert res["status"] == "READY_FOR_REVIEW"
    assert res["trades_dropped"] == 1
    assert res["dropped_values"] == [10.0]
    assert res["post_trim_expectancy_r"] < 0
    assert res["is_robust_to_trim"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_stress_remove_best_trades`
Expected: FAIL with `ImportError: cannot import name 'stress_remove_best_trades'`

- [ ] **Step 3: Implement `stress_remove_best_trades` in `research/research_stress.py`**

```python
def stress_remove_best_trades(
    r_values: List[float],
    *,
    drop_pct: float = 0.05,
    minimum_trades: int = 20,
) -> Dict[str, Any]:
    """Break-it stress test: remove the best N% of trades and test if edge survives."""
    if not isinstance(r_values, list) or len(r_values) < minimum_trades:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": f"Sample size {len(r_values) if isinstance(r_values, list) else 0} below minimum {minimum_trades}",
            "execution_authority": False,
        }
    try:
        clean = [float(x) for x in r_values if math.isfinite(float(x))]
    except (TypeError, ValueError):
        return {"status": "INVALID_INPUT", "reason": "Non-numeric values", "execution_authority": False}

    if len(clean) < minimum_trades:
        return {"status": "INSUFFICIENT_DATA", "reason": "Not enough finite trades", "execution_authority": False}

    sorted_trades = sorted(clean, reverse=True)
    k_drop = max(1, int(math.ceil(len(sorted_trades) * drop_pct)))
    dropped = sorted_trades[:k_drop]
    remaining = sorted_trades[k_drop:]

    pre_exp = float(np.mean(clean))
    post_exp = float(np.mean(remaining)) if remaining else 0.0
    wins = [r for r in remaining if r > 0]
    losses = [r for r in remaining if r < 0]
    win_rate = len(wins) / len(remaining) if remaining else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    mc_res = monte_carlo(remaining, paths=1000) if remaining else {}

    return {
        "status": "READY_FOR_REVIEW",
        "original_trade_count": len(clean),
        "trades_dropped": k_drop,
        "dropped_values": dropped,
        "remaining_trade_count": len(remaining),
        "pre_trim_expectancy_r": round(pre_exp, 4),
        "post_trim_expectancy_r": round(post_exp, 4),
        "post_trim_win_rate": round(win_rate, 4),
        "post_trim_profit_factor": round(profit_factor, 4),
        "is_robust_to_trim": post_exp > 0.0 and profit_factor > 1.0,
        "monte_carlo_trimmed": mc_res,
        "execution_authority": False,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_stress_remove_best_trades`
Expected: PASS

---

### Task 3: Automated Null-Hypothesis Trade Permutation Test

**Files:**
- Modify: `research/baseline_research.py`
- Test: `tests/test_robustness_gaps.py`

**Interfaces:**
- Produces: `run_null_hypothesis_trade_permutation_test(r_values: List[float], n_permutations: int = 1000, seed: int = 42) -> Dict[str, Any]`
- Consumes: List of trade R-multiples.

- [ ] **Step 1: Write the failing test for `run_null_hypothesis_trade_permutation_test`**

```python
from research.baseline_research import run_null_hypothesis_trade_permutation_test

def test_null_hypothesis_permutation_strong_edge():
    # 50 consistent positive trades
    trades = [1.5, -0.8, 2.0, -1.0, 1.2, 0.9] * 10
    res = run_null_hypothesis_trade_permutation_test(trades, n_permutations=500, seed=42)
    assert res["status"] == "EVALUATED"
    assert res["p_value"] < 0.05
    assert res["beats_null_at_5pct"] is True

def test_null_hypothesis_permutation_pure_noise():
    # Symmetric 50-50 noise around 0
    trades = [1.0, -1.0] * 25
    res = run_null_hypothesis_trade_permutation_test(trades, n_permutations=500, seed=42)
    assert res["status"] == "EVALUATED"
    assert res["p_value"] >= 0.05
    assert res["beats_null_at_5pct"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_null_hypothesis_permutation`
Expected: FAIL with `ImportError: cannot import name 'run_null_hypothesis_trade_permutation_test'`

- [ ] **Step 3: Implement `run_null_hypothesis_trade_permutation_test` in `research/baseline_research.py`**

```python
def run_null_hypothesis_trade_permutation_test(
    r_values: List[float],
    *,
    n_permutations: int = 1000,
    seed: int = 42,
    minimum_trades: int = 20,
) -> Dict[str, Any]:
    """Test strategy edge against null hypothesis of random trade entries with identical magnitude/timing."""
    if not isinstance(r_values, list) or len(r_values) < minimum_trades:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": f"Sample size {len(r_values) if isinstance(r_values, list) else 0} below minimum {minimum_trades}",
            "execution_authority": False,
        }
    clean = np.asarray([float(x) for x in r_values if math.isfinite(float(x))], dtype=float)
    if len(clean) < minimum_trades:
        return {"status": "INSUFFICIENT_DATA", "reason": "Not enough finite trades", "execution_authority": False}

    actual_ev = float(np.mean(clean))
    rng = np.random.RandomState(seed)
    
    # Under null hypothesis H0 of no directional edge: trade direction is a random coin flip (+1 / -1)
    magnitudes = np.abs(clean)
    null_evs = np.zeros(n_permutations, dtype=float)
    for i in range(n_permutations):
        random_signs = rng.choice([-1.0, 1.0], size=len(magnitudes))
        null_evs[i] = np.mean(magnitudes * random_signs)

    # One-sided p-value: probability that random null entry produces EV >= actual EV
    exceedances = np.sum(null_evs >= actual_ev)
    p_value = float(exceedances / n_permutations)

    return {
        "status": "EVALUATED",
        "sample_size": len(clean),
        "actual_ev_r": round(actual_ev, 4),
        "null_ev_median": round(float(np.median(null_evs)), 4),
        "null_ev_p95": round(float(np.percentile(null_evs, 95)), 4),
        "p_value": round(p_value, 4),
        "beats_null_at_5pct": p_value < 0.05,
        "n_permutations": n_permutations,
        "execution_authority": False,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_null_hypothesis_permutation`
Expected: PASS

---

### Task 4: MAP-Elites Quality-Diversity Behavioral Archive

**Files:**
- Modify: `core/evolutionary_optimizer.py`
- Test: `tests/test_robustness_gaps.py`

**Interfaces:**
- Produces: `MAPElitesArchive(n_holding_bins: int = 5, n_risk_reward_bins: int = 5)`
- Methods: `add_candidate(gene: StrategyGene) -> bool`, `get_all_elites() -> List[StrategyGene]`, `coverage_ratio() -> float`

- [ ] **Step 1: Write the failing test for `MAPElitesArchive`**

```python
from core.evolutionary_optimizer import MAPElitesArchive, StrategyGene

def test_map_elites_archive_niche_placement_and_replacement():
    archive = MAPElitesArchive(n_holding_bins=5, n_risk_reward_bins=5)
    
    # Candidate 1: holding_period=10, take_profit/stop_loss = 3.0/1.5 = 2.0, fitness=1.2
    gene1 = StrategyGene(
        gene_id="g1", holding_period_bars=10, stop_loss_atr=1.5,
        take_profit_atr=3.0, fitness=1.2
    )
    added = archive.add_candidate(gene1)
    assert added is True
    assert len(archive.get_all_elites()) == 1
    
    # Candidate 2: same niche (holding=10, rr=2.0) but lower fitness=0.8 -> Rejected
    gene2 = StrategyGene(
        gene_id="g2", holding_period_bars=10, stop_loss_atr=1.5,
        take_profit_atr=3.0, fitness=0.8
    )
    assert archive.add_candidate(gene2) is False
    assert archive.get_all_elites()[0].gene_id == "g1"
    
    # Candidate 3: same niche but higher fitness=1.9 -> Replaces g1
    gene3 = StrategyGene(
        gene_id="g3", holding_period_bars=10, stop_loss_atr=1.5,
        take_profit_atr=3.0, fitness=1.9
    )
    assert archive.add_candidate(gene3) is True
    assert archive.get_all_elites()[0].gene_id == "g3"
    
    # Candidate 4: different niche (holding=45, rr=5.0) -> Added without collision
    gene4 = StrategyGene(
        gene_id="g4", holding_period_bars=45, stop_loss_atr=1.0,
        take_profit_atr=5.0, fitness=1.0
    )
    assert archive.add_candidate(gene4) is True
    assert len(archive.get_all_elites()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_map_elites_archive`
Expected: FAIL with `ImportError: cannot import name 'MAPElitesArchive'`

- [ ] **Step 3: Implement `MAPElitesArchive` in `core/evolutionary_optimizer.py`**

```python
class MAPElitesArchive:
    """Multi-dimensional Archive of Phenotypic Elites (MAP-Elites).
    
    Maintains a 2D behavioral niche grid:
      Dimension 1: Holding Period (speed/duration: scalp to swing)
      Dimension 2: Payoff Shape (Take Profit / Stop Loss ratio)
    
    Prevents population collapse into a single behavioral clone.
    """

    def __init__(
        self,
        n_holding_bins: int = 5,
        n_risk_reward_bins: int = 5,
        min_holding: int = 5,
        max_holding: int = 50,
        min_rr: float = 0.5,
        max_rr: float = 5.0,
    ):
        self.n_holding_bins = n_holding_bins
        self.n_risk_reward_bins = n_risk_reward_bins
        self.min_holding = min_holding
        self.max_holding = max_holding
        self.min_rr = min_rr
        self.max_rr = max_rr
        self.grid: Dict[Tuple[int, int], StrategyGene] = {}

    def _get_cell_coord(self, gene: StrategyGene) -> Tuple[int, int]:
        holding = float(gene.holding_period_bars)
        rr = (float(gene.take_profit_atr) / max(0.1, float(gene.stop_loss_atr)))

        # Bin 1: holding period
        norm_h = (holding - self.min_holding) / max(1.0, (self.max_holding - self.min_holding))
        bin_h = int(np.clip(norm_h * self.n_holding_bins, 0, self.n_holding_bins - 1))

        # Bin 2: risk-reward
        norm_rr = (rr - self.min_rr) / max(0.1, (self.max_rr - self.min_rr))
        bin_rr = int(np.clip(norm_rr * self.n_risk_reward_bins, 0, self.n_risk_reward_bins - 1))

        return (bin_h, bin_rr)

    def add_candidate(self, gene: StrategyGene) -> bool:
        """Add candidate to niche if cell is empty or candidate has strictly higher fitness."""
        coord = self._get_cell_coord(gene)
        current_elite = self.grid.get(coord)
        if current_elite is None or gene.fitness > current_elite.fitness:
            self.grid[coord] = copy.deepcopy(gene)
            return True
        return False

    def get_all_elites(self) -> List[StrategyGene]:
        """Return all distinct niche champions."""
        return list(self.grid.values())

    def coverage_ratio(self) -> float:
        """Fraction of behavioral cells filled."""
        total_cells = self.n_holding_bins * self.n_risk_reward_bins
        return len(self.grid) / total_cells if total_cells > 0 else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/test_robustness_gaps.py -k test_map_elites_archive`
Expected: PASS

---

### Task 5: Run Full Verification Suite

- [ ] **Step 1: Run complete `tests/test_robustness_gaps.py` test suite**

Run: `rtk pytest tests/test_robustness_gaps.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run regressions across core metrics and optimization tests**

Run: `rtk pytest tests/test_multiple_testing_corrections.py tests/test_walk_forward_validator.py tests/test_evolutionary_optimizer.py -v`
Expected: ALL PASS
