"""Comprehensive Tests for Autonomous Systems

Tests all new components:
- Memory Orchestrator
- Knowledge Retrieval
- Meta-Improvement Orchestrator
- Kronos Signal Fusion
- Advanced RL Pipeline
- Metrics Persistence
- Alert Rules
- Degradation Detector
- Auto Promotion
- Prometheus Exporter

Usage:
    pytest tests/test_autonomous_systems.py -v
    pytest tests/test_autonomous_systems.py -v -k "memory"
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directories to path
_root = str(Path(__file__).parent.parent)
if _root not in os.sys.path:
    os.sys.path.insert(0, _root)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# ============================================================================
# MEMORY ORCHESTRATOR TESTS
# ============================================================================

class TestMemoryOrchestrator:
    """Tests for the unified memory orchestrator."""

    def test_init_creates_tables(self, temp_db):
        """Test that initialization creates required tables."""
        from operations.memory_orchestrator import MemoryOrchestrator
        orch = MemoryOrchestrator(db_path=temp_db)

        con = sqlite3.connect(temp_db)
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con.close()

        table_names = [t[0] for t in tables]
        assert "unified_memory" in table_names

    def test_store_and_retrieve(self, temp_db):
        """Test storing and retrieving memories."""
        from operations.memory_orchestrator import MemoryOrchestrator
        orch = MemoryOrchestrator(db_path=temp_db)

        # Store a memory
        memory_id = orch.store(
            layer="working",
            content={"trade": "EURUSD", "outcome": "win"},
            memory_type="trade_result",
            importance=0.8,
        )
        assert memory_id > 0

        # Retrieve it
        results = orch.retrieve("EURUSD", layers=["working"])
        assert len(results) > 0
        assert results[0]["content"]["trade"] == "EURUSD"

    def test_consolidation(self, temp_db):
        """Test memory consolidation from working to episodic."""
        from operations.memory_orchestrator import MemoryOrchestrator
        orch = MemoryOrchestrator(db_path=temp_db)

        # Store a high-importance memory
        orch.store(
            layer="working",
            content={"key": "value"},
            importance=0.8,
        )

        # Run consolidation
        result = orch.consolidate()
        assert "working_to_episodic" in result

    def test_decay(self, temp_db):
        """Test memory decay."""
        from operations.memory_orchestrator import MemoryOrchestrator
        orch = MemoryOrchestrator(db_path=temp_db)

        # Store a memory with short TTL
        orch.store(
            layer="working",
            content={"key": "value"},
            importance=0.2,
            ttl_hours=0,  # Immediate expiry
        )

        # Run decay
        result = orch.decay()
        assert "working" in result

    def test_stats(self, temp_db):
        """Test statistics collection."""
        from operations.memory_orchestrator import MemoryOrchestrator
        orch = MemoryOrchestrator(db_path=temp_db)

        stats = orch.get_stats()
        assert "working" in stats
        assert "episodic" in stats
        assert "semantic" in stats
        assert "core" in stats
        assert "total" in stats


# ============================================================================
# KNOWLEDGE RETRIEVAL TESTS
# ============================================================================

class TestKnowledgeRetrieval:
    """Tests for the unified knowledge retrieval API."""

    def test_init(self, temp_db):
        """Test initialization."""
        from intelligence.knowledge_retrieval import KnowledgeRetrieval
        kb = KnowledgeRetrieval(db_path=temp_db)
        assert kb is not None

    def test_query(self, temp_db):
        """Test querying knowledge sources."""
        from intelligence.knowledge_retrieval import KnowledgeRetrieval
        kb = KnowledgeRetrieval(db_path=temp_db)

        result = kb.query("OPEC oil supply")
        assert "query" in result
        assert "results" in result
        assert "sources_queried" in result

    def test_entity_extraction(self, temp_db):
        """Test entity extraction from queries."""
        from intelligence.knowledge_retrieval import KnowledgeRetrieval
        kb = KnowledgeRetrieval(db_path=temp_db)

        entities = kb._extract_entities("EURUSD and GBPUSD are trending")
        assert "EURUSD" in entities
        assert "GBPUSD" in entities

    def test_domain_extraction(self, temp_db):
        """Test domain extraction from queries."""
        from intelligence.knowledge_retrieval import KnowledgeRetrieval
        kb = KnowledgeRetrieval(db_path=temp_db)

        assert kb._extract_domain("OPEC oil production cut") == "ENERGY"
        assert kb._extract_domain("semiconductor shortage") == "TECHNOLOGY"
        assert kb._extract_domain("war in Ukraine") == "GEOPOLITICS"

    def test_stats(self, temp_db):
        """Test statistics."""
        from intelligence.knowledge_retrieval import KnowledgeRetrieval
        kb = KnowledgeRetrieval(db_path=temp_db)

        stats = kb.get_stats()
        assert "total_queries" in stats


# ============================================================================
# META-IMPROVEMENT ORCHESTRATOR TESTS
# ============================================================================

class TestMetaImprovementOrchestrator:
    """Tests for the meta-improvement orchestrator."""

    def test_init(self, temp_db):
        """Test initialization."""
        from operations.meta_improvement_orchestrator import MetaImprovementOrchestrator
        orch = MetaImprovementOrchestrator(db_path=temp_db)
        assert orch is not None

    def test_health_check(self, temp_db):
        """Test health check."""
        from operations.meta_improvement_orchestrator import MetaImprovementOrchestrator
        orch = MetaImprovementOrchestrator(db_path=temp_db)

        health = orch.health_check()
        assert "timestamp" in health
        assert "subsystems" in health
        assert "overall_status" in health

    def test_stats(self, temp_db):
        """Test statistics."""
        from operations.meta_improvement_orchestrator import MetaImprovementOrchestrator
        orch = MetaImprovementOrchestrator(db_path=temp_db)

        stats = orch.get_stats()
        assert "total_cycles" in stats
        assert "improvements_applied" in stats


# ============================================================================
# KRONOS SIGNAL FUSION TESTS
# ============================================================================

class TestKronosSignalFusion:
    """Tests for the Kronos signal fusion engine."""

    def test_init(self, temp_dir):
        """Test initialization."""
        from research.kronos_signal_fusion import KronosSignalFusion
        fusion = KronosSignalFusion(base_dir=temp_dir)
        assert fusion is not None

    def test_fuse_predictions(self, temp_dir):
        """Test prediction fusion."""
        from research.kronos_signal_fusion import KronosSignalFusion
        fusion = KronosSignalFusion(base_dir=temp_dir)

        predictions = {
            "M15": {"direction": 0.5, "magnitude": 0.3, "confidence": 0.7, "timestamp": time.time()},
            "H1": {"direction": 0.6, "magnitude": 0.4, "confidence": 0.8, "timestamp": time.time()},
            "H4": {"direction": 0.4, "magnitude": 0.5, "confidence": 0.6, "timestamp": time.time()},
        }

        result = fusion._fuse_predictions(predictions, regime="NORMAL")
        assert "action" in result
        assert "direction" in result
        assert "confidence" in result
        assert result["action"] in ["BUY", "SELL", "HOLD"]

    def test_regime_weights(self, temp_dir):
        """Test regime-specific weighting."""
        from research.kronos_signal_fusion import KronosSignalFusion
        fusion = KronosSignalFusion(base_dir=temp_dir)

        assert "LOW_VOL_TREND" in fusion.REGIME_WEIGHTS
        assert "HIGH_VOL_CRISIS" in fusion.REGIME_WEIGHTS
        assert "direction" in fusion.REGIME_WEIGHTS["NORMAL"]

    def test_default_signal(self, temp_dir):
        """Test default signal generation."""
        from research.kronos_signal_fusion import KronosSignalFusion
        fusion = KronosSignalFusion(base_dir=temp_dir)

        signal = fusion._default_signal("EURUSD", "test")
        assert signal["action"] == "HOLD"
        assert signal["direction"] == 0.0
        assert signal["confidence"] == 0.0

    def test_stats(self, temp_dir):
        """Test statistics."""
        from research.kronos_signal_fusion import KronosSignalFusion
        fusion = KronosSignalFusion(base_dir=temp_dir)

        stats = fusion.get_stats()
        assert "total_fusions" in stats
        assert "regime_weights" in stats


# ============================================================================
# ADVANCED RL PIPELINE TESTS
# ============================================================================

class TestAdvancedRLPipeline:
    """Tests for the advanced RL training pipeline."""

    def test_init(self, temp_dir):
        """Test initialization."""
        from research.advanced_rl_pipeline import AdvancedRLPipeline
        pipeline = AdvancedRLPipeline(base_dir=temp_dir)
        assert pipeline is not None

    def test_agent_profiles(self):
        """Test agent profiles are defined."""
        from research.advanced_rl_pipeline import AGENT_PROFILES
        assert "aggressive" in AGENT_PROFILES
        assert "conservative" in AGENT_PROFILES
        assert "balanced" in AGENT_PROFILES

    def test_agent_profile_to_dict(self):
        """Test agent profile serialization."""
        from research.advanced_rl_pipeline import AgentProfile
        profile = AgentProfile("test", risk_tolerance=0.5)
        d = profile.to_dict()
        assert d["name"] == "test"
        assert d["risk_tolerance"] == 0.5

    def test_stats(self, temp_dir):
        """Test statistics."""
        from research.advanced_rl_pipeline import AdvancedRLPipeline
        pipeline = AdvancedRLPipeline(base_dir=temp_dir)

        stats = pipeline.get_stats()
        assert "total_episodes" in stats
        assert "hyperparams" in stats


# ============================================================================
# METRICS PERSISTENCE TESTS
# ============================================================================

class TestMetricsPersistence:
    """Tests for the metrics persistence store."""

    def test_init_creates_tables(self, temp_db):
        """Test that initialization creates required tables."""
        from operations.metrics_persistence import MetricsStore
        store = MetricsStore(db_path=temp_db)

        con = sqlite3.connect(temp_db)
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con.close()

        table_names = [t[0] for t in tables]
        assert "metrics_trades" in table_names
        assert "metrics_latency" in table_names
        assert "metrics_costs" in table_names
        assert "metrics_equity" in table_names

    def test_record_trade(self, temp_db):
        """Test recording a trade."""
        from operations.metrics_persistence import MetricsStore
        store = MetricsStore(db_path=temp_db)

        store.record_trade({
            "ticket": 12345,
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "exit_price": 1.1050,
            "gross_pnl": 50.0,
            "risk_amount": 100.0,
        })

        # Verify
        con = sqlite3.connect(temp_db)
        row = con.execute("SELECT * FROM metrics_trades WHERE ticket=12345").fetchone()
        con.close()
        assert row is not None

    def test_record_latency(self, temp_db):
        """Test recording latency."""
        from operations.metrics_persistence import MetricsStore
        store = MetricsStore(db_path=temp_db)

        store.record_latency("llm_call", "complete", 250.0)

        con = sqlite3.connect(temp_db)
        row = con.execute("SELECT * FROM metrics_latency WHERE component='llm_call'").fetchone()
        con.close()
        assert row is not None

    def test_record_equity(self, temp_db):
        """Test recording equity snapshot."""
        from operations.metrics_persistence import MetricsStore
        store = MetricsStore(db_path=temp_db)

        store.record_equity(100000, 101000, 0.02, 0.01)

        con = sqlite3.connect(temp_db)
        row = con.execute("SELECT * FROM metrics_equity ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        assert row is not None

    def test_rolling_metrics(self, temp_db):
        """Test rolling metrics computation."""
        from operations.metrics_persistence import MetricsStore
        store = MetricsStore(db_path=temp_db)

        # Record some trades
        for i in range(10):
            store.record_trade({
                "ticket": i,
                "symbol": "EURUSD",
                "side": "BUY" if i % 2 == 0 else "SELL",
                "gross_pnl": 10.0 if i % 2 == 0 else -5.0,
                "risk_amount": 100.0,
            })

        metrics = store.get_rolling_metrics(window=10)
        assert "trade_count" in metrics
        assert "win_rate" in metrics
        assert "avg_r" in metrics


# ============================================================================
# ALERT RULES TESTS
# ============================================================================

class TestAlertRules:
    """Tests for the alert rules engine."""

    def test_init(self, temp_dir):
        """Test initialization."""
        from operations.alert_rules import AlertRulesEngine
        engine = AlertRulesEngine(base_dir=temp_dir)
        assert engine is not None

    def test_default_rules_loaded(self, temp_dir):
        """Test that default rules are loaded."""
        from operations.alert_rules import AlertRulesEngine
        engine = AlertRulesEngine(base_dir=temp_dir)

        rules = engine.get_rules()
        assert len(rules) > 0
        assert any(r["name"] == "drawdown_warning" for r in rules)

    def test_evaluate_triggers_alert(self, temp_dir):
        """Test that evaluation triggers alerts."""
        from operations.alert_rules import AlertRulesEngine
        engine = AlertRulesEngine(base_dir=temp_dir)

        # Trigger drawdown warning (3%)
        metrics = {"drawdown_pct": 0.04}
        alerts = engine.evaluate(metrics)
        assert len(alerts) > 0
        assert alerts[0]["severity"] == "WARNING"

    def test_evaluate_no_trigger(self, temp_dir):
        """Test that normal metrics don't trigger alerts."""
        from operations.alert_rules import AlertRulesEngine
        engine = AlertRulesEngine(base_dir=temp_dir)

        metrics = {"drawdown_pct": 0.01}
        alerts = engine.evaluate(metrics)
        assert len(alerts) == 0

    def test_stats(self, temp_dir):
        """Test statistics."""
        from operations.alert_rules import AlertRulesEngine
        engine = AlertRulesEngine(base_dir=temp_dir)

        stats = engine.get_stats()
        assert "total_rules" in stats
        assert "total_alerts_fired" in stats


