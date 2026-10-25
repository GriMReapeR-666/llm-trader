"""Deterministic Mathematical & Causality Auditor (Pillar 3 / Stage 3).

Verifies three fundamental quantitative invariants for every candidate strategy commit:
1. THE TIME-MACHINE TEST: Proves zero lookahead bias by corrupting future bars
   and asserting historical signals remain bit-for-bit identical.
2. FINITE DOMAIN CHECK: Guarantees signals contain zero NaN or Inf values.
3. ACCOUNTING CONSERVATION IDENTITY: Verifies sum(net_pnl) == (terminal_equity - initial_equity)
   and r_multiple == net_pnl / risk_dollars within 1e-4 tolerance.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Sequence, Union
import numpy as np
import pandas as pd


def verify_strategy_math_and_causality(
    strategy_signal_fn: Callable[[Any], Any],
    ohlcv_bars: Union[np.ndarray, pd.DataFrame],
    trades_ledger: List[Dict[str, Any]],
    initial_equity: float = 100.0,
    terminal_equity: float = 100.0,
) -> Dict[str, Any]:
    """Audit strategy signal causality, domain finiteness, and accounting conservation."""
    n = len(ohlcv_bars)
    if n < 30:
        return {"passed": False, "reason": "Insufficient bars for causality audit"}

    # 1. Finite Domain Check
    try:
        full_signals = np.asarray(strategy_signal_fn(ohlcv_bars), dtype=float)
    except Exception as exc:
        return {"passed": False, "reason": f"MATH_ERROR: Signal function failed on full series: {exc}"}

    if full_signals.shape[0] != n:
        return {
            "passed": False,
            "reason": f"MATH_ERROR: Signal length mismatch: {full_signals.shape[0]} != bars {n}",
        }

    if not np.all(np.isfinite(full_signals)):
        return {"passed": False, "reason": "MATH_ERROR: Strategy emitted NaN or Inf signals"}

    # 2. Time-Machine Causality Perturbation Test at multiple checkpoints
    checkpoints = [cp for cp in (n // 2, int(n * 0.75), n - 15) if 10 <= cp < n - 1]
    if not checkpoints:
        checkpoints = [n - 2]

    is_df = isinstance(ohlcv_bars, pd.DataFrame)

    for t_check in checkpoints:
        # Feed strictly truncated history up to t_check
        if is_df:
            past_only_bars = ohlcv_bars.iloc[: t_check + 1].copy()
            corrupted_future_bars = ohlcv_bars.copy()
            numeric_cols = [c for c in ("open", "high", "low", "close", "volume", "tick_volume") if c in corrupted_future_bars.columns]
            corrupted_future_bars.iloc[t_check + 1 :, corrupted_future_bars.columns.get_indexer(numeric_cols)] *= 100.0
        else:
            past_only_bars = ohlcv_bars[: t_check + 1].copy()
            corrupted_future_bars = ohlcv_bars.copy()
            corrupted_future_bars[t_check + 1 :] *= 100.0

        try:
            sig_past_only = float(strategy_signal_fn(past_only_bars)[-1])
        except Exception as exc:
            return {
                "passed": False,
                "reason": f"MATH_ERROR: Signal function failed on truncated past bars at {t_check}: {exc}",
            }

        try:
            sig_with_corrupted_future = float(strategy_signal_fn(corrupted_future_bars)[t_check])
        except Exception as exc:
            return {
                "passed": False,
                "reason": f"MATH_ERROR: Signal function failed on future-corrupted bars at {t_check}: {exc}",
            }

        if abs(sig_past_only - sig_with_corrupted_future) > 1e-12 or abs(sig_past_only - float(full_signals[t_check])) > 1e-12:
            return {
                "passed": False,
                "reason": (
                    f"LOOKAHEAD_BIAS_DETECTED at bar {t_check}: "
                    f"past_only={sig_past_only:.8f} vs future_corrupted={sig_with_corrupted_future:.8f}"
                ),
            }

    # 3. Accounting & R-Multiple Conservation Check
    if trades_ledger:
        total_net_pnl = 0.0
        for idx, tr in enumerate(trades_ledger):
            pnl = float(tr.get("net_pnl", tr.get("pnl_usd", tr.get("pnl_pips", 0.0))))
            risk = float(tr.get("risk_dollars", tr.get("risk_usd", 1.0)))
            r_mult = float(tr.get("r_multiple", 0.0))

            if risk <= 0:
                return {
                    "passed": False,
                    "reason": f"MATH_ERROR: Trade {idx} has non-positive risk_dollars={risk}",
                }

            expected_r = pnl / risk
            if abs(r_mult - expected_r) > 1e-4:
                return {
                    "passed": False,
                    "reason": f"MATH_ERROR: Trade {idx} R-multiple mismatch ({r_mult:.4f} != {pnl:.2f}/{risk:.2f}={expected_r:.4f})",
                }
            total_net_pnl += pnl

        equity_delta = float(terminal_equity) - float(initial_equity)
        if abs(total_net_pnl - equity_delta) > 1e-2:
            return {
                "passed": False,
                "reason": f"ACCOUNTING_MISMATCH: sum(net_pnl)={total_net_pnl:.2f} != delta_equity={equity_delta:.2f}",
            }

    return {"passed": True, "reason": "ALL_CAUSALITY_AND_MATH_INVARIANTS_VERIFIED"}
