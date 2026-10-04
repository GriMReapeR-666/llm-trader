"""Wire check: macro continuation rejected by opportunity fusion in _validate_proposal."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mt5_trader import LLMOrchestrator
from intelligence.opportunity_fusion import score_opportunity


def _make_passing_overfit_gate():
    """Return an OverfitGate mock that always passes check_strategy."""
    gate = MagicMock()
    gate.check_strategy.return_value = SimpleNamespace(
        strategy="swing", status="PASS", risk_level="LOW",
        grade="A", reason="test pass", checked_at=0,
    )
    return gate


def test_validate_proposal_blocks_macro_without_causal(tmp_path):
    risk = MagicMock()
    risk.risk_per_trade = 0.01
    with patch("mt5_trader.TradingStore") as store_cls:
        store = MagicMock()
        store.path = str(tmp_path / "v.db")
        store_cls.return_value = store
        orch = LLMOrchestrator(
            MagicMock(), risk, MagicMock(), "api_key", enable_rl=False,
            journal_mode="PAPER",
        )
    orch.store = MagicMock()
    orch.operations = MagicMock()
    orch.run_id = "run"
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])

    # Force classify_setup to return macro continuation eligible.
    decision = {
        "action": "BUY",
        "entry": 1.10,
        "stop_loss": 1.09,
        "take_profit": 1.13,
        "confidence": 0.8,
    }
    toon = {
        "indicators": {"ema_50": [1.0, 1.1], "adx_14": [20, 25], "atr_14": [0.001, 0.001]},
        "feature_snapshot": {"ready": False, "values": {}},
        "multi_timeframe": {},
        "technical_analysis": {"key_levels": [], "primary_trend": "BULLISH"},
        "ohlc": [{"close": 1.09}, {"close": 1.10}],
        "dom": {},
        "news": [],
        "context": {"calendar_source": "mt5_calendar"},
        "volume_analysis": {"volume_ratio": 1.2},
        "microstructure": {"allowed": True, "flags": []},
        "macro_causal_context_regime": [],  # no edges -> BLOCK for macro
        "causal_setup_proposals": [],
    }

    orch.overfit_gate = _make_passing_overfit_gate()

    with patch("mt5_trader.classify_setup") as classify:
        classify.return_value = {
            "setup_id": "macro_continuation_v1",
            "eligible": True,
            "reason": "matched",
            "spec": {"min_rr": 2.5, "event_exclusion": True, "version": "1"},
        }
        with patch("mt5_trader.mt5", None):
            with patch("mt5_trader.evaluate_data_requirements") as req:
                req.return_value = {"available": True, "reasons": []}
                with patch("mt5_trader.evaluate_market_conditions") as cond:
                    cond.return_value = MagicMock(allowed=True, reasons=[])
                    proposal, result = orch._validate_proposal(
                        decision, toon, "EURUSD", "swing", paper_mode=True,
                    )

    assert proposal is not None
    assert result.approved is False
    assert any("opportunity fusion" in r for r in result.reasons)
    assert toon["opportunity_fusion"]["status"] == "BLOCK"
    assert toon["opportunity_fusion"]["execution_authority"] is False


def test_fusion_helper_matches_gate_expectation():
    # Sanity: same helper used by the gate.
    blocked = score_opportunity(
        setup_id="macro_continuation_v1", side="BUY", symbol="EURUSD",
        event_state={"blackout": False, "reasons": []}, causal_edges=[],
        ta_eligible=True,
    )
    assert blocked["status"] == "BLOCK"


def test_validate_proposal_passes_macro_when_propagation_agrees(tmp_path):
    risk = MagicMock()
    risk.risk_per_trade = 0.01
    with patch("mt5_trader.TradingStore") as store_cls:
        store = MagicMock()
        store.path = str(tmp_path / "v.db")
        store_cls.return_value = store
        orch = LLMOrchestrator(
            MagicMock(), risk, MagicMock(), "api_key", enable_rl=False,
            journal_mode="PAPER",
        )
    orch.store = MagicMock()
    orch.operations = MagicMock()
    orch.run_id = "run"
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
    orch.overfit_gate = _make_passing_overfit_gate()

    decision = {
        "action": "BUY",
        "entry": 1.10,
        "stop_loss": 1.09,
        "take_profit": 1.13,
        "confidence": 0.8,
    }
    toon = {
        "indicators": {"ema_50": [1.0, 1.1], "adx_14": [20, 25], "atr_14": [0.001, 0.001]},
        "feature_snapshot": {"ready": False, "values": {}},
        "multi_timeframe": {},
        "technical_analysis": {"key_levels": [], "primary_trend": "BULLISH"},
        "ohlc": [{"close": 1.09}, {"close": 1.10}],
        "dom": {},
        "news": [],
        "context": {"calendar_source": "mt5_calendar"},
        "volume_analysis": {"volume_ratio": 1.2},
        "microstructure": {"allowed": True, "flags": []},
        "macro_causal_context_regime": [],
        "causal_setup_proposals": [],
        "causal_propagation": {
            "status": "OK",
            "path_id": "cpath:gateprop",
            "bias": "BUY",
            "score": 0.55,
            "hops": [{"edge_id": 1}, {"edge_id": 2}],
            "execution_authority": False,
        },
    }

    with patch("mt5_trader.classify_setup") as classify:
        classify.return_value = {
            "setup_id": "macro_continuation_v1",
            "eligible": True,
            "reason": "matched",
            "spec": {"min_rr": 2.5, "event_exclusion": True, "version": "1"},
        }
        with patch("mt5_trader.mt5", None):
            with patch("mt5_trader.evaluate_data_requirements") as req:
                req.return_value = {"available": True, "reasons": []}
                with patch("mt5_trader.evaluate_market_conditions") as cond:
                    cond.return_value = MagicMock(allowed=True, reasons=[])
                    # May still fail later validation (clock/tick); we only assert
                    # fusion did not BLOCK and recorded propagation agree.
                    proposal, result = orch._validate_proposal(
                        decision, toon, "EURUSD", "swing", paper_mode=True,
                    )

    assert proposal is not None
    # Fusion may or may not be set depending on how far validation gets
    if "opportunity_fusion" in toon:
        fusion = toon["opportunity_fusion"]
        assert fusion["status"] != "BLOCK"
        assert fusion["propagation_agree"] is True
        assert fusion["causal_path_id"] == "cpath:gateprop"
        assert fusion["execution_authority"] is False
    # If rejected, reason must not be opportunity fusion.
    if not result.approved:
        assert not any("opportunity fusion" in r for r in (result.reasons or []))
