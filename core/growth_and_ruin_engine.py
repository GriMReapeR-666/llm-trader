"""Growth and Ruin Engine — Small-Capital Compounding, Regime Monte Carlo, and Account Flip."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

class SizingMode(str, Enum):
    FIXED_FRACTIONAL = "FIXED_FRACTIONAL"
    VOLATILITY_TARGETING = "VOLATILITY_TARGETING"
    HALF_KELLY = "HALF_KELLY"
    DYNAMIC_LEVERAGE = "DYNAMIC_LEVERAGE"

@dataclass
class GrowthSimulationResult:
    starting_capital: float
    terminal_equity: float
    net_pnl: float
    total_trades: int
    max_drawdown: float
    is_ruined: bool
    equity_path: List[float] = field(default_factory=list)
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class SmallCapitalGrowthSimulator:
    """Simulates small capital account compounding under strict risk & drawdown bounds."""

    def __init__(self, seed: Optional[int] = 42):
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def simulate_path(
        self,
        starting_capital: float,
        trade_rs: Sequence[float],
        sizing_mode: SizingMode = SizingMode.FIXED_FRACTIONAL,
        base_risk_pct: float = 0.015,
        kill_dd_pct: float = 0.15,
        vol_multipliers: Optional[Sequence[float]] = None,
    ) -> GrowthSimulationResult:
        equity = float(starting_capital)
        peak = equity
        path = [equity]
        is_ruined = False
        max_dd = 0.0
        trades_executed = 0

        for idx, r_mult in enumerate(trade_rs):
            # Calculate risk fraction
            if sizing_mode == SizingMode.FIXED_FRACTIONAL:
                risk_pct = base_risk_pct
            elif sizing_mode == SizingMode.HALF_KELLY:
                # Constrained half-Kelly: conservative floor 0.5%, cap 2.0%
                risk_pct = min(0.02, max(0.005, base_risk_pct * 0.75))
            elif sizing_mode == SizingMode.VOLATILITY_TARGETING:
                vol_m = vol_multipliers[idx] if vol_multipliers and idx < len(vol_multipliers) else 1.0
                risk_pct = min(0.02, base_risk_pct / max(0.5, vol_m))
            else:  # DYNAMIC_LEVERAGE
                dd_ratio = max_dd / kill_dd_pct if kill_dd_pct > 0 else 0.0
                risk_pct = base_risk_pct * max(0.25, 1.0 - dd_ratio)

            risk_amount = equity * risk_pct
            trade_pnl = risk_amount * float(r_mult)
            equity += trade_pnl
            trades_executed += 1

            if equity <= 0.0:
                equity = 0.0
                is_ruined = True
                path.append(equity)
                break

            path.append(equity)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

            if dd >= kill_dd_pct:
                is_ruined = True
                break

        return GrowthSimulationResult(
            starting_capital=starting_capital,
            terminal_equity=round(equity, 2),
            net_pnl=round(equity - starting_capital, 2),
            total_trades=trades_executed,
            max_drawdown=round(max_dd, 4),
            is_ruined=is_ruined,
            equity_path=[round(e, 2) for e in path],
            execution_authority=False,
        )

    def simulate_sequence_risk(
        self,
        starting_capital: float,
        trade_rs: Sequence[float],
        n_permutations: int = 200,
        base_risk_pct: float = 0.015,
        kill_dd_pct: float = 0.15,
    ) -> Dict[str, float]:
        r_arr = np.asarray(trade_rs, dtype=np.float64)
        terminals: List[float] = []
        ruined_count = 0
        max_dds: List[float] = []

        for _ in range(n_permutations):
            shuffled_rs = self.rng.permutation(r_arr)
            res = self.simulate_path(starting_capital, shuffled_rs, base_risk_pct=base_risk_pct, kill_dd_pct=kill_dd_pct)
            terminals.append(res.terminal_equity)
            max_dds.append(res.max_drawdown)
            if res.is_ruined:
                ruined_count += 1

        terminals.sort()
        return {
            "median_terminal_equity": float(np.median(terminals)),
            "p5_terminal_equity": float(np.percentile(terminals, 5)),
            "p95_terminal_equity": float(np.percentile(terminals, 95)),
            "mean_max_drawdown": float(np.mean(max_dds)),
            "prob_drawdown_over_15": float(ruined_count / n_permutations),
        }

    def detect_equity_drift(
        self,
        pnls: Sequence[float],
        window: int = 20,
        degrade_threshold: float = 0.25,
    ) -> Tuple[bool, float]:
        if len(pnls) < 2 * window:
            return False, 0.0
        prior = float(np.mean(pnls[-2 * window : -window]))
        current = float(np.mean(pnls[-window:]))
        if prior <= 0:
            return False, 0.0
        degradation = (prior - current) / abs(prior)
        is_degraded = degradation >= degrade_threshold
        return is_degraded, float(round(degradation, 4))
