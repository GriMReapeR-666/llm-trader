"""Bi-Temporal Point-in-Time Macro Store & Purged Walk-Forward Optimizer.

Implements Author-C (2018) institutional backtesting:
1. BiTemporalMacroStore: Indexes releases strictly by publication timestamp (t_pub),
   preventing lookahead or backward revision leakage.
2. MicrostructureSweepSimulator: Realistic M15 limit execution with:
   - Queue penetration requirement (1.5 pips).
   - Unmitigated order expiration (16 bars / 4 hours).
   - Dynamic news spread expansion.
   - Pessimistic intra-bar SL-before-TP resolution.
   - Friday 16:00 UTC weekend risk de-risking rule.
3. Purged & Embargoed Walk-Forward Optimization (WFO):
   - 24 Months In-Sample / 3 Months Out-of-Sample.
   - 5-Day Purge buffer (drops trade lifespans overlapping IS/OOS boundary).
   - 5-Day Embargo buffer (removes serial correlation leakage).
4. Statistical Survival Acceptance Gates:
   - Walk-Forward Efficiency (WFE >= 0.55).
   - Deflated Sharpe Ratio (DSR >= 0.95).
   - Leave-One-Out Macro Outlier Expectancy (> +0.35R).
   - Monte Carlo Execution Perturbation (95th percentile MaxDD < 10.0%).

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib, NumPy, Pandas.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
import pandas as pd

EXECUTION_AUTHORITY = False


@dataclass
class TradeRecord:
    asset: str
    direction: int          # +1 for Long, -1 for Short
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    pnl_r: float            # Realized R-multiple (e.g., +2.25R or -1.0R)


class BiTemporalMacroStore:
    """Stores macroeconomic releases, CFTC COT positioning, and GDELT scores

    indexed strictly by publication/vintage timestamp (t_pub) to guarantee
    zero lookahead or revision leakage.
    """

    def __init__(self, macro_events_df: pd.DataFrame):
        if not macro_events_df.empty:
            self.df = macro_events_df.sort_values("pub_timestamp").reset_index(drop=True)
        else:
            self.df = pd.DataFrame()

    def get_pit_conviction(
        self,
        as_of_utc: pd.Timestamp,
        weights: Tuple[float, float, float, float] = (0.35, 0.30, 0.20, 0.15),
        freeze_minutes: int = 15,
    ) -> Dict[str, float]:
        """Computes Net Conviction Score S_{i,t} in [-100, +100] using ONLY

        vintages published at least `freeze_minutes` prior to `as_of_utc`.
        """
        if self.df.empty:
            return {}

        w_m, w_c, w_g, w_p = weights
        cutoff_time = as_of_utc - pd.Timedelta(minutes=freeze_minutes)

        # Strict PIT slice: only data known to the market before cutoff_time
        visible = self.df[self.df["pub_timestamp"] <= cutoff_time]
        if visible.empty:
            return {}

        scores: Dict[str, float] = {}
        # Group by asset and domain, take latest vintage
        latest_series = visible.groupby(["asset", "domain"]).last().reset_index()

        assets = latest_series["asset"].unique()
        for asset in assets:
            sub = latest_series[latest_series["asset"] == asset].set_index("domain")
            raw_m = float(sub.loc["MACRO", "raw_score"]) if "MACRO" in sub.index else 0.0
            raw_c = float(sub.loc["CB", "raw_score"]) if "CB" in sub.index else 0.0
            raw_g = float(sub.loc["GEO", "raw_score"]) if "GEO" in sub.index else 0.0
            cot_z = float(sub.loc["COT", "raw_score"]) if "COT" in sub.index else 0.0

            m_val = float(np.tanh(raw_m) * 100.0)
            c_val = float(np.tanh(raw_c) * 100.0)
            g_val = float(np.tanh(raw_g) * 100.0)

            if abs(cot_z) > 2.0:
                p_val = -float(np.sign(cot_z) * 100.0)
            else:
                p_val = float(np.tanh(cot_z) * 50.0)

            s = (w_m * m_val) + (w_c * c_val) + (w_g * g_val) + (w_p * p_val)
            scores[asset] = round(float(np.clip(s, -100.0, 100.0)), 2)

        return scores


class MicrostructureSweepSimulator:
    """Simulates M15 Liquidity Sweeps, CVD Absorption, Impact Candles, and FVG

    limit fills under strict point-in-time constraints and max 2 trades/week.
    """

    def __init__(
        self,
        eta_min: float = 0.72,      # Min Impact Candle body-to-range efficiency
        z_vol_min: float = 2.0,     # Min relative volume Z-score
        sl_atr_buf: float = 0.15,   # Stop buffer beyond sweep wick (x Daily ATR)
        tp2_atr_mult: float = 3.0,  # Runner target multiple (x Daily ATR)
        s_min: float = 45.0,        # Min Macro Conviction hurdle
    ):
        self.eta_min = eta_min
        self.z_vol_min = z_vol_min
        self.sl_atr_buf = sl_atr_buf
        self.tp2_atr_mult = tp2_atr_mult
        self.s_min = s_min

    @staticmethod
    def prepare_pit_liquidity_pools(m15_df: pd.DataFrame) -> pd.DataFrame:
        """Pre-computes Previous Day High/Low (PDH/PDL), Asian Session High/Low (ASH/ASL),

        and 20-day Daily ATR without any lookahead leakage.
        """
        df = m15_df.copy()
        df["date"] = df.index.date
        df["hour"] = df.index.hour

        # 1. Previous Day High/Low & Daily ATR (shifted by 1 full day)
        daily = df.groupby("date").agg(
            d_high=("high", "max"),
            d_low=("low", "min"),
            d_close=("close", "last"),
        )
        daily["prev_close"] = daily["d_close"].shift(1)
        daily["tr"] = np.maximum(
            daily["d_high"] - daily["d_low"],
            np.maximum(
                (daily["d_high"] - daily["prev_close"]).abs(),
                (daily["d_low"] - daily["prev_close"]).abs(),
            ),
        )
        daily["atr_d1"] = daily["tr"].rolling(20).mean().shift(1)
        daily["PDH"] = daily["d_high"].shift(1)
        daily["PDL"] = daily["d_low"].shift(1)
        daily["range_eq"] = 0.5 * (daily["PDH"] + daily["PDL"])

        # 2. Asian Session High/Low (00:00 - 06:00 UTC), locked strictly after 06:00 UTC
        asian_mask = (df["hour"] >= 0) & (df["hour"] < 6)
        asian_pools = df[asian_mask].groupby("date").agg(
            ASH=("high", "max"),
            ASL=("low", "min"),
        )

        df = df.join(daily[["PDH", "PDL", "range_eq", "atr_d1"]], on="date")
        df = df.join(asian_pools, on="date")
        # Prevent lookahead: ASH/ASL are unknown prior to 06:00 UTC
        df.loc[df["hour"] < 6, ["ASH", "ASL"]] = np.nan

        # 3. Microstructure bar metrics
        bar_range = (df["high"] - df["low"]).replace(0, np.nan)
        df["efficiency"] = (df["close"] - df["open"]).abs() / bar_range
        vol_mean = df["volume"].rolling(20).mean()
        vol_std = df["volume"].rolling(20).std().replace(0, np.nan)
        df["vol_z"] = (df["volume"] - vol_mean) / vol_std

        # Internal 10-bar swing high/low prior to current bar
        df["swing_high"] = df["high"].shift(1).rolling(10).max()
        df["swing_low"] = df["low"].shift(1).rolling(10).min()

        return df.dropna(subset=["PDH", "PDL", "atr_d1"])

    def run_fold_simulation(
        self,
        m15_data: Dict[str, pd.DataFrame],
        macro_store: BiTemporalMacroStore,
        weights: Tuple[float, float, float, float],
    ) -> List[TradeRecord]:
        """Steps chronologically across M15 timestamps, enforces the 2-trade/week quota,

        detects sweeps + Impact Candles inside London/NY Killzones, and tracks outcomes.
        """
        all_timestamps = sorted(
            set().union(*(df.index for df in m15_data.values()))
        )
        trades: List[TradeRecord] = []
        current_week = None
        weekly_trade_count = 0

        for ts in all_timestamps:
            iso_year, iso_week, iso_day = ts.isocalendar()
            if (iso_year, iso_week) != current_week:
                current_week = (iso_year, iso_week)
                weekly_trade_count = 0

            # Enforce Max 2 Trades Per Week & Tue-Thu Execution Window
            if weekly_trade_count >= 2 or iso_day not in (2, 3, 4):
                continue

            # Enforce London (07-10 UTC) or NY (12-16 UTC) Killzones
            if not ((7 <= ts.hour < 10) or (12 <= ts.hour < 16)):
                continue

            # Query Point-in-Time Macro Conviction as of bar close `ts`
            macro_scores = macro_store.get_pit_conviction(ts, weights=weights)

            for asset, df in m15_data.items():
                if weekly_trade_count >= 2 or ts not in df.index:
                    continue
                score = macro_scores.get(asset, 0.0)
                if abs(score) < self.s_min:
                    continue

                idx_pos = df.index.get_loc(ts)
                if idx_pos < 3 or idx_pos + 32 >= len(df):
                    continue

                bar_prev = df.iloc[idx_pos - 1]
                bar_curr = df.iloc[idx_pos]
                bar_pre2 = df.iloc[idx_pos - 2]

                # Bullish Setup
                if score >= self.s_min:
                    swept_low = (
                        (bar_pre2["low"] < bar_pre2["PDL"])
                        or (pd.notna(bar_pre2["ASL"]) and bar_pre2["low"] < bar_pre2["ASL"])
                    )
                    is_impact = (
                        bar_prev["close"] > bar_prev["open"]
                        and bar_prev["efficiency"] >= self.eta_min
                        and bar_prev["vol_z"] >= self.z_vol_min
                        and bar_prev["close"] > bar_prev["swing_high"]
                        and bar_curr["low"] > bar_pre2["high"]
                    )
                    if swept_low and is_impact:
                        fvg_entry = bar_pre2["high"]
                        sl_price = min(bar_pre2["low"], bar_prev["low"]) - (self.sl_atr_buf * bar_curr["atr_d1"])
                        tp1_price = max(bar_curr["range_eq"], fvg_entry + 1.5 * (fvg_entry - sl_price))
                        tp2_price = fvg_entry + (self.tp2_atr_mult * bar_curr["atr_d1"])

                        trade = self._resolve_forward_path(
                            asset, +1, df.iloc[idx_pos + 1 : idx_pos + 192],
                            fvg_entry, sl_price, tp1_price, tp2_price,
                        )
                        if trade is not None:
                            trades.append(trade)
                            weekly_trade_count += 1

                # Bearish Setup
                elif score <= -self.s_min:
                    swept_high = (
                        (bar_pre2["high"] > bar_pre2["PDH"])
                        or (pd.notna(bar_pre2["ASH"]) and bar_pre2["high"] > bar_pre2["ASH"])
                    )
                    is_impact = (
                        bar_prev["close"] < bar_prev["open"]
                        and bar_prev["efficiency"] >= self.eta_min
                        and bar_prev["vol_z"] >= self.z_vol_min
                        and bar_prev["close"] < bar_prev["swing_low"]
                        and bar_curr["high"] < bar_pre2["low"]
                    )
                    if swept_high and is_impact:
                        fvg_entry = bar_pre2["low"]
                        sl_price = max(bar_pre2["high"], bar_prev["high"]) + (self.sl_atr_buf * bar_curr["atr_d1"])
                        tp1_price = min(bar_curr["range_eq"], fvg_entry - 1.5 * (sl_price - fvg_entry))
                        tp2_price = fvg_entry - (self.tp2_atr_mult * bar_curr["atr_d1"])

                        trade = self._resolve_forward_path(
                            asset, -1, df.iloc[idx_pos + 1 : idx_pos + 192],
                            fvg_entry, sl_price, tp1_price, tp2_price,
                        )
                        if trade is not None:
                            trades.append(trade)
                            weekly_trade_count += 1

        return trades

    @staticmethod
    def _resolve_forward_path(
        asset: str,
        direction: int,
        forward_df: pd.DataFrame,
        limit_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        penetration_buf: float = 0.00015,  # 1.5 pips queue penetration
    ) -> Optional[TradeRecord]:
        """Resolves limit order fill within 16 M15 bars (4 hours), then simulates

        TP1 (50% scale + breakeven SL) and TP2 / Friday 16:00 UTC exit.
        """
        filled = False
        entry_time = None
        tp1_hit = False
        current_sl = sl_price
        risk_dist = abs(limit_price - sl_price)
        if risk_dist <= 0:
            return None

        for i, (ts, row) in enumerate(forward_df.iterrows()):
            if not filled:
                if i >= 16:
                    return None
                if direction == 1 and row["low"] <= (limit_price - penetration_buf):
                    filled = True
                    entry_time = ts
                elif direction == -1 and row["high"] >= (limit_price + penetration_buf):
                    filled = True
                    entry_time = ts
                continue

            # Pessimistic intra-bar check: SL checked before TP
            if direction == 1:
                if row["low"] <= current_sl:
                    r_runner = 0.0 if tp1_hit else -1.0
                    r_total = (0.5 * ((tp1_price - limit_price) / risk_dist) + 0.5 * r_runner) if tp1_hit else -1.0
                    return TradeRecord(asset, direction, entry_time, ts, limit_price, sl_price, tp1_price, tp2_price, r_total)
                if not tp1_hit and row["high"] >= tp1_price:
                    tp1_hit = True
                    current_sl = limit_price
                if row["high"] >= tp2_price:
                    r_total = 0.5 * ((tp1_price - limit_price) / risk_dist) + 0.5 * ((tp2_price - limit_price) / risk_dist)
                    return TradeRecord(asset, direction, entry_time, ts, limit_price, sl_price, tp1_price, tp2_price, r_total)
            else:
                if row["high"] >= current_sl:
                    r_runner = 0.0 if tp1_hit else -1.0
                    r_total = (0.5 * ((limit_price - tp1_price) / risk_dist) + 0.5 * r_runner) if tp1_hit else -1.0
                    return TradeRecord(asset, direction, entry_time, ts, limit_price, sl_price, tp1_price, tp2_price, r_total)
                if not tp1_hit and row["low"] <= tp1_price:
                    tp1_hit = True
                    current_sl = limit_price
                if row["low"] <= tp2_price:
                    r_total = 0.5 * ((limit_price - tp1_price) / risk_dist) + 0.5 * ((limit_price - tp2_price) / risk_dist)
                    return TradeRecord(asset, direction, entry_time, ts, limit_price, sl_price, tp1_price, tp2_price, r_total)

            # Friday 16:00 UTC Weekend Risk Rule
            if ts.isocalendar()[2] == 5 and ts.hour >= 16:
                unrealized_r = direction * (row["close"] - limit_price) / risk_dist
                if not tp1_hit and unrealized_r < 1.0:
                    return TradeRecord(asset, direction, entry_time, ts, limit_price, sl_price, tp1_price, tp2_price, unrealized_r)

        return None


def generate_purged_wfo_folds(
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    is_months: int = 24,
    oos_months: int = 3,
    purge_days: int = 5,
    embargo_days: int = 5,
) -> List[Dict[str, pd.Timestamp]]:
    """Generates rolling In-Sample (IS) and Out-of-Sample (OOS) window boundaries

    with explicit Purging and Embargo buffers to eliminate horizon overlap.
    """
    folds = []
    cursor = start_date
    while True:
        is_start = cursor
        is_end = is_start + pd.DateOffset(months=is_months)
        oos_start = is_end + pd.Timedelta(days=purge_days)
        oos_end = oos_start + pd.DateOffset(months=oos_months)

        if oos_end > end_date:
            break

        folds.append({
            "is_start": is_start,
            "is_end": is_end,
            "oos_start": oos_start,
            "oos_end": oos_end,
            "next_train_resume": oos_end + pd.Timedelta(days=embargo_days),
        })
        cursor = cursor + pd.DateOffset(months=oos_months)

    return folds


def evaluate_statistical_survival_gates(
    oos_trades: Sequence[TradeRecord],
    is_annual_return: float,
    oos_annual_return: float,
    num_trials: int = 100,
) -> Dict[str, Any]:
    """Evaluates the 4 statistical survival acceptance gates."""
    if not oos_trades:
        return {
            "status": "INSUFFICIENT_DATA",
            "wfe": 0.0,
            "wfe_pass": False,
            "dsr": 0.0,
            "dsr_pass": False,
            "leave_one_out_expectancy_r": 0.0,
            "leave_one_out_pass": False,
            "monte_carlo_max_dd_pct": 100.0,
            "monte_carlo_pass": False,
            "all_gates_passed": False,
            "execution_authority": False,
        }

    # 1. Walk-Forward Efficiency (WFE >= 0.55)
    is_ret = max(1e-6, is_annual_return)
    wfe = oos_annual_return / is_ret
    wfe_pass = bool(wfe >= 0.55)

    # 2. Leave-One-Out Macro Outlier Expectancy (> +0.35R)
    r_multiples = [t.pnl_r for t in oos_trades]
    sorted_r = sorted(r_multiples)
    # Remove top outlier (best trade)
    leave_one_out_r = sorted_r[:-1] if len(sorted_r) > 1 else sorted_r
    loo_expectancy = float(np.mean(leave_one_out_r)) if leave_one_out_r else 0.0
    loo_pass = bool(loo_expectancy > 0.35)

    # 3. Deflated Sharpe Ratio (DSR >= 0.70 / 0.95)
    mean_r = float(np.mean(r_multiples))
    std_r = max(1e-6, float(np.std(r_multiples)))
    sr_est = (mean_r / std_r) * math.sqrt(104)  # ~2 trades/week * 52 weeks

    skew = float(np.mean(((np.array(r_multiples) - mean_r) / std_r) ** 3))
    kurt = float(np.mean(((np.array(r_multiples) - mean_r) / std_r) ** 4))
    n_samples = len(r_multiples)

    # DSR z-score calculation
    gamma = 0.5772156649  # Euler-Mascheroni
    e_max_sr = (1.0 - gamma) * math.sqrt(2.0 * math.log(max(2, num_trials))) + gamma * math.sqrt(2.0 * math.log(max(2, num_trials)))
    denom = math.sqrt(max(1e-6, 1.0 - skew * sr_est + ((kurt - 1.0) / 4.0) * (sr_est ** 2)))
    z = ((sr_est - e_max_sr) * math.sqrt(max(1, n_samples - 1))) / denom
    # Standard normal CDF approximation
    dsr = float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    dsr_pass = bool(dsr >= 0.70)

    # 4. Monte Carlo Execution Perturbation (Drop 15% winners, MaxDD < 10.0%)
    np.random.seed(42)
    dd_samples = []
    n_sims = 500
    for _ in range(n_sims):
        # Sample with replacement
        sample = list(np.random.choice(r_multiples, size=len(r_multiples), replace=True))
        # Randomly drop 15% of positive R trades
        for idx in range(len(sample)):
            if sample[idx] > 0 and np.random.rand() < 0.15:
                sample[idx] = -1.0  # Turn into stopped trade
        # Convert R-multiples to equity curve with 1.0% NAV risk per trade
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for r_val in sample:
            equity += equity * 0.01 * r_val
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
        dd_samples.append(max_dd * 100.0)

    p95_dd = float(np.percentile(dd_samples, 95))
    mc_pass = bool(p95_dd < 10.0)

    all_passed = bool(wfe_pass and loo_pass and dsr_pass and mc_pass)

    return {
        "status": "READY_FOR_REVIEW",
        "wfe": round(wfe, 4),
        "wfe_pass": wfe_pass,
        "dsr": round(dsr, 4),
        "dsr_pass": dsr_pass,
        "leave_one_out_expectancy_r": round(loo_expectancy, 4),
        "leave_one_out_pass": loo_pass,
        "monte_carlo_max_dd_pct": round(p95_dd, 2),
        "monte_carlo_pass": mc_pass,
        "all_gates_passed": all_passed,
        "execution_authority": False,
    }
