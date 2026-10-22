"""End-to-end integration tests for Bounded-Autonomy LLM Agent Pipeline (NZE, 2026).

Verifies:
1. CLI flag `--bounded-autonomy` in `mt5_trader.py`
2. Orchestrator initialization and dynamic wiring
3. Complete execute_cycle integration with RuleProposer S, Boolean Veto V_LLM, and Risk Shield R
4. Proposition 1 Monotone Exposure enforcement during cycle execution
5. Audit log generation in JSONL format
"""
import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch
import pytest

from core.bounded_autonomy_pipeline import (
    BoundedAutonomyPipeline,
    CandidateOrder,
    ExecutedDecision,
    LLMVetoOperator,
    LLMVetoVerdict,
    PortfolioState,
    RiskShield,
    RuleProposer,
    SizingRiskBudget,
    create_bounded_pipeline,
)
from core.trading_safety import TradingStore
from intelligence.llm_orchestrator import LLMOrchestrator


@pytest.fixture
def mock_store(tmp_path):
    db_path = str(tmp_path / "test_state.db")
    return TradingStore(db_path)


@pytest.fixture
def base_orchestrator(mock_store, tmp_path):
    """Create a minimal mock LLMOrchestrator."""
    client = MagicMock()
    client.get_positions.return_value = []
    
    risk = MagicMock()
    risk.balance = 10000.0
    risk.initial_balance = 10000.0
    risk.risk_per_trade = 0.01
    risk.calc_position_size.return_value = 0.1
    
    perf = MagicMock()
    perf.history = []
    perf.calc_rolling_sharpe.return_value = 0.0
    perf.calc_rolling_sortino.return_value = 0.0
    perf.calc_max_drawdown.return_value = 0.0
    
    audit_file = str(tmp_path / "bounded_audit.jsonl")
    
    orch = LLMOrchestrator(
        client=client,
        risk_manager=risk,
        perf_monitor=perf,
        api_key="test_key",
        model_name="test_model",
        journal_mode="PAPER",
        account_fingerprint="test_fp",
        store=mock_store,
        bounded_autonomy=True,
    )
    orch.wire_bounded_autonomy(enabled=True, audit_log_path=audit_file)  # D-001: mock= kwarg removed from API
    # Deterministic test approver (D-001): restores the old mock=True
    # default-approve veto semantics. Individual tests override as needed.
    orch.bounded_pipeline.veto_operator.llm_caller = lambda payload: {
        "approved": True,
        "rationale": "Test deterministic approver.",
        "confidence": 1.0,
    }

    
    # Mock heavy external I/O & scheduler loops
    orch.data_provider.api_manager = None
    orch.data_provider.trade_manager = None
    mock_fetcher = MagicMock()
    mock_fetcher.get_summary.return_value = {
        "nfp_prediction": None,
        "cpi_prediction": None,
        "esi": {"esi_score": 50.0},
        "nfp_is_live": False,
        "cpi_is_live": False,
    }
    orch._live_indicator_fetcher = mock_fetcher
    orch._news_corr_engine = MagicMock()
    orch._run_maintenance_prefix = MagicMock()
    orch._run_schedules_bounded = MagicMock()
    orch._capture_intermarket_proxy_returns = MagicMock(return_value={})
    orch.microstructure = MagicMock()
    orch.microstructure.observe.return_value = {}
    orch.correlation_risk = MagicMock()
    orch.correlation_risk.record_completed_bars.return_value = 0
    orch.correlation_risk.point_in_time_observations.return_value = {}
    orch.correlation_risk.correlated_open_risk.return_value = {"correlated_open_risk": 0.0}
    orch.adaptive_research = MagicMock()
    orch.adaptive_research.update_from_closes = MagicMock(return_value={})
    
    with patch(
        "intelligence.llm_orchestrator.process_cycle_data_sources",
        return_value={
            "status": "PROCESSED",
            "observations": {},
            "macro_entities_extra": [],
        },
    ):
        yield orch, audit_file


