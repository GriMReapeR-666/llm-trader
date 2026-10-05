import math
import pytest
from core.account_sizing import AccountSizingEngine
from core.risk_manager import RiskManager


class TestRiskManagementHardening:
    def test_calculate_atr_volatility_scalar(self):
        rm = RiskManager(balance=10000.0, state_file=None)

        # 1. Normalizing: baseline == current -> 1.0
        assert rm.calculate_atr_volatility_scalar(current_atr=0.0010, baseline_atr=0.0010) == 1.0

        # 2. Surging volatility: current is 2x baseline -> 0.50
        assert rm.calculate_atr_volatility_scalar(current_atr=0.0020, baseline_atr=0.0010) == 0.50

        # 3. Compressed volatility: current is 0.5x baseline -> 1.75 (clamped)
        assert rm.calculate_atr_volatility_scalar(current_atr=0.0005, baseline_atr=0.0010) == 1.75

        # 4. Clamping bounds [0.25, 1.75]
        assert rm.calculate_atr_volatility_scalar(current_atr=0.0100, baseline_atr=0.0010) == 0.25

        # 5. Degenerate inputs fail safely to 1.0
        assert rm.calculate_atr_volatility_scalar(None, 0.0010) == 1.0
        assert rm.calculate_atr_volatility_scalar(0.0010, None) == 1.0
        assert rm.calculate_atr_volatility_scalar(0.0, 0.0010) == 1.0
        assert rm.calculate_atr_volatility_scalar(float("nan"), 0.0010) == 1.0
        assert rm.calculate_atr_volatility_scalar(0.0010, float("inf")) == 1.0

    def test_account_sizing_engine_with_atr_scaling(self):
        engine = AccountSizingEngine(risk_per_trade=0.01, min_lot_size=0.01, tick_size=0.0001, tick_value=10.0)

        # Baseline sizing without ATR
        res_base = engine.calculate_position_size(
            account_equity=10000.0,
            entry_price=1.1000,
            stop_loss_price=1.0950, # 50 pips stop = $500 loss per lot. 1R = $100 -> 0.20 lots
        )
        assert res_base.volatility_scalar == 1.0
        assert res_base.position_size_lots == 0.20

        # Sizing with high volatility (current ATR is double baseline)
        res_high_vol = engine.calculate_position_size(
            account_equity=10000.0,
            entry_price=1.1000,
            stop_loss_price=1.0950,
            current_atr=0.0020,
            baseline_atr=0.0010,
        )
        assert res_high_vol.volatility_scalar == 0.50
        assert res_high_vol.position_size_lots == 0.10 # Scaled down by 50%

        # Sizing with low volatility (current ATR is 0.7x baseline)
        res_low_vol = engine.calculate_position_size(
            account_equity=10000.0,
            entry_price=1.1000,
            stop_loss_price=1.0950,
            current_atr=0.0007,
            baseline_atr=0.0010,
        )
        assert round(res_low_vol.volatility_scalar, 2) == 1.43
        assert res_low_vol.position_size_lots > 0.20

    def test_calculate_parametric_cvar(self):
        rm = RiskManager(balance=10000.0, state_file=None)

        # 1. Insufficient samples (< 15)
        rm.trade_r_history = [1.2, -0.8, 2.0]
        cvar_fallback = rm.calculate_parametric_cvar()
        assert cvar_fallback["available"] is False
        assert cvar_fallback["method"] == "insufficient_samples_fallback"

        # 2. Sufficient samples (20 trade R samples)
        # Mix of wins and losses
        rm.trade_r_history = [
            1.5, 2.0, -1.0, 1.2, -0.9, -1.0, 1.8, -1.0, 2.5, -0.5,
            1.1, -1.0, 1.3, -0.8, 1.9, -1.0, 0.5, 1.4, -1.0, 2.2
        ]
        cvar_95 = rm.calculate_parametric_cvar(confidence_level=0.95)
        assert cvar_95["available"] is True
        assert cvar_95["n_samples"] == 20
        assert cvar_95["method"] == "parametric_gaussian"
        assert cvar_95["var_r"] >= 0.0
        # Expected shortfall must always be greater than or equal to VaR
        assert cvar_95["cvar_r"] >= cvar_95["var_r"]

        # 3. 99% confidence level must produce larger or equal risk metrics than 95%
        cvar_99 = rm.calculate_parametric_cvar(confidence_level=0.99)
        assert cvar_99["var_r"] >= cvar_95["var_r"]
        assert cvar_99["cvar_r"] >= cvar_95["cvar_r"]

    def test_calc_position_size_with_cvar_and_meta_health(self):
        rm = RiskManager(balance=10000.0, state_file=None)
        # Normal baseline
        rm.trade_r_history = []
        vol_baseline = rm.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=1.0)
        assert vol_baseline > 0.0

        # High CVaR regime: positive expectancy (EV > 0, Kelly=1.0) but fat-tail losses (CVaR_95 ~ 4.98R > 2.0R)
        rm.trade_r_history = [2.0, 2.0, -3.5, 2.0, 2.0, -3.5, 2.0, 2.0, -3.5, 2.0] * 2
        vol_cvar_compressed = rm.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=1.0)
        assert vol_cvar_compressed < vol_baseline
        assert vol_cvar_compressed > 0.0

        # Meta-health multiplier direct argument
        rm.trade_r_history = []
        vol_half_meta = rm.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=1.0, meta_health_multiplier=0.50)
        assert vol_half_meta == round(vol_baseline * 0.50, 2)

        # Meta-health multiplier via market context dict
        vol_quarter_market = rm.calc_position_size(
            "EURUSD", 1.1000, 1.0950, confidence=1.0,
            market={"meta_health_multiplier": 0.25}
        )
        assert vol_quarter_market == round(vol_baseline * 0.25, 2)

    def test_spmc_and_analytical_kill_switch_integration(self):
        """Verify SPMC regime filtering (Tolusic 2026) and analytical kill-switch (Masoumian 2026)."""
        rm = RiskManager(balance=10000.0, state_file=None)
        assert rm.spmc_filter is not None
        assert rm.spmc_filter.current_regime in ("GOOD", "G")
        assert rm.spmc_filter.get_sizing_multiplier() == 1.0

        # Feed 3 losses to drive SPMC regime into BAD
        rm.record_trade_result(-100.0)
        rm.record_trade_result(-100.0)
        rm.record_trade_result(-100.0)
        assert rm.spmc_filter.current_regime in ("BAD", "B")
        assert rm.spmc_filter.get_sizing_multiplier() == 0.25

        # Feed trades to trigger analytical kill-switch recalibration
        rm.trade_r_history = []
        for _ in range(6):
            rm.record_trade_result(100.0)
        for _ in range(9):
            rm.record_trade_result(-100.0)

        assert len(rm.trade_r_history) == 15
        assert 3 <= rm.max_consecutive_losses <= 10

    def test_paper_harness_balance_seed_never_zero(self, monkeypatch, tmp_path):
        """Verify paper/harness RiskManager balance seed never crashes with ValueError."""
        from core.harness.kernel import TradingContext
        from core.harness.plugins import RiskManagementPlugin

        db = str(tmp_path / "test_trading.db")

        # 1. Default balance without MT5
        ctx1 = TradingContext(name="test_default")
        p1 = RiskManagementPlugin(db_path=db)
        p1.apply(ctx1)
        assert ctx1.get("risk_manager").balance == 10000.0

        # 2. Explicit zero fallback
        ctx2 = TradingContext(name="test_zero")
        p2 = RiskManagementPlugin(db_path=db, balance=0.0)
        p2.apply(ctx2)
        assert ctx2.get("risk_manager").balance == 10000.0

        # 3. PAPER_EQUITY env override
        monkeypatch.setenv("PAPER_EQUITY", "25000.0")
        ctx3 = TradingContext(name="test_env")
        p3 = RiskManagementPlugin(db_path=db, balance=0.0)
        p3.apply(ctx3)
        assert ctx3.get("risk_manager").balance == 25000.0

    def test_paper_heat_provider_and_fail_closed(self):
        """Verify paper heat provider integration and fail-closed heat cap."""
        rm = RiskManager(balance=10000.0, state_file=None)
        
        # 1. Custom heat provider returns 2.5R
        rm.set_portfolio_heat_provider(lambda: 2.5)
        assert rm.open_portfolio_heat_r() == 2.5
        # Budget is 4.0R, adding 1.0R (total 3.5R) passes
        assert rm.check_portfolio_heat(1.0) is True
        # Adding 2.0R (total 4.5R) exceeds 4.0R budget -> fails
        assert rm.check_portfolio_heat(2.0) is False

        # 2. Heat provider returns None (unmeasurable)
        rm.set_portfolio_heat_provider(lambda: None)
        # If active positions exist, must fail closed
        rm.set_active_positions_provider(lambda: 1)
        assert rm.check_portfolio_heat(1.0) is False

        # If no active positions exist, returns True
        rm.set_active_positions_provider(lambda: 0)
        # Without MT5, heat is unmeasurable with 0 positions -> allowed
        import core.risk_manager as rm_mod
        orig_mt5 = rm_mod.mt5
        try:
            rm_mod.mt5 = None
            assert rm.check_portfolio_heat(1.0) is True
        finally:
            rm_mod.mt5 = orig_mt5

    def test_meta_adaptive_risk_mutation_gating(self, tmp_path):
        """Verify meta-adaptive risk mutation is blocked unless research influence approved.

        1a-F3: the base risk is never written back. Each cycle computes an
        effective risk per proposal; increases need research-influence
        approval, decreases don't. rm.risk_per_trade stays 0.01 in both cases.
        """
        from unittest.mock import MagicMock
        from intelligence.llm_orchestrator import LLMOrchestrator
        from core.risk_manager import RiskManager

        rm = RiskManager(balance=10000.0, state_file=None)
        rm.risk_per_trade = 0.01

        orch = MagicMock(spec=LLMOrchestrator)
        orch.risk = rm
        orch.run_id = "test_run"
        orch.operations = MagicMock()
        # PR 4.3 reads the once-configured base risk, not the live field.
        orch._base_risk_per_trade = 0.01

        # Bind the real method
        orch._apply_advanced_auto_tools = LLMOrchestrator._apply_advanced_auto_tools.__get__(orch, LLMOrchestrator)

        toon = {
            "auto_quant_tools": {
                "results": {
                    "meta_adaptive": {
                        "status": "OK",
                        "current_regime": "TRENDING",
                        "adapted_profile": {"risk_multiplier": 1.5, "position_scale": 1.0},
                    }
                }
            }
        }

        # Case 1: Research influence NOT allowed -> risk_per_trade stays 0.01
        orch._research_influence_allowed = MagicMock(return_value=False)
        orch._apply_advanced_auto_tools(toon, paper_mode=True)
        assert rm.risk_per_trade == 0.01
        assert toon["meta_adaptive_override"]["applied"] is False

        # Case 2: Research influence allowed -> effective risk 0.015 recorded
        # in the toon override, but the base field is never mutated (1a-F3).
        orch._research_influence_allowed = MagicMock(return_value=True)
        orch._apply_advanced_auto_tools(toon, paper_mode=True)
        assert rm.risk_per_trade == 0.01
        assert toon["meta_adaptive_override"]["applied"] is True
        assert toon["meta_adaptive_override"]["adapted_risk_per_trade"] == pytest.approx(0.015)

    def test_advisory_gates_copula_and_bocpd_wiring(self, tmp_path):
        """Verify hub copula tail risk compresses heat budget and BOCPD vetoes in _validate_proposal."""
        from unittest.mock import MagicMock, patch
        from intelligence.llm_orchestrator import LLMOrchestrator
        from core.risk_manager import RiskManager
        from core.trading_safety import DeterministicValidator, TradingStore

        db = str(tmp_path / "state.db")
        store = TradingStore(db)
        rm = RiskManager(balance=10000.0, state_file=None)
        rm.max_portfolio_heat_r = 4.0

        orch = MagicMock(spec=LLMOrchestrator)
        orch.risk = rm
        orch.store = MagicMock()
        orch.run_id = "test_run"
        orch.operations = MagicMock()
        orch.validator = DeterministicValidator(store=store)
        orch._regime_changepoint_probability = MagicMock(return_value=0.05)
        orch._estimate_friction_r = MagicMock(return_value=0.05)
        orch.overfit_gate = MagicMock()
        orch.overfit_gate.validate_proposal.return_value = MagicMock(passed=True, reasons=[])
        orch.model_name = "test_model"
        orch.data_quality = MagicMock()
        orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])
        orch._ingest_lag_envelope = MagicMock(return_value={"lag_confidence": 1.0})

        # Bind _validate_proposal
        orch._validate_proposal = LLMOrchestrator._validate_proposal.__get__(orch, LLMOrchestrator)

        decision = {
            "action": "BUY", "entry": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1100, "confidence": 0.8
        }
        toon = {
            "indicators": {"ema_50": [1.09, 1.095], "adx_14": [20, 25], "atr_14": [0.001, 0.001]},
            "feature_snapshot": {"ready": False, "values": {}},
            "multi_timeframe": {},
            "technical_analysis": {"key_levels": []},
            "ohlc": [{"close": 1.0990}, {"close": 1.1000}],
            "dom": {"best_ask": 1.1001, "best_bid": 1.0999},
            "news": [],
            "context": {"calendar_source": "mt5_calendar"},
            "volume_analysis": {"volume_ratio": 1.2},
            "microstructure": {"allowed": True, "flags": []},
            "causal_setup_proposals": [],
            "live_advisory_hub": {
                "copula_tail": {"tail_risk_state": "CRISIS", "adjusted_heat_budget_r": 1.5},
                "bocpd": {"changepoint_probability": 0.85},
            }
        }

        with patch("mt5_trader.classify_setup") as classify:
            classify.return_value = {
                "setup_id": "trend_continuation_v1", "eligible": True, "reason": "matched",
                "spec": {"min_rr": 1.5, "event_exclusion": False, "version": "1"}
            }
            with patch("mt5_trader.evaluate_data_requirements") as req:
                req.return_value = {"available": True, "reasons": []}
                with patch("mt5_trader.evaluate_market_conditions") as cond:
                    cond.return_value = MagicMock(allowed=True, reasons=[])
                    proposal, result = orch._validate_proposal(decision, toon, "EURUSD", "scalp", paper_mode=True)

        # 1. Copula tail risk compressed the heat budget to 1.5R (per proposal feature, per H4 fix)
        assert proposal.features.get("heat_budget_r") == 1.5

        # 2. BOCPD changepoint probability 0.85 exceeded threshold (0.45) -> vetoed
        assert result.approved is False
        assert any("changepoint" in r.lower() for r in result.reasons)

    def test_pre_pulse_macro_ingest_caching(self):
        """Verify pre_pulse_macro_ingest caches snapshot for parallel strategy cycles."""
        from unittest.mock import MagicMock
        from intelligence.llm_orchestrator import LLMOrchestrator

        orch = MagicMock(spec=LLMOrchestrator)
        orch.data_provider = MagicMock()
        orch.data_provider.calendar = None
        orch.data_provider.client = None
        orch.data_provider.api_manager = None
        orch.macro_graph = None

        orch.pre_pulse_macro_ingest = LLMOrchestrator.pre_pulse_macro_ingest.__get__(orch, LLMOrchestrator)

        entry = orch.pre_pulse_macro_ingest("EURUSD", paper_mode=True)
        assert entry["symbol"] == "EURUSD"
        assert "ts" in entry
        assert orch._pulse_macro_cache["EURUSD"] == entry

    def test_gui_api_deck_truth(self, tmp_path, monkeypatch):
        """Verify /api/tasks and /api/scanner fail-closed and read truth from journal."""
        from fastapi import Response
        import ui.gui_api as gui_mod
        from core.trading_safety import TradingStore

        db_path = str(tmp_path / "test_truth.db")
        store = TradingStore(db_path)
        monkeypatch.setattr(gui_mod, "_JOURNAL_DB", db_path)

        # 1. Scanner empty without cache -> header ENGINE_OFFLINE, empty list
        resp = Response()
        res = gui_mod._scanner(resp)
        assert res == []
        assert resp.headers["X-Engine-Status"] == "ENGINE_OFFLINE"

        # 2. Scanner reads from opportunity_bus_open in store if present
        store.set_state("opportunity_bus_open", [{"symbol": "EURUSD", "score": 0.85}])
        resp2 = Response()
        res2 = gui_mod._scanner(resp2)
        assert len(res2) == 1
        assert res2[0]["symbol"] == "EURUSD"
        assert resp2.headers["X-Engine-Status"] == "ONLINE"

        # 3. Tasks fallback to agent_tasks in SQLite
        import sqlite3
        with sqlite3.connect(db_path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS agent_tasks (idempotency_key TEXT, agent TEXT, task_type TEXT, status TEXT, created_at TEXT, payload TEXT)")
            con.execute("INSERT INTO agent_tasks VALUES ('task-1', 'scalp', 'analyse_candle', 'completed', '2026-09-23T12:00:00+00:00', '{\"symbol\": \"EURUSD\"}')")

        tasks = gui_mod._tasks()
        assert len(tasks) >= 1
        assert tasks[0]["id"] == "task-1"
        assert tasks[0]["status"] == "done"
        assert tasks[0]["symbol"] == "EURUSD"

    def test_learning_loop_schedules_execution_and_journaling(self, tmp_path, monkeypatch):
        """Verify executors dispatch learning schedules and persist last_self_improvement and CPCV fields."""
        from operations.executors import try_handle
        from core.trading_safety import TradingStore
        from operations.learning_loop import run_self_improvement, run_weekly_research, run_daily_review, run_session_review

        db_path = str(tmp_path / "test_learning_loop.db")
        store = TradingStore(db_path)
        monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", db_path)

        # 1. try_handle dispatching self_improvement
        ok_self = try_handle("self_improvement", {"trigger": "test"})
        assert ok_self is True
        saved_state = store.get_state("last_self_improvement")
        assert isinstance(saved_state, dict)
        assert "captured_at" in saved_state
        assert saved_state.get("execution_authority") is False

        # 2. weekly_research execution and CPCV fields
        weekly_res = run_weekly_research({}, context=None)
        assert isinstance(weekly_res, dict)
        assert "timestamp" in weekly_res
        assert weekly_res.get("execution_authority") is False
        assert "cpcv_report" in weekly_res
        assert weekly_res["cpcv_report"].get("execution_authority") is False
        assert "research_sprint" in weekly_res

        # 3. session_review & daily_review dispatch
        assert try_handle("session_review", {}) is True
        assert try_handle("daily_review", {}) is True



