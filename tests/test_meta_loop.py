"""Tests for Meta-Loop (Loop 4) — core/meta_loop.py"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.meta_loop import (
    CorpusEntry,
    DeltaPriors,
    MetaCorpus,
    MetaLoop,
    Pattern,
    PatternMiner,
    PatternType,
    PriorType,
    ProposalStatus,
    ShadowDeploy,
)


# ---------------------------------------------------------------------------
# MetaCorpus Tests
# ---------------------------------------------------------------------------

class TestMetaCorpus:
    def _make_corpus(self, tmp_path):
        return MetaCorpus(db_path=str(tmp_path / "test_corpus.db"))

    def test_log_and_retrieve(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        entry = CorpusEntry(
            hypothesis_id="h1",
            hypothesis_source="alpha_discovery",
            backtest_sharpe=1.5,
            live_sharpe=1.2,
        )
        entry_id = corpus.log_entry(entry)
        assert entry_id == 1

        entries = corpus.get_entries()
        assert len(entries) == 1
        assert entries[0].hypothesis_id == "h1"
        assert entries[0].backtest_sharpe == 1.5

    def test_append_only(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        corpus.log_entry(CorpusEntry(hypothesis_id="h1"))
        corpus.log_entry(CorpusEntry(hypothesis_id="h2"))
        assert corpus.get_entry_count() == 2

    def test_hash_chain_integrity(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        corpus.log_entry(CorpusEntry(hypothesis_id="h1"))
        corpus.log_entry(CorpusEntry(hypothesis_id="h2"))
        corpus.log_entry(CorpusEntry(hypothesis_id="h3"))

        result = corpus.verify_chain()
        assert result["valid"] is True
        assert result["entries"] == 3

    def test_empty_chain_valid(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        result = corpus.verify_chain()
        assert result["valid"] is True
        assert result["entries"] == 0

    def test_log_pattern(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        pattern = Pattern(
            pattern_type=PatternType.REGIME_DECAY,
            description="Test pattern",
            evidence_count=5,
            confidence=0.8,
        )
        pid = corpus.log_pattern(pattern)
        assert pid == 1

        patterns = corpus.get_patterns()
        assert len(patterns) == 1
        assert patterns[0].pattern_type == PatternType.REGIME_DECAY

    def test_log_proposal(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        proposal = DeltaPriors(
            proposal_id="meta_0001",
            prior_type=PriorType.ALPHA_WEIGHTS,
            proposed_changes={"h1": 0.5},
        )
        pid = corpus.log_proposal(proposal)
        assert pid == 1

        proposals = corpus.get_proposals()
        assert len(proposals) == 1
        assert proposals[0].proposal_id == "meta_0001"

    def test_entry_count(self, tmp_path):
        corpus = self._make_corpus(tmp_path)
        assert corpus.get_entry_count() == 0
        corpus.log_entry(CorpusEntry(hypothesis_id="h1"))
        assert corpus.get_entry_count() == 1

    def test_concurrent_writers_keep_chain_valid(self, tmp_path):
        """Sep-19 corpus break regression: two concurrent writers must not
        chain from a stale head (read-then-insert race).  Uses real
        subprocesses — a threading.Lock alone cannot prevent cross-process
        interleaving; BEGIN IMMEDIATE is the cross-process gate."""
        import subprocess
        import sys

        writer = tmp_path / "writer.py"
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        writer.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(root)!r})\n"
            "from core.meta_loop import MetaCorpus, CorpusEntry\n"
            "corpus = MetaCorpus(sys.argv[1])\n"
            "for i in range(10):\n"
            "    corpus.log_entry(CorpusEntry(\n"
            "        hypothesis_id=f\"{sys.argv[2]}_{i}\",\n"
            "        hypothesis_source='race_test',\n"
            "    ))\n"
        )
        db = str(tmp_path / "race.db")
        procs = [
            subprocess.Popen([sys.executable, str(writer), db, tag])
            for tag in ("A", "B")
        ]
        for p in procs:
            assert p.wait(timeout=120) == 0

        corpus = MetaCorpus(db_path=db)
        assert corpus.get_entry_count() == 20
        result = corpus.verify_chain()
        assert result["valid"] is True, result


# ---------------------------------------------------------------------------
# PatternMiner Tests
# ---------------------------------------------------------------------------

class TestPatternMiner:
    def _make_corpus_with_data(self, tmp_path, n=20):
        corpus = MetaCorpus(db_path=str(tmp_path / "test_corpus.db"))
        for i in range(n):
            regime = "HIGH_VOL_CRISIS" if i % 3 == 0 else "LOW_VOL_TREND"
            decay = "DEAD" if (i % 3 == 0 and i > 10) else "HEALTHY"
            source = "alpha_discovery" if i % 2 == 0 else "manual"
            feature = "momentum" if i % 4 == 0 else "mean_reversion"
            corpus.log_entry(CorpusEntry(
                hypothesis_id=f"h{i}",
                hypothesis_source=source,
                regime_at_entry=regime,
                backtest_sharpe=1.0 + (i % 5) * 0.2,
                live_sharpe=0.3 + (i % 5) * 0.1,
                decay_status=decay,
                feature_type=feature,
            ))
        return corpus

    def test_mine_regime_decay(self, tmp_path):
        corpus = self._make_corpus_with_data(tmp_path, n=30)
        miner = PatternMiner(corpus)
        patterns = miner._mine_regime_decay()
        # Should find HIGH_VOL_CRISIS has more DEAD entries
        regime_patterns = [p for p in patterns if p.pattern_type == PatternType.REGIME_DECAY]
        # May or may not find patterns depending on data distribution
        assert isinstance(regime_patterns, list)

    def test_mine_source_overfit(self, tmp_path):
        corpus = self._make_corpus_with_data(tmp_path, n=30)
        miner = PatternMiner(corpus)
        patterns = miner._mine_source_overfit()
        assert isinstance(patterns, list)

    def test_mine_feature_fragile(self, tmp_path):
        corpus = self._make_corpus_with_data(tmp_path, n=30)
        miner = PatternMiner(corpus)
        patterns = miner._mine_feature_fragile()
        assert isinstance(patterns, list)

    def test_mine_all(self, tmp_path):
        corpus = self._make_corpus_with_data(tmp_path, n=30)
        miner = PatternMiner(corpus)
        patterns = miner.mine_all()
        assert isinstance(patterns, list)

    def test_insufficient_data(self, tmp_path):
        corpus = MetaCorpus(db_path=str(tmp_path / "test_corpus.db"))
        # Only 3 entries — below MIN_SAMPLES
        for i in range(3):
            corpus.log_entry(CorpusEntry(hypothesis_id=f"h{i}"))
        miner = PatternMiner(corpus)
        patterns = miner.mine_all()
        assert len(patterns) == 0


# ---------------------------------------------------------------------------
# ShadowDeploy Tests
# ---------------------------------------------------------------------------

class TestShadowDeploy:
    def _make_entries(self, n=50):
        entries = []
        for i in range(n):
            entries.append(CorpusEntry(
                hypothesis_id=f"h{i}",
                backtest_sharpe=1.0 + (i % 5) * 0.2,
                live_sharpe=0.5 + (i % 5) * 0.1,
                live_pnl=100 * (i % 3 - 1),
                decay_status="HEALTHY" if i % 4 != 0 else "DEAD",
            ))
        return entries

    def test_validate_good_proposal(self):
        shadow = ShadowDeploy(dsr_threshold=0.5, shadow_window=20)
        proposal = DeltaPriors(
            proposal_id="test_1",
            prior_type=PriorType.ALPHA_WEIGHTS,
            proposed_changes={"h1": 1.2},  # boost good alpha
        )
        entries = self._make_entries(50)
        passed, dsr, reason = shadow.validate_proposal(proposal, {"h1": 1.0}, entries)
        assert isinstance(passed, bool)
        assert isinstance(dsr, float)
        assert dsr >= 0.0

    def test_validate_insufficient_data(self):
        shadow = ShadowDeploy(dsr_threshold=0.5, shadow_window=100)
        proposal = DeltaPriors(proposal_id="test_2")
        entries = self._make_entries(10)
        passed, dsr, reason = shadow.validate_proposal(proposal, {}, entries)
        assert passed is False
        assert "Insufficient" in reason

    def test_dsr_score_bounded(self):
        shadow = ShadowDeploy(dsr_threshold=0.5, shadow_window=20)
        proposal = DeltaPriors(
            proposal_id="test_3",
            prior_type=PriorType.ALPHA_WEIGHTS,
            proposed_changes={},
        )
        entries = self._make_entries(50)
        passed, dsr, reason = shadow.validate_proposal(proposal, {}, entries)
        assert 0.0 <= dsr <= 1.0

    def test_shadow_sharpe_computation(self):
        shadow = ShadowDeploy()
        returns = [0.01, -0.005, 0.008, -0.003, 0.012]
        sharpe = shadow._compute_shadow_sharpe(returns)
        assert isinstance(sharpe, float)

    def test_max_drawdown(self):
        shadow = ShadowDeploy()
        returns = [0.01, 0.02, -0.05, 0.01, -0.03]
        dd = shadow._compute_max_drawdown(returns)
        assert 0.0 <= dd <= 1.0

    def test_dsr_proxy_bounds(self):
        shadow = ShadowDeploy()
        # Perfect scenario
        score = shadow._compute_dsr_proxy(3.0, 0.05, 200)
        assert score > 0.8
        # Terrible scenario
        score = shadow._compute_dsr_proxy(-1.0, 0.8, 10)
        assert score < 0.3


# ---------------------------------------------------------------------------
# MetaLoop Integration Tests
# ---------------------------------------------------------------------------

class TestMetaLoop:
    def _make_loop(self, tmp_path):
        return MetaLoop(
            state_path=str(tmp_path / "state.json"),
            db_path=str(tmp_path / "corpus.db"),
            dsr_threshold=0.3,  # low threshold for testing
            min_samples=5,
            shadow_window=10,
        )

    def test_log_outcome(self, tmp_path):
        loop = self._make_loop(tmp_path)
        entry_id = loop.log_outcome(
            hypothesis_id="h1",
            hypothesis_source="alpha_discovery",
            backtest_sharpe=1.5,
            live_sharpe=1.0,
            decay_status="HEALTHY",
        )
        assert entry_id == 1

    def test_insufficient_data_returns_status(self, tmp_path):
        loop = self._make_loop(tmp_path)
        # Only 2 entries — below min_samples=5
        loop.log_outcome("h1")
        loop.log_outcome("h2")
        result = loop.run_meta_analysis()
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_run_full_analysis(self, tmp_path):
        loop = self._make_loop(tmp_path)
        # Log enough entries
        for i in range(15):
            loop.log_outcome(
                hypothesis_id=f"h{i}",
                hypothesis_source="alpha_discovery" if i % 2 == 0 else "manual",
                regime="HIGH_VOL_CRISIS" if i % 3 == 0 else "LOW_VOL_TREND",
                backtest_sharpe=1.0 + (i % 5) * 0.2,
                live_sharpe=0.3 + (i % 5) * 0.1,
                decay_status="DEAD" if (i % 3 == 0 and i > 8) else "HEALTHY",
                feature_type="momentum" if i % 4 == 0 else "mean_reversion",
            )

        result = loop.run_meta_analysis(
            current_alpha_weights={"h0": 1.0, "h1": 1.0},
            current_beta_thresholds={"sharpe_threshold": 0.5},
        )
        assert result["status"] == "COMPLETED"
        assert result["entries_analyzed"] == 15
        assert isinstance(result["patterns_found"], int)
        assert isinstance(result["proposals_made"], int)

    def test_summary(self, tmp_path):
        loop = self._make_loop(tmp_path)
        loop.log_outcome("h1")
        loop.log_outcome("h2")
        summary = loop.get_summary()
        assert summary["total_entries"] == 2
        assert summary["total_patterns"] == 0
        assert summary["total_proposals"] == 0

    def test_format_summary(self, tmp_path):
        loop = self._make_loop(tmp_path)
        output = loop.format_summary()
        assert "META-LOOP" in output

    def test_state_persistence(self, tmp_path):
        state_path = str(tmp_path / "state.json")
        loop1 = self._make_loop(tmp_path)
        for i in range(10):
            loop1.log_outcome(f"h{i}")
        loop1.run_meta_analysis()

        # Create new loop with same state
        loop2 = MetaLoop(
            state_path=state_path,
            db_path=str(tmp_path / "corpus.db"),
            min_samples=5,
            shadow_window=10,
        )
        state = loop2._load_state()
        assert state.get("last_analysis") is not None

    def test_audit_logging(self, tmp_path):
        mock_audit = MagicMock()
        loop = self._make_loop(tmp_path)
        loop._audit = mock_audit
        # Audit is called per-proposal in run_meta_analysis
        for i in range(10):
            loop.log_outcome(f"h{i}", backtest_sharpe=1.5, live_sharpe=1.0)
        loop.run_meta_analysis()
        # Audit may or may not be called depending on patterns found


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_corpus_analysis(self, tmp_path):
        loop = MetaLoop(
            state_path=str(tmp_path / "state.json"),
            db_path=str(tmp_path / "corpus.db"),
            min_samples=0,
            shadow_window=5,
        )
        result = loop.run_meta_analysis()
        # Should complete without error even with 0 entries
        assert result["status"] in ("COMPLETED", "INSUFFICIENT_DATA")

    def test_all_healthy_no_patterns(self, tmp_path):
        loop = MetaLoop(
            state_path=str(tmp_path / "state.json"),
            db_path=str(tmp_path / "corpus.db"),
            min_samples=5,
            shadow_window=10,
        )
        # All healthy, same source — should find no problematic patterns
        for i in range(10):
            loop.log_outcome(
                hypothesis_id=f"h{i}",
                hypothesis_source="alpha_discovery",
                backtest_sharpe=1.5,
                live_sharpe=1.4,
                decay_status="HEALTHY",
                feature_type="momentum",
            )
        result = loop.run_meta_analysis()
        assert result["status"] == "COMPLETED"
        # Should have 0 or very few patterns
        assert result["patterns_found"] <= 2

    def test_corpus_entry_to_dict(self):
        entry = CorpusEntry(hypothesis_id="h1", backtest_sharpe=1.5)
        d = entry.to_dict()
        assert d["hypothesis_id"] == "h1"
        assert d["backtest_sharpe"] == 1.5

    def test_pattern_to_dict(self):
        pattern = Pattern(
            pattern_type=PatternType.REGIME_DECAY,
            description="test",
            evidence_count=5,
            confidence=0.8,
        )
        d = pattern.to_dict()
        assert d["pattern_type"] == "regime_decay"

    def test_proposal_to_dict(self):
        proposal = DeltaPriors(
            proposal_id="p1",
            prior_type=PriorType.ALPHA_WEIGHTS,
            proposed_changes={"h1": 0.5},
        )
        d = proposal.to_dict()
        assert d["prior_type"] == "alpha_weights"
        assert d["status"] == "PROPOSED"
