"""Unit tests for kronos_bridge.py."""
import pytest
from kronos_bridge import (
    BACKEND_HEURISTIC,
    BACKEND_NEURAL,
    CandleForecast,
    KronosKLineTokenizer,
    KronosPredictor,
    KronosMemoryIntegrator,
)


def test_kline_tokenizer():
    """Verify KronosKLineTokenizer converts candles to discrete tokens."""
    tokenizer = KronosKLineTokenizer(codebook_size=512)
    token = tokenizer.tokenize_candle(open_p=100.0, high_p=105.0, low_p=98.0, close_p=104.0, volume=1500.0)
    assert isinstance(token, int)
    assert 0 <= token < 512

    ohlcv_list = [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.5, "volume": 500.0},
        {"open": 101.5, "high": 104.0, "low": 101.0, "close": 103.8, "volume": 800.0},
    ]
    tokens = tokenizer.tokenize_series(ohlcv_list)
    assert len(tokens) == 2


def test_kronos_predictor_heuristic_is_honestly_labeled():
    """Forced heuristic path must never claim neural weights or execution authority."""
    predictor = KronosPredictor("kronos-mini", force_heuristic=True)
    assert predictor.backend == BACKEND_HEURISTIC
    assert predictor.weights_loaded is False
    assert predictor.execution_authority is False

    history = [
        {"open": 100.0 + i, "high": 102.0 + i, "low": 99.0 + i, "close": 101.0 + i, "volume": 1000.0}
        for i in range(20)
    ]
    forecast = predictor.predict(symbol="BTCUSD", ohlcv_history=history, pred_len=12, sample_count=5)

    assert isinstance(forecast, CandleForecast)
    assert forecast.symbol == "BTCUSD"
    assert forecast.forecast_horizon_bars == 12
    assert len(forecast.trajectories) == 5
    assert 0.0 <= forecast.confidence_score <= 0.55
    assert forecast.backend == BACKEND_HEURISTIC
    assert forecast.weights_loaded is False
    assert forecast.execution_authority is False
    advisory = forecast.as_advisory_dict()
    assert advisory["execution_authority"] is False
    assert advisory["backend"] == BACKEND_HEURISTIC


def test_kronos_predictor_neural_when_weights_present():
    """When local mini weights + torch load, backend must be labeled neural."""
    predictor = KronosPredictor("kronos-mini", prefer_neural=True, force_heuristic=False)
    status = predictor.backend_status()
    assert status["execution_authority"] is False
    if not predictor.weights_loaded:
        pytest.skip(f"neural weights unavailable: {predictor.load_reason}")
    assert predictor.backend == BACKEND_NEURAL
    history = [
        {"open": 100.0 + i * 0.1, "high": 100.2 + i * 0.1, "low": 99.8 + i * 0.1,
         "close": 100.1 + i * 0.1, "volume": 1000.0}
        for i in range(32)
    ]
    forecast = predictor.predict(symbol="EURUSD", ohlcv_history=history, pred_len=4, sample_count=2)
    assert forecast.backend == BACKEND_NEURAL
    assert forecast.weights_loaded is True
    assert forecast.execution_authority is False
    assert len(forecast.trajectories) >= 1


def test_synthetic_scenario_generation():
    """Verify generate_synthetic_regime produces valid K-line series."""
    predictor = KronosPredictor(force_heuristic=True)
    candles = predictor.generate_synthetic_regime(symbol="XAUUSD", start_price=2000.0, length=50, regime="bull")

    assert len(candles) == 50
    assert candles[0]["bar"] == 0
    assert "open" in candles[0]
    assert "close" in candles[0]
    assert "volume" in candles[0]
    assert candles[0]["execution_authority"] is False
    assert candles[0]["backend"] == BACKEND_HEURISTIC


def test_kronos_memory_integrator():
    """Verify KronosMemoryIntegrator converts forecasts into TradingMemory L1 facts."""
    forecast = CandleForecast(
        symbol="EURUSD",
        timeframe="H1",
        current_close=1.0850,
        predicted_close_mean=1.0920,
        predicted_close_std=0.0010,
        predicted_volatility_5d=0.015,
        forecast_horizon_bars=24,
        trajectories=[[1.0850, 1.0920]],
        backend=BACKEND_HEURISTIC,
        model_name="kronos-mini",
        weights_loaded=False,
    )

    atom = KronosMemoryIntegrator.format_kronos_l1_atom(forecast)

    assert atom["type"] == "fact"
    assert atom["subject_key"] == "kronos:EURUSD:H1"
    assert "BULLISH" in atom["content"]
    assert "heuristic" in atom["content"]
    assert atom["confidence"] > 0.0
    assert atom["execution_authority"] is False
    assert atom["metadata"]["backend"] == BACKEND_HEURISTIC