# ============================================================================
# DEGRADATION DETECTOR TESTS
# ============================================================================

class TestDegradationDetector:
    """Tests for the degradation detector."""

    def test_init(self, temp_dir):
        """Test initialization."""
        from operations.degradation_detector import DegradationDetector
        detector = DegradationDetector(base_dir=temp_dir)
        assert detector is not None

    def test_check_normal(self, temp_dir):
        """Test normal metrics don't trigger action."""
        from operations.degradation_detector import DegradationDetector
        detector = DegradationDetector(base_dir=temp_dir)

        metrics = {
            "drawdown_pct": 0.01,
            "consecutive_losses": 1,
            "rolling_win_rate": 0.5,
            "rolling_expectancy_r": 0.5,
            "drawdown_duration_hours": 5,
            "overall_divergence": 0.1,
            "equity": 100000,
        }

        action = detector.check(metrics)
        assert action is None

    def test_check_high_drawdown(self, temp_dir):
        """Test high drawdown triggers action."""
        from operations.degradation_detector import DegradationDetector
        detector = DegradationDetector(base_dir=temp_dir)

        metrics = {
            "drawdown_pct": 0.07,  # 7% > 6% threshold
            "consecutive_losses": 1,
            "rolling_win_rate": 0.5,
            "rolling_expectancy_r": 0.5,
            "drawdown_duration_hours": 5,
            "overall_divergence": 0.1,
            "equity": 93000,
        }

        action = detector.check(metrics)
        assert action is not None
        assert action["action"] == "PAUSE_NEW"

    def test_check_consecutive_losses(self, temp_dir):
        """Test consecutive losses trigger action."""
        from operations.degradation_detector import DegradationDetector
        detector = DegradationDetector(base_dir=temp_dir)

        metrics = {
            "drawdown_pct": 0.02,
            "consecutive_losses": 6,  # > 5 threshold
            "rolling_win_rate": 0.5,
            "rolling_expectancy_r": 0.5,
            "drawdown_duration_hours": 5,
            "overall_divergence": 0.1,
            "equity": 98000,
        }

        action = detector.check(metrics)
        assert action is not None
        assert action["action"] == "REDUCE"

    def test_stats(self, temp_dir):
        """Test statistics."""
        from operations.degradation_detector import DegradationDetector
        detector = DegradationDetector(base_dir=temp_dir)

        status = detector.get_status()
        assert "current_action" in status
        assert "is_frozen" in status


