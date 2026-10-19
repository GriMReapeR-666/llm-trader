"""Unit test suite for the 5 Quantitative Engine BaseStrategy Plugins."""
import pytest
from strategies.loader import StrategyLoader
from strategies.base import StrategySignal
from strategies.kalman_statarb_strategy import KalmanStatArbStrategy
from strategies.cointegration_pair_strategy import CointegrationPairStrategy
from strategies.avellaneda_stoikov_scalp_strategy import AvellanedaStoikovScalpStrategy
from strategies.macro_event_alpha_strategy import MacroEventAlphaStrategy
from strategies.kronos_neural_strategy import KronosNeuralStrategy


def test_strategy_loader_discovers_all_five():
    loader = StrategyLoader()
    strats = loader.discover()
    expected = {
        "kalman_statarb",
        "cointegration_pair",
        "avellaneda_stoikov_scalp",
        "macro_event_alpha",
        "kronos_neural",
    }
    assert expected.issubset(set(strats.keys()))


def test_kalman_statarb_strategy_evaluate():
    strat = KalmanStatArbStrategy()
    assert strat.trading_style == "SWING"
    assert strat.score_type == "Z_SCORE"

    # Evaluate normal context
    ctx = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "pair_symbol": "GBPUSD",
        "pair_price": 1.2500,
        "digits": 5,
        "point": 0.0001,
    }
    signal = strat.evaluate(ctx)
    assert isinstance(signal, StrategySignal)
    assert signal.strategy == "kalman_statarb"
    assert "z_score" in signal.metadata
    assert signal.metadata["trading_style"] == "SWING"
    assert signal.metadata["score_type"] == "Z_SCORE"


def test_cointegration_pair_strategy_evaluate():
    strat = CointegrationPairStrategy()
    assert strat.trading_style == "SWING"
    assert strat.score_type == "COINTEGRATION"

    ctx = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "pair_symbol": "GBPUSD",
        "pair_price": 1.2500,
        "digits": 5,
        "point": 0.0001,
    }
    signal = strat.evaluate(ctx)
    assert isinstance(signal, StrategySignal)
    assert signal.strategy == "cointegration_pair"
    assert "is_cointegrated" in signal.metadata
    assert signal.metadata["trading_style"] == "SWING"


def test_avellaneda_stoikov_scalp_strategy_evaluate():
    strat = AvellanedaStoikovScalpStrategy()
    assert strat.trading_style == "SCALP"
    assert strat.score_type == "SPREAD_CAPTURE"

    # Normal context with balanced inventory
    ctx = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "digits": 5,
        "point": 0.0001,
        "inventory_units": 0.0,
        "features": {"dom_imbalance": 0.25, "atr": 0.0010},
    }
    signal = strat.evaluate(ctx)
    assert isinstance(signal, StrategySignal)
    assert signal.strategy == "avellaneda_stoikov_scalp"
    assert signal.side in {"BUY", "SELL", "HOLD"}
    assert "reservation_price" in signal.metadata
    assert signal.metadata["trading_style"] == "SCALP"


def test_macro_event_alpha_strategy_evaluate():
    strat = MacroEventAlphaStrategy()
    assert strat.trading_style == "LONG_TERM"
    assert strat.score_type == "MACRO_SURPRISE"

    # Without macro release data -> HOLD
    ctx = {"symbol": "EURUSD", "price": 1.0850, "digits": 5}
    signal = strat.evaluate(ctx)
    assert signal.side == "HOLD"

    # With macro release data
    ctx_macro = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "digits": 5,
        "point": 0.0001,
        "macro": {
            "event_name": "CPI",
            "actual": 3.5,
            "consensus": 3.1,
            "previous": 3.1,
        },
    }
    sig_macro = strat.evaluate(ctx_macro)
    assert isinstance(sig_macro, StrategySignal)
    assert sig_macro.strategy == "macro_event_alpha"
    assert "z_delta" in sig_macro.metadata


def test_kronos_neural_strategy_evaluate():
    strat = KronosNeuralStrategy(force_heuristic=True)
    assert strat.trading_style == "SWING"
    assert strat.score_type == "NEURAL_PROBABILITY"

    ctx = {
        "symbol": "EURUSD",
        "price": 1.0850,
        "digits": 5,
        "point": 0.0001,
    }
    signal = strat.evaluate(ctx)
    assert isinstance(signal, StrategySignal)
    assert signal.strategy == "kronos_neural"
    assert "composite_alpha_score" in signal.metadata
    assert signal.metadata["trading_style"] == "SWING"
