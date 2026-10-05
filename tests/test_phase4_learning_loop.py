"""Phase 4 learning-loop wiring: RL close updates, action bridge, slippage calibration."""
from __future__ import annotations

from unittest.mock import MagicMock

from intelligence.lag_delay_engine import SlippageCalibrator, SmithPredictorEngine
from research.rl_action_bridge import (
    BridgedExecutionParams,
    bridge_rl_action,
    merge_bridge_into_decision,
)
from research.rl_trainer import RLTrainer
from core.trading_safety import SCALP_MAGIC, SWING_MAGIC


def test_bridge_maps_buy_to_continuous_params():
    toon = {
        "dom": {"best_bid": 1.1000, "best_ask": 1.1002},
        "indicators": {"atr_14": 0.0010},
        "timeframe": "M15",
    }
    bridged = bridge_rl_action("BUY", toon=toon)
    assert bridged.action == "BUY"
    assert bridged.execution_authority is False
    assert bridged.entry == 1.1002
    assert bridged.stop_loss < bridged.entry < bridged.take_profit
    assert bridged.magic == SCALP_MAGIC
    assert bridged.source == "rl_action_bridge"


def test_bridge_hold_and_missing_context():
    assert bridge_rl_action("HOLD").action == "HOLD"
    assert bridge_rl_action("BUY", toon={}).action == "HOLD"  # no price/ATR


def test_bridge_swing_magic_for_higher_tf():
    toon = {
        "dom": {"best_bid": 1.2, "best_ask": 1.201},
        "indicators": {"atr_14": 0.01},
        "timeframe": "H4 swing",
    }
    bridged = bridge_rl_action("SELL", toon=toon)
    assert bridged.action == "SELL"
    assert bridged.magic == SWING_MAGIC
    assert bridged.stop_loss > bridged.entry > bridged.take_profit


def test_merge_fills_missing_llm_fields_only():
    bridged = BridgedExecutionParams(
        action="BUY", entry=1.1, stop_loss=1.09, take_profit=1.13,
        strategy="scalp", magic=SCALP_MAGIC,
    )
    decision = {"action": "BUY", "entry": None, "stop_loss": None, "confidence": 0.7}
    merged = merge_bridge_into_decision(decision, bridged, fill_missing_only=True)
    assert merged["entry"] == 1.1
    assert merged["stop_loss"] == 1.09
    assert merged["take_profit"] == 1.13
    assert merged["confidence"] == 0.7
    assert merged["execution_authority"] is False
    assert "rl_bridged_params" in merged


def test_slippage_calibrator_moves_coeff_toward_realized():
    cal = SlippageCalibrator(atr_coeff=0.1)
    # Realized much larger than default prediction → coeff should rise
    for _ in range(20):
        atr, delay = 0.001, 0.25
        predicted = atr * cal.atr_coeff * (delay ** 0.5)
        realized = atr * 0.4 * (delay ** 0.5)
        cal.record(predicted_slippage=predicted, realized_slippage=realized, atr=atr, delay_sec=delay)
    assert cal.atr_coeff > 0.15
    stats = cal.stats()
    assert stats["observation_count"] == 20
    assert stats["mean_abs_error"] is not None


def test_smith_predictor_uses_calibrator_coeff():
    cal = SlippageCalibrator(atr_coeff=0.5)
    SmithPredictorEngine.set_calibrator(cal)
    pred_hi = SmithPredictorEngine.predict_state("EURUSD", 1.1, staleness_ms=100, current_atr=0.001)
    cal2 = SlippageCalibrator(atr_coeff=0.05)
    SmithPredictorEngine.set_calibrator(cal2)
    pred_lo = SmithPredictorEngine.predict_state("EURUSD", 1.1, staleness_ms=100, current_atr=0.001)
    assert pred_hi.predicted_slippage > pred_lo.predicted_slippage
    # restore default
    SmithPredictorEngine.set_calibrator(SlippageCalibrator())


def test_rl_trainer_update_from_close_payload():
    trainer = RLTrainer(model_path="__tmp_rl_phase4.json")
    before = trainer.total_updates
    state = {
        "rsi_14": 55, "macd_histogram": 0.1, "ma_alignment": 1,
        "confluence_score": 7, "near_sr": True, "htf_trend": 1,
        "news_sentiment": 0.2, "volatility_regime": "medium",
        "trend_strength": 0.6, "session": "london",
    }
    reward = trainer.calculate_reward(
        {"pnl": 20.0, "risk": 10.0, "confluence_score": 7, "broker_reconciled": True}
    )
    trainer.update(state, "BUY", reward, {"position": "FLAT"}, done=True)
    assert trainer.total_updates == before + 1
    assert any(v["BUY"] != 0.0 for v in trainer.q_table.values())


def test_rl_learning_allowed_paper_vs_live():
    # Lightweight stand-in for orchestrator gate logic
    class Scope:
        def __init__(self, mode): self.mode = mode

    class FakeOrch:
        def __init__(self, mode, enable=True, offline=False):
            self.enable_rl = enable
            self.rl_trainer = object()
            self.rl_offline_updates_enabled = offline
            self.store = MagicMock()
            self.store.scope = Scope(mode)

        def _rl_learning_allowed(self):
            if not self.enable_rl or not self.rl_trainer:
                return False
            if self.rl_offline_updates_enabled:
                return True
            mode = str(getattr(getattr(self.store, "scope", None), "mode", "") or "").upper()
            if mode in {"PAPER", "DEMO", "SIM", "SIMULATION"}:
                return True
            if mode == "LIVE":
                return False
            return True

    assert FakeOrch("PAPER")._rl_learning_allowed() is True
    assert FakeOrch("DEMO")._rl_learning_allowed() is True
    assert FakeOrch("LIVE")._rl_learning_allowed() is False
    assert FakeOrch("LIVE", offline=True)._rl_learning_allowed() is True