def test_cli_argument_parsing():
    """Verify that --bounded-autonomy flag is present in mt5_trader.py argument parser."""
    import inspect
    from mt5_trader import main
    src = inspect.getsource(main)
    assert "--bounded-autonomy" in src
    assert "Enable Bounded-Autonomy LLM Agent Pipeline" in src


def test_orchestrator_initialization_and_wiring(mock_store, tmp_path):
    """Verify LLMOrchestrator initialization and wire_bounded_autonomy toggling."""
    client = MagicMock()
    risk = MagicMock()
    risk.balance = 10000.0
    risk.risk_per_trade = 0.01
    perf = MagicMock()
    
    audit_file = str(tmp_path / "audit.jsonl")
    
    # 1. Initialized with bounded_autonomy=True
    orch = LLMOrchestrator(
        client=client,
        risk_manager=risk,
        perf_monitor=perf,
        api_key="test_key",
        store=mock_store,
        bounded_autonomy=True,
    )
    orch.wire_bounded_autonomy(enabled=True, audit_log_path=audit_file)  # D-001 fix: mock= kwarg removed from API
    assert orch.enable_bounded_autonomy is True
    assert orch.bounded_pipeline is not None
    assert isinstance(orch.bounded_pipeline, BoundedAutonomyPipeline)
    
    # 2. Toggle off
    orch.wire_bounded_autonomy(enabled=False)
    assert orch.enable_bounded_autonomy is False
    assert orch.bounded_pipeline is None


