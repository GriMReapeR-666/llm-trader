"""P08 OpenQuant (Reid et al. 2026) - gates, sigmoid scoring, population norm."""
import math

import pytest

from research.openquant_eval import (
    TOTAL_WEIGHT,
    benchmark_relative,
    check_admissibility,
    component_scores,
    composite_score,
    data_quality_gate,
    data_quality_score,
    population_normalize,
    sigmoid,
)


def _good():
    return {"excess_return": 0.10, "jensen_alpha": 0.05, "information_ratio": 1.0,
            "up_down_capture": 1.2, "drawdown_vs_bench": 0.05, "sharpe": 1.5,
            "sortino": 2.0, "calmar": 1.5, "profit_factor": 2.0,
            "win_loss_ratio": 2.0, "expectancy": 0.01, "trade_consistency": 100,
            "max_drawdown": -0.20}


def test_total_weight_is_table_sum():
    # Paper text claims W=25 but Table 1 rows sum to 23.0; denom follows the table
    assert TOTAL_WEIGHT == pytest.approx(23.0)


def test_gates_all_five():
    assert check_admissibility(_good()).admissible
    for key, bad in [("jensen_alpha", -0.01), ("excess_return", -0.01),
                     ("sharpe", -0.5), ("profit_factor", 0.9),
                     ("max_drawdown", -0.60)]:
        m = _good(); m[key] = bad
        v = check_admissibility(m)
        assert not v.admissible and v.failed, key


def test_sigmoid_centred_at_mu():
    assert sigmoid(1.0, 1.0, 2.0) == pytest.approx(0.5)
    assert sigmoid(99.0, 1.0, 2.0) > 0.99
    assert sigmoid(-99.0, 1.0, 2.0) < 0.01


def test_composite_bounds_and_gate_zero():
    s = composite_score(_good())
    assert 0.0 <= s <= 100.0
    assert s > 50.0  # strong metrics beat the centred average
    m = _good(); m["sharpe"] = -1.0
    assert composite_score(m) == 0.0  # inadmissible -> excluded, not ranked


def test_population_norm_average_maps_half():
    pool = [_good(), _good(), _good()]
    normed = population_normalize(pool)
    assert len(normed) == 3
    for row in normed:
        for v in row.values():
            assert v == pytest.approx(0.5)


def test_population_norm_small_pool_passthrough():
    pool = [_good(), _good()]
    assert population_normalize(pool) == [component_scores(_good())] * 2


def test_data_quality_eq12():
    assert data_quality_score(0.0, 0.0) == 1.0
    assert data_quality_score(0.1, 0.2) == pytest.approx(0.8)
    ok, q = data_quality_gate(29, 0.0, 0.0)
    assert ok is False
    ok, q = data_quality_gate(30, 0.0, 0.0)
    assert ok is True and q == 1.0


def test_benchmark_relative_sanity():
    bench = [0.001 * ((i % 5) - 2) for i in range(100)]
    # persistent +5bp edge with small noise (constant edge => zero active vol)
    strat = [b + 0.0005 + (0.0001 if i % 2 else -0.0001) for i, b in enumerate(bench)]
    out = benchmark_relative(strat, bench)
    assert out["excess_return"] > 0
    assert out["jensen_alpha"] > 0
    assert out["information_ratio"] > 0
    assert math.isfinite(out["up_down_capture"])


def test_model_registry_openquant_gate(tmp_path):
    """Integration test: model_registry.review rejects models failing OpenQuant benchmark dominance."""
    import json
    import sqlite3
    from research import model_registry

    db_file = str(tmp_path / "test_reg.db")
    # Initialize table via register
    good_evidence = {
        "walk_forward_passed": True,
        "purged_validation_passed": True,
        "point_in_time": True,
        "sample_gate_passed": True,
        "cost_model_validated": True,
        # OpenQuant failing metric:
        "jensen_alpha": -0.05,
        "excess_return": 0.05,
        "sharpe": 1.2,
        "profit_factor": 1.5,
        "max_drawdown": -0.20,
    }
    rec = model_registry.register(
        db_file, model_key="strat_bad_alpha", version="1.0.0",
        kind="classifier", feature_schema_version="1.0", training_data_hash="hash123",
        artifact_uri="file:///models/strat.pkl",
        metrics=good_evidence,
    )
    model_id = rec["id"]

    # Review as APPROVED must raise ValueError due to OpenQuant failure
    with pytest.raises(ValueError, match="OpenQuant benchmark dominance"):
        model_registry.review(db_file, model_id=model_id, status="APPROVED", reviewer="quant_lead")

    # Now register with fully passing OpenQuant metrics
    good_evidence["jensen_alpha"] = 0.03
    rec2 = model_registry.register(
        db_file, model_key="strat_good_alpha", version="1.0.0",
        kind="classifier", feature_schema_version="1.0", training_data_hash="hash123",
        artifact_uri="file:///models/strat.pkl",
        metrics=good_evidence,
    )
    res = model_registry.review(db_file, model_id=rec2["id"], status="APPROVED", reviewer="quant_lead")
    assert res["status"] == "APPROVED"


def test_auto_promotion_openquant_gate():
    """Integration test: AutoPromoter._check_openquant_admissibility enforces Eq. 6."""
    from research.auto_promotion import AutoPromoter

    promoter = AutoPromoter()
    # Inadmissible strategy
    bad_current = {
        "status": "VALIDATING",
        "metrics": {
            "jensen_alpha": 0.02,
            "excess_return": -0.01,  # fails excess_return <= 0
            "sharpe": 1.2,
            "profit_factor": 1.5,
            "max_drawdown": -0.20,
        }
    }
    res_bad = promoter._check_openquant_admissibility("strat_neg_excess", bad_current)
    assert res_bad["passed"] is False
    assert "excess_return<=0" in res_bad["failed_gates"]

    # Admissible strategy
    good_current = {
        "status": "VALIDATING",
        "metrics": _good()
    }
    res_good = promoter._check_openquant_admissibility("strat_good", good_current)
    assert res_good["passed"] is True
    assert res_good["composite_score"] > 50.0

