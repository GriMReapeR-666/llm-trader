# API Reference — Core Modules

> **Modules covered:** `core/alpha_factors.py`, `core/alpha_ensemble.py`, `core/trading_safety.py`
> **Last verified:** 2026-08-28

---

## Table of Contents

1. [alpha_factors.py — Quant Platform Alpha Factor Library](#1-alpha_factorspy)
2. [alpha_ensemble.py — Alpha Combination Engine](#2-alpha_ensemblepy)
3. [trading_safety.py — Deterministic Safety Primitives](#3-trading_safetypy)

---

## 1. `alpha_factors.py`

> **Path:** `core/alpha_factors.py`
> **Lines:** 525
> **Dependencies:** `numpy`, `pandas`
> **No LLM dependency.** Fully deterministic, testable in isolation.

### Module Docstring

```
Quant Platform Alpha101 Factor Library for Single-Asset FX Trading.

Implements 23 formulaic alpha factors from the Quant Platform 101 Formulaic
Alphas paper, adapted for single-asset time-series (Forex).

All factors return pd.Series aligned to the input index. Reusable primitives
are defined at module level so they can be composed into new custom factors.

References:
    Kakushadze, Z. (2016). "101 Formulaic Alphas." SSRN 2701346.
```

---

### 1.1 Reusable Primitives

These are the building blocks for composing alpha factors. All accept and return `pd.Series`.

#### `ts_rank(df, window) → pd.Series`

Rolling percentile rank of the last value within the window.

```python
def ts_rank(df: pd.Series, window: int) -> pd.Series:
    """Rolling percentile rank of the last value within the window.

    For each bar, rank the current value among the last *window* values.
    Returns values in (0, 1].
    """
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `df` | `pd.Series` | Input price/value series |
| `window` | `int` | Rolling window size (bars) |
| **Returns** | `pd.Series` | Percentile rank in (0, 1] |

```python
# Example: rank close prices over 10-bar window
ranked = ts_rank(df["close"], 10)
# ranked[-1] = 0.85 means current close is at 85th percentile of last 10 bars
```

**Complexity:** O(n × window)

---

#### `ts_argmax(df, window) → pd.Series`

Which bar in the rolling window hit the maximum (1-indexed from left).

```python
def ts_argmax(df: pd.Series, window: int) -> pd.Series:
    """Which bar in the rolling window hit the max (1-indexed from left)."""
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `df` | `pd.Series` | Input series |
| `window` | `int` | Rolling window size |
| **Returns** | `pd.Series` | 1-indexed position of max value in window |

```python
# Example: which of the last 5 bars had the highest close?
position = ts_argmax(df["close"], 5)
# position[-1] = 1 means the most recent bar was the highest
# position[-1] = 5 means 5 bars ago was the highest
```

**Complexity:** O(n × window)

---

#### `ts_argmin(df, window) → pd.Series`

Which bar in the rolling window hit the minimum (1-indexed from left).

```python
def ts_argmin(df: pd.Series, window: int) -> pd.Series:
    """Which bar in the rolling window hit the min (1-indexed from left)."""
```

**Complexity:** O(n × window)

---

#### `ts_min(df, window) → pd.Series`

Rolling minimum over the past `window` bars.

```python
def ts_min(df: pd.Series, window: int) -> pd.Series:
    """Rolling minimum over the past *window* bars."""
```

```python
# Example: lowest close in last 12 bars
lowest = ts_min(df["close"], 12)
```

**Complexity:** O(n) — uses pandas `rolling().min()`

---

#### `ts_max(df, window) → pd.Series`

Rolling maximum over the past `window` bars.

```python
def ts_max(df: pd.Series, window: int) -> pd.Series:
    """Rolling maximum over the past *window* bars."""
```

**Complexity:** O(n)

---

#### `delta(df, period=1) → pd.Series`

Momentum: current value minus value `period` bars ago.

```python
def delta(df: pd.Series, period: int = 1) -> pd.Series:
    """Momentum: current value minus value *period* bars ago."""
```

```python
# Example: 1-bar momentum (close-to-close change)
momentum = delta(df["close"], 1)  # equivalent to df["close"].diff(1)

# 5-bar momentum
momentum_5 = delta(df["close"], 5)
```

**Complexity:** O(n)

---

#### `delay(df, period=1) → pd.Series`

Lag the series by `period` bars.

```python
def delay(df: pd.Series, period: int = 1) -> pd.Series:
    """Lag the series by *period* bars."""
```

```python
# Example: yesterday's close
yesterday_close = delay(df["close"], 1)
```

**Complexity:** O(n)

---

#### `rank(df) → pd.Series`

Cross-sectional rank normalized to (0, 1]. For a single series, this is the percentile rank of the full series (not rolling).

```python
def rank(df: pd.Series) -> pd.Series:
    """Cross-sectional rank normalized to (0, 1].

    For a single series this is equivalent to the percentile rank of the
    full series (not rolling — useful for normalising a factor).
    """
```

```python
# Example: rank all close prices from 0 to 1
ranked = rank(df["close"])
# ranked[-1] = 0.95 means current close is near the all-time high
```

**Complexity:** O(n)

---

#### `scale(df, k=1.0) → pd.Series`

Rescale so that `sum(abs(df)) == k`.

```python
def scale(df: pd.Series, k: float = 1.0) -> pd.Series:
    """Rescale so that sum(abs(df)) == k."""
```

```python
# Example: scale signal so total absolute exposure = 1.0
scaled = scale(signal, k=1.0)
assert abs(scaled.abs().sum() - 1.0) < 1e-10
```

**Complexity:** O(n)

---

#### `decay_linear(df, period) → pd.Series`

Linearly-weighted moving average. Most recent bar has weight = `period`, oldest bar has weight = 1.

```python
def decay_linear(df: pd.Series, period: int) -> pd.Series:
    """Linearly-weighted moving average (most recent bar has weight = period)."""
```

```python
# Example: 10-bar linear-weighted MA (more responsive than SMA)
lma = decay_linear(df["close"], 10)
```

**Complexity:** O(n) — uses numpy convolution

---

#### `signed_power(df, exp) → pd.Series`

Raise each value to `exp`, preserving the sign.

```python
def signed_power(df: pd.Series, exp: float) -> pd.Series:
    """Raise each value to *exp*, preserving the sign."""
```

```python
# Example: square values while preserving sign
# -3 → -9, 2 → 4, -0.5 → -0.25
result = signed_power(pd.Series([-3, 2, -0.5]), 2.0)
```

**Complexity:** O(n)

---

#### `log_safe(df) → pd.Series`

Natural log, guarding against non-positive values (clips to 1e-10).

```python
def log_safe(df: pd.Series) -> pd.Series:
    """Natural log, guarding against non-positive values."""
```

**Complexity:** O(n)

---

### 1.2 Alpha Factors

All alpha functions accept an OHLCV `pd.DataFrame` with columns `open`, `high`, `low`, `close`, `volume` and return a `pd.Series` aligned to the input index.

#### Alpha Catalog

| Function | Alpha # | Category | Formula Summary | Min Bars |
|----------|---------|----------|-----------------|----------|
| `alpha_001(df)` | #1 | Volatility | `rank(ts_argmax(SignedPower(returns<0 ? stddev(returns,20) : close, 2), 5)) - 0.5` | 25 |
| `alpha_005(df)` | #5 | VWAP | `rank(open - avg(vwap,10)) * (-1 * abs(rank(close - vwap)))` | 15 |
| `alpha_009(df)` | #9 | Momentum | Momentum flip detector (5-bar min/max of deltas) | 10 |
| `alpha_012(df)` | #12 | Volume-Price | `sign(delta(volume,1)) * (-1 * delta(close,1))` | 3 |
| `alpha_014(df)` | #14 | Returns | `-1 * rank(delta(returns,3)) * correlation(open, volume, 10)` | 15 |
| `alpha_017(df)` | #17 | Composite | `-1 * rank(ts_rank(close,10)) * rank(delta(delta(close,1),1)) * rank(ts_rank(volume/adv20,5))` | 25 |
| `alpha_019(df)` | #19 | Mean Reversion | `-1 * sign((close-delay(close,7))+delta(close,7)) * (1+rank(1+sum(returns,250)))` | 255 |
| `alpha_022(df)` | #22 | Correlation | `-1 * delta(corr(high, volume, 5), 5) * rank(stddev(close, 20))` | 25 |
| `alpha_023(df)` | #23 | Overbought | `(sma(high,20) < high) ? -1*delta(high,2) : 0` | 22 |
| `alpha_025(df)` | #25 | Bearish | `rank((-1*returns) * adv20 * vwap * (high-close))` | 25 |
| `alpha_028(df)` | #28 | Value | `scale(corr(adv20, low, 5) + (high+low)/2 - close)` | 25 |
| `alpha_030(df)` | #30 | Consensus | `(1-rank(sign(deltas))) * (sum(volume,5)/sum(volume,20))` | 25 |
| `alpha_034(df)` | #34 | Volatility | `rank(2-rank(stddev(returns,2)/stddev(returns,5))-rank(delta(close,1)))` | 10 |
| `alpha_035(df)` | #35 | Composite | `ts_rank(volume,32) * (1-ts_rank(close+high-low,16)) * (1-ts_rank(returns,32))` | 37 |
| `alpha_036(df)` | #36 | Composite | 5-component weighted composite (200-bar window) | 205 |
| `alpha_037(df)` | #37 | Mean Reversion | `rank(corr(delay(open-close,1), close, 200)) + rank(open-close)` | 205 |
| `alpha_038(df)` | #38 | Gap | `-1 * rank(ts_rank(close,10)) * rank(close/open)` | 15 |
| `alpha_041(df)` | #41 | VWAP | `sqrt(high * low) - vwap` | 5 |
| `alpha_043(df)` | #43 | Volume | `ts_rank(volume/adv20, 20) * ts_rank(-1*delta(close,7), 8)` | 25 |
| `alpha_053(df)` | #53 | Stochastic | `-1 * delta(((close-low)-(high-close))/(close-low), 9)` | 14 |
| `alpha_055(df)` | #55 | Stochastic | `-1 * corr(rank(stoch), rank(volume), 6)` | 18 |
| `alpha_060(df)` | #60 | Tick Rule | `-1 * (2*scale(rank(imbalance)) - scale(rank(ts_argmax(close,10))))` | 15 |
| `alpha_101(df)` | #101 | Composite | `((close-open)/((high-low)+.001)) * volume` | 5 |

#### Usage Examples

```python
import pandas as pd
from alpha_factors import alpha_012, alpha_053, compute_alpha_factors

# Compute a single alpha
signal = alpha_012(df)  # Volume-price divergence

# Compute all alphas at once
df_with_alphas = compute_alpha_factors(df)

# Compute specific alphas
df_subset = compute_alpha_factors(df, ["ALPHA012", "ALPHA053", "ALPHA034"])

# Access individual alpha columns
print(df_with_alphas[["alpha_012", "alpha_053"]].tail())
```

#### Alpha Computation Patterns

```python
from alpha_factors import (
    compute_alpha_factors,       # Batch computation
    _ALPHA_REGISTRY,            # Registry dict: "ALPHA012" → alpha_012
)

# List all available alphas
print(list(_ALPHA_REGISTRY.keys()))
# ['ALPHA001', 'ALPHA005', 'ALPHA009', 'ALPHA012', ..., 'ALPHA101']

# Compute and inspect
df = compute_alpha_factors(ohlcv_df, ["ALPHA012"])
print(df[["close", "alpha_012"]].tail(10))
```

---

### 1.3 VWAP Helper

```python
def _vwap(df: pd.DataFrame) -> pd.Series:
    """Compute VWAP from OHLCV data.

    If 'vwap' column already exists, use it. Otherwise approximate as
    (high + low + close) / 3 weighted by volume (typical price method).
    """
```

---

### 1.4 Registry

```python
_ALPHA_REGISTRY: Dict[str, Callable[[pd.DataFrame], pd.Series]]
```

Maps request strings (e.g., `"ALPHA012"`) to alpha functions. Used by `compute_alpha_factors()` for batch computation.

```python
# Custom alpha using primitives
def my_custom_alpha(df):
    """My custom momentum-reversal alpha."""
    from alpha_factors import delta, ts_rank, rank
    momentum = delta(df["close"], 5)
    reversal = -1 * delta(df["close"], 20)
    volume_rank = ts_rank(df["volume"], 10)
    return rank(momentum) * rank(reversal) * volume_rank
```

---

### 1.5 `compute_alpha_factors(df, requests=None) → pd.DataFrame`

Batch computation of alpha factors.

```python
def compute_alpha_factors(
    df: pd.DataFrame,
    requests: list[str] | None = None
) -> pd.DataFrame:
    """Compute Quant Platform alpha factors and append them as columns.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns: open, high, low, close, volume.
    requests : list[str] | None
        Which alphas to compute. Defaults to the full set.
        Accepts any key from _ALPHA_REGISTRY (case-insensitive).

    Returns
    -------
    pd.DataFrame
        Copy of df with alpha columns appended (e.g. ``alpha_012``).
    """
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `df` | `pd.DataFrame` | *required* | OHLCV data with `open`, `high`, `low`, `close`, `volume` columns |
| `requests` | `list[str] \| None` | `None` (all) | Which alphas to compute. Accepts `"ALPHA012"`, `"alpha_012"`, etc. |
| **Returns** | `pd.DataFrame` | | Copy of `df` with alpha columns appended |
| **Raises** | `ValueError` | | If an unknown alpha name is requested |

```python
# Full example
import pandas as pd
import numpy as np

# Create sample OHLCV data
np.random.seed(42)
n = 300
df = pd.DataFrame({
    "open":   np.random.uniform(1.08, 1.12, n),
    "high":   np.random.uniform(1.10, 1.14, n),
    "low":    np.random.uniform(1.06, 1.10, n),
    "close":  np.random.uniform(1.08, 1.12, n),
    "volume": np.random.randint(1000, 10000, n).astype(float),
})

# Compute all alphas
result = compute_alpha_factors(df)
print(result.columns.tolist())
# ['open', 'high', 'low', 'close', 'volume', 'alpha_001', 'alpha_005', ..., 'alpha_101']

# Compute only specific alphas
result = compute_alpha_factors(df, ["ALPHA012", "ALPHA053", "ALPHA034"])
print(result[["close", "alpha_012", "alpha_053", "alpha_034"]].tail())
```

---

## 2. `alpha_ensemble.py`

> **Path:** `core/alpha_ensemble.py`
> **Lines:** 446
> **Dependencies:** `numpy`, `pandas`, `scipy.stats`

### Module Docstring

```
Alpha Combination Engine — Quant Platform-style Alpha Ensemble.

Combines multiple individual alpha factors into a single composite signal score,
weighted by their historical predictive power (Information Coefficient).

Design follows the Quant Platform Brain approach:
  1. Normalize each alpha to z-scores (removes scale differences)
  2. Compute rolling Spearman IC (rank correlation with forward returns)
  3. Weight alphas by their IC_IR (IC / std(IC)) — higher IR = more weight
  4. Combine into a composite signal
  5. Report quality metrics per alpha and overall
```

---

### 2.1 Data Classes

#### `AlphaQuality`

```python
@dataclass
class AlphaQuality:
    """Quality metrics for a single alpha factor."""
    name: str
    ic_mean: float = 0.0          # Mean Information Coefficient
    ic_std: float = 0.0           # Standard deviation of IC
    ic_ir: float = 0.0            # IC Information Ratio (ic_mean / ic_std)
    ic_positive_pct: float = 0.0  # % of periods with positive IC
    turnover: float = 0.0         # Average absolute daily change in normalized alpha
    half_life: int = 0            # Bars until IC decays to 50% of its peak
    stability: float = 0.0        # Rank correlation of IC over first/second half
    weight: float = 0.0           # Assigned weight in the ensemble
```

| Field | Type | Range | Interpretation |
|-------|------|-------|----------------|
| `ic_mean` | `float` | [-1, 1] | > 0 = predictive, < 0 = anti-predictive |
| `ic_std` | `float` | [0, ∞) | Lower = more consistent |
| `ic_ir` | `float` | (-∞, ∞) | **Key metric.** > 0.5 = strong, < 0 = useless |
| `ic_positive_pct` | `float` | [0, 1] | % of windows with positive IC |
| `turnover` | `float` | [0, ∞) | Lower = more stable signal |
| `half_life` | `int` | [0, ∞) | Higher = more persistent alpha |
| `stability` | `float` | [-1, 1] | > 0.5 = consistent over time |
| `weight` | `float` | [0, 1] | Assigned ensemble weight |

---

#### `EnsembleResult`

```python
@dataclass
class EnsembleResult:
    """Result of combining multiple alphas into a composite signal."""
    composite: pd.Series                    # The composite signal (z-score)
    composite_raw: pd.Series                # Raw weighted sum before z-scoring
    alpha_weights: Dict[str, float]         # Final weights per alpha
    alpha_qualities: Dict[str, AlphaQuality]  # Quality metrics per alpha
    n_alphas_used: int                      # How many alphas had valid IC
    ic_mean_overall: float = 0.0            # Weighted average IC across alphas
```

---

### 2.2 Normalization Functions

#### `zscore_normalize(series, window=None) → pd.Series`

```python
def zscore_normalize(series: pd.Series, window: Optional[int] = None) -> pd.Series:
    """Z-score normalize a series.

    If *window* is None, uses expanding window (full history up to each bar).
    If *window* is set, uses a rolling window for normalization.
    """
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `series` | `pd.Series` | *required* | Input series to normalize |
| `window` | `int \| None` | `None` | `None` = expanding, `int` = rolling window |
| **Returns** | `pd.Series` | | Z-scored series (mean=0, std=1) |

```python
# Expanding window (no look-ahead bias)
normalized = zscore_normalize(alpha_signal)

# Rolling 252-bar window
normalized = zscore_normalize(alpha_signal, window=252)
```

**Complexity:** O(n)

---

#### `rank_normalize(series, window=None) → pd.Series`

```python
def rank_normalize(series: pd.Series, window: Optional[int] = None) -> pd.Series:
    """Percentile rank normalize to [0, 1]."""
```

**Complexity:** O(n × window)

---

### 2.3 Information Coefficient Functions

#### `rolling_ic(alpha, forward_returns, window=60) → pd.Series`

```python
def rolling_ic(
    alpha: pd.Series,
    forward_returns: pd.Series,
    window: int = 60,
) -> pd.Series:
    """Rolling Spearman rank IC between alpha and forward returns.

    Spearman is used instead of Pearson because alpha signals are often
    non-linearly related to returns — rank correlation is more robust.
    """
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `alpha` | `pd.Series` | *required* | Normalized alpha signal |
| `forward_returns` | `pd.Series` | *required* | Forward returns (shifted appropriately) |
| `window` | `int` | `60` | Rolling window for IC computation |
| **Returns** | `pd.Series` | | Rolling Spearman IC values |

```python
# Compute forward returns (1-bar ahead)
fwd_ret = df["close"].pct_change().shift(-1)

# Compute rolling IC for alpha_012
ic = rolling_ic(df["alpha_012"], fwd_ret, window=60)
print(f"Latest IC: {ic.iloc[-1]:.4f}")
```

**Complexity:** O(n × window)

---

#### `expanding_ic(alpha, forward_returns, min_periods=40) → pd.Series`

```python
def expanding_ic(
    alpha: pd.Series,
    forward_returns: pd.Series,
    min_periods: int = 40,
) -> pd.Series:
    """Expanding-window Spearman IC (no look-ahead bias)."""
```

**Complexity:** O(n²)

---

### 2.4 Alpha Quality Assessment

#### `compute_alpha_quality(alpha, forward_returns, name, ic_window=60) → AlphaQuality`

```python
def compute_alpha_quality(
    alpha: pd.Series,
    forward_returns: pd.Series,
    name: str,
    ic_window: int = 60,
) -> AlphaQuality:
    """Compute quality metrics for a single alpha factor."""
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `alpha` | `pd.Series` | *required* | Normalized alpha signal |
| `forward_returns` | `pd.Series` | *required* | Forward returns |
| `name` | `str` | *required* | Alpha identifier (e.g., `"alpha_012"`) |
| `ic_window` | `int` | `60` | Rolling IC window |
| **Returns** | `AlphaQuality` | | Full quality metrics |

```python
# Assess a single alpha
fwd_ret = df["close"].pct_change().shift(-1)
quality = compute_alpha_quality(
    zscore_normalize(df["alpha_012"]),
    fwd_ret,
    name="alpha_012",
    ic_window=60
)
print(f"IC_IR: {quality.ic_ir:.3f}, Stability: {quality.stability:.3f}")
```

---

### 2.5 Weight Calculation

#### `compute_ic_weights(qualities, min_ir=0.0) → Dict[str, float]`

```python
def compute_ic_weights(
    qualities: Dict[str, AlphaQuality],
    min_ir: float = 0.0,
) -> Dict[str, float]:
    """Compute ensemble weights from alpha quality metrics.

    Weighting scheme (Quant Platform-style):
      weight_i = max(IC_IR_i - min_ir, 0) / sum(max(IC_IR_j - min_ir, 0))

    Alphas with negative IC_IR (worse than random) get zero weight.
    """
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `qualities` | `Dict[str, AlphaQuality]` | *required* | Per-alpha quality metrics |
| `min_ir` | `float` | `0.0` | Minimum IC_IR threshold for inclusion |
| **Returns** | `Dict[str, float]` | | Alpha name → weight mapping (sums to 1.0) |

```python
# Compute weights
weights = compute_ic_weights(qualities, min_ir=0.0)
print(weights)
# {'alpha_055': 0.78, 'alpha_034': 0.22, 'alpha_012': 0.0}  # alpha_012 zeroed (negative IC_IR)
```

---

### 2.6 Main Ensemble Engine

#### `combine_alphas(alpha_df, alpha_columns, ...) → EnsembleResult`

```python
def combine_alphas(
    alpha_df: pd.DataFrame,
    alpha_columns: List[str],
    forward_return_col: str = "_fwd_return",
    forward_return_periods: int = 1,
    ic_window: int = 60,
    normalize_method: str = "zscore",
    weighting: str = "ic_ir",
) -> EnsembleResult:
    """Combine multiple alpha factors into a composite signal.

    Parameters
    ----------
    alpha_df : pd.DataFrame
        DataFrame containing alpha columns and OHLCV data.
    alpha_columns : list[str]
        Names of the alpha columns to combine.
    forward_return_col : str
        Column name for forward returns. If not present, computed from "close".
    forward_return_periods : int
        How many bars ahead for forward returns (IC calculation).
    ic_window : int
        Rolling window size for IC computation.
    normalize_method : str
        "zscore" or "rank" — how to normalize individual alphas.
    weighting : str
        "ic_ir" — weight by IC Information Ratio.
        "equal" — equal weight to all alphas.
        "ic" — weight by mean IC.

    Returns
    -------
    EnsembleResult with composite signal, weights, and quality metrics.
    """
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `alpha_df` | `pd.DataFrame` | *required* | Must contain alpha columns + `close` |
| `alpha_columns` | `List[str]` | *required* | Alpha column names to combine |
| `forward_return_col` | `str` | `"_fwd_return"` | Forward returns column (auto-computed if missing) |
| `forward_return_periods` | `int` | `1` | Bars ahead for forward returns |
| `ic_window` | `int` | `60` | Rolling IC window |
| `normalize_method` | `str` | `"zscore"` | `"zscore"` or `"rank"` |
| `weighting` | `str` | `"ic_ir"` | `"ic_ir"`, `"equal"`, or `"ic"` |
| **Returns** | `EnsembleResult` | | Composite signal + weights + qualities |

```python
from alpha_ensemble import combine_alphas, interpret_composite, format_quality_report

# Step 1: Compute alphas
from alpha_factors import compute_alpha_factors
df = compute_alpha_factors(ohlcv_df)

# Step 2: Combine into ensemble
result = combine_alphas(
    alpha_df=df,
    alpha_columns=["alpha_012", "alpha_053", "alpha_055", "alpha_034", "alpha_017"],
    ic_window=60,
    normalize_method="zscore",
    weighting="ic_ir",
)

# Step 3: Interpret the composite signal
interpretation = interpret_composite(result, current_idx=-1)
print(f"Signal: {interpretation['signal']}")
print(f"Strength: {interpretation['strength']:.2f}")
print(f"Confidence: {interpretation['confidence']}")

# Step 4: Print quality report
print(format_quality_report(result))
```

**Complexity:** O(n × window × k) where k = number of alphas

---

### 2.7 Signal Interpretation

#### `interpret_composite(result, current_idx=-1, threshold_entry=1.0, threshold_exit=0.0) → Dict`

```python
def interpret_composite(
    result: EnsembleResult,
    current_idx: int = -1,
    threshold_entry: float = 1.0,
    threshold_exit: float = 0.0,
) -> Dict[str, any]:
    """Interpret the composite signal at a given bar index.

    Returns a dict with:
      - signal: "LONG", "SHORT", "NEUTRAL"
      - strength: float (absolute composite value)
      - confidence: "HIGH" / "MEDIUM" / "LOW"
      - contributing_alphas: list of dicts sorted by contribution
      - quality_report: summary text
      - composite_value: the z-scored composite value
      - raw_value: the raw weighted sum
    """
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `result` | `EnsembleResult` | *required* | Output from `combine_alphas()` |
| `current_idx` | `int` | `-1` | Bar index to interpret (default: latest) |
| `threshold_entry` | `float` | `1.0` | Composite threshold for LONG/SHORT signal |
| `threshold_exit` | `float` | `0.0` | Composite threshold for NEUTRAL |
| **Returns** | `Dict` | | Signal interpretation dict |

**Signal rules:**
- `composite > threshold_entry` → `"LONG"`
- `composite < -threshold_entry` → `"SHORT"`
- Otherwise → `"NEUTRAL"`

**Confidence rules:**
- `|composite| > 2.0` → `"HIGH"`
- `|composite| > 1.0` → `"MEDIUM"`
- Otherwise → `"LOW"`

```python
# Full interpretation example
interp = interpret_composite(result, current_idx=-1, threshold_entry=1.0)

print(f"Signal:     {interp['signal']}")       # "LONG" / "SHORT" / "NEUTRAL"
print(f"Strength:   {interp['strength']:.2f}")  # 1.45
print(f"Confidence: {interp['confidence']}")     # "MEDIUM"
print(f"Value:      {interp['composite_value']:.4f}")  # 1.4523

print("\nContributing alphas:")
for alpha in interp["contributing_alphas"][:5]:
    print(f"  {alpha['alpha']}: weight={alpha['weight']:.3f}, "
          f"IC_IR={alpha['ic_ir']:.3f}, {alpha['direction']}")
```

---

#### `format_quality_report(result) → str`

```python
def format_quality_report(result: EnsembleResult) -> str:
    """Format a human-readable quality report for all alphas."""
```

Returns a formatted string like:

```
======================================================================
ALPHA ENSEMBLE QUALITY REPORT
======================================================================
Alphas used: 12/18
Overall IC:  0.0385

Alpha         Weight  IC_Mean    IC_IR    IC+% Turnover    HL  Stable
----------------------------------------------------------------------
alpha_055     0.780   0.0080    0.655   55.2%    0.0421    12   0.723
alpha_034     0.220   0.0045    0.312   52.1%    0.0315     8   0.651
alpha_012     0.000  -0.0012   -0.089   48.3%    0.0534     0   0.102
======================================================================
```

---

### 2.8 Complete Pipeline Example

```python
import pandas as pd
import numpy as np
from alpha_factors import compute_alpha_factors
from alpha_ensemble import combine_alphas, interpret_composite, format_quality_report

# 1. Fetch/load OHLCV data
df = fetch_ohlc("EURUSD", "H1", count=1000)  # your data source

# 2. Compute individual alpha factors
df = compute_alpha_factors(df, [
    "ALPHA012", "ALPHA053", "ALPHA055", "ALPHA034", "ALPHA017",
    "ALPHA009", "ALPHA041", "ALPHA101", "ALPHA030", "ALPHA035",
])

# 3. Combine into ensemble
alpha_cols = [c for c in df.columns if c.startswith("alpha_")]
result = combine_alphas(
    alpha_df=df,
    alpha_columns=alpha_cols,
    ic_window=60,
    weighting="ic_ir",
)

# 4. Interpret
interp = interpret_composite(result, current_idx=-1)
print(f"Composite signal: {interp['signal']} ({interp['confidence']})")

# 5. Quality report
print(format_quality_report(result))

# 6. Access weights for downstream use
for name, weight in sorted(result.alpha_weights.items(), key=lambda x: -x[1]):
    if weight > 0:
        q = result.alpha_qualities[name]
        print(f"  {name}: weight={weight:.3f} IC_IR={q.ic_ir:.3f}")
```

---

## 3. `trading_safety.py`

> **Path:** `core/trading_safety.py`
> **Lines:** 972
> **Dependencies:** `dataclasses`, `hashlib`, `json`, `math`, `os`, `sqlite3`
> **No LLM dependency.** Deterministic, restart-safe, fully testable.

### Module Docstring

```
Deterministic, restart-safe safety primitives for the MT5 trader.

This module deliberately has no LLM dependency. It can be unit tested and
replayed from journal data before a proposal is allowed to reach MT5.
```

---

### 3.1 Constants

```python
SWING_MAGIC = 460001   # Magic number for swing strategy orders
SCALP_MAGIC = 460002   # Magic number for scalp strategy orders
```

---

### 3.2 `TradeProposal`

Immutable trade proposal. Created by the LLM orchestrator, validated before execution.

```python
@dataclass(frozen=True)
class TradeProposal:
    strategy: str            # "swing" or "scalp"
    symbol: str              # e.g., "EURUSD"
    side: str                # "BUY" or "SELL"
    entry: float             # Planned entry price
    stop_loss: float         # Stop loss price
    take_profit: float       # Take profit price
    confidence: float        # LLM confidence [0, 1]
    features: Dict[str, Any] # Feature snapshot (setup_id, regime, indicators, etc.)
    model: str = ""          # LLM model used
    prompt_hash: str = ""    # Hash of the prompt for deduplication
```

| Property | Type | Description |
|----------|------|-------------|
| `magic` | `int` | `460001` (swing) or `460002` (scalp) |
| `reward_risk` | `float` | `abs(take_profit - entry) / abs(entry - stop_loss)` |
| `setup_key()` | `str` | SHA-256 hash of strategy+setup+symbol+side+entry+regime |

```python
# Create a proposal
proposal = TradeProposal(
    strategy="scalp",
    symbol="EURUSD",
    side="BUY",
    entry=1.0850,
    stop_loss=1.0830,
    take_profit=1.0890,
    confidence=0.85,
    features={"setup_id": "engulfing_v1", "regime": "trending", "adx": 28},
    model="mistralai/mistral-7b-instruct",
)

print(proposal.magic)        # 460002
print(proposal.reward_risk)  # 2.0 (40 pip TP / 20 pip SL)
print(proposal.setup_key())  # "a1b2c3d4e5f6..."
```

---

### 3.3 `ValidationResult`

```python
@dataclass
class ValidationResult:
    approved: bool                    # True if proposal passes all gates
    reasons: List[str]                # Rejection reasons (empty if approved)
    confluence_score: int             # Deterministic confluence score
    rule_trace: Dict[str, Any] = {}   # Per-rule pass/fail with values
```

```python
result = ValidationResult(
    approved=True,
    reasons=[],
    confluence_score=5,
    rule_trace={
        "reward_risk_valid": {"passed": True, "value": 2.0, "limit": 1.5},
        "deterministic_confluence": {"passed": True, "value": 5, "limit": 3},
    }
)
```

---

### 3.4 `TradingStore`

SQLite persistence store for proposals, trades, orders, and journal.

```python
class TradingStore:
    """Small SQLite store for limits, proposals, orders, and durable journal."""

    def __init__(
        self,
        path: str = "trading_state.db",
        mode: Optional[str] = None,          # "DEMO", "LIVE", "PAPER"
        account_fingerprint: str = ""         # "login|server" for scope isolation
    ):
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | `"trading_state.db"` | SQLite database path |
| `mode` | `str \| None` | `None` | Journal scope: `"DEMO"`, `"LIVE"`, or `None` |
| `account_fingerprint` | `str` | `""` | Account identity for scope isolation |

#### Key Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_state(key, default=None)` | `→ Any` | Read durable state key |
| `set_state(key, value)` | `→ None` | Write durable state key |
| `update_state(key, mutator, default=None)` | `→ Any` | Atomic read-modify-write |
| `kill_switch_active()` | `→ bool` | Check if kill switch is active |
| `set_kill_switch(active, reason="")` | `→ None` | Activate/deactivate kill switch |
| `is_kill_switch_active()` | `→ bool` | Alias for `kill_switch_active()` |
| `log_proposal(proposal, result)` | `→ None` | Record proposal + validation result |
| `record_setup_instance(proposal, payload=None)` | `→ int` | Create candidate setup instance |
| `transition_setup_instance(instance_id, state, ...)` | `→ None` | Transition setup state machine |
| `latest_setup_instance(setup_key)` | `→ Row \| None` | Get latest setup for a key |
| `record_manual_activity(action, ...)` | `→ None` | Record manual broker activity |
| `log_lifecycle_event(ticket, setup_key, event_type, actor, payload)` | `→ None` | Append lifecycle event |
| `record_execution_report(...)` | `→ None` | Append broker execution evidence |
| `record_execution_review(intent_key, ...)` | `→ None` | Record execution review |
| `get_unresolved_reviews()` | `→ List[Dict]` | Get open reviews |
| `latest_proposal(setup_key)` | `→ Dict \| None` | Get latest proposal for a setup |

#### Setup State Machine

```
CANDIDATE → VALIDATED → ACCEPTED → FILLED → MANAGED → CLOSED
    ↓          ↓           ↓          ↓         ↓
  REJECTED   REJECTED    MISSED    PARTIAL   PARTIAL
    ↓          ↓           ↓          ↓         ↓
  INVALIDATED INVALIDATED INVALIDATED INVALIDATED INVALIDATED
    ↓          ↓           ↓          ↓         ↓
  UNRESOLVED  UNRESOLVED  UNRESOLVED UNRESOLVED UNRESOLVED
```

```python
# Example: full proposal lifecycle
store = TradingStore("trading_state.db", mode="DEMO", account_fingerprint="5055089321|MetaQuotes-Demo")

# Log proposal
store.log_proposal(proposal, validation_result)

# Record setup instance
instance_id = store.record_setup_instance(proposal)

# Transition through states
store.transition_setup_instance(instance_id, "VALIDATED", reason="all rules passed")
store.transition_setup_instance(instance_id, "ACCEPTED", ticket=12345)
store.transition_setup_instance(instance_id, "FILLED", ticket=12345, reason="limit order filled")
store.transition_setup_instance(instance_id, "MANAGED", ticket=12345)
store.transition_setup_instance(instance_id, "CLOSED", ticket=12345, reason="take profit hit")
```

---

### 3.5 `DeterministicValidator`

The primary pre-trade validation engine. All checks are deterministic — no LLM calls, no randomness.

```python
class DeterministicValidator:
    """Hard rule gate. All unavailable critical data is rejected, never assumed safe."""

    def __init__(
        self,
        store: TradingStore,
        min_rr: float = 2.0,                    # Minimum reward:risk ratio
        min_confluence: int = 3,                 # Minimum confluence score
        lifecycle_gate=None,                     # Optional setup lifecycle gate
        min_net_ev_r: float = 0.0,              # Minimum net expectancy (R)
        min_ev_samples: int = 30,               # Min trades before EV gate activates
        default_friction_r: float = 0.05,        # Default friction cost (R)
        max_changepoint_probability: float = 0.45,  # BOCPD regime-change veto
    ):
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `store` | `TradingStore` | *required* | Persistence store |
| `min_rr` | `float` | `2.0` | Minimum reward:risk ratio |
| `min_confluence` | `int` | `3` | Minimum confluence score |
| `lifecycle_gate` | `callable \| None` | `None` | `fn(setup_id) → dict \| None` |
| `min_net_ev_r` | `float` | `0.0` | Floor for rolling net expectancy |
| `min_ev_samples` | `int` | `30` | Min trades before EV gate activates |
| `default_friction_r` | `float` | `0.05` | Assumed friction per trade (R) |
| `max_changepoint_probability` | `float` | `0.45` | BOCPD regime-change veto threshold |

#### `validate(proposal, market) → ValidationResult`

```python
def validate(self, proposal: TradeProposal, market: Dict[str, Any]) -> ValidationResult:
```

**Parameters:**
- `proposal`: The `TradeProposal` to validate
- `market`: Market context dict with keys like `market_open`, `tick_age_ms`, `spread`, `session_allowed`, etc.

**Validation rules (22 checks):**

| # | Rule | Key in `market` dict | Rejection Message |
|---|------|---------------------|-------------------|
| 1 | Kill switch | — | `"kill switch active"` |
| 2 | Market open | `market_open` | `"market not open"` |
| 3 | Tick freshness | `tick_age_ms`, `max_tick_age_ms` | `"stale tick"` |
| 4 | Clock health | `clock_ok` | `"broker/local clock health rejected tick"` |
| 5 | Symbol tradable | `symbol_tradable` | `"symbol not tradable"` |
| 6 | Spread limit | `spread`, `max_spread` | `"spread too wide"` |
| 7 | Session filter | `session_allowed` | `"session/rollover no-trade filter"` |
| 8 | Liquidity | `liquidity_ok` | `"liquidity filter rejected entry"` |
| 9 | Microstructure | `microstructure_ok` | `"microstructure filter rejected entry"` |
| 10 | Data requirements | `required_data_ok` | custom reasons |
| 11 | Market conditions | `market_condition_reasons` | custom reasons |
| 12 | Entry tolerance | `execution_price`, `entry_tolerance` | `"price moved beyond planned-entry tolerance"` |
| 13 | Candle/breakout trigger | `completed_candle`, `breakout_event` | `"no completed-candle/breakout trigger"` |
| 14 | Stop distance | `stop_distance`, `min_stop_distance` | `"stop too close"` |
| 15 | R:R ratio | `setup_min_rr` | `"reward/risk below setup minimum {rr}"` |
| 16 | Event window | `setup_event_exclusion`, `high_impact_news` | `"setup excludes high-impact event window"` |
| 17 | Confluence score | features dict | `"insufficient deterministic confluence"` |
| 18 | News safety | `high_impact_news` | `"news data missing or high-impact event"` |
| 19 | Setup cooldown | — | `"setup recently considered"` |
| 20 | Lifecycle gate | `setup_id` | `"setup lifecycle FROZEN: ..."` |
| 21 | Lag confidence | `lag_confidence` | `"lag confidence decayed below threshold"` |
| 22 | Net expectancy | rolling journal stats | `"rolling net expectancy {ev:.3f}R below floor {floor:.2f}R"` |
| 23 | Regime changepoint | `changepoint_probability` | `"regime changepoint probability above veto threshold"` |

```python
# Example: validate a proposal
market = {
    "market_open": True,
    "tick_age_ms": 150,
    "max_tick_age_ms": 5000,
    "spread": 1.2,
    "max_spread": 2.0,
    "symbol_tradable": True,
    "session_allowed": True,
    "liquidity_ok": True,
    "microstructure_ok": True,
    "required_data_ok": True,
    "clock_ok": True,
    "completed_candle": True,
    "execution_price": 1.0850,
    "entry_tolerance": 0.0005,
    "stop_distance": 0.0020,
    "min_stop_distance": 0.0010,
    "high_impact_news": False,
    "setup_event_exclusion": False,
}

result = validator.validate(proposal, market)

if result.approved:
    print("Proposal APPROVED")
    print(f"Confluence score: {result.confluence_score}")
else:
    print(f"Proposal REJECTED: {result.reasons}")
    for rule, trace in result.rule_trace.items():
        if not trace.get("passed"):
            print(f"  FAILED: {rule} = {trace}")
```

---

### 3.6 `PortfolioRiskArbiter`

Portfolio-level risk gating. Checks correlation, concentration, and heat budget.

```python
class PortfolioRiskArbiter:
    def __init__(
        self,
        store: TradingStore,
        daily_loss_fraction: float = 0.03,       # 3% daily loss limit
        weekly_loss_fraction: float = 0.06,      # 6% weekly loss limit
        max_drawdown_fraction: float = 0.10,     # 10% max drawdown
        max_open_risk_fraction: float = 0.03,    # 3% max open risk
        max_correlated_risk_fraction: float = 0.02,  # 2% max correlated risk
    ):
```

#### `permit(equity, new_risk, bucket_risk=0.0, bucket="", correlated_risk=0.0) → Tuple[bool, str]`

```python
def permit(
    self,
    equity: float,
    new_risk: float,
    bucket_risk: float = 0.0,
    bucket: str = "",
    correlated_risk: float = 0.0,
) -> Tuple[bool, str]:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `equity` | `float` | Current account equity |
| `new_risk` | `float` | Risk amount of the new trade (entry to SL) |
| `bucket_risk` | `float` | Existing risk in the same bucket |
| `bucket` | `str` | Bucket name (e.g., `"EUR_LONG"`) |
| `correlated_risk` | `float` | Total correlated exposure risk |
| **Returns** | `Tuple[bool, str]` | `(approved, reason)` |

**Checks (in order):**
1. Max drawdown (10% of peak equity → kill switch)
2. Daily loss limit (3% of equity)
3. Weekly loss limit (6% of equity)
4. Portfolio open-risk limit (3% of equity)
5. Correlated bucket risk limit (2% of equity)
6. Rolling correlated exposure limit

```python
# Example: check if a trade is permitted
arbiter = PortfolioRiskArbiter(store)

approved, reason = arbiter.permit(
    equity=10000,
    new_risk=100,           # $100 risk (1% of $10K)
    bucket_risk=50,         # $50 already at risk in EUR bucket
    bucket="EUR_LONG",
    correlated_risk=80,     # $80 total correlated exposure
)

if approved:
    print("Trade permitted")
else:
    print(f"Trade blocked: {reason}")
```

---

### 3.7 Utility Functions

#### `safe_risk_fraction(percent_or_fraction, cap=0.01) → float`

```python
def safe_risk_fraction(percent_or_fraction: float, cap: float = 0.01) -> float:
    """Accept a UI percentage or fraction and return a capped fraction."""
```

Normalizes risk input:
- `1.0` → `0.01` (1%, treated as percentage)
- `0.5` → `0.005` (0.5%)
- `100` → `0.01` (capped)

```python
safe_risk_fraction(1.0)    # → 0.01 (1%)
safe_risk_fraction(2.0)    # → 0.01 (capped at 1%)
safe_risk_fraction(0.005)  # → 0.005 (0.5%)
safe_risk_fraction(50)     # → 0.01 (capped at 1%)
```

---

#### `exposure_buckets(symbol, side) → set[str]`

```python
def exposure_buckets(symbol: str, side: str) -> set[str]:
    """Map a trade to conservative base/quote-driver buckets."""
```

Maps a forex pair to directional exposure buckets:

```python
exposure_buckets("EURUSD", "BUY")   # → {"EUR_LONG", "USD_SHORT"}
exposure_buckets("EURUSD", "SELL")  # → {"EUR_SHORT", "USD_LONG"}
exposure_buckets("XAUUSD", "BUY")   # → {"GOLD_LONG", "USD_SHORT"}
exposure_buckets("GBPJPY", "SELL")  # → {"GBP_SHORT", "JPY_LONG"}
```

---

## Appendix: Import Quick Reference

```python
# Alpha factors
from core.alpha_factors import (
    # Primitives
    ts_rank, ts_argmax, ts_argmin, ts_min, ts_max,
    delta, delay, rank, scale, decay_linear,
    signed_power, log_safe,
    # Alpha functions
    alpha_001, alpha_005, alpha_009, alpha_012, alpha_014,
    alpha_017, alpha_019, alpha_022, alpha_023, alpha_025,
    alpha_028, alpha_030, alpha_034, alpha_035, alpha_036,
    alpha_037, alpha_038, alpha_041, alpha_043, alpha_053,
    alpha_055, alpha_060, alpha_101,
    # Batch computation
    compute_alpha_factors,
    # Registry
    _ALPHA_REGISTRY,
)

# Alpha ensemble
from core.alpha_ensemble import (
    # Data classes
    AlphaQuality,
    EnsembleResult,
    # Normalization
    zscore_normalize,
    rank_normalize,
    # IC computation
    rolling_ic,
    expanding_ic,
    # Quality assessment
    compute_alpha_quality,
    compute_ic_weights,
    # Main engine
    combine_alphas,
    # Interpretation
    interpret_composite,
    format_quality_report,
)

# Trading safety
from core.trading_safety import (
    # Constants
    SWING_MAGIC,       # 460001
    SCALP_MAGIC,       # 460002
    # Data classes
    TradeProposal,
    ValidationResult,
    # Core classes
    TradingStore,
    DeterministicValidator,
    PortfolioRiskArbiter,
    # Utilities
    safe_risk_fraction,
    exposure_buckets,
)
```
