"""Unit tests for operations/autonomous_evolution_loop.py."""
import sqlite3
import pytest
import numpy as np

from research.strategy_git import StrategyGit
from intelligence.strategy_reasoner import StrategyReasoner
from operations.autonomous_evolution_loop import run_autonomous_strategy_evolution_sprint


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_evolution_sprint_executes_and_commits(mem_db):
    sg = StrategyGit(mem_db)
    reasoner = StrategyReasoner(llm_router=None)

    # Initial parent commit
    parent_genome = {"strategy_id": "test_strat", "sl_atr_mult": 1.5, "allowed_regimes": ["ALL"]}
    init_res = sg.commit(
        strategy_id="test_strat",
        branch_name="main",
        genome=parent_genome,
        commit_message="Initial parent",
        cpcv_oos_sharpes=[0.2] * 6,
        pbo=0.35,
        dsr=0.50,
        math_audit_passed=True,
    )
    parent_hash = init_res["commit_hash"]

    # Parent trades showing regime contamination
    parent_trades = (
        [{"r_multiple": 0.6, "regime": "LOW_VOL_MEAN_REVERT"} for _ in range(15)] +
        [{"r_multiple": -0.5, "regime": "TREND_EXPANSION"} for _ in range(15)]
    )

    np.random.seed(42)
    n = 200
    prices = 100.0 + np.cumsum(np.random.normal(0.05, 1.0, n))
    ohlcv = np.column_stack([prices, prices + 0.5, prices - 0.5, prices, np.ones(n) * 1000])

    def mock_backtest_runner(genome, bars):
        # Child produces 25 trades with solid positive edge
        trades = [
            {"net_pnl": 15.0, "risk_dollars": 10.0, "r_multiple": 1.5}
            for _ in range(25)
        ]
        sig_fn = lambda b: np.zeros(len(b))
        return trades, sig_fn

    sprint_res = run_autonomous_strategy_evolution_sprint(
        strategy_id="test_strat",
        current_genome=parent_genome,
        parent_commit_hash=parent_hash,
        parent_trades=parent_trades,
        ohlcv_bars=ohlcv,
        backtest_runner_fn=mock_backtest_runner,
        strategy_git=sg,
        strategy_reasoner=reasoner,
        validated_alphas_pool=[],
        n_effective_trials=13.0,
    )

    assert sprint_res["strategy_id"] == "test_strat"
    assert len(sprint_res["evaluated_branches"]) >= 1
    eval_br = sprint_res["evaluated_branches"][0]
    assert eval_br["commit_hash"] is not None
    assert eval_br["status"] == "EVALUATED"
