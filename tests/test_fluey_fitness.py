"""FLUEY pattern fitness test — unit tests, including the governance rules.

Contract references (docs/PROJECT_SUCCESS_CONTRACT.md):
  element 8  — a missing dependency reads as refusal
  element 11 — every pattern tested is a trial
  element 12 — the split is purged and embargoed
  element 13 — cost before promise
"""
import pandas as pd

from core.trial_ledger import TrialLedger
from research.fluey_fitness import (
    FitnessConfig,
    FlueyFitnessGate,
    PatternFitness,
    Sample,
    atr_series,
    collect_samples,
    direction_for,
    evaluate,
    evaluate_patterns,
    record_pattern_trials,
    split_samples,
)

# A stated (here: zero) cost assumption, so verdicts can be produced.
CFG = FitnessConfig(cost_r=0.0)


def _samples(pattern, is_r, oos_r, is_start=0, oos_start=1000):
    rows = [Sample(index=is_start + i, pattern=pattern, direction=1, r=r) for i, r in enumerate(is_r)]
    rows += [Sample(index=oos_start + i, pattern=pattern, direction=1, r=r) for i, r in enumerate(oos_r)]
    return rows


def test_direction_for_maps_biases():
    assert direction_for("bullish_reversal") == 1
    assert direction_for("bullish_continuation") == 1
    assert direction_for("bearish_reversal") == -1
    assert direction_for("indecision") == 0


def test_atr_series_uses_true_range():
    bars = [(10.0, 11.0, 9.0, 10.0), (10.0, 12.0, 9.5, 11.5)]
    # bar 1 true range: max(12-9.5, |12-10|, |9.5-10|) = 2.5
    single = atr_series(bars, period=1)
    assert single[0] == 2.0
    assert single[1] == 2.5
    # a two-bar ATR is the mean of the two true ranges
    assert atr_series(bars, period=2)[1] == 2.25


def test_collect_samples_maps_direction_and_r_units():
    bars = [
        (10.0, 11.0, 9.0, 10.0),
        (10.0, 12.0, 9.5, 11.5),
        (11.5, 13.0, 11.0, 12.5),
        (12.5, 14.0, 12.0, 13.5),
    ]
    atr = [1.0, 1.0, 1.0, 1.0]
    labels = [("bullish_engulfing", "bullish_reversal", 0)]
    samples = collect_samples(bars, labels, atr, horizon=2)
    assert len(samples) == 1
    assert samples[0].direction == 1
    # (close[2] - close[0]) / atr = (12.5 - 10.0) / 1.0
    assert samples[0].r == 2.5


def test_collect_samples_skips_indecision_and_short_series():
    bars = [(10.0, 11.0, 9.0, 10.0)] * 3
    atr = [1.0, 1.0, 1.0]
    assert collect_samples(bars, [("tri_star", "indecision", 0)], atr, horizon=1) == []
    assert collect_samples(bars, [("hammer", "bullish_reversal", 2)], atr, horizon=1) == []


def test_evaluate_approves_consistent_positive_edge():
    rows = _samples("hammer", [1.0, 1.4] * 10, [1.0, 1.4] * 10)
    result = evaluate(rows, split_index=500, cfg=CFG)["hammer"]
    assert result.approved
    assert result.oos_mean_r > 0
    assert result.oos_t >= 2.0
    assert result.n == 40


def test_evaluate_rejects_negative_edge():
    rows = _samples("hammer", [1.0, 1.4] * 10, [-1.0, -1.4] * 10)
    result = evaluate(rows, split_index=500, cfg=CFG)["hammer"]
    assert not result.approved
    assert "no positive out-of-sample edge" in result.reason


def test_evaluate_rejects_sign_flip_between_blocks():
    rows = _samples("hammer", [1.0, 1.4] * 10, [-1.0, -1.4] * 10)
    result = evaluate(rows, split_index=500, cfg=CFG)["hammer"]
    assert "in-sample and out-of-sample edges disagree" in result.reason


def test_evaluate_rejects_too_few_out_of_sample_signals():
    rows = _samples("hammer", [1.0, 1.4] * 10, [1.0, 1.4])
    result = evaluate(rows, split_index=500, cfg=CFG)["hammer"]
    assert not result.approved
    assert "out-of-sample signals" in result.reason


def test_evaluate_rejects_t_statistic_below_threshold():
    # Positive edge, but too noisy for the t-statistic to clear 2.0
    oos = [1.0] * 15 + [-2.0] * 5
    rows = _samples("hammer", [1.0] * 20, oos)
    result = evaluate(rows, split_index=500, cfg=CFG)["hammer"]
    assert result.oos_mean_r > 0
    assert result.oos_t < 2.0
    assert not result.approved
    assert "below" in result.reason


# --- Governance -----------------------------------------------------------

