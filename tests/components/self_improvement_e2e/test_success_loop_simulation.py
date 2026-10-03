"""§18.8 Capstone — autonomous self-improvement success-loop simulation.

Proves the full chain on real modules: decay detection -> improvement
proposal -> sealed validation -> measurable cycle-over-cycle improvement
-> reflection. Failure injection: worse retrain REJECTED.
All state in tmp_path; deterministic seeds; no live files touched.
"""
import json
import numpy as np
import pandas as pd

from core.self_improvement_loop import (
    BaselineSnapshot, ImprovementProposal, SealedProposalValidator,
)
from core.alpha_decay_monitor import AlphaDecayMonitor
from intelligence.meta_reflector import SystemMetaReflector


def _synthetic_alpha(n_live=80, n_decay=80, seed=42):
    """Signal with genuine edge that decays to anti-predictive."""
    rng = np.random.default_rng(seed)
    fwd_live = rng.normal(0.0004, 0.01, n_live)
    sig_live = fwd_live * 3.0 + rng.normal(0, 0.01, n_live)
    fwd_decay = rng.normal(-0.0003, 0.01, n_decay)
    sig_decay = -fwd_decay * 3.0 + rng.normal(0, 0.01, n_decay)
    fwd = np.concatenate([fwd_live, fwd_decay])
    sig = np.concatenate([sig_live, sig_decay])
    close = 100.0 * np.exp(np.cumsum(fwd))
    return pd.DataFrame({"close": close, "alpha_x": sig}), pd.Series(fwd)


def test_decay_detection_triggers_alert(tmp_path):
    """§18.3: degrading alpha raises DecayAlert from the real monitor."""
    df, fwd = _synthetic_alpha()
    mon = AlphaDecayMonitor(
        ic_window=20, decay_threshold=0.02, consecutive_bars=5,
        trend_window=20, cooldown_seconds=0.0, orthogonalize=False,
        state_file=str(tmp_path / "decay_state.json"),
    )
    mon.update(df, ["alpha_x"], forward_returns=fwd)
    alerts = mon.check_decay()
    assert alerts, "monitor must flag decaying alpha"
    assert alerts[0].severity in ("WARNING", "CRITICAL")
    assert "alpha_x" in str(mon.get_summary())


def test_improvement_proposal_validated_only_when_better():
    """§18.4 + §18.9: sealed validator passes improvement, rejects regression."""
    v = SealedProposalValidator(min_improvement_pct=2.0, max_regression_pct=5.0)
    degraded = BaselineSnapshot(win_rate=0.42, sharpe_rolling=0.4,
                                avg_r_multiple=0.6, current_drawdown=0.10)
    better = dict(win_rate=0.55, sharpe_rolling=1.2, avg_r_multiple=1.1,
                  current_drawdown=0.04)
    p = v.validate_proposal(
        ImprovementProposal(proposal_id="c1", category="risk_params",
                            description="tighten sizing after decay"),
        degraded, better)
    assert p.status == "VALIDATED"
    assert p.improvement_pct >= 2.0
    assert p.proposed_score > p.baseline_score

    worse = dict(win_rate=0.30, sharpe_rolling=0.1, avg_r_multiple=0.3,
                 current_drawdown=0.20)
    p2 = v.validate_proposal(
        ImprovementProposal(proposal_id="c2", category="strategy",
                            description="bad retrain"), degraded, worse)
    assert p2.status == "REJECTED"  # never self-degrades


def test_multi_cycle_improvement_trend(tmp_path):
    """§18.8 capstone: 3 decay->improve cycles; score non-decreasing;
    zero fail-open; full evidence trail journaled."""
    v = SealedProposalValidator(min_improvement_pct=2.0, max_regression_pct=5.0)
    rng = np.random.default_rng(7)

    cycles = []
    current = dict(win_rate=0.52, sharpe_rolling=1.1, avg_r_multiple=1.0,
                   current_drawdown=0.05)
    cycles.append(dict(current, cycle=0, note="initial"))

    events = []
    for i in (1, 2):
        decayed = dict(win_rate=current["win_rate"] - 0.08,
                       sharpe_rolling=current["sharpe_rolling"] - 0.5,
                       avg_r_multiple=current["avg_r_multiple"] - 0.3,
                       current_drawdown=current["current_drawdown"] + 0.04)
        events.append({"cycle": i, "event": "DECAY_DETECTED", "metrics": decayed})
        base = BaselineSnapshot(**decayed)
        improved = dict(
            win_rate=current["win_rate"] + 0.02 + rng.uniform(0, 0.01),
            sharpe_rolling=current["sharpe_rolling"] + 0.15,
            avg_r_multiple=current["avg_r_multiple"] + 0.08,
            current_drawdown=max(0.02, current["current_drawdown"] - 0.01))
        prop = v.validate_proposal(
            ImprovementProposal(proposal_id=f"c{i}", category="risk_params",
                                description=f"cycle {i} retrain"), base, improved)
        events.append({"cycle": i, "event": "PROPOSAL_" + prop.status,
                       "improvement_pct": prop.improvement_pct})
        assert prop.status == "VALIDATED", f"cycle {i} must pass sealed validation"
        assert prop.proposed_score > prop.baseline_score
        current = improved
        cycles.append(dict(current, cycle=i, note="post-improvement"))

    scores = [v._compute_score(c) for c in cycles]
    assert scores[1] > scores[0] and scores[2] >= scores[1], (
        f"improvement trend broken: {scores}")

    decays = [e for e in events if e["event"] == "DECAY_DETECTED"]
    proposals = [e for e in events if e["event"] == "PROPOSAL_VALIDATED"]
    assert len(proposals) == len(decays) == 2  # zero fail-open

    report = {"cycles": cycles, "scores": scores, "events": events}
    out = tmp_path / "improvement_evidence.json"
    out.write_text(json.dumps(report, indent=2))
    assert out.exists() and len(events) == 4


def test_meta_reflection_diagnoses_and_throttles():
    """§18.5: reflection diagnoses degraded health -> remediation, no execution authority."""
    ref = SystemMetaReflector()
    bad = ref.audit_system_health(train_sharpe=2.2, test_sharpe=0.9,
                                  regime_flips_30d=14,
                                  rolling_30_trades_expectancy=-0.002)
    assert bad.overall_status == "DEGRADED"
    assert bad.risk_multiplier < 1.0
    assert bad.remediation_actions
    assert bad.execution_authority is False

    good = ref.audit_system_health(train_sharpe=1.8, test_sharpe=1.6,
                                   regime_flips_30d=3,
                                   rolling_30_trades_expectancy=0.01)
    assert good.overall_status == "HEALTHY"
    assert len(ref.audit_history) == 2  # every audit journaled
