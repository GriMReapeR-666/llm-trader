import os
import sqlite3
import pytest

from research.research_runs import ensure, record, list_runs
from research.baseline_research import baseline_report
from research.supervised_training import train as train_supervised_baseline
from research.contextual_bandit import bandit_report
from core.trading_safety import TradingStore


def test_research_validation_runs_record_and_list(tmp_path):
    db_file = str(tmp_path / "test_research.db")

    # The authoritative store creates this ledger on first initialization.
    TradingStore(db_file)
    # Calling ensure again must remain idempotent for migrated journals.
    ensure(db_file)
    assert os.path.exists(db_file)
    with sqlite3.connect(db_file) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_validation_runs'").fetchone()

    # 2. Record a baseline research report
    baseline_rep = baseline_report(db_file)
    rec1 = record(db_file, "baseline", baseline_rep)

    assert isinstance(rec1, dict)
    assert rec1["run_kind"] == "baseline"
    assert rec1["execution_authority"] is False
    assert len(rec1["run_id"]) == 32

    # 3. Record a supervised research report
    sup_rep = train_supervised_baseline(db_file)
    rec2 = record(db_file, "supervised", sup_rep)
    assert rec2["run_kind"] == "supervised"

    # 4. Record a bandit research report
    bandit_rep = bandit_report(db_file)
    rec3 = record(db_file, "bandit", bandit_rep)
    assert rec3["run_kind"] == "bandit"

    # 5. List all runs
    all_runs = list_runs(db_file)
    assert len(all_runs) == 3

    # 6. Filter runs by run_kind
    baseline_runs = list_runs(db_file, run_kind="baseline")
    assert len(baseline_runs) == 1
    assert baseline_runs[0]["run_kind"] == "baseline"
    assert baseline_runs[0]["execution_authority"] is False

    supervised_runs = list_runs(db_file, run_kind="supervised")
    assert len(supervised_runs) == 1

    bandit_runs = list_runs(db_file, run_kind="bandit")
    assert len(bandit_runs) == 1


def test_research_validation_runs_preserve_hashes_and_apply_limit(tmp_path):
    db_file = str(tmp_path / "test_hashes.db")
    report = {
        "status": "READY_FOR_REVIEW",
        "dataset": {"sample_size": 125, "training_data_hash": "dataset-v1"},
        "configuration": {"seed": 7, "embargo_seconds": 3600},
        "execution_authority": True,  # persisted ledger must override this claim
    }
    first = record(db_file, "baseline", report)
    second = record(db_file, "baseline", {**report, "status": "INSUFFICIENT_DATA"})
    rows = list_runs(db_file, run_kind="baseline", limit=1)
    assert len(rows) == 1
    assert rows[0]["run_id"] == second["run_id"]
    assert rows[0]["dataset_hash"] == first["dataset_hash"]
    assert len(rows[0]["report_hash"]) == 64
    assert rows[0]["configuration"]["embargo_seconds"] == 3600
    assert rows[0]["report"]["execution_authority"] is True
    assert rows[0]["execution_authority"] is False


def test_research_validation_runs_are_database_append_only(tmp_path):
    db_file = str(tmp_path / "test_append_only.db")
    rec = record(db_file, "baseline", {"status": "TEST", "dataset": {"sample_size": 1}})
    with sqlite3.connect(db_file) as con:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE research_validation_runs SET status='CORRUPTED' WHERE run_id=?", (rec["run_id"],))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM research_validation_runs WHERE run_id=?", (rec["run_id"],))


def test_research_validation_runs_invalid_input(tmp_path):
    db_file = str(tmp_path / "test_invalid.db")
    
    with pytest.raises(ValueError, match="research report must be an object"):
        record(db_file, "baseline", "not_a_dict")

    with pytest.raises(ValueError, match="research run kind is required"):
        record(db_file, "", {"status": "TEST"})
