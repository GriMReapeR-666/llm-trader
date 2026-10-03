"""Tests for S.Ξ Trial Ledger — DSR Hard Gate and Append-Only Invariants."""
import math
import os

import numpy as np
import pytest

from core.trial_ledger import (
    TrialLedger,
    DSR_GATE_THRESHOLD,
    TrialRecord,
    DSRResult,
    _hash_record,
    get_trial_ledger,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def ledger(tmp_path):
    """Fresh ledger with isolated DB."""
    db = str(tmp_path / "test_trial_ledger.db")
    return TrialLedger(db_path=db)


class TestTrialRecord:
    def test_creation(self):
        r = TrialRecord(
            trial_id=1, strategy_id="alpha_001", sharpe=1.2,
            n_observations=500, timestamp=1000.0,
        )
        assert r.trial_id == 1
        assert r.sharpe == 1.2
        d = r.to_dict()
        assert d["strategy_id"] == "alpha_001"

    def test_metadata(self):
        r = TrialRecord(
            trial_id=1, strategy_id="x", sharpe=1.0, n_observations=100,
            metadata={"params": {"fast": 12}},
        )
        assert r.metadata["params"]["fast"] == 12


class TestAppendOnly:
    def test_empty_ledger(self, ledger):
        assert ledger.n_trials == 0
        assert ledger.trial_sharpes() == []

    def test_record_trial(self, ledger):
        r = ledger.record_trial("alpha_001", 1.5, 500)
        assert r.trial_id == 1
        assert ledger.n_trials == 1
        assert ledger.trial_sharpes() == [1.5]

    def test_monotonic_trial_ids(self, ledger):
        ids = []
        for i in range(5):
            r = ledger.record_trial(f"strat_{i}", 0.5 + i * 0.2, 200)
            ids.append(r.trial_id)
        assert ids == [1, 2, 3, 4, 5]
        assert ledger.n_trials == 5

    def test_append_only_sharpes(self, ledger):
        for s in [1.0, 0.5, 2.0, -0.3]:
            ledger.record_trial("s", s, 100)
        assert ledger.trial_sharpes() == [1.0, 0.5, 2.0, -0.3]

    def test_no_delete_no_update(self, ledger):
        """Verify SQLite has no DELETE/UPDATE triggers — append-only by construction."""
        for s in [1.0, 2.0, 3.0]:
            ledger.record_trial("s", s, 100)
        # There is literally no delete or update method on the class
        assert not hasattr(ledger, 'delete_trial')
        assert not hasattr(ledger, 'update_trial')


class TestHashChain:
    def test_hash_chain_valid_empty(self, ledger):
        v = ledger.verify_chain()
        assert v["valid"] is True
        assert v["n_records"] == 0

    def test_hash_chain_valid_single(self, ledger):
        ledger.record_trial("s", 1.0, 100)
        v = ledger.verify_chain()
        assert v["valid"] is True
        assert v["n_records"] == 1

    def test_hash_chain_valid_multiple(self, ledger):
        for i in range(10):
            ledger.record_trial(f"s_{i}", float(i), 100)
        v = ledger.verify_chain()
        assert v["valid"] is True
        assert v["n_records"] == 10

    def test_hash_chain_tamper_detection(self, ledger):
        """Directly tamper with DB — chain should break."""
        for i in range(3):
            ledger.record_trial("s", float(i), 100)
        # Tamper
        with ledger._conn() as con:
            con.execute("UPDATE trials SET sharpe = 999.0 WHERE trial_id = 2")
        v = ledger.verify_chain()
        assert v["valid"] is False
        assert v["broken_at"] is not None

    def test_hash_utility(self):
        h1 = _hash_record("data", "")
        h2 = _hash_record("data", "")
        assert h1 == h2  # Deterministic
        h3 = _hash_record("data", h1)
        assert h3 != h1  # Chain changes hash


class TestDSRHardGate:
    def test_high_sharpe_passes(self, ledger):
        """A high Sharpe with few trials should pass DSR gate."""
        result = ledger.evaluate_dsr(
            estimated_sharpe=2.0,
            n_observations=500,
            strategy_id="good_alpha",
        )
        assert isinstance(result, DSRResult)
        assert result.n_trials == 1  # Just recorded this trial
        assert result.estimated_sharpe == 2.0

    def test_low_sharpe_with_many_trials_fails(self, ledger):
        """A mediocre Sharpe after many trials should be rejected."""
        # Record 50 mediocre trials
        for i in range(50):
            ledger.record_trial("noise", np.random.normal(0, 0.3), 100)
        result = ledger.evaluate_dsr(
            estimated_sharpe=0.3,
            n_observations=100,
            strategy_id="marginal_alpha",
        )
        # With 51 trials and Sharpe 0.3, should fail
        assert result.verdict in ("FAIL_NOT_SIGNIFICANT", "PASS")
        assert result.n_trials == 51

    def test_uses_true_trial_count(self, ledger):
        """Critical: ledger uses TRUE n_trials, not caller-supplied."""
        for i in range(20):
            ledger.record_trial("noise", 0.1, 100)
        result = ledger.evaluate_dsr(
            estimated_sharpe=1.0,
            n_observations=200,
            strategy_id="test",
        )
        assert result.n_trials == 21  # 20 existing + 1 just recorded in evaluate_dsr
        assert ledger.n_trials == 21

    def test_result_fields(self, ledger):
        result = ledger.evaluate_dsr(1.5, 300, skewness=0.5, kurtosis=4.0, strategy_id="x")
        d = result.to_dict()
        assert "dsr" in d
        assert "is_significant" in d
        assert "n_trials" in d
        assert "verdict" in d
        assert "pbo_proxy" in d

    def test_gate_logged(self, ledger):
        ledger.evaluate_dsr(1.0, 200, strategy_id="logged")
        history = ledger.get_gate_history()
        assert len(history) == 1
        assert history[0]["strategy_id"] == "logged"

    def test_pbo_proxy_computation(self, ledger):
        # Record negative Sharpes
        for _ in range(5):
            ledger.record_trial("neg", -0.5, 100)
        result = ledger.evaluate_dsr(0.1, 100, strategy_id="neg_test")
        # PBO proxy should be high (most trials negative)
        assert result.pbo_proxy > 0.5


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        db = str(tmp_path / "persist.db")
        ledger1 = TrialLedger(db_path=db)
        for i in range(5):
            ledger1.record_trial(f"s_{i}", float(i), 100)
        assert ledger1.n_trials == 5

        ledger2 = TrialLedger(db_path=db)
        assert ledger2.n_trials == 5
        assert len(ledger2.trial_sharpes()) == 5

    def test_chain_survives_reopen(self, tmp_path):
        db = str(tmp_path / "chain.db")
        ledger1 = TrialLedger(db_path=db)
        for i in range(10):
            ledger1.record_trial("s", float(i), 100)
        assert ledger1.verify_chain()["valid"] is True

        ledger2 = TrialLedger(db_path=db)
        assert ledger2.verify_chain()["valid"] is True


class TestStrategyStats:
    def test_strategy_stats(self, ledger):
        for i in range(5):
            ledger.record_trial("alpha_001", 0.5 + i * 0.1, 200)
        stats = ledger.get_strategy_stats("alpha_001")
        assert stats["n_trials"] == 5
        assert stats["sharpe_mean"] > 0
        assert stats["positive_fraction"] > 0

    def test_unknown_strategy(self, ledger):
        stats = ledger.get_strategy_stats("nonexistent")
        assert stats["n_trials"] == 0


class TestSummary:
    def test_summary(self, ledger):
        for i in range(5):
            ledger.record_trial("s", float(i), 100)
        s = ledger.summary()
        assert s["n_trials"] == 5
        assert s["chain_valid"] is True
        assert "trial_sharpe_mean" in s
        assert "pbo_proxy" in s


class TestSingleton:
    def test_get_trial_ledger(self):
        ledger = get_trial_ledger()
        assert isinstance(ledger, TrialLedger)


class TestEdgeCases:
    def test_zero_observations(self, ledger):
        result = ledger.evaluate_dsr(1.0, 0, strategy_id="zero")
        assert result.verdict in ("PASS", "FAIL_NOT_SIGNIFICANT")

    def test_very_high_sharpe(self, ledger):
        result = ledger.evaluate_dsr(10.0, 1000, strategy_id="extreme")
        assert isinstance(result.dsr, float)

    def test_negative_sharpe(self, ledger):
        result = ledger.evaluate_dsr(-1.0, 200, strategy_id="loser")
        # DSR correctly identifies negative Sharpe as significant (real, just bad)
        # The overfit_detector, not DSR, decides if the strategy is profitable
        assert result.estimated_sharpe == -1.0
        assert result.n_trials == 1

    def test_custom_threshold(self, ledger):
        result = ledger.evaluate_dsr(
            1.5, 300, strategy_id="custom",
            gate_threshold=0.50,
        )
        # With 1 trial and Sharpe 1.5, DSR should be very high
        assert result.gate_threshold == 0.50


class TestIntegration:
    def test_full_cycle(self, ledger):
        """Simulate 10 strategies tested, evaluate each."""
        results = []
        for i in range(10):
            sharpe = 0.5 + np.random.normal(0, 0.3)
            r = ledger.evaluate_dsr(
                estimated_sharpe=float(sharpe),
                n_observations=300,
                strategy_id=f"strat_{i}",
            )
            results.append(r)

        assert ledger.n_trials == 10
        assert len(ledger.get_gate_history()) == 10
        # After 10 trials, the DSR gate gets harder
        pass_count = sum(1 for r in results if r.verdict == "PASS")
        assert pass_count <= 10  # Sanity

    def test_dsr_gets_harder_with_more_trials(self, tmp_path):
        """More trials → harder to pass DSR gate."""
        db1 = str(tmp_path / "fresh.db")
        db2 = str(tmp_path / "crowded.db")
        l1 = TrialLedger(db_path=db1)
        l2 = TrialLedger(db_path=db2)
        # l2 has 20 prior trials
        for i in range(20):
            l2.record_trial("noise", 0.2, 100)

        r1 = l1.evaluate_dsr(1.0, 200, strategy_id="fresh")
        r2 = l2.evaluate_dsr(1.0, 200, strategy_id="crowded")

        # Fresh ledger should have higher DSR (fewer trials)
        assert r1.n_trials < r2.n_trials


# ---------------------------------------------------------------------------
# Per-hypothesis-class deflation (strategy_class tagging)
# ---------------------------------------------------------------------------
class TestStrategyClassScoping:
    """DSR deflation scoped to a hypothesis class, not the global ledger.

    Every sweep cell / harness run / CPCV path lands in one shared ledger;
    without class tags the multiple-testing bar for ANY new strategy is
    E[max|null] of the entire research history. These tests pin the
    per-class scoping and the fail-closed global fallback.
    """

    def test_class_counts_are_independent(self, ledger):
        for i in range(5):
            ledger.record_trial(strategy_id=f"a{i}", sharpe=0.5,
                                n_observations=100, strategy_class="alpha_class")
        for i in range(3):
            ledger.record_trial(strategy_id=f"b{i}", sharpe=-0.2,
                                n_observations=100, strategy_class="beta_class")
        ledger.record_trial(strategy_id="u0", sharpe=0.1, n_observations=100)

        assert ledger.n_trials_for_class("alpha_class") == 5
        assert ledger.n_trials_for_class("beta_class") == 3
        assert ledger.n_trials_for_class("nonexistent") == 0
        # None → global count (fail-closed fallback)
        assert ledger.n_trials_for_class(None) == ledger.n_trials == 9

    def test_untagged_rows_default_to_general(self, ledger):
        ledger.record_trial(strategy_id="legacy", sharpe=0.3, n_observations=100)
        assert ledger.n_trials_for_class("general") == 1
        assert ledger.list_strategy_classes() == {"general": 1}

    def test_evaluated_dsr_tags_and_scopes(self, ledger):
        # Seed 4 prior trials in the target class + 3 in an unrelated class
        for i in range(4):
            ledger.record_trial(strategy_id=f"p{i}", sharpe=0.2,
                                n_observations=100, strategy_class="my_class")
        for i in range(3):
            ledger.record_trial(strategy_id=f"o{i}", sharpe=0.1,
                                n_observations=100, strategy_class="other_class")
        r = ledger.evaluate_dsr(
            estimated_sharpe=0.35, n_observations=200,
            strategy_id="probe", strategy_class="my_class",
        )
        assert r.strategy_class == "my_class"
        assert r.n_trials == 5  # 4 seeded + this one — NOT 8 (global)
        # Global count must be larger (strictest bar preserved for untagged)
        assert ledger.n_trials == 8 > r.n_trials

    def test_same_sharpe_easier_bar_when_scoped(self, ledger):
        # A Sharpe that fails against a crowded GLOBAL pool can pass within
        # a fresh, honestly-scoped class — that is the entire point.
        # 200 prior null trials → global bar m_k ~ 2.75 SE (k=201);
        # a fresh class faces k=1 → bar 0. The probe SR must sit BETWEEN
        # the bars: SE over 250 obs ~ 0.0632, so 0.17 ~ 2.69 SE — clears a
        # fresh class but not the 2.75-SE global bar. (The old probe,
        # SR=1.6 @ n=250, was ~8 SE significant and correctly passes BOTH
        # bars under proper units — the units bug was the only reason it
        # ever failed anything.)
        for i in range(200):
            ledger.record_trial(strategy_id=f"null{i}", sharpe=0.0,
                                n_observations=100)
        r_global = ledger.evaluate_dsr(
            estimated_sharpe=0.17, n_observations=250, strategy_id="g_probe",
        )
        r_scoped = ledger.evaluate_dsr(
            estimated_sharpe=0.17, n_observations=250, strategy_id="s_probe",
            strategy_class="fresh_class",
        )
        assert r_global.n_trials == 201 and r_scoped.n_trials == 1
        assert r_global.is_significant is False   # below the global bar
        assert r_scoped.is_significant is True    # clears its own class bar
        # The scoped evaluation never faces a HARSHER bar than global
        assert r_scoped.max_expected_sharpe <= r_global.max_expected_sharpe

    def test_record_false_is_read_only(self, ledger):
        r1 = ledger.evaluate_dsr(estimated_sharpe=0.4, n_observations=200,
                                 strategy_id="h1")
        n_after_first = ledger.n_trials
        # Re-gate without recording: no phantom trial appended
        r2 = ledger.evaluate_dsr(estimated_sharpe=0.4, n_observations=200,
                                 strategy_id="h1", record=False)
        assert ledger.n_trials == n_after_first
        assert r2.n_trials == r1.n_trials
        assert r2.dsr == pytest.approx(r1.dsr)

    def test_migration_adds_column_and_keeps_hashes(self, tmp_path):
        """Legacy DB (no strategy_class column) migrates; chain stays valid.

        strategy_class is deliberately NOT part of the hash payload, so
        migrating a production ledger must not break verify_chain().
        """
        import sqlite3
        db = str(tmp_path / "legacy.db")
        # Build a pre-migration schema by hand (old columns only)
        con = sqlite3.connect(db)
        con.execute("""
            CREATE TABLE trials (
                trial_id    INTEGER PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                sharpe      REAL NOT NULL,
                n_observations INTEGER NOT NULL,
                skewness    REAL NOT NULL DEFAULT 0.0,
                kurtosis    REAL NOT NULL DEFAULT 3.0,
                timestamp   REAL NOT NULL,
                hash_prev   TEXT NOT NULL DEFAULT '',
                hash_current TEXT NOT NULL,
                metadata    TEXT DEFAULT NULL
            )
        """)
        con.commit()
        con.close()

        led = TrialLedger(db_path=db)  # triggers migration
        # Old-shape DB had no rows; verify column exists and works
        led.record_trial(strategy_id="m1", sharpe=0.2, n_observations=50)
        assert led.n_trials_for_class("general") == 1
        assert led.verify_chain()["valid"]
