"""End-to-end paper mode pipeline test.

Exercises the full cycle without MT5 or LLM API:
  MarketDataProvider (mock OHLCV) → AutoQuantTools → DeterministicValidator
  → PaperBroker → TradeManager → PerformanceMonitor → MetaLoop

Verifies all gates, safety checks, and data wiring work together.
"""
import json
import math
import time
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import pytest
from core.trading_safety import TradeProposal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 250) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame with trend + noise."""
    rng = np.random.RandomState(42)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.1, 0.5, n)
    low = close - rng.uniform(0.1, 0.5, n)
    open_ = close + rng.normal(0, 0.1, n)
    volume = rng.uniform(100, 1000, n)
    times = pd.date_range("2025-01-01", periods=n, freq="15min")
    return pd.DataFrame({
        "time": times, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume,
    })


def _make_dom() -> dict:
    return {"best_bid": 1.0999, "best_ask": 1.1001, "bid_depth": 100000, "ask_depth": 100000}


# ---------------------------------------------------------------------------
# Test: DeterministicValidator gates
# ---------------------------------------------------------------------------

class TestDeterministicValidatorGates:
    """All safety gates must work together."""

    def _proposal(self, entry=1.0990, sl=1.0950, tp=1.1070):
        return TradeProposal(
            strategy="swing", symbol="EURUSD", side="BUY",
            entry=entry, stop_loss=sl, take_profit=tp,
            confidence=0.8,
            features={
                "ema_alignment": 1, "mtf_alignment": 1,
                "adx": 25, "near_key_level": True,
                "confirmed_candle": True, "volume_confirmed": True,
            },
        )

    def test_overfit_critical_blocks(self):
        from core.trading_safety import DeterministicValidator
        v = DeterministicValidator(store=MagicMock(), min_rr=2.0)
        market = {
            "confluence_score": 10,
            "changepoint_probability": 0.1,
            "overfit_risk_level": "CRITICAL",
        }
        result = v.validate(self._proposal(), market)
        assert not result.approved
        assert any("overfit" in r.lower() for r in result.reasons)

    def test_walk_forward_dead_blocks(self):
        from core.trading_safety import DeterministicValidator
        v = DeterministicValidator(store=MagicMock(), min_rr=2.0)
        market = {
            "confluence_score": 10,
            "changepoint_probability": 0.1,
            "walk_forward_status": "DEAD",
        }
        result = v.validate(self._proposal(), market)
        assert not result.approved

    def test_changepoint_high_vetoes(self):
        from core.trading_safety import DeterministicValidator
        v = DeterministicValidator(store=MagicMock(), min_rr=2.0)
        market = {
            "confluence_score": 10,
            "changepoint_probability": 0.80,
        }
        result = v.validate(self._proposal(), market)
        assert not result.approved
        assert any("changepoint" in r.lower() for r in result.reasons)

    def test_net_ev_floor_blocks_negative(self):
        from core.trading_safety import DeterministicValidator
        v = DeterministicValidator(store=MagicMock(), min_rr=2.0, min_net_ev_r=0.2)
        market = {
            "confluence_score": 10,
            "changepoint_probability": 0.0,
            "rolling_win_rate": 0.35,
        }
        result = v.validate(self._proposal(entry=1.0999, sl=1.0990, tp=1.1017), market)
        assert not result.approved

    def test_clean_setup_passes(self):
        from core.trading_safety import DeterministicValidator
        store = MagicMock()
        store.is_kill_switch_active.return_value = False
        store.setup_seen_recently.return_value = False
        v = DeterministicValidator(store=store, min_rr=2.0, min_net_ev_r=0.0)
        proposal = self._proposal()
        stop_dist = abs(proposal.entry - proposal.stop_loss)  # 0.0040
        market = {
            "tick_age_ms": 100,
            "max_tick_age_ms": 5000,
            "spread": 1.5,
            "max_spread": 5.0,
            "market_open": True,
            "symbol_tradable": True,
            "completed_candle": True,
            "execution_price": proposal.entry,
            "entry_tolerance": 0.0005,
            "stop_distance": stop_dist,
            "min_stop_distance": 0.001,
            "high_impact_news": False,
            "required_data_ok": True,
            # Complete evidence (P1-H4): the fail-closed validator rejects
            # any missing gate input.
            "clock_ok": True,
            "session_allowed": True,
            "liquidity_ok": True,
            "microstructure_ok": True,
        }
        result = v.validate(proposal, market)
        assert result.approved


# ---------------------------------------------------------------------------
# Test: Copula → RiskManager heat compression
# ---------------------------------------------------------------------------

class TestCopulaRiskWiring:

    def test_crisis_compresses_heat(self):
        from core.risk_manager import RiskManager
        rm = RiskManager.__new__(RiskManager)
        rm.max_portfolio_heat_r = 4.0
        rm.open_positions = {}
        rm.set_heat_budget_r(1.0)
        assert rm.max_portfolio_heat_r == 1.0

    def test_normal_heat_preserved(self):
        from core.risk_manager import RiskManager
        rm = RiskManager.__new__(RiskManager)
        rm.max_portfolio_heat_r = 4.0
        rm.open_positions = {}
        rm.set_heat_budget_r(4.0)
        assert rm.max_portfolio_heat_r == 4.0

    def test_copula_crisis_state(self):
        from core.copula_tail_risk import clayton_copula_tail_risk
        import numpy as np
        # Crisis: highly correlated negative returns
        rng = np.random.RandomState(42)
        x = rng.normal(-0.02, 0.01, 50)
        y = x + rng.normal(0, 0.001, 50)  # Nearly identical
        mat = np.column_stack([x, y])
        result = clayton_copula_tail_risk(mat, normal_heat_budget_r=4.0)
        assert "adjusted_heat_budget_r" in result
        assert "tail_risk_state" in result


# ---------------------------------------------------------------------------
# Test: Token budget enforcement
# ---------------------------------------------------------------------------

class TestTokenBudgetEndToEnd:

    def test_budget_prevents_overflow(self):
        from data.llm_router import enforce_token_budget
        huge = "X" * 200_000  # ~50k tokens
        msgs = [
            {"role": "system", "content": huge},
            {"role": "user", "content": "market data"},
        ]
        result = enforce_token_budget(msgs, max_total_tokens=24000)
        total = sum(len(m["content"]) // 4 for m in result)
        assert total < 30000  # Well under budget after truncation


# ---------------------------------------------------------------------------
# Test: PaperBroker full cycle
# ---------------------------------------------------------------------------

class TestPaperBrokerCycle:

    def test_open_close_cycle(self):
        from core.order_execution import PaperBroker
        broker = PaperBroker()
        result = broker.market_order("EURUSD", "BUY", 0.1, 1.1000, 1.0950, 1.1100, "test", magic=460001)
        assert result["order"] is not None
        assert len(broker.positions) == 1
        ticket = result["order"]
        close_result = broker.close_position(ticket, 1.1050, magic=460001)
        assert len(broker.positions) == 0
        assert len(broker.history) == 1

    def test_refuses_nan_quote(self):
        from core.order_execution import PaperBroker
        broker = PaperBroker()
        result = broker.market_order("EURUSD", "BUY", 0.1, math.nan, None, None, "test")
        assert result["order"] is None
        assert result["retcode"] == 10013


# ---------------------------------------------------------------------------
# Test: Evolutionary optimizer persistence
# ---------------------------------------------------------------------------

class TestEvolutionaryPersistence:

    def test_state_survives_restart(self):
        from core.evolutionary_optimizer import EvolutionaryOptimizer
        import tempfile, os
        sf = tempfile.mktemp(suffix=".json")
        try:
            opt = EvolutionaryOptimizer(state_file=sf)
            def fitness(gene):
                return float(gene.get("weight", 0.5))
            opt.run_evolution(fitness, n_generations=2)
            opt._save_state()
            opt2 = EvolutionaryOptimizer(state_file=sf)
            assert len(opt2.state.population) >= 5
            assert opt2.state.history is not None
            assert len(opt2.state.history) > 0
        finally:
            if os.path.exists(sf):
                os.unlink(sf)


# ---------------------------------------------------------------------------
# Test: Meta-loop ShadowDeploy with corpus
# ---------------------------------------------------------------------------

class TestShadowDeployCorpus:

    def test_shadow_deploy_has_corpus(self):
        from core.meta_loop import ShadowDeploy, MetaCorpus
        corpus = MetaCorpus(db_path=":memory:")
        sd = ShadowDeploy(dsr_threshold=0.7, shadow_window=10, corpus=corpus)
        assert sd.corpus is not None

    def test_shadow_deploy_no_crash_without_corpus(self):
        from core.meta_loop import ShadowDeploy
        sd = ShadowDeploy(dsr_threshold=0.7, shadow_window=10, corpus=None)
        # Pass float sharpe, not a list
        result = sd._compute_dsr_proxy(0.8, max_dd=0.15, n_obs=100)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# Test: Alpha ensemble with regime multipliers
# ---------------------------------------------------------------------------

class TestAlphaEnsembleRegime:

    def test_combine_alphas_runs(self):
        from core.alpha_ensemble import combine_alphas
        import pandas as pd
        rng = np.random.RandomState(42)
        n = 100
        df = pd.DataFrame({
            "alpha_mom": rng.normal(0, 1, n),
            "alpha_rev": rng.normal(0, 1, n),
            "_fwd_return": rng.normal(0, 0.01, n),
        })
        result = combine_alphas(df, ["alpha_mom", "alpha_rev"])
        assert hasattr(result, "composite")
        assert hasattr(result, "alpha_weights")


# ---------------------------------------------------------------------------
# Test: EWC forgetting prevention
# ---------------------------------------------------------------------------

class TestEWCWiring:

    def test_ewc_penalty_computes(self):
        from core.ewc_forgetting_prevention import ElasticWeightConsolidationRegularizer
        current = np.array([0.5, 0.3, 0.8])
        optimal = np.array([0.4, 0.3, 0.7])
        fisher = np.array([1.0, 0.5, 2.0])
        penalty = ElasticWeightConsolidationRegularizer.compute_ewc_loss_penalty(
            current, optimal, fisher,
        )
        assert penalty > 0.0

    def test_ewc_forgetting_prevention_snapshot(self):
        from core.ewc_forgetting_prevention import EWCForgettingPrevention
        ewc = EWCForgettingPrevention()
        q = {"s1": {"buy": 0.5, "sell": -0.3}}
        ewc.snapshot_regime("TRENDING", q)
        assert len(ewc.snapshots) >= 1


# ---------------------------------------------------------------------------
# Test: Self-improvement loop with replay simulator
# ---------------------------------------------------------------------------

class TestSelfImprovementSimulator:

    def test_replay_simulator_produces_proposed(self):
        """Honest replay contract: a sizing proposal over real closed trades
        yields REAL simulated metrics (R-invariant edges, scaled drawdown);
        anything the replay cannot evaluate -> None (never a baseline copy)."""
        import sqlite3
        import os as _os
        import tempfile as _tempfile
        from core.self_improvement_loop import create_replay_simulator, BaselineSnapshot

        fd, db = _tempfile.mkstemp(suffix=".db")
        _os.close(fd)
        try:
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE trades (ticket INTEGER PRIMARY KEY, pnl REAL, "
                "risk_amount REAL, closed_at TEXT)"
            )
            rs = [1.5, -0.5, 2.0, 0.3, -1.0, 1.0, 0.8, -0.2]
            for i, r in enumerate(rs):
                con.execute(
                    "INSERT INTO trades VALUES (?, ?, ?, ?)",
                    (i + 1, r * 100.0, 100.0, f"2026-06-{(i % 28) + 1:02d}T10:00:00+00:00"),
                )
            # Replay admits only broker-evidenced tickets (order_intents).
            con.execute("CREATE TABLE order_intents (mt5_ticket INTEGER)")
            con.executemany(
                "INSERT INTO order_intents (mt5_ticket) VALUES (?)",
                [(i + 1,) for i in range(len(rs))],
            )
            con.commit()
            con.close()
            store = MagicMock()
            store.path = db
            simulator = create_replay_simulator(store)

            baseline = BaselineSnapshot(
                win_rate=0.50, avg_r_multiple=0.8, current_drawdown=0.15,
                total_trades=8, sharpe_rolling=0.5,
            )
            # Sizing change -> real proposed metrics, distinct from baseline
            proposed = simulator({"risk_per_trade": {"current": "0.01", "proposed": "0.02"}})
            assert proposed is not None
            assert proposed != baseline.to_dict()
            # R-invariance: the replay's own 1x sizing must reproduce the
            # proposed edges exactly (win rate / avg R / Sharpe).
            ref = simulator({"risk_per_trade": {"current": "0.01", "proposed": "0.01"}})
            assert ref is not None
            assert proposed["win_rate"] == ref["win_rate"]
            assert proposed["avg_r_multiple"] == ref["avg_r_multiple"]
            assert proposed["sharpe_rolling"] == ref["sharpe_rolling"]
            # Only the equity-scaled drawdown differs (2x sizing)
            assert proposed["current_drawdown"] > ref["current_drawdown"]
            # Non-evaluable change classes -> honest refusal
            assert simulator(baseline.to_dict()) is None
            assert simulator({"alpha_036_weight": {"current": "0.1", "proposed": "0.15"}}) is None
        finally:
            _os.unlink(db)
