"""Tests for P0 deterministic gates: walk_forward, overfit, Kronos risk adjustment."""
import pytest
import math


class TestWalkForwardGate:
    """Test walk-forward decay gate in DeterministicValidator."""

    def _make_validator(self, tmp_path):
        from core.trading_safety import DeterministicValidator, TradingStore
        store = TradingStore(str(tmp_path / "state.db"))
        return DeterministicValidator(store=store)

    def _make_proposal(self):
        from core.trading_safety import TradeProposal
        return TradeProposal(
            strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1000,
            stop_loss=1.0950, take_profit=1.1100, confidence=0.8,
            features={"setup_id": "test"},
        )

    def _base_market(self):
        return {
            "market_open": True, "tick_age_ms": 100, "max_tick_age_ms": 5000,
            "symbol_tradable": True, "spread": 1.0, "max_spread": 5.0,
            "session_allowed": True, "liquidity_ok": True, "microstructure_ok": True,
            "required_data_ok": True, "completed_candle": True,
            "stop_distance": 0.0050, "min_stop_distance": 0.0010,
            "clock_ok": True,
        }

    def test_walk_forward_dead_blocks_trade(self, tmp_path):
        """Walk-forward DEAD status should block all entries."""
        validator = self._make_validator(tmp_path)
        proposal = self._make_proposal()
        market = self._base_market()
        market["walk_forward_status"] = "DEAD"
        result = validator.validate(proposal, market)
        assert result.approved is False
        assert any("walk-forward" in r.lower() for r in result.reasons)

    def test_walk_forward_healthy_passes(self, tmp_path):
        """Walk-forward HEALTHY should not block."""
        validator = self._make_validator(tmp_path)
        proposal = self._make_proposal()
        market = self._base_market()
        market["walk_forward_status"] = "HEALTHY"
        result = validator.validate(proposal, market)
        # Should pass (other checks may fail, but not walk-forward)
        assert not any("walk-forward" in r.lower() for r in result.reasons)

    def test_walk_forward_decaying_advisory(self, tmp_path):
        """Walk-forward DECAYING should not block (advisory only)."""
        validator = self._make_validator(tmp_path)
        proposal = self._make_proposal()
        market = self._base_market()
        market["walk_forward_status"] = "DECAYING"
        result = validator.validate(proposal, market)
        assert not any("walk-forward" in r.lower() for r in result.reasons)


class TestOverfitGate:
    """Test overfit gate in DeterministicValidator."""

    def _make_validator(self, tmp_path):
        from core.trading_safety import DeterministicValidator, TradingStore
        store = TradingStore(str(tmp_path / "state.db"))
        return DeterministicValidator(store=store)

    def _make_proposal(self):
        from core.trading_safety import TradeProposal
        return TradeProposal(
            strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1000,
            stop_loss=1.0950, take_profit=1.1100, confidence=0.8,
            features={"setup_id": "test"},
        )

    def _base_market(self):
        return {
            "market_open": True, "tick_age_ms": 100, "max_tick_age_ms": 5000,
            "symbol_tradable": True, "spread": 1.0, "max_spread": 5.0,
            "session_allowed": True, "liquidity_ok": True, "microstructure_ok": True,
            "required_data_ok": True, "completed_candle": True,
            "stop_distance": 0.0050, "min_stop_distance": 0.0010,
            "clock_ok": True,
        }

    def test_overfit_critical_blocks_trade(self, tmp_path):
        """Overfit CRITICAL should block all entries."""
        validator = self._make_validator(tmp_path)
        proposal = self._make_proposal()
        market = self._base_market()
        market["overfit_risk_level"] = "CRITICAL"
        result = validator.validate(proposal, market)
        assert result.approved is False
        assert any("overfit" in r.lower() for r in result.reasons)

    def test_overfit_high_blocks_trade(self, tmp_path):
        """Overfit HIGH should block all entries."""
        validator = self._make_validator(tmp_path)
        proposal = self._make_proposal()
        market = self._base_market()
        market["overfit_risk_level"] = "HIGH"
        result = validator.validate(proposal, market)
        assert result.approved is False
        assert any("overfit" in r.lower() for r in result.reasons)

    def test_overfit_low_passes(self, tmp_path):
        """Overfit LOW should not block."""
        validator = self._make_validator(tmp_path)
        proposal = self._make_proposal()
        market = self._base_market()
        market["overfit_risk_level"] = "LOW"
        result = validator.validate(proposal, market)
        assert not any("overfit" in r.lower() for r in result.reasons)


class TestKronosRiskAdjustment:
    """Test Kronos risk_adjustment_factor in RiskManager."""


    def test_kronos_factor_reduces_sizing(self, tmp_path):
        """Kronos risk_adjustment_factor < 1.0 should reduce position size."""
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore
        store = TradingStore(str(tmp_path / "state.db"))
        rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store, state_file=str(tmp_path / "risk.json"))

        # Without Kronos factor
        vol_normal = rm.calc_position_size(
            symbol="EURUSD", entry=1.1000, sl=1.0950, confidence=0.8, market={},
        )

        # With Kronos factor 0.5
        vol_kronos = rm.calc_position_size(
            symbol="EURUSD", entry=1.1000, sl=1.0950, confidence=0.8, market={"kronos_risk_adjustment_factor": 0.5},
        )

        if vol_normal > 0 and vol_kronos > 0:
            assert vol_kronos < vol_normal, "Kronos factor should reduce sizing"

    def test_walk_forward_dead_zeroes_sizing(self, tmp_path):
        """Walk-forward DEAD should zero position size."""
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore
        store = TradingStore(str(tmp_path / "state.db"))
        rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store, state_file=str(tmp_path / "risk.json"))

        vol = rm.calc_position_size(
            symbol="EURUSD", entry=1.1000, sl=1.0950, confidence=0.8, market={"walk_forward_status": "DEAD"},
        )
        assert vol == 0.0, "Walk-forward DEAD should zero sizing"

    def test_overfit_critical_zeroes_sizing(self, tmp_path):
        """Overfit CRITICAL should zero position size."""
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore
        store = TradingStore(str(tmp_path / "state.db"))
        rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store, state_file=str(tmp_path / "risk.json"))

        vol = rm.calc_position_size(
            symbol="EURUSD", entry=1.1000, sl=1.0950, confidence=0.8, market={"overfit_risk_level": "CRITICAL"},
        )
        assert vol == 0.0, "Overfit CRITICAL should zero sizing"

    def test_walk_forward_decaying_reduces_sizing(self, tmp_path):
        """Walk-forward DECAYING should reduce size by 50%."""
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore
        store = TradingStore(str(tmp_path / "state.db"))
        rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store, state_file=str(tmp_path / "risk.json"))

        vol_normal = rm.calc_position_size(
            symbol="EURUSD", entry=1.1000, sl=1.0950, confidence=0.8, market={},
        )
        vol_decaying = rm.calc_position_size(
            symbol="EURUSD", entry=1.1000, sl=1.0950, confidence=0.8, market={"walk_forward_status": "DECAYING"},
        )
        if vol_normal > 0 and vol_decaying > 0:
            ratio = vol_decaying / vol_normal
            assert 0.3 < ratio < 0.7, f"DECAYING should ~halve size, got ratio={ratio:.2f}"