def test_no_cost_assumption_refuses_every_verdict():
    rows = _samples("hammer", [1.0, 1.4] * 10, [1.0, 1.4] * 10)
    result = evaluate(rows, split_index=500)["hammer"]  # default config: no cost
    assert not result.approved
    assert "no cost assumption" in result.reason
    # No pre-cost edge may escape the module.
    assert result.oos_mean_r == 0.0
    assert result.oos_t == 0.0


def test_cost_is_subtracted_from_the_reported_edge():
    rows = _samples("hammer", [1.0, 1.4] * 10, [1.0, 1.4] * 10)
    gross = evaluate(rows, split_index=500, cfg=CFG)["hammer"]
    net = evaluate(rows, split_index=500, cfg=FitnessConfig(cost_r=0.25))["hammer"]
    assert round(net.oos_mean_r, 6) == round(gross.oos_mean_r - 0.25, 6)
    assert net.cost_r == 0.25


def test_split_purges_in_sample_signals_that_reach_into_the_out_of_sample_block():
    # horizon 5: a signal at index 495 has its forward window inside the
    # out-of-sample block, so it must be purged from the in-sample half.
    rows = [
        Sample(index=490, pattern="hammer", direction=1, r=1.0),
        Sample(index=495, pattern="hammer", direction=1, r=1.0),
        Sample(index=1000, pattern="hammer", direction=1, r=1.0),
    ]
    in_rows, out_rows = split_samples(rows, split_index=500, cfg=CFG)
    assert [s.index for s in in_rows] == [490]
    assert [s.index for s in out_rows] == [1000]


def test_split_embargoes_out_of_sample_signals_formed_from_in_sample_bars():
    # pattern_lookback 5: a signal at index 502 was formed from bars
    # 498-502, which are in the in-sample block.
    rows = [
        Sample(index=502, pattern="hammer", direction=1, r=1.0),
        Sample(index=505, pattern="hammer", direction=1, r=1.0),
    ]
    in_rows, out_rows = split_samples(rows, split_index=500, cfg=CFG)
    assert in_rows == []
    assert [s.index for s in out_rows] == [505]


def _fitness(pattern, approved):
    return PatternFitness(
        pattern=pattern,
        n=40,
        is_n=20,
        oos_n=20,
        is_mean_r=1.0,
        oos_mean_r=1.0,
        oos_std_r=0.4,
        oos_t=5.0,
        hit_rate=0.6,
        approved=approved,
        reason="",
        cost_r=0.0,
    )


def test_gate_refuses_when_hypotheses_were_not_counted():
    gate = FlueyFitnessGate({"hammer": _fitness("hammer", True)})
    assert not gate.is_approved("hammer")
    assert gate.approved() == []
    assert "trial ledger" in gate.blocked_reason()


def test_gate_refuses_when_only_some_hypotheses_were_counted():
    gate = FlueyFitnessGate(
        {"hammer": _fitness("hammer", True), "doji": _fitness("doji", True)},
        trials_recorded=1,
    )
    assert gate.approved() == []
    assert "1 of 2" in gate.blocked_reason()


def test_gate_separates_approved_from_rejected():
    gate = FlueyFitnessGate(
        {"hammer": _fitness("hammer", True), "doji_cross": _fitness("doji_cross", False)},
        trials_recorded=2,
    )
    assert gate.is_approved("hammer")
    assert not gate.is_approved("doji_cross")
    assert gate.approved() == ["hammer"]
    assert gate.rejected() == ["doji_cross"]
    assert "hammer" in gate.to_dict()["patterns"]


def test_record_pattern_trials_counts_every_hypothesis(tmp_path):
    ledger = TrialLedger(db_path=str(tmp_path / "ledger.db"))
    results = {
        "hammer": _fitness("hammer", True),
        "doji": _fitness("doji", False),
    }
    before = ledger.n_trials
    recorded = record_pattern_trials(ledger, results)
    assert recorded == 2
    assert ledger.n_trials == before + 2
    chain = ledger.verify_chain()
    assert chain["valid"]
    assert chain["n_records"] == before + 2


def test_evaluate_patterns_end_to_end_on_synthetic_ohlcv():
    neutral = [(100.0, 101.0, 99.0, 100.5)] * 20
    engulfing = [(100.0, 101.0, 99.0, 99.5), (99.4, 102.0, 99.2, 101.5)]
    tail = [(100.0, 101.0, 99.0, 100.5)] * 10
    rows = neutral + engulfing + tail
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    results = evaluate_patterns(frame, FitnessConfig(horizon=5, min_trades=6, cost_r=0.0))
    assert "bullish_engulfing" in results
    fitness = results["bullish_engulfing"]
    assert fitness.n >= 1
    # A single signal can never be approved.
    assert not fitness.approved
    assert "out-of-sample signals" in fitness.reason
