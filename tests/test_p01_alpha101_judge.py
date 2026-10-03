"""P01 (Vu et al. 2026) - 101 KB Psi + deterministic Judge rubric. Unit tests."""
import pandas as pd
import pytest

from research.alpha_miner import (
    PAPER_DEFAULTS,
    build_knowledge_base,
    judge_candidate,
)


@pytest.fixture(scope="module")
def kb():
    return build_knowledge_base()


def test_paper_defaults_match_appendix_a4():
    p = PAPER_DEFAULTS
    assert (p.k_inner, p.k_outer) == (5, 5)
    assert p.judge_threshold == 7.5
    assert p.tau_ic == 0.01
    assert p.signal_clip == (-5.0, 5.0)
    assert p.forward_horizon == 5
    assert p.vol_ratio_min == 1.0
    assert p.retrieval_top_k == 1


def test_kb_loads_registry_records(kb):
    assert len(kb) >= 20  # core.alpha_factors registry size
    rec = kb.get("ALPHA012") or kb.get("ALPHA009")
    assert rec is not None
    assert rec.code and rec.operators and rec.description
    assert rec.inputs <= {"open", "high", "low", "close", "volume", "returns",
                          "tick_volume", "df"}


def test_kb_retrieve_top1_momentum(kb):
    hits = kb.retrieve("high momentum stocks with low volatility breakout", top_k=1)
    assert len(hits) == 1
    rec, score = hits[0]
    assert score > 0.0
    assert rec.alpha_id.startswith("ALPHA")


def test_kb_retrieve_respects_top_k(kb):
    assert len(kb.retrieve("volume spike reversal", top_k=3)) == 3


def test_judge_accepts_clean_candidate():
    expr = ("close.pct_change(20) * log1p(volume / volume.rolling(20).mean())"
            ".fillna(0).clip(-5, 5)")
    s = judge_candidate(expr)
    assert s.lookahead == 10.0
    assert s.accepted, s.to_dict()


def test_judge_rejects_lookahead():
    s = judge_candidate("close.shift(-1) - close")
    assert s.lookahead == 0.0
    assert not s.accepted
    assert any("future" in f or "shift" in f for f in s.feedback)


def test_judge_rejects_constant_signal():
    s = judge_candidate("close * 0 + 1.0", signal=pd.Series([1.0] * 50))
    assert s.robustness == 0.0
    assert not s.accepted


def test_judge_rejects_syntax_error():
    s = judge_candidate("close.(((")
    assert s.logic <= 1.0
    assert not s.accepted


def test_judge_rejects_python_loop():
    s = judge_candidate("sum(close[i] for i in range(10))")
    assert s.logic < 8.0
    assert s.feedback  # GeneratorExp flagged as disallowed construct


def test_pipeline_judge_filter_wiring():
    from research.alpha_miner.pipeline import AlphaMiningPipeline, AlphaMiningConfig
    from research.alpha_miner.expression_tree import AlphaExpression

    cfg = AlphaMiningConfig(use_knowledge_base=True, use_judge_rubric=True, judge_threshold=7.5)
    pipeline = AlphaMiningPipeline(config=cfg)
    assert pipeline.kb is not None
    assert len(pipeline.kb) >= 20

    # Test _judge_filter_alphas
    clean_expr = AlphaExpression(operator="leaf", field="close")
    bad_expr = AlphaExpression(operator="leaf", field="invalid_syntax(((")

    ohlcv = pd.DataFrame({
        "open": [10.0] * 50,
        "high": [11.0] * 50,
        "low": [9.0] * 50,
        "close": [10.5 + 0.1 * i for i in range(50)],
        "volume": [1000] * 50,
    })

    filtered = pipeline._judge_filter_alphas([(clean_expr, 0.05), (bad_expr, 0.08)], ohlcv, verbose=False)
    assert len(filtered) == 1
    assert filtered[0][0] == clean_expr


def test_auto_promotion_judge_gate():
    from research.auto_promotion import AutoPromoter

    promoter = AutoPromoter()
    # Strategy with valid formula
    res_clean = promoter._check_judge_rubric("alpha_test_1", {"metrics": {"formula": "close.pct_change(5).clip(-3, 3)"}})
    assert res_clean["passed"] is True
    assert res_clean["score"] >= 7.5

    # Strategy with lookahead formula
    res_lookahead = promoter._check_judge_rubric("alpha_test_2", {"metrics": {"formula": "close.shift(-1) - close"}})
    assert res_lookahead["passed"] is False

    # Strategy without a formula must FAIL CLOSED. "Not applicable" is not
    # the same as "passed": returning True here let an ungraded strategy
    # satisfy the promotion gate, so an absent formula is now a refusal.
    res_none = promoter._check_judge_rubric("alpha_test_3", {"metrics": {}})
    assert res_none["passed"] is False
    assert res_none["reason"] == "not_applicable"