# ============================================================================
# AUTO PROMOTION TESTS
# ============================================================================

class TestAutoPromotion:
    """Tests for the auto-promotion system."""

    def test_init(self, temp_db):
        """Test initialization."""
        from research.auto_promotion import AutoPromoter
        promoter = AutoPromoter(base_dir=str(Path(temp_db).parent))
        assert promoter is not None

    def test_get_allowed_transitions(self, temp_db):
        """Test allowed transitions."""
        from research.auto_promotion import AutoPromoter
        promoter = AutoPromoter(base_dir=str(Path(temp_db).parent))

        transitions = promoter._get_allowed_transitions("CANDIDATE")
        assert "VALIDATING" in transitions

    def test_get_next_status(self, temp_db):
        """Test next status computation."""
        from research.auto_promotion import AutoPromoter
        promoter = AutoPromoter(base_dir=str(Path(temp_db).parent))

        assert promoter._get_next_status("CANDIDATE") == "VALIDATING"
        assert promoter._get_next_status("VALIDATING") == "PAPER_PROVEN"
        assert promoter._get_next_status("DEPLOYED") is None

    def test_stats(self, temp_db):
        """Test statistics."""
        from research.auto_promotion import AutoPromoter
        promoter = AutoPromoter(base_dir=str(Path(temp_db).parent))

        status = promoter.get_status()
        assert "last_check" in status
        assert "total_promotions" in status


