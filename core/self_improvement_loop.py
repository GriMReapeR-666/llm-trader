"""Recursive Self-Improvement Loop — AQuA Sealed Sandbox + Dual Research Loops.

Implements the self-improvement cycle:
  1. BASELINE: Capture current system performance snapshot
  2. ANALYZE: LLM identifies underperforming components
  3. PROPOSE: LLM suggests parameter/weight adjustments
  4. VALIDATE: Sealed sandbox evaluates proposals against baseline
  5. APPLY: Only improvements that beat baseline are deployed
  6. TRACK: Record improvement history to prevent regression

The sealed sandbox principle (AQuA):
  - The agent proposes changes within a constrained DSL
  - The evaluation environment is FIXED and cannot be modified
  - This prevents "generation leakage" and "selection leakage"

References:
  - AQuA (2026): Sealed sandbox + recursive self-improvement
  - FactorMiner (2026): Experience memory for accumulated knowledge
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from state_persistence import StoreBackedState

import numpy as np

logger = logging.getLogger("SelfImprovement")


# ---------------------------------------------------------------------------
# Baseline Snapshot
# ---------------------------------------------------------------------------

@dataclass
class BaselineSnapshot:
    """A point-in-time snapshot of system performance."""
    timestamp: float = 0.0
    cycle_num: int = 0
    # Alpha ensemble metrics
    ic_mean_overall: float = 0.0
    ic_ir_overall: float = 0.0
    n_alphas_active: int = 0
    alpha_weights: Dict[str, float] = field(default_factory=dict)
    # Regime metrics
    current_regime: str = ""
    regime_confidence: float = 0.0
    regime_dwell_bars: int = 0
    # Risk metrics
    open_positions: int = 0
    portfolio_heat_r: float = 0.0
    current_drawdown: float = 0.0
    # Trade performance
    win_rate: float = 0.0
    avg_r_multiple: float = 0.0
    sharpe_rolling: float = 0.0
    total_trades: int = 0
    # CPCV health
    cpcv_pbo: float = 0.0
    cpcv_dsr: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Walk-Forward Replay Simulator
# ---------------------------------------------------------------------------


def create_replay_simulator(
    store: Any,
    alpha_weights: Optional[Dict[str, float]] = None,
    carry_metrics: Optional[Dict[str, float]] = None,
    base_risk_per_trade: float = 0.01,
) -> Callable[[Dict[str, Any]], Optional[Dict[str, float]]]:
    """Honest walk-forward replay simulator for trade-management proposals.

    Replays closed trades from the store and applies exactly ONE class of
    change honestly: position sizing. R multiples are size-invariant by
    definition, so a sizing change moves only the cost-scaled equity path
    (drawdown) — never the per-trade edge. Metrics the replay cannot
    legitimately produce (e.g. alpha IC) are carried through unchanged via
    ``carry_metrics`` instead of being fabricated.

    Contract:
      - sizing changes (risk_per_trade, position_scale, ...) -> metrics dict
      - anything the replay cannot evaluate (alpha re-weights, entry
        thresholds, regime params) -> ``None`` — the engine records
        DEFERRED instead of inventing a baseline-copy "0% rejection"

    Args:
        store: TradingStore instance (uses ``store.path`` for the DB).
        alpha_weights: Unused; kept for call-site compatibility. Alpha
            re-weighting is deliberately NOT evaluable by trade replay.
        carry_metrics: Baseline metrics to carry through unchanged.
        base_risk_per_trade: Risk fraction used to convert the R-curve
            drawdown into an equity fraction (must match the baseline
            builder's convention; default 1%).
    """
    del alpha_weights  # alpha re-weighting is NOT evaluable by trade replay
    carry = dict(carry_metrics or {})

    def _load_closed_trades(limit: int = 200) -> List[Tuple[float, float]]:
        import sqlite3
        db_path = getattr(store, "path", "trading_state.db")
        try:
            try:
                from research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            tickets = sorted(broker_reconciled_tickets(str(db_path)))
        except Exception:
            tickets = []
        if not tickets:
            return []
        con = sqlite3.connect(str(db_path))
        try:
            marks = ",".join("?" for _ in tickets)
            rows = con.execute(
                "SELECT pnl, risk_amount FROM trades "
                "WHERE closed_at IS NOT NULL AND pnl IS NOT NULL "
                "AND risk_amount IS NOT NULL AND risk_amount != 0 "
                "AND ticket IN (%s) "
                "ORDER BY closed_at DESC LIMIT ?" % marks,
                (*tickets, limit),
            ).fetchall()
        finally:
            con.close()
        return [(float(p), float(r)) for p, r in rows]

    try:
        trades = _load_closed_trades()
    except Exception as e:
        logger.debug("Replay simulator could not load trades: %s", e)
        trades = []

    def _metrics(sizing_ratio: float) -> Dict[str, float]:
        rs = np.array([pnl / risk for pnl, risk in trades], dtype=float)

        win_rate = float((rs > 0).sum() / len(rs))
        avg_r = float(rs.mean())
        std = float(rs.std(ddof=1)) if len(rs) > 1 else 0.0
        sharpe = float(rs.mean() / std * np.sqrt(252)) if std > 0 else 0.0

        # R-curve drawdown -> equity fraction at the scaled risk level.
        cum = np.concatenate([[0.0], np.cumsum(rs)])
        peak = np.maximum.accumulate(cum)
        dd_r = float(np.max(peak - cum)) if len(cum) > 1 else 0.0
        dd_frac = dd_r * base_risk_per_trade * sizing_ratio

        out: Dict[str, float] = {
            "win_rate": win_rate,
            "avg_r_multiple": avg_r,
            "sharpe_rolling": sharpe,
            "current_drawdown": dd_frac,
            "total_trades": float(len(rs)),
        }
        out.update(carry)
        return out

    _SIZING_KEYS = ("risk_per_trade", "position_scale", "position_size", "lot_multiplier")

    def _extract_change(change: Any) -> Optional[float]:
        if isinstance(change, dict):
            for key in ("proposed", "value", "new"):
                if key in change:
                    return _extract_change(change[key])
            return None
        if isinstance(change, (int, float)):
            return float(change)
        if isinstance(change, str):
            try:
                return float(change)
            except ValueError:
                return None
        return None

    def _simulate(params: Dict[str, Any]) -> Optional[Dict[str, float]]:
        if not trades:
            return None  # no history -> cannot simulate, never fabricate

        sizing_ratio = 1.0
        saw_sizing = False
        for name, change in (params or {}).items():
            if name in _SIZING_KEYS:
                proposed = _extract_change(change)
                current = None
                if isinstance(change, dict) and "current" in change:
                    current = _extract_change(change["current"])
                if proposed is None or proposed <= 0:
                    return None
                if current and current > 0:
                    sizing_ratio *= proposed / current
                else:
                    sizing_ratio *= proposed / base_risk_per_trade
                saw_sizing = True
            else:
                # Trade replay cannot honestly evaluate this change class.
                return None
        if not saw_sizing:
            return None
        return _metrics(sizing_ratio)

    return _simulate


# ---------------------------------------------------------------------------
# Improvement Proposal
# ---------------------------------------------------------------------------

@dataclass
class ImprovementProposal:
    """A proposed improvement to the system."""
    proposal_id: str = ""
    category: str = ""  # alpha_weights, risk_params, regime_params, strategy
    description: str = ""
    parameter_changes: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    expected_impact: str = ""
    # Validation results
    baseline_score: float = 0.0
    proposed_score: float = 0.0
    improvement_pct: float = 0.0
    status: str = "PENDING"  # PENDING, VALIDATED, APPLIED, REJECTED, DEFERRED
    rejection_reason: str = ""
    applied_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Sealed Sandbox Validator (AQuA Principle)
# ---------------------------------------------------------------------------

class SealedProposalValidator:
    """Validates improvement proposals in a sealed sandbox.

    The validator is FIXED — the improvement agent cannot modify it.
    This prevents the agent from "gaming" the evaluation metrics.
    """

    def __init__(
        self,
        min_improvement_pct: float = 2.0,
        max_regression_pct: float = 5.0,
        min_trades_for_validation: int = 20,
    ):
        self.min_improvement_pct = min_improvement_pct
        self.max_regression_pct = max_regression_pct
        self.min_trades_for_validation = min_trades_for_validation

    def _compute_score(self, metrics: Dict[str, Any]) -> float:
        """Compute a composite performance score.

        Weighted combination of key metrics:
        - IC mean (25%)
        - Win rate (20%)
        - Sharpe (20%)
        - Avg R multiple (15%)
        - Drawdown score (20%) — less negative is better
        """
        ic = abs(metrics.get("ic_mean_overall", 0))
        wr = metrics.get("win_rate", 0)
        sharpe = metrics.get("sharpe_rolling", 0)
        avg_r = metrics.get("avg_r_multiple", 0)
        # Drawdown units: the production baseline convention is an equity
        # FRACTION (e.g. 0.05 = 5%, from the R-curve * risk% conversion).
        # Percent-scale values (> 1.0) pass through unchanged.
        drawdown = metrics.get("current_drawdown", 0)
        dd_raw = abs(float(drawdown or 0))
        dd_pct = dd_raw * 100.0 if dd_raw <= 1.0 else dd_raw
        dd_score = max(0.0, 100.0 - min(dd_pct, 100.0))

        score = (
            ic * 100 * 0.25 +       # IC mean contribution
            wr * 100 * 0.20 +       # Win rate contribution
            sharpe * 20 * 0.20 +    # Sharpe contribution (scaled)
            max(avg_r, 0) * 20 * 0.15 +  # R multiple contribution
            dd_score * 0.20          # Drawdown score (higher is better)
        )
        return float(score)
    def validate_proposal(
        self,
        proposal: ImprovementProposal,
        baseline: BaselineSnapshot,
        proposed_metrics: Dict[str, float],
    ) -> ImprovementProposal:
        """Validate a proposal against the baseline.

        Args:
            proposal: The proposed improvement.
            baseline: Current system performance.
            proposed_metrics: Performance metrics from proposed changes.

        Returns:
            Updated proposal with validation results.
        """
        # Compute composite score from key metrics
        baseline_score = self._compute_score(baseline.to_dict())
        proposed_score = self._compute_score(proposed_metrics)

        proposal.baseline_score = baseline_score
        proposal.proposed_score = proposed_score

        if baseline_score == 0:
            proposal.improvement_pct = 0.0
        else:
            proposal.improvement_pct = ((proposed_score - baseline_score) / abs(baseline_score)) * 100

        # Check: must improve by at least min_improvement_pct
        if proposal.improvement_pct < self.min_improvement_pct:
            proposal.status = "REJECTED"
            proposal.rejection_reason = (
                f"Insufficient improvement: {proposal.improvement_pct:.1f}% < {self.min_improvement_pct}%"
            )
            return proposal

        # Check: must not regress more than max_regression_pct on any single metric
        regressions = self._check_regressions(baseline.to_dict(), proposed_metrics)
        if regressions:
            worst = max(regressions.values())
            if worst > self.max_regression_pct:
                proposal.status = "REJECTED"
                proposal.rejection_reason = (
                    f"Unacceptable regression: {worst:.1f}% in {max(regressions, key=regressions.get)}"
                )
                return proposal

        proposal.status = "VALIDATED"
        return proposal

    def _check_regressions(
        self,
        baseline: Dict[str, Any],
        proposed: Dict[str, Any],
    ) -> Dict[str, float]:
        """Check for unacceptable regressions in individual metrics."""
        regressions = {}
        checks = [
            ("win_rate", "Win Rate"),
            ("sharpe_rolling", "Sharpe"),
            ("avg_r_multiple", "Avg R Multiple"),
        ]
        for key, name in checks:
            base_val = baseline.get(key, 0)
            prop_val = proposed.get(key, 0)
            if base_val != 0:
                change_pct = ((prop_val - base_val) / abs(base_val)) * 100
                if change_pct < -self.max_regression_pct:
                    regressions[name] = abs(change_pct)
        return regressions



# ---------------------------------------------------------------------------
# Empirical Simulator — replays real trade history against proposed changes
# ---------------------------------------------------------------------------

@dataclass
class _Trade:
    pnl: float
    r_multiple: float
    exit_reason: str


class EmpiricalSimulator:
    """Loads real trade history from trading_state.db and simulates proposed changes."""

    def __init__(self, db_path: str = "trading_state.db", max_trades: int = 500):
        self.db_path = db_path
        self.max_trades = max_trades
        self.trades: List[_Trade] = []
        self._load_trades()

    def _load_trades(self) -> None:
        try:
            import sqlite3
            try:
                from research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            tickets = sorted(broker_reconciled_tickets(self.db_path))
            if not tickets:
                return
            con = sqlite3.connect(self.db_path)
            marks = ",".join("?" for _ in tickets)
            rows = con.execute(
                "SELECT pnl, risk_amount, exit_reason FROM trades "
                "WHERE pnl IS NOT NULL AND closed_at IS NOT NULL "
                "AND ticket IN (%s) "
                "ORDER BY rowid DESC LIMIT ?" % marks,
                (*tickets, self.max_trades),
            ).fetchall()
            con.close()
            for pnl, risk_amount, exit_reason in rows:
                r = pnl / risk_amount if risk_amount and risk_amount != 0 else 0.0
                self.trades.append(_Trade(pnl=pnl, r_multiple=r, exit_reason=exit_reason or ""))
        except Exception as e:
            logger.debug("Failed to load trades for simulator: %s", e)

    @property
    def n(self) -> int:
        return len(self.trades)

    def baseline_metrics(self) -> Dict[str, float]:
        return self._metrics_for(self.trades)

    def simulate(self, proposal: ImprovementProposal) -> Optional[Dict[str, float]]:
        if not self.trades:
            return None
        changes = proposal.parameter_changes
        category = proposal.category

        if "entry_confidence_threshold" in changes:
            return None

        if "risk_per_trade" in changes:
            proposed_val = self._extract_float(changes["risk_per_trade"])
            if proposed_val is None or proposed_val <= 0:
                return None
            risk_amounts = [t.pnl / t.r_multiple if t.r_multiple != 0 else 0 for t in self.trades]
            median_risk = float(np.median(risk_amounts)) if risk_amounts else 0.01
            if median_risk == 0:
                return None
            ratio = proposed_val / median_risk
            scaled = [_Trade(pnl=t.pnl * ratio, r_multiple=t.r_multiple * ratio,
                             exit_reason=t.exit_reason) for t in self.trades]
            return self._metrics_for(scaled)

        if "position_scale" in changes:
            proposed_val = self._extract_float(changes["position_scale"])
            if proposed_val is None or proposed_val <= 0:
                return None
            scaled = [_Trade(pnl=t.pnl * proposed_val, r_multiple=t.r_multiple * proposed_val,
                             exit_reason=t.exit_reason) for t in self.trades]
            return self._metrics_for(scaled)

        if category == "risk_params":
            for v in changes.values():
                val = self._extract_float(v)
                if val is not None and val > 0:
                    risk_amounts = [t.pnl / t.r_multiple if t.r_multiple != 0 else 0 for t in self.trades]
                    median_risk = float(np.median(risk_amounts)) if risk_amounts else 0.01
                    if median_risk == 0:
                        return None
                    ratio = val / median_risk
                    scaled = [_Trade(pnl=t.pnl * ratio, r_multiple=t.r_multiple * ratio,
                                     exit_reason=t.exit_reason) for t in self.trades]
                    return self._metrics_for(scaled)

        return None

    @staticmethod
    def _extract_float(v) -> Optional[float]:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for key in ("proposed", "value", "new"):
                if key in v:
                    return EmpiricalSimulator._extract_float(v[key])
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return None

    def _metrics_for(self, trades: List[_Trade]) -> Dict[str, float]:
        n = len(trades)
        if n == 0:
            return {"win_rate": 0.0, "avg_r_multiple": 0.0, "sharpe_rolling": 0.0,
                    "current_drawdown": 0.0, "total_trades": 0, "expectancy": 0.0}

        pnls = np.array([t.pnl for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]

        win_rate = len(wins) / n if n else 0.0
        avg_win = float(np.mean(wins)) if len(wins) else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) else 0.0
        expectancy = float(np.mean(pnls))

        avg_r = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0

        std = float(np.std(pnls, ddof=1)) if n > 1 else 0.0
        sharpe = (expectancy / std * np.sqrt(252)) if std > 0 else 0.0

        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        drawdown = cum - peak
        max_dd = float(np.min(drawdown)) if len(drawdown) else 0.0

        return {
            "win_rate": win_rate,
            "avg_r_multiple": avg_r,
            "sharpe_rolling": sharpe,
            "current_drawdown": max_dd,
            "total_trades": n,
            "expectancy": expectancy,
        }


# ---------------------------------------------------------------------------
# Experience Memory (FactorMiner-style)
# ---------------------------------------------------------------------------

@dataclass
class ImprovementMemory:
    """Persistent memory of improvement attempts."""
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    applied_count: int = 0
    rejected_count: int = 0
    total_improvement_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_proposals": len(self.proposals),
            "applied": self.applied_count,
            "rejected": self.rejected_count,
            "total_improvement_pct": round(self.total_improvement_pct, 2),
            "recent_proposals": self.proposals[-10:],
        }


class ImprovementExperienceMemory(StoreBackedState):
    """Persistent file-backed improvement memory."""

    store_key = "self_improvement_state_memory"

    def __init__(self, state_file: str = "self_improvement_state.json", store: Optional[Any] = None):
        self.state_file = state_file
        self.store = store
        self.memory = self._load()
        if store is not None:
            self.attach_store(store)

    def _load(self) -> ImprovementMemory:
        try:
            data = self._load_state_dict()
            if data:
                mem = ImprovementMemory(
                    applied_count=data.get("applied", 0),
                    rejected_count=data.get("rejected", 0),
                    total_improvement_pct=data.get("total_improvement_pct", 0.0),
                )
                mem.proposals = data.get("proposals", [])
                return mem
        except Exception as exc:
            logger.debug("memory operation failed: %s", exc)
        return ImprovementMemory()

    def save(self) -> None:
        try:
            self._save_state_dict(self.memory.to_dict())
        except Exception as e:
            logger.debug("Failed to save improvement state: %s", e)

    def record_proposal(self, proposal: ImprovementProposal) -> None:
        self.memory.proposals.append(proposal.to_dict())
        if proposal.status == "APPLIED":
            self.memory.applied_count += 1
            self.memory.total_improvement_pct += proposal.improvement_pct
        elif proposal.status == "REJECTED":
            self.memory.rejected_count += 1
        # Keep last 100
        if len(self.memory.proposals) > 100:
            self.memory.proposals = self.memory.proposals[-100:]
        self.save()


# ---------------------------------------------------------------------------
# Self-Improvement Engine
# ---------------------------------------------------------------------------

@dataclass
class SelfImprovementState:
    """Persistent state for the self-improvement loop."""
    cycle_count: int = 0
    improvement_count: int = 0
    last_baseline: Dict[str, Any] = field(default_factory=dict)
    current_params: Dict[str, Any] = field(default_factory=dict)
    improvement_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_count": self.cycle_count,
            "improvement_count": self.improvement_count,
            "last_baseline": self.last_baseline,
            "current_params": self.current_params,
            "history_count": len(self.improvement_history),
            "recent_history": self.improvement_history[-5:],
        }


class RecursiveSelfImprovementEngine(StoreBackedState):
    """Recursive self-improvement engine using AQuA principles.

    Cycle:
      1. BASELINE: Capture current performance
      2. ANALYZE: Identify weaknesses
      3. PROPOSE: Generate improvement proposals
      4. VALIDATE: Sealed sandbox evaluation
      5. APPLY: Deploy validated improvements
      6. TRACK: Record history, prevent regression
    """

    store_key = "self_improvement_state"

    def __init__(
        self,
        state_file: str = "self_improvement_state.json",
        min_improvement_pct: float = 2.0,
        max_history: int = 50,
        db_path: str = "trading_state.db",
        simulator: Optional[EmpiricalSimulator] = None,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.state = self._load_state()
        self.memory = ImprovementExperienceMemory(
            state_file=state_file.replace(".json", "_memory.json"),
            store=store,
        )
        self.validator = SealedProposalValidator(min_improvement_pct=min_improvement_pct)
        self.max_history = max_history
        self.simulator = simulator if simulator is not None else EmpiricalSimulator(db_path=db_path)
        if store is not None:
            self.attach_store(store)

    def _load_state(self) -> SelfImprovementState:
        try:
            data = self._load_state_dict()
            if data:
                state = SelfImprovementState(
                    cycle_count=data.get("cycle_count", 0),
                    improvement_count=data.get("improvement_count", 0),
                )
                state.last_baseline = data.get("last_baseline", {})
                state.current_params = data.get("current_params", {})
                state.improvement_history = data.get("improvement_history", [])
                return state
        except Exception as exc:
            logger.warning("state load failed: %s", exc)
        return SelfImprovementState()

    def _save_state(self) -> None:
        try:
            self._save_state_dict(self.state.to_dict())
        except Exception as e:
            logger.debug("Failed to save self-improvement state: %s", e)

    def capture_baseline(
        self,
        cycle_num: int,
        ensemble_result: Optional[Dict[str, Any]] = None,
        regime_data: Optional[Dict[str, Any]] = None,
        risk_data: Optional[Dict[str, Any]] = None,
        trade_data: Optional[Dict[str, Any]] = None,
        cpcv_data: Optional[Dict[str, Any]] = None,
    ) -> BaselineSnapshot:
        """Capture a performance baseline snapshot."""
        snapshot = BaselineSnapshot(
            timestamp=time.time(),
            cycle_num=cycle_num,
        )

        if ensemble_result:
            snapshot.ic_mean_overall = ensemble_result.get("overall_ic", 0)
            snapshot.n_alphas_active = ensemble_result.get("n_alphas_used", 0)
            snapshot.alpha_weights = ensemble_result.get("weights", {})

        if regime_data:
            snapshot.current_regime = regime_data.get("regime", {}).get("primary_regime", "")
            snapshot.regime_confidence = regime_data.get("regime", {}).get("confidence", 0)

        if risk_data:
            snapshot.portfolio_heat_r = risk_data.get("open_r", 0)
            snapshot.current_drawdown = risk_data.get("drawdown", 0)

        if trade_data:
            snapshot.win_rate = trade_data.get("win_rate", 0)
            snapshot.avg_r_multiple = trade_data.get("avg_r", 0)
            snapshot.sharpe_rolling = trade_data.get("sharpe", 0)
            snapshot.total_trades = trade_data.get("total_trades", 0)

        if cpcv_data:
            snapshot.cpcv_pbo = cpcv_data.get("probability_of_backtest_overfitting_pbo", 0)
            snapshot.cpcv_dsr = cpcv_data.get("deflated_sharpe_ratio", 0)

        self.state.last_baseline = snapshot.to_dict()
        self._save_state()
        return snapshot

    def build_analysis_prompt(self, baseline: BaselineSnapshot) -> str:
        """Build LLM prompt for analyzing weaknesses and proposing improvements."""
        ctx = baseline.to_dict()
        history = self.state.to_dict().get("recent_history", [])

        prompt = f"""You are an autonomous quantitative trading system self-improvement agent.

CURRENT SYSTEM STATE:
- Cycle: {ctx['cycle_num']}
- Regime: {ctx['current_regime']} (confidence: {ctx['regime_confidence']:.2f})
- Active alphas: {ctx['n_alphas_active']}
- IC mean: {ctx['ic_mean_overall']:.4f}
- Win rate: {ctx['win_rate']:.2%}
- Avg R multiple: {ctx['avg_r_multiple']:.2f}
- Rolling Sharpe: {ctx['sharpe_rolling']:.2f}
- Portfolio heat: {ctx['portfolio_heat_r']:.1f}R
- Current drawdown: {ctx['current_drawdown']:.2%}
- CPCV PBO: {ctx['cpcv_pbo']:.3f}
- CPCV DSR: {ctx['cpcv_dsr']:.3f}
- Total trades: {ctx['total_trades']}

ALPHA WEIGHTS:
{json.dumps(ctx.get('alpha_weights', {}), indent=2)}

RECENT IMPROVEMENT HISTORY:
{json.dumps(history, indent=2)}

ANALYZE and propose improvements in these categories:
1. ALPHA_WEIGHTS: Adjust alpha weights (boost strong performers, reduce weak)
2. RISK_PARAMS: Adjust risk_per_trade, max_drawdown, stop/take-profit levels
3. STRATEGY: Adjust entry confidence thresholds, holding periods, trailing stops

RULES:
- Each proposal must target ONE specific parameter change
- Propose realistic changes (max 20% adjustment per parameter)
- Explain the rationale based on the data above
- Do NOT propose changes that would increase risk during drawdowns

Output format (one proposal per block):
```
CATEGORY: alpha_weights
PARAMETER: alpha_036_weight
CURRENT_VALUE: {ctx.get('alpha_weights', {}).get('alpha_036', 0.1):.4f}
PROPOSED_VALUE: <new_value>
RATIONALE: <why this change should improve performance>
EXPECTED_IMPACT: <expected metric improvement>
```

Generate 1-3 improvement proposals.
"""
        return prompt

    def parse_llm_proposals(self, response: str) -> List[ImprovementProposal]:
        """Parse LLM response into improvement proposals."""
        proposals = []
        blocks = response.split("```")
        for i in range(1, len(blocks), 2):
            block = blocks[i].strip()
            if not block:
                continue
            lines = block.split("\n")
            params = {}
            for line in lines:
                if ":" in line:
                    key, val = line.split(":", 1)
                    params[key.strip()] = val.strip()
            if "CATEGORY" in params:
                proposals.append(ImprovementProposal(
                    proposal_id=f"prop_{self.state.cycle_count}_{i}",
                    category=params.get("CATEGORY", "unknown"),
                    description=params.get("RATIONALE", ""),
                    parameter_changes={
                        params.get("PARAMETER", "unknown"): {
                            "current": params.get("CURRENT_VALUE", "0"),
                            "proposed": params.get("PROPOSED_VALUE", "0"),
                        }
                    },
                    rationale=params.get("RATIONALE", ""),
                    expected_impact=params.get("EXPECTED_IMPACT", ""),
                ))
        return proposals

    def run_improvement_cycle(
        self,
        baseline: Optional[BaselineSnapshot] = None,
        llm_analyze_fn: Optional[Callable[[str], str]] = None,
        simulate_proposed_metrics: Optional[Callable[[Dict[str, Any]], Dict[str, float]]] = None,
    ) -> List[ImprovementProposal]:
        """Execute one self-improvement cycle.

        Args:
            baseline: Current performance snapshot (captured if None).
            llm_analyze_fn: LLM function for analysis.
            simulate_proposed_metrics: Function to simulate metrics for proposed changes.

        Returns:
            List of evaluated proposals (APPLIED / VALIDATED / REJECTED /
            DEFERRED). Proposals the simulator cannot evaluate are DEFERRED
            with an honest reason — never a fabricated 0% rejection.
        """
        self.state.cycle_count += 1

        # Step 1: BASELINE
        if baseline is None:
            baseline = BaselineSnapshot(
                timestamp=time.time(),
                cycle_num=self.state.cycle_count,
            )
            # Populate from simulator if available
            if self.simulator.n > 0:
                sim_metrics = self.simulator.baseline_metrics()
                baseline.win_rate = sim_metrics.get("win_rate", 0)
                baseline.avg_r_multiple = sim_metrics.get("avg_r_multiple", 0)
                baseline.sharpe_rolling = sim_metrics.get("sharpe_rolling", 0)
                baseline.total_trades = sim_metrics.get("total_trades", 0)
                baseline.current_drawdown = sim_metrics.get("current_drawdown", 0)

        # Step 2: ANALYZE
                baseline.current_drawdown = sim_metrics.get("current_drawdown", 0)

        # Step 2: ANALYZE
        prompt = self.build_analysis_prompt(baseline)
        if llm_analyze_fn is not None:
            try:
                response = llm_analyze_fn(prompt)
                proposals = self.parse_llm_proposals(response)
            except Exception as e:
                logger.warning("LLM analysis failed: %s", e)
                proposals = self._fallback_proposals(baseline)
        else:
            proposals = self._fallback_proposals(baseline)

        # Step 3-5: VALIDATE and APPLY
        applied: List[ImprovementProposal] = []
        evaluated: List[ImprovementProposal] = []
        for proposal in proposals[:3]:  # Max 3 proposals per cycle
            # Simulate proposed metrics. A None result (no simulator, an
            # unsupported change class, or a simulator error) DEFERS the
            # proposal honestly — we never substitute the baseline metrics
            # as "proposed", which fabricated a fake 0% rejection.
            proposed_metrics: Optional[Dict[str, Any]] = None
            sim_note = ""
            if simulate_proposed_metrics is not None:
                try:
                    proposed_metrics = simulate_proposed_metrics(proposal.parameter_changes)
                except Exception as e:
                    proposed_metrics = None
                    sim_note = f"simulator error: {str(e)[:120]}"
            elif self.simulator.n > 0:
                try:
                    proposed_metrics = self.simulator.simulate(proposal)
                except Exception as e:
                    proposed_metrics = None
                    sim_note = f"simulator error: {str(e)[:120]}"
                if proposed_metrics is None and not sim_note:
                    sim_note = "Simulator cannot evaluate this proposal category"
            if proposed_metrics is None:
                proposal.status = "DEFERRED"
                proposal.rejection_reason = sim_note or (
                    "No proposed-metrics simulator wired for this change type"
                )
                evaluated.append(proposal)
                self.memory.record_proposal(proposal)
                continue

            # Validate in sealed sandbox
            proposal = self.validator.validate_proposal(proposal, baseline, proposed_metrics)

            # Overfit check: if proposal is validated, verify proposed metrics
            # don't indicate overfitting (e.g., Sharpe improvement with few trades)
            if proposal.status == "VALIDATED":
                proposal = self._check_overfit(proposal, baseline, proposed_metrics)

            if proposal.status == "VALIDATED":
                proposal.status = "APPLIED"
                proposal.applied_at = time.time()
                applied.append(proposal)
                self.state.improvement_count += 1
                self.state.improvement_history.append(proposal.to_dict())
                if len(self.state.improvement_history) > self.max_history:
                    self.state.improvement_history = self.state.improvement_history[-self.max_history:]
                logger.info(
                    "APPLIED: %s | improvement=%.1f%%",
                    proposal.category, proposal.improvement_pct,
                )
            else:
                logger.info(
                    "REJECTED: %s | %s",
                    proposal.category, proposal.rejection_reason,
                )

            evaluated.append(proposal)
            self.memory.record_proposal(proposal)

        self._save_state()
        return evaluated

    def _check_overfit(
        self,
        proposal: ImprovementProposal,
        baseline: BaselineSnapshot,
        proposed_metrics: Dict[str, Any],
    ) -> ImprovementProposal:
        """Reject validated proposals if the proposed metrics indicate overfitting.

        Checks:
        1. If proposed Sharpe is very high but total_trades is low → likely overfit
        2. If proposed win_rate improvement is >20pp from baseline → suspicious
        3. If CPCV PBO is high → backtest overfit probability is elevated
        """
        base_sharpe = baseline.sharpe_rolling
        prop_sharpe = proposed_metrics.get("sharpe_rolling", base_sharpe)
        prop_trades = proposed_metrics.get("total_trades", baseline.total_trades)
        prop_wr = proposed_metrics.get("win_rate", baseline.win_rate)
        pbo = proposed_metrics.get("cpcv_pbo", baseline.cpcv_pbo)
        n_strategies = max(1, len(self.state.improvement_history) + 1)

        # Check 1: High Sharpe with too few trades
        if prop_sharpe > 1.5 and prop_trades < 30:
            proposal.status = "REJECTED"
            proposal.rejection_reason = (
                f"Overfit risk: Sharpe {prop_sharpe:.2f} with only {prop_trades} trades "
                f"(need >= 30 for statistical significance)"
            )
            logger.warning("OVERFIT REJECT: %s — %s", proposal.category, proposal.rejection_reason)
            return proposal

        # Check 2: Suspicious win rate improvement
        wr_improvement = prop_wr - baseline.win_rate
        if wr_improvement > 0.20 and baseline.win_rate > 0:
            proposal.status = "REJECTED"
            proposal.rejection_reason = (
                f"Overfit risk: win rate jump {wr_improvement:+.1%} "
                f"({baseline.win_rate:.1%} → {prop_wr:.1%}) is suspiciously large"
            )
            logger.warning("OVERFIT REJECT: %s — %s", proposal.category, proposal.rejection_reason)
            return proposal

        # Check 3: CPCV PBO too high
        if pbo > 0.5:
            proposal.status = "REJECTED"
            proposal.rejection_reason = (
                f"Overfit risk: CPCV PBO={pbo:.3f} > 0.5 — "
                f"backtest overfit probability is elevated"
            )
            logger.warning("OVERFIT REJECT: %s — %s", proposal.category, proposal.rejection_reason)
            return proposal

        # Check 4: Deflated Sharpe test via overfit detector
        try:
            from core.overfit_detector import deflated_sharpe_test
            dsr = deflated_sharpe_test(
                estimated_sharpe=prop_sharpe,
                n_trials=n_strategies,
                sample_size=max(1, prop_trades),
            )
            if dsr["rating"] == "LIKELY_OVERFIT":
                proposal.status = "REJECTED"
                proposal.rejection_reason = (
                    f"Overfit risk: deflated Sharpe rating={dsr['rating']} "
                    f"(p={dsr['p_value']:.3f}) — likely overfit after {n_strategies} trials"
                )
                logger.warning("OVERFIT REJECT: %s — %s", proposal.category, proposal.rejection_reason)
                return proposal
        except Exception:
            pass  # If deflated Sharpe fails, don't block the proposal

        return proposal

    def _fallback_proposals(self, baseline: BaselineSnapshot) -> List[ImprovementProposal]:
        """Pre-defined fallback proposals when LLM is unavailable.

        Uses the empirical simulator to propose realistic parameter changes
        based on actual trade history.
        """
        proposals = []

        # If win rate is below 55%, suggest tightening entry
        if baseline.win_rate < 0.55 and baseline.win_rate > 0:
            proposals.append(ImprovementProposal(
                proposal_id=f"fallback_{self.state.cycle_count}_1",
                category="strategy",
                description="Tighten entry confidence threshold due to low win rate",
                parameter_changes={"entry_confidence_threshold": {"current": "0.50", "proposed": "0.60"}},
                rationale=f"Win rate {baseline.win_rate:.1%} is below 55% — tighten entry criteria",
                expected_impact="Higher win rate, fewer but higher-quality trades",
            ))

        # If drawdown is elevated (negative value), suggest reducing risk
        # Note: current_drawdown is stored as a negative number (e.g., -0.05 for 5% drawdown)
        if baseline.current_drawdown < -0.05:
            proposals.append(ImprovementProposal(
                proposal_id=f"fallback_{self.state.cycle_count}_2",
                category="risk_params",
                description="Reduce risk per trade during elevated drawdown",
                parameter_changes={"risk_per_trade": {"current": "0.01", "proposed": "0.005"}},
                rationale=f"Drawdown {baseline.current_drawdown:.1%} exceeds 5% — reduce exposure",
                expected_impact="Slower equity decline, preservation of capital",
            ))

        # If Sharpe is below 2.0, suggest reducing position scale
        if baseline.sharpe_rolling < 2.0 and baseline.sharpe_rolling > 0:
            proposals.append(ImprovementProposal(
                proposal_id=f"fallback_{self.state.cycle_count}_3",
                category="strategy",
                description="Reduce position scale when Sharpe is suboptimal",
                parameter_changes={"position_scale": {"current": "1.0", "proposed": "0.75"}},
                rationale=f"Rolling Sharpe {baseline.sharpe_rolling:.2f} is below 2.0 — reduce exposure",
                expected_impact="Lower risk, wait for better opportunities",
            ))

        # Always propose a risk reduction if simulator has data (empirical improvement)
        if not proposals and self.simulator.n > 0:
            proposals.append(ImprovementProposal(
                proposal_id=f"fallback_{self.state.cycle_count}_empirical",
                category="risk_params",
                description="Empirically optimize risk based on trade history",
                parameter_changes={"risk_per_trade": {"current": "0.01", "proposed": "0.008"}},
                rationale=f"Empirical optimization based on {self.simulator.n} historical trades",
                expected_impact="Slightly reduced risk to improve risk-adjusted returns",
            ))

        return proposals

    def get_status(self) -> Dict[str, Any]:
        """Return full engine status."""
        return {
            "self_improvement": self.state.to_dict(),
            "memory": self.memory.memory.to_dict(),
            "simulator_trades": self.simulator.n,
        }
