"""Unit tests for research/strategy_git.py: Version control, diffing, Wilcoxon PR gate, and rollback."""
import sqlite3
import pytest
import numpy as np

from research.strategy_git import StrategyGit


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_strategy_git_commit_and_diff(mem_db):
    sg = StrategyGit(mem_db)
    genome_v1 = {"strategy_id": "statarb", "sl_atr": 1.5, "allowed_regimes": ["ALL"]}
    c1 = sg.commit(
        strategy_id="statarb",
        branch_name="main",
        genome=genome_v1,
        commit_message="Initial commit",
        cpcv_oos_sharpes=[0.2, 0.25, 0.18, 0.22, 0.24, 0.21],
        pbo=0.35,
        dsr=0.60,
        math_audit_passed=True,
    )
    assert c1["committed"] is True
    h1 = c1["commit_hash"]

    genome_v2 = {"strategy_id": "statarb", "sl_atr": 2.0, "allowed_regimes": ["MEAN_REVERTING"]}
    c2 = sg.commit(
        strategy_id="statarb",
        branch_name="shadow/fix-regime",
        genome=genome_v2,
        commit_message="Regime-lock and widen SL",
        cpcv_oos_sharpes=[0.5, 0.55, 0.48, 0.52, 0.54, 0.51],
        pbo=0.08,
        dsr=0.98,
        math_audit_passed=True,
        parent_hash=h1,
    )
    assert c2["committed"] is True
    h2 = c2["commit_hash"]

    diff_res = sg.diff(h1, h2)
    assert "sl_atr" in diff_res["genome_changes"]
    assert diff_res["genome_changes"]["sl_atr"] == {"from": 1.5, "to": 2.0}
    assert diff_res["sharpe_delta"] > 0


def test_strategy_git_rejects_unverified_math(mem_db):
    sg = StrategyGit(mem_db)
    genome = {"strategy_id": "bad_strat"}
    res = sg.commit(
        strategy_id="bad_strat",
        branch_name="main",
        genome=genome,
        commit_message="Leaky strategy",
        cpcv_oos_sharpes=[1.5] * 6,
        pbo=0.01,
        dsr=0.99,
        math_audit_passed=False, # REJECTED
    )
    assert res["committed"] is False
    assert "REJECTED" in res["reason"]


def test_strategy_git_statistical_pr_merge_gate(mem_db):
    sg = StrategyGit(mem_db)
    # Target branch: base Sharpe ~0.25
    c1 = sg.commit(
        strategy_id="strat_a",
        branch_name="canary",
        genome={"sl": 1.0},
        commit_message="base",
        cpcv_oos_sharpes=[0.25, 0.22, 0.28, 0.24, 0.26, 0.23, 0.25, 0.21],
        pbo=0.20,
        dsr=0.96,
        math_audit_passed=True,
    )
    # Candidate branch: child Sharpe ~0.55 (clearly superior)
    c2 = sg.commit(
        strategy_id="strat_a",
        branch_name="shadow/fix",
        genome={"sl": 1.5},
        commit_message="improved",
        cpcv_oos_sharpes=[0.55, 0.52, 0.58, 0.54, 0.56, 0.53, 0.55, 0.51],
        pbo=0.05,
        dsr=0.99,
        math_audit_passed=True,
        parent_hash=c1["commit_hash"],
    )

    merge_res = sg.merge_pull_request(
        strategy_id="strat_a",
        source_branch="shadow/fix",
        target_branch="canary",
        max_p_val=0.05,
    )
    assert merge_res["merged"] is True
    assert merge_res["new_head"] == c2["commit_hash"]


def test_strategy_git_revert_to_parent(mem_db):
    sg = StrategyGit(mem_db)
    c1 = sg.commit(
        strategy_id="strat_b",
        branch_name="main",
        genome={"v": 1},
        commit_message="v1",
        cpcv_oos_sharpes=[0.4] * 6,
        pbo=0.1,
        dsr=0.96,
        math_audit_passed=True,
    )
    c2 = sg.commit(
        strategy_id="strat_b",
        branch_name="main",
        genome={"v": 2},
        commit_message="v2",
        cpcv_oos_sharpes=[0.45] * 6,
        pbo=0.1,
        dsr=0.97,
        math_audit_passed=True,
        parent_hash=c1["commit_hash"],
    )
    # Live CUSUM drift triggers autonomous revert
    rev = sg.revert_to_parent(strategy_id="strat_b", branch_name="main")
    assert rev["reverted"] is True
    assert rev["reverted_to"] == c1["commit_hash"]
