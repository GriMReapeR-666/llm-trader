"""The RL Q-table must persist atomically and survive a restart.

Regression guard for save_model() using a plain open(path,'w'), which truncates
in place: a crash or full disk mid-dump left a torn JSON file and the entire
learned Q-table was unrecoverable, silently restarting learning from zero.
"""
import json
import os

import pytest

from research.rl_trainer import RLTrainer

REAL_STATE = {
    "rsi_14": 58.0, "macd_histogram": 0.0012, "ma_alignment": 1,
    "atr_normalized": 0.0021, "confluence_score": 5, "near_sr": False,
    "news_sentiment": 0.5, "volatility_regime": "medium",
    "trend_strength": 0.54, "htf_trend": 1, "session": "LONDON",
}


def _train(trainer, n):
    for i in range(n):
        trainer.update(dict(REAL_STATE), "BUY" if i % 2 else "SELL",
                         5.0 + i, dict(REAL_STATE), done=True)
        # Replicate the orchestrator's %10 persistence rule.
        if trainer.total_updates % 10 == 0:
            trainer.save_model()


def test_state_survives_restart(tmp_path):
    path = str(tmp_path / "rl_model.json")
    t = RLTrainer(model_path=path)
    _train(t, 20)
    assert os.path.exists(path)

    reloaded = RLTrainer(model_path=path)
    assert len(reloaded.q_table) == len(t.q_table)
    assert reloaded.total_updates == t.total_updates
    key = next(iter(t.q_table))
    assert reloaded.q_table[key] == pytest.approx(t.q_table[key])


def test_save_is_atomic_no_temp_files_left(tmp_path):
    """The temp file must be renamed away, never left on disk."""
    path = str(tmp_path / "rl_model.json")
    t = RLTrainer(model_path=path)
    _train(t, 10)
    leftovers = [n for n in os.listdir(str(tmp_path)) if n.startswith(".rl_model-")]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_save_creates_missing_directory(tmp_path):
    """A configured path whose directory does not exist must still persist."""
    path = str(tmp_path / "nested" / "deeper" / "rl_model.json")
    t = RLTrainer(model_path=path)
    _train(t, 10)
    assert os.path.exists(path)
    assert len(RLTrainer(model_path=path).q_table) >= 1


def test_existing_file_is_never_truncated_by_a_failed_write(tmp_path, monkeypatch):
    """A mid-dump failure must leave the PREVIOUS file intact.

    This is the actual regression: with open(path,'w') a failure partway through
    json.dump destroyed the prior good checkpoint.
    """
    path = str(tmp_path / "rl_model.json")
    t = RLTrainer(model_path=path)
    _train(t, 10)
    good = json.loads(open(path, encoding="utf-8").read())
    assert good["q_table"], "need a non-empty baseline checkpoint"

    import research.rl_trainer as rt

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rt.json, "dump", boom)
    _train(t, 10)   # another save attempt, this one fails
    monkeypatch.undo()

    still = json.loads(open(path, encoding="utf-8").read())
    assert still["q_table"], "the previously saved Q-table was destroyed by a failed write"
    assert len(still["q_table"]) == len(good["q_table"])
    assert RLTrainer(model_path=path).q_table, "checkpoint is no longer loadable"


def test_corrupt_file_does_not_raise(tmp_path):
    """A corrupt file is survivable: start empty, never crash the boot."""
    path = str(tmp_path / "rl_model.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"q_table": {"vol:bin_0|rsi:bi')
    t = RLTrainer(model_path=path)
    assert len(t.q_table) == 0


def test_state_key_from_production_schema_is_stable(tmp_path):
    """The real _build_rl_state shape must not fall back to default_state."""
    t = RLTrainer(model_path=str(tmp_path / "rl.json"))
    key = t._build_state_key(REAL_STATE)
    assert "default_state" not in key
    assert key == t._build_state_key(dict(REAL_STATE)), "key is not deterministic"