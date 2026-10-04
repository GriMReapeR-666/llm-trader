"""Account growth path, projection, and ruin bounds (research / risk advice only).

import logging

logger = logging.getLogger(__name__)
All outputs are labelled simulated or descriptive.  Nothing in this module can
grant execution authority or mutate live risk settings by itself — callers may
feed ``suggested_risk_per_trade`` into PortfolioRiskArbiter configuration only
after an operator review.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def equity_curve(pnls: Sequence[float], *, starting_equity: float = 0.0) -> Dict[str, Any]:
    """Build a cumulative equity path and path drawdown from closed trade PnLs."""
    if not _finite(starting_equity):
        raise ValueError("starting_equity must be finite")
    path: List[float] = [float(starting_equity)]
    peak = float(starting_equity)
    max_dd = 0.0
    max_dd_pct = 0.0
    for raw in pnls:
        if not _finite(raw):
            raise ValueError("pnl path contains a non-finite value")
        equity = path[-1] + float(raw)
        path.append(equity)
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, drawdown / peak)
    terminal = path[-1]
    net = terminal - float(starting_equity)
    return {
        "starting_equity": float(starting_equity),
        "terminal_equity": terminal,
        "net_pnl": net,
        "points": len(path),
        "equity_path": path,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "execution_authority": False,
        "kind": "descriptive",
    }


def rolling_expectancy(pnls: Sequence[float], window: int = 20) -> Dict[str, Any]:
    """Rolling mean PnL; used to detect degradation for risk throttling."""
    if not isinstance(window, int) or isinstance(window, bool) or window < 2:
        raise ValueError("window must be an integer >= 2")
    values = [float(v) for v in pnls]
    if any(not math.isfinite(v) for v in values):
        raise ValueError("pnl path contains a non-finite value")
    if len(values) < window:
        return {
            "status": "INSUFFICIENT_DATA",
            "window": window,
            "sample_size": len(values),
            "current": None,
            "prior": None,
            "degradation": None,
            "execution_authority": False,
        }
    current = sum(values[-window:]) / window
    prior_slice = values[-2 * window:-window] if len(values) >= 2 * window else values[:-window]
    prior = sum(prior_slice) / len(prior_slice) if prior_slice else None
    degradation = None if prior in (None, 0) else (prior - current) / abs(prior)
    return {
        "status": "OK",
        "window": window,
        "sample_size": len(values),
        "current": current,
        "prior": prior,
        "degradation": degradation,
        "execution_authority": False,
    }


def suggest_risk_per_trade(
    *,
    base_risk: float,
    fractional_kelly: float,
    expectancy_r: Optional[float],
    degradation: Optional[float],
    max_risk: float = 0.02,
    min_risk: float = 0.001,
    degrade_threshold: float = 0.25,
) -> Dict[str, Any]:
    """Throttle risk when edge degrades; never exceeds max_risk; authority locked."""
    if not all(_finite(v) for v in (base_risk, fractional_kelly, max_risk, min_risk, degrade_threshold)):
        raise ValueError("risk inputs must be finite")
    if not 0 < min_risk <= max_risk <= 1:
        raise ValueError("risk bounds invalid")
    kelly_cap = max(0.0, float(fractional_kelly))
    suggested = min(float(base_risk), kelly_cap if kelly_cap > 0 else float(base_risk), float(max_risk))
    reasons: List[str] = []
    if expectancy_r is None or not _finite(expectancy_r):
        # Unknown is not a confident neutral. This module's own rule (see
        # rl_trainer._numeric_bin) is that a missing observation must not be
        # coerced into a plausible value, because doing so turns "we have no
        # evidence" into "the evidence is fine". With no broker-evidenced
        # trades, expectancy_r is None, and holding risk at the operator base
        # would assert an unmeasured edge. Throttle to the floor with a
        # distinct, auditable reason. This only ever REDUCES risk.
        suggested = float(min_risk)
        reasons.append("expectancy_unknown_no_measured_evidence")
    elif float(expectancy_r) <= 0:
        suggested = float(min_risk)
        reasons.append("non_positive_expectancy_r")
    if degradation is not None and _finite(degradation) and degradation >= degrade_threshold:
        suggested = min(suggested, float(base_risk) * 0.5, float(max_risk))
        reasons.append("rolling_expectancy_degraded")
    if kelly_cap <= 0:
        reasons.append("kelly_ineligible_or_zero")
    suggested = max(float(min_risk), min(float(max_risk), suggested))
    return {
        "suggested_risk_per_trade": suggested,
        "base_risk": float(base_risk),
        "fractional_kelly": kelly_cap,
        "reasons": reasons,
        "execution_authority": False,
        "kind": "advisory",
    }


def project_growth(
    *,
    starting_equity: float,
    trades: int,
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float,
    risk_fraction: float,
    kill_drawdown_fraction: float = 0.20,
    seed: int = 0,
    paths: int = 500,
) -> Dict[str, Any]:
    """Simulate equity paths under fixed R distribution and a hard DD kill switch.

    Loss magnitude uses ``avg_loss_r`` as a positive R distance.  Each trade
    risks ``risk_fraction * equity``.  If drawdown from peak reaches
    ``kill_drawdown_fraction``, the path halts (ruin/kill).
    """
    if trades < 1 or paths < 1:
        raise ValueError("trades and paths must be >= 1")
    if not all(_finite(v) for v in (
        starting_equity, win_rate, avg_win_r, avg_loss_r, risk_fraction, kill_drawdown_fraction,
    )):
        raise ValueError("projection inputs must be finite")
    if starting_equity <= 0 or not 0 <= win_rate <= 1:
        raise ValueError("starting_equity/win_rate invalid")
    if avg_win_r <= 0 or avg_loss_r <= 0:
        raise ValueError("avg_win_r and avg_loss_r must be positive R distances")
    if not 0 < risk_fraction <= 1 or not 0 < kill_drawdown_fraction <= 1:
        raise ValueError("risk_fraction/kill_drawdown_fraction invalid")

    rng = random.Random(int(seed))
    terminals: List[float] = []
    ruined = 0
    max_dds: List[float] = []

    for _ in range(int(paths)):
        equity = float(starting_equity)
        peak = equity
        killed = False
        path_max_dd = 0.0
        for _trade in range(int(trades)):
            risk_amount = equity * float(risk_fraction)
            if rng.random() < win_rate:
                equity += risk_amount * float(avg_win_r)
            else:
                equity -= risk_amount * float(avg_loss_r)
            if equity <= 0:
                equity = 0.0
                killed = True
                break
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            path_max_dd = max(path_max_dd, dd)
            if dd >= kill_drawdown_fraction:
                killed = True
                break
        if killed:
            ruined += 1
        terminals.append(equity)
        max_dds.append(path_max_dd)

    terminals_sorted = sorted(terminals)
    def _pct(p: float) -> float:
        if not terminals_sorted:
            return 0.0
        idx = min(len(terminals_sorted) - 1, max(0, int(round(p * (len(terminals_sorted) - 1)))))
        return terminals_sorted[idx]

    mean_terminal = sum(terminals) / len(terminals)
    expected_r = win_rate * avg_win_r - (1.0 - win_rate) * avg_loss_r
    # Compound approximation under constant fractional risk (no path variance).
    approx_growth = (1.0 + risk_fraction * expected_r) ** trades if expected_r > -1.0 / max(risk_fraction, 1e-12) else 0.0

    return {
        "kind": "simulated",
        "starting_equity": float(starting_equity),
        "trades": int(trades),
        "paths": int(paths),
        "seed": int(seed),
        "win_rate": float(win_rate),
        "avg_win_r": float(avg_win_r),
        "avg_loss_r": float(avg_loss_r),
        "risk_fraction": float(risk_fraction),
        "kill_drawdown_fraction": float(kill_drawdown_fraction),
        "expectancy_r": expected_r,
        "approx_terminal_multiplier": approx_growth,
        "approx_terminal_equity": float(starting_equity) * approx_growth,
        "mean_terminal_equity": mean_terminal,
        "p05_terminal_equity": _pct(0.05),
        "p50_terminal_equity": _pct(0.50),
        "p95_terminal_equity": _pct(0.95),
        "ruin_or_kill_rate": ruined / float(paths),
        "mean_path_max_drawdown_pct": sum(max_dds) / len(max_dds),
        "execution_authority": False,
        "limitation": (
            "Projection is advisory simulation under i.i.d. R assumptions; "
            "not a live sizing permission."
        ),
    }


def growth_report_from_r_multiples(
    r_multiples: Sequence[float],
    *,
    starting_equity: float = 10_000.0,
    risk_fraction: float = 0.01,
    base_risk: float = 0.01,
    projection_trades: int = 100,
    seed: int = 0,
) -> Dict[str, Any]:
    """Combine descriptive equity path, Kelly-style throttle, and forward projection."""
    rs = [float(r) for r in r_multiples]
    if any(not math.isfinite(r) for r in rs):
        raise ValueError("r_multiples must be finite")

    # Currency path assuming constant fractional risk on a notional account.
    pnls = [starting_equity * risk_fraction * r for r in rs]
    # Recompute path with compounding risk on evolving equity for descriptive curve.
    compound_pnls: List[float] = []
    equity = float(starting_equity)
    for r in rs:
        pnl = equity * float(risk_fraction) * r
        compound_pnls.append(pnl)
        equity += pnl
        if equity <= 0:
            break
    curve = equity_curve(compound_pnls, starting_equity=starting_equity)
    roll = rolling_expectancy(compound_pnls, window=min(20, max(2, len(compound_pnls))))

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    win_rate = len(wins) / len(rs) if rs else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    expectancy_r = sum(rs) / len(rs) if rs else 0.0

    from research.adaptive_models import fractional_bayesian_kelly
    kelly = fractional_bayesian_kelly(len(wins), len(losses), avg_win or 0.0, avg_loss or 0.0)
    throttle = suggest_risk_per_trade(
        base_risk=base_risk,
        fractional_kelly=float(kelly.get("fractional_kelly") or 0.0),
        expectancy_r=expectancy_r,
        degradation=roll.get("degradation"),
    )

    projection = None
    if len(rs) >= 2 and avg_win > 0 and avg_loss > 0 and 0 < win_rate < 1:
        projection = project_growth(
            starting_equity=starting_equity,
            trades=int(projection_trades),
            win_rate=win_rate,
            avg_win_r=avg_win,
            avg_loss_r=avg_loss,
            risk_fraction=float(throttle["suggested_risk_per_trade"]),
            seed=seed,
        )

    return {
        "sample_size": len(rs),
        "expectancy_r": expectancy_r,
        "win_rate": win_rate,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "equity_curve": curve,
        "rolling_expectancy": roll,
        "kelly": kelly,
        "risk_throttle": throttle,
        "projection": projection,
        "execution_authority": False,
        "kind": "account_growth_report",
    }


def r_multiples_from_journal(path: str) -> List[float]:
    """Load leakage-safe R multiples from the authoritative journal dataset."""
    try:
        from research.research_validation import labelled_dataset
    except ImportError:
        from research.research_validation import labelled_dataset
    rows = labelled_dataset(path).get("rows", [])
    values: List[float] = []
    for row in rows:
        try:
            value = float(row["r_multiple"])
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


class AccountGrowthController:
    """Advisory growth controller that may only reduce configured risk.

    Persists an advisory snapshot on the trading store.  Callers must still
    pass through PortfolioRiskArbiter; this controller never sets
    execution_authority or raises risk above the operator-configured base.
    """

    STATE_KEY = "account_growth_advisory"

    # Risk floor used when no measured expectancy exists: a quarter of the
    # operator base, i.e. size down rather than assert an unmeasured edge. Kept
    # as a named constant so the advisory's reduction is attributable.
    UNKNOWN_RISK_RATIO = 0.25

    def __init__(self, store: Any, *, base_risk: float = 0.01):
        if not _finite(base_risk) or not 0 < float(base_risk) <= 0.01:
            raise ValueError("base_risk must be a finite fraction in (0, 0.01]")
        self.store = store
        self.base_risk = float(base_risk)
        self._last: Optional[Dict[str, Any]] = None

    def refresh(
        self,
        *,
        starting_equity: float = 10_000.0,
        projection_trades: int = 100,
        seed: int = 0,
    ) -> Dict[str, Any]:
        path = str(getattr(self.store, "path", "trading_state.db"))
        rs = r_multiples_from_journal(path)
        if len(rs) < 2:
            # No broker-evidenced trades. Route through the throttle so the
            # advisory reports a defensible (reduced) risk and an auditable
            # reason, rather than silently holding the operator base as if an
            # edge had been measured. Still advisory only, never above base.
            _throttle = suggest_risk_per_trade(
                base_risk=self.base_risk,
                fractional_kelly=0.0,
                expectancy_r=None,
                degradation=None,
                max_risk=self.base_risk,
                min_risk=min(self.base_risk,
                             self.base_risk * self.UNKNOWN_RISK_RATIO),
            )
            advisory = {
                "status": "INSUFFICIENT_DATA",
                "sample_size": len(rs),
                "suggested_risk_per_trade": min(self.base_risk,
                                                float(_throttle["suggested_risk_per_trade"])),
                "base_risk": self.base_risk,
                "reasons": _throttle.get("reasons", []),
                "execution_authority": False,
                "kind": "account_growth_advisory",
            }
        else:
            report = growth_report_from_r_multiples(
                rs,
                starting_equity=float(starting_equity),
                risk_fraction=self.base_risk,
                base_risk=self.base_risk,
                projection_trades=projection_trades,
                seed=seed,
            )
            throttle = report.get("risk_throttle") or {}
            suggested = float(throttle.get("suggested_risk_per_trade") or self.base_risk)
            # Hard invariant: never increase above operator-configured base.
            suggested = min(self.base_risk, suggested)
            roll = report.get("rolling_expectancy") or {}
            degradation = roll.get("degradation") if isinstance(roll, dict) else None
            throttle_applied = suggested + 1e-12 < self.base_risk
            try:
                from operations.reward_sustainability import throttle_agreement_reward
                throttle_reward = throttle_agreement_reward(
                    degradation=degradation,
                    throttle_applied=throttle_applied,
                )
            except Exception:
                throttle_reward = {
                    "status": "SKIPPED",
                    "value": 0.0,
                    "execution_authority": False,
                }
            advisory = {
                "status": "OK",
                "sample_size": report.get("sample_size", len(rs)),
                "expectancy_r": report.get("expectancy_r"),
                "suggested_risk_per_trade": suggested,
                "base_risk": self.base_risk,
                "risk_throttle": throttle,
                "projection": report.get("projection"),
                "rolling_expectancy": report.get("rolling_expectancy"),
                "throttle_agreement_reward": throttle_reward,
                "execution_authority": False,
                "kind": "account_growth_advisory",
            }
        try:
            self.store.set_state(self.STATE_KEY, advisory)
        except Exception as exc:
            logger.debug("report generation failed: %s", exc)
        self._last = advisory
        return advisory

    def _advisory(self) -> Dict[str, Any]:
        if self._last is not None:
            return self._last
        try:
            stored = self.store.get_state(self.STATE_KEY, {}) or {}
        except Exception:
            stored = {}
        if isinstance(stored, dict) and stored:
            self._last = stored
            return stored
        return {
            "status": "UNINITIALIZED",
            "suggested_risk_per_trade": self.base_risk,
            "base_risk": self.base_risk,
            "execution_authority": False,
        }

    def effective_risk_fraction(self, configured_risk: float) -> float:
        """Return min(configured, advisory); never raises risk; authority locked."""
        if not _finite(configured_risk) or configured_risk <= 0:
            return self.base_risk
        configured = float(configured_risk)
        advisory = self._advisory()
        try:
            suggested = float(advisory.get("suggested_risk_per_trade", configured))
        except (TypeError, ValueError, OverflowError):
            suggested = configured
        if not math.isfinite(suggested) or suggested <= 0:
            suggested = configured
        return min(configured, suggested, self.base_risk)

    def arbiter_risk_budget_scale(self) -> float:
        """Scale in (0, 1] for open-risk budgets when growth degrades."""
        advisory = self._advisory()
        base = max(self.base_risk, 1e-9)
        effective = self.effective_risk_fraction(self.base_risk)
        return max(0.25, min(1.0, effective / base))

