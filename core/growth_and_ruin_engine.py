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

@dataclass
class MonteCarloRuinResult:
    starting_capital: float
    num_simulations: int
    num_trades: int
    median_terminal_equity: float
    p5_terminal_equity: float
    p95_terminal_equity: float
    median_max_dd: float
    probability_of_ruin: float
    probability_dd_over_20: float
    confidence_target_flip: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class AdvancedMonteCarloEngine:
    """Vectorized Markov regime-switching Monte Carlo & parameter perturbation simulator."""

    def __init__(self, seed: Optional[int] = 42):
        self.rng = np.random.default_rng(seed)

    def run_simulation(
        self,
        trades_by_regime: Dict[str, np.ndarray],
        starting_capital: float = 1000.0,
        n_trades: int = 100,
        n_simulations: int = 1000,
        ruin_dd_threshold: float = 0.50,
        target_multiplier: float = 2.0,
        transition_matrix: Optional[np.ndarray] = None,
    ) -> MonteCarloRuinResult:
        regime_names = list(trades_by_regime.keys())
        n_regimes = len(regime_names)
        if n_regimes == 0:
            raise ValueError("trades_by_regime cannot be empty")

        # 3-state default transition matrix: 80% persistence
        if transition_matrix is None:
            if n_regimes == 3:
                transition_matrix = np.array([
                    [0.80, 0.15, 0.05],  # BULL -> BULL, BEAR, SHOCK
                    [0.20, 0.70, 0.10],  # BEAR -> BULL, BEAR, SHOCK
                    [0.30, 0.30, 0.40],  # SHOCK -> BULL, BEAR, SHOCK
                ])
            else:
                transition_matrix = np.eye(n_regimes) * 0.8 + (0.2 / n_regimes)

        target_capital = starting_capital * target_multiplier
        final_equities = np.zeros(n_simulations, dtype=np.float64)
        max_drawdowns = np.zeros(n_simulations, dtype=np.float64)
        ruin_count = 0
        dd_20_count = 0
        flip_success_count = 0

        for s in range(n_simulations):
            equity = starting_capital
            peak = equity
            curr_regime = 0
            path_max_dd = 0.0
            flipped = False
            ruined = False

            for _ in range(n_trades):
                # Step regime via Markov chain
                probs = transition_matrix[curr_regime]
                prob_sum = np.sum(probs)
                if prob_sum > 0:
                    probs = probs / prob_sum
                curr_regime = int(self.rng.choice(n_regimes, p=probs))

                # Sample trade return from active regime
                pool = trades_by_regime[regime_names[curr_regime]]
                ret = float(self.rng.choice(pool)) if len(pool) > 0 else 0.0

                equity += equity * ret
                if equity <= 0.0:
                    equity = 0.0
                    ruined = True
                    break

                if equity >= target_capital:
                    flipped = True

                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak if peak > 0 else 0.0
                if dd > path_max_dd:
                    path_max_dd = dd

                if dd >= ruin_dd_threshold:
                    ruined = True
                    break

            if ruined:
                ruin_count += 1
            if path_max_dd >= 0.20:
                dd_20_count += 1
            if flipped:
                flip_success_count += 1

            final_equities[s] = equity
            max_drawdowns[s] = path_max_dd

        final_equities.sort()
        return MonteCarloRuinResult(
            starting_capital=starting_capital,
            num_simulations=n_simulations,
            num_trades=n_trades,
            median_terminal_equity=float(np.median(final_equities)),
            p5_terminal_equity=float(np.percentile(final_equities, 5)),
            p95_terminal_equity=float(np.percentile(final_equities, 95)),
            median_max_dd=float(round(np.median(max_drawdowns), 4)),
            probability_of_ruin=float(round(ruin_count / n_simulations, 4)),
            probability_dd_over_20=float(round(dd_20_count / n_simulations, 4)),
            confidence_target_flip=float(round(flip_success_count / n_simulations, 4)),
            execution_authority=False,
        )



