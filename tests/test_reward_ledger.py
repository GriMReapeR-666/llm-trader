from pathlib import Path
import sqlite3

import pytest

from operations.reward_ledger import (
    compute_terminal_reward,
    emit_reward_event,
    emit_terminal_trade_rewards,
    list_reward_events,
)
from research.rl_trainer import RLTrainer


def test_compute_terminal_reward_blocks_unreconciled():
    out = compute_terminal_reward({"pnl": 10, "risk": 5, "broker_reconciled": False})
    assert out["status"] == "BLOCKED"
    assert out["reward"] == 0.0
    assert out["execution_authority"] is False


def test_compute_terminal_reward_uses_r_multiple_and_caps_process():
    out = compute_terminal_reward(
        {
            "pnl": 12.0,
            "risk": 10.0,
            "r_multiple": 1.2,
            "broker_reconciled": True,
            "confluence_score": 8,
            "news_aligned": True,
            "max_drawdown": 0.03,
        }
    )
    assert out["status"] == "OK"
    assert out["r_net"] == pytest.approx(1.2)
    # process capped at 0.15 * max(1, |r|) = 0.15*1.2 = 0.18
    assert abs(out["process"]) <= out["process_cap"] + 1e-9
    assert out["reward"] == pytest.approx(out["r_clipped"] + out["process"])
    assert out["execution_authority"] is False


def test_reward_events_append_only(tmp_path: Path):
    path = str(tmp_path / "rewards.db")
    event = emit_reward_event(
        path,
        layer="L0",
        agent="TechnicalNet",
        action="emit_opportunity",
        value=0.05,
        candidate_id="abc",
    )
    assert event["execution_authority"] is False
    rows = list_reward_events(path)
    assert len(rows) == 1
    with sqlite3.connect(path) as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE reward_events SET value=1 WHERE id=1")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM reward_events WHERE id=1")


def test_emit_terminal_trade_rewards_writes_credit_shares(tmp_path: Path):
    path = str(tmp_path / "term.db")
    trade = {
        "ticket": 42,
        "pnl": 20.0,
        "risk": 10.0,
        "r_multiple": 2.0,
        "broker_reconciled": True,
        "causal_edge_ids": [7],
        "candidate_id": "cand-1",
        "rl_action": "BUY",
    }
    recorded = emit_terminal_trade_rewards(path, trade, run_id="run-1")
    assert recorded["status"] == "RECORDED"
    assert recorded["execution_authority"] is False
    events = list_reward_events(path, ticket=42)
    agents = {e["agent"] for e in events}
    assert "MarketModel" in agents
    assert "CausalValidator" in agents
    assert "TechnicalNet" in agents
    terminal = [e for e in events if e["action"] == "terminal_trade_close"][0]
    assert terminal["value"] == pytest.approx(recorded["reward"])


def test_rl_trainer_calculate_reward_fail_closed(tmp_path: Path):
    trainer = RLTrainer(model_path=str(tmp_path / "rl.json"))
    assert trainer.calculate_reward({"pnl": 5, "risk": 1}) == 0.0  # missing reconciled
    reward = trainer.calculate_reward(
        {"pnl": 5.0, "risk": 5.0, "broker_reconciled": True, "r_multiple": 1.0}
    )
    assert reward == pytest.approx(1.0)