def test_bounded_autonomy_execute_cycle_hold(base_orchestrator):
    """Verify that flat prices result in a deterministic HOLD proposal and audit logging."""
    orch, audit_file = base_orchestrator
    
    # Construct mock TOON with flat price series (no trend)
    flat_prices = [100.0] * 50
    mock_toon = {
        "ohlc": [{"close": p, "open": p, "high": p, "low": p} for p in flat_prices],
        "dom": {"best_bid": 100.0, "best_ask": 100.05},
        "indicators": {"atr_14": 0.0010},
        "account": {"balance": 10000.0, "equity": 10000.0},
        "news": [],
        "journal_context": {},
    }
    
    orch.data_provider.fetch_data = MagicMock(return_value=mock_toon)
    orch._completed_candle_candidate = MagicMock(return_value="candle_1")
    orch.operations.claim_task = MagicMock(return_value=True)
    orch.operations.heartbeat = MagicMock()
    orch.operations.complete_task = MagicMock()
    
    decision, vol = orch.execute_cycle(
        symbol="EURUSD",
        timeframe="M15",
        cycle_num=1,
        total_cycles=1,
        paper_mode=True,
    )
    
    assert decision["action"] == "HOLD"
    assert vol == 0.0
    assert "[BoundedAutonomy]" in decision["reasoning"]
    assert "bounded_autonomy" in decision
    
    # Verify structured audit log entry was recorded
    assert os.path.exists(audit_file)
    with open(audit_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["symbol"] == "EURUSD"
        assert record["proposer"]["action"] == "HOLD"


def test_bounded_autonomy_execute_cycle_veto(base_orchestrator):
    """Verify that an active proposal is clamped to HOLD when the LLM veto rejects it."""
    orch, audit_file = base_orchestrator
    
    # Configure veto to reject
    orch.bounded_pipeline.veto_operator.llm_caller = lambda payload: {
        "approved": False,
        "rationale": "High-impact macro event impending, vetoing trade.",
        "confidence": 0.99,
    }
    
    # Force proposer to return an active BUY candidate
    prices = [100.0 + i * 0.5 for i in range(50)]
    mock_toon = {
        "ohlc": [{"close": p, "open": p, "high": p, "low": p} for p in prices],
        "dom": {"best_bid": 125.0, "best_ask": 125.05},
        "indicators": {"atr_14": 0.50},
        "account": {"balance": 10000.0, "equity": 10000.0},
        "news": [],
        "journal_context": {},
    }
    
    orch.data_provider.fetch_data = MagicMock(return_value=mock_toon)
    orch._completed_candle_candidate = MagicMock(return_value="candle_2")
    orch.operations.claim_task = MagicMock(return_value=True)
    orch.operations.heartbeat = MagicMock()
    orch.operations.complete_task = MagicMock()
    
    decision, vol = orch.execute_cycle(
        symbol="EURUSD",
        timeframe="M15",
        cycle_num=2,
        total_cycles=2,
        paper_mode=True,
    )
    
    assert decision["action"] == "HOLD"
    assert vol == 0.0
    assert "Vetoed" in decision["reasoning"]
    assert "High-impact macro event impending" in decision["reasoning"]


def test_bounded_autonomy_monotone_exposure_cap(base_orchestrator):
    """Verify Proposition 1: Monotone Exposure guarantee caps safe_vol to candidate size."""
    orch, audit_file = base_orchestrator
    
    # Proposer produces a candidate with quantity 0.05
    orch.bounded_pipeline.proposer.default_quantity = 0.05
    orch.bounded_pipeline.risk_shield.budget.contract_sizes = {"EURUSD": 1.0}
    
    # Veto approves
    orch.bounded_pipeline.veto_operator.llm_caller = lambda payload: {
        "approved": True,
        "rationale": "Clear technical trend breakout confirmed.",
        "confidence": 0.95,
    }
    
    # Risk manager attempts to calculate a large lot size (0.50)
    orch.risk.calc_position_size.return_value = 0.50
    
    prices = [100.0 + i * 0.5 for i in range(50)]
    mock_toon = {
        "ohlc": [{"close": p, "open": p, "high": p, "low": p} for p in prices],
        "dom": {"best_bid": 125.0, "best_ask": 125.05},
        "indicators": {"atr_14": 0.50, "alpha_ensemble": {}},
        "account": {"balance": 10000.0, "equity": 10000.0},
        "news": [],
        "journal_context": {},
    }
    
    orch.data_provider.fetch_data = MagicMock(return_value=mock_toon)
    orch._completed_candle_candidate = MagicMock(return_value="candle_3")
    orch.operations.claim_task = MagicMock(return_value=True)
    orch.operations.heartbeat = MagicMock()
    orch.operations.complete_task = MagicMock()
    
    # Mock proposal validation and risk gates so execution proceeds to sizing cap
    mock_proposal = MagicMock()
    mock_proposal.magic = 10101
    mock_proposal.setup_key.return_value = "EURUSD:M15:test"
    mock_proposal.strategy = "test"
    mock_val = MagicMock()
    mock_val.approved = True
    mock_val.reasons = []
    orch._validate_proposal = MagicMock(return_value=(mock_proposal, mock_val))
    orch.risk.check_cooldown.return_value = True
    orch.risk.check_rate_limit.return_value = True
    orch.risk.check_active_trade_limit.return_value = True
    orch.arbiter.permit = MagicMock(return_value=(True, "approved"))
    
    # Mock order execution components
    mock_plan = MagicMock()
    mock_plan.order_type = "BUY_LIMIT"
    mock_plan.entry = 125.0
    mock_plan.expires_at = "2026-09-10T18:00:00"
    mock_plan.as_dict.return_value = {}
    orch._build_execution_plan = MagicMock(return_value=(mock_plan, {}))
    orch.data_provider.place_pending_order = MagicMock(return_value={"order": 12345})
    
    decision, vol = orch.execute_cycle(
        symbol="EURUSD",
        timeframe="M15",
        cycle_num=3,
        total_cycles=3,
        paper_mode=True,
    )
    
    # Candidate requested 0.05; RiskManager returned 0.50.
    # The Monotone Exposure cap must strictly force volume <= 0.05!
    call_args = orch.data_provider.place_pending_order.call_args
    assert call_args is not None
    placed_volume = call_args.kwargs.get("volume")
    assert placed_volume == 0.05
    assert placed_volume <= orch.bounded_pipeline.proposer.default_quantity