class RiskBucket(str, Enum):
    CORE = "CORE"
    GROWTH = "GROWTH"
    HEDGE = "HEDGE"


@dataclass
class AccountFlipState:
    starting_capital: float
    current_equity: float
    high_water_mark: float
    active_drawdown: float
    flip_count: int
    risk_multiplier: float
    is_halted: bool
    bucket_allocations: Dict[RiskBucket, float]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "starting_capital": self.starting_capital,
            "current_equity": self.current_equity,
            "high_water_mark": self.high_water_mark,
            "active_drawdown": self.active_drawdown,
            "flip_count": self.flip_count,
            "risk_multiplier": self.risk_multiplier,
            "is_halted": self.is_halted,
            "bucket_allocations": {k.value: v for k, v in self.bucket_allocations.items()},
            "execution_authority": self.execution_authority,
        }


class AccountFlipController:
    """Manages account flip milestone locking, risk bucket allocation, and tiered drawdown defense."""

    def __init__(
        self,
        starting_capital: float = 1000.0,
        flip_target_mult: float = 2.0,
        emit_telemetry: bool = True,
    ):
        self.flip_target_mult = flip_target_mult
        self.emit_telemetry = emit_telemetry
        self.state = AccountFlipState(
            starting_capital=starting_capital,
            current_equity=starting_capital,
            high_water_mark=starting_capital,
            active_drawdown=0.0,
            flip_count=0,
            risk_multiplier=1.0,
            is_halted=False,
            bucket_allocations={
                RiskBucket.CORE: 0.50,
                RiskBucket.GROWTH: 0.30,
                RiskBucket.HEDGE: 0.20,
            },
            execution_authority=False,
        )

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.emit_telemetry:
            return
        try:
            from core.agent_event_bus import emit_agent_event
            emit_agent_event(sender="AccountFlipController", event_type=event_type, payload=payload)
        except Exception:
            pass

    def get_effective_risk_multiplier(self) -> float:
        return self.state.risk_multiplier

    def get_bucket_allocations(self) -> Dict[RiskBucket, float]:
        return self.state.bucket_allocations

    def update_equity(self, equity: float) -> AccountFlipState:
        eq = float(equity)
        self.state.current_equity = eq
        if eq > self.state.high_water_mark:
            self.state.high_water_mark = eq

        hwm = self.state.high_water_mark
        dd = (hwm - eq) / hwm if hwm > 0 else 0.0
        self.state.active_drawdown = round(dd, 4)

        # Check Account Flip milestone (e.g. 2x baseline)
        if eq >= self.state.starting_capital * self.flip_target_mult:
            self.state.flip_count += 1
            self.state.starting_capital = eq  # Reset baseline
            self.state.high_water_mark = eq
            self.state.active_drawdown = 0.0
            self.state.risk_multiplier = 1.0
            self._emit("ACCOUNT_FLIP_TRIGGERED", self.state.to_dict())
            return self.state

        # Tiered Drawdown Escalation
        if dd >= 0.20:
            self.state.is_halted = True
            self.state.risk_multiplier = 0.0
            self._emit("DRAWDOWN_HALT_TRIGGERED", {"drawdown": dd, "equity": eq})
        elif dd >= 0.15:
            self.state.risk_multiplier = 0.25
            self.state.bucket_allocations = {
                RiskBucket.CORE: 0.70,
                RiskBucket.GROWTH: 0.0,
                RiskBucket.HEDGE: 0.30,
            }
            self._emit("DRAWDOWN_DEFENSE_TIER2", {"drawdown": dd, "equity": eq})
        elif dd >= 0.10:
            self.state.risk_multiplier = 0.50
            self._emit("DRAWDOWN_DEFENSE_TIER1", {"drawdown": dd, "equity": eq})
        else:
            self.state.risk_multiplier = 1.0
            self.state.bucket_allocations = {
                RiskBucket.CORE: 0.50,
                RiskBucket.GROWTH: 0.30,
                RiskBucket.HEDGE: 0.20,
            }

        return self.state
