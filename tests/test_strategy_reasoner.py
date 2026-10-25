"""Unit tests for intelligence/strategy_reasoner.py: Autopsy-driven mutation & Alpha-Grafting."""
import pytest
from intelligence.strategy_reasoner import StrategyReasoner


def test_deterministic_structural_repair_from_autopsy():
    reasoner = StrategyReasoner(llm_router=None)
    parent_genome = {
        "strategy_id": "test_strat",
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.0,
        "breakeven_trigger_r": 1.5,
        "signal_polarity": 1,
        "allowed_regimes": ["BULL_TREND", "TREND_EXPANSION"],
    }
    autopsy_report = {
        "status": "AUTOPSY_COMPLETE",
        "overall_mean_r": -0.2,
        "recommended_mutations": [
            {"op": "RESTRICT_ALLOWED_REGIMES", "allowed_regimes": ["LOW_VOL_MEAN_REVERT"]},
            {"op": "LOWER_BREAKEVEN_TRIGGER_R", "new_trigger_r": 0.95},
        ],
    }

    branches = reasoner.propose_improvement_branches(
        strategy_id="test_strat",
        parent_genome=parent_genome,
        autopsy_report=autopsy_report,
        failed_commit_diffs=[],
    )
    assert len(branches) >= 1
    fix_branch = branches[0]
    assert "shadow/fix-" in fix_branch["branch_name"]
    mutated = fix_branch["mutated_genome"]
    assert mutated["allowed_regimes"] == ["LOW_VOL_MEAN_REVERT"]
    assert mutated["breakeven_trigger_r"] == 0.95


def test_structural_alpha_grafting_on_dead_signal():
    reasoner = StrategyReasoner(llm_router=None)
    parent_genome = {
        "strategy_id": "dead_strat",
        "primary_alpha_dsl": "ts_mean(close, 5)",
    }
    autopsy_report = {
        "status": "AUTOPSY_COMPLETE",
        "overall_mean_r": -0.4,
        "recommended_mutations": [], # Dead signal, no regime rescue
    }
    alphas_pool = [
        {"expression": "ts_rank(vwap / close, 14)", "ic_ir": 1.85, "turnover": 0.2},
        {"expression": "correlation(volume, close, 10)", "ic_ir": 0.95, "turnover": 0.4},
    ]
    branches = reasoner.propose_improvement_branches(
        strategy_id="dead_strat",
        parent_genome=parent_genome,
        autopsy_report=autopsy_report,
        failed_commit_diffs=[],
        validated_alphas_pool=alphas_pool,
    )
    assert len(branches) >= 1
    graft_branch = [b for b in branches if "graft-alpha" in b["branch_name"]][0]
    assert graft_branch["mutated_genome"]["primary_alpha_dsl"] == "ts_rank(vwap / close, 14)"