# ============================================================================
# PROMETHEUS EXPORTER TESTS
# ============================================================================

class TestPrometheusExporter:
    """Tests for the Prometheus metrics exporter."""

    def test_collect_metrics(self, temp_db):
        """Test metrics collection."""
        from operations.prometheus_exporter import _collect_metrics
        # This will use the default DB which may not exist
        # Just verify it doesn't crash
        metrics = _collect_metrics()
        assert isinstance(metrics, dict)

    def test_format_prometheus(self):
        """Test Prometheus format output."""
        from operations.prometheus_exporter import _format_prometheus

        metrics = {
            "trades_total": 100,
            "trades_win_rate": 0.55,
            "trades_total_pnl": 1500.0,
            "account_balance": 101500.0,
        }

        output = _format_prometheus(metrics)
        assert "trades_total 100" in output
        assert "trades_win_rate 0.5500" in output
        assert "# HELP" in output
        assert "# TYPE" in output


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """Integration tests for the full autonomous system."""

    def test_full_pipeline_flow(self, temp_db, temp_dir):
        """Test the full pipeline from memory to action."""
        from operations.memory_orchestrator import MemoryOrchestrator
        from operations.metrics_persistence import MetricsStore
        from operations.alert_rules import AlertRulesEngine

        # Initialize all components
        mem_orch = MemoryOrchestrator(db_path=temp_db)
        metrics = MetricsStore(db_path=temp_db)
        alerts = AlertRulesEngine(base_dir=temp_dir)

        # Store a memory
        mem_orch.store(
            layer="working",
            content={"trade": "EURUSD", "outcome": "win", "lesson": "trend following worked"},
            importance=0.8,
        )

        # Record metrics
        metrics.record_trade({
            "ticket": 1,
            "symbol": "EURUSD",
            "side": "BUY",
            "gross_pnl": 50.0,
            "risk_amount": 100.0,
        })
        metrics.record_equity(100000, 100050, 0.01, 0.01)

        # Check alerts
        alert_metrics = {"drawdown_pct": 0.01}
        alert_result = alerts.evaluate(alert_metrics)

        # Verify everything worked
        memories = mem_orch.retrieve("EURUSD")
        assert len(memories) > 0

        rolling = metrics.get_rolling_metrics()
        assert rolling["trade_count"] > 0

    def test_memory_to_knowledge_flow(self, temp_db):
        """Test flow from memory storage to knowledge retrieval."""
        from operations.memory_orchestrator import MemoryOrchestrator

        orch = MemoryOrchestrator(db_path=temp_db)

        # Store knowledge
        orch.store(
            layer="semantic",
            content={
                "topic": "OPEC supply shock",
                "implication": "EURUSD typically drops 200 pips",
                "confidence": 0.7,
            },
            memory_type="knowledge",
            importance=0.9,
            tags=["opec", "supply", "eurusd"],
        )

        # Retrieve it
        results = orch.search("OPEC supply EURUSD")
        assert len(results) > 0
        assert results[0]["content"]["topic"] == "OPEC supply shock"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
