from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field


class OHLCVBar(BaseModel):
    open: float = Field(..., description="Open price")
    high: float = Field(..., description="High price")
    low: float = Field(..., description="Low price")
    close: float = Field(..., description="Close price")
    volume: float = Field(default=1000.0, description="Tick volume")
    amount: Optional[float] = Field(default=None, description="Trade amount (volume*close if omitted)")
    spread: Optional[float] = Field(default=None, description="MT5 spread points (optional)")
    real_volume: Optional[float] = Field(default=None, description="MT5 real_volume (optional)")
    time: Optional[int] = Field(default=None, description="Unix timestamp (MT5 schema)")


class ForecastParams(BaseModel):
    symbol: str = Field(default="EURUSD", description="Symbol e.g. EURUSD, XAUUSD")
    timeframe: str = Field(default="H1", description="Timeframe H1/M15/D1 etc (advisory)")
    ohlcv_history: List[Dict[str, Any]] = Field(..., description="List of OHLCV dicts with open/high/low/close/volume[/amount]")
    pred_len: int = Field(default=24, ge=1, le=120, description="Forecast horizon in bars")
    sample_count: int = Field(default=10, ge=1, le=32, description="Number of sampled trajectories")
    temperature: float = Field(default=1.0, ge=0.1, le=2.0, description="Sampling temperature")
    top_p: float = Field(default=0.9, ge=0.1, le=1.0, description="Nucleus top_p")
    model_key: str = Field(default="kronos-mini", description="kronos-mini | kronos-small | kronos-base")


class BatchForecastParams(BaseModel):
    symbols: List[str] = Field(..., description="Symbols list")
    ohlcv_histories: List[List[Dict[str, Any]]] = Field(..., description="Parallel list of OHLCV histories per symbol")
    pred_len: int = Field(default=24, ge=1, le=120)
    sample_count: int = Field(default=10, ge=1, le=16)
    temperature: float = Field(default=1.0, ge=0.1, le=2.0)
    top_p: float = Field(default=0.9, ge=0.1, le=1.0)
    model_key: str = Field(default="kronos-mini")


class TokenizeParams(BaseModel):
    ohlcv_history: List[Dict[str, Any]] = Field(..., description="OHLCV history to tokenize")
    codebook_size: int = Field(default=1024, ge=128, le=8192)


class VolatilityParams(BaseModel):
    symbol: str = Field(default="EURUSD")
    ohlcv_history: List[Dict[str, Any]] = Field(..., description="OHLCV history")
    horizon_bars: int = Field(default=24, ge=1, le=240)
    model_key: str = Field(default="kronos-mini")


class RiskSizingParams(BaseModel):
    symbol: str = Field(default="EURUSD")
    ohlcv_history: List[Dict[str, Any]] = Field(..., description="OHLCV history")
    account_balance: float = Field(default=10000.0, gt=0)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=5.0)
    entry_price: Optional[float] = Field(default=None)
    stop_loss: Optional[float] = Field(default=None)
    model_key: str = Field(default="kronos-mini")


class SyntheticParams(BaseModel):
    symbol: str = Field(default="EURUSD")
    start_price: float = Field(default=1.0850, gt=0)
    length: int = Field(default=100, ge=10, le=500)
    regime: str = Field(default="volatile", description="bull | bear | volatile | range | flash_crash")
    seed: int = Field(default=42)


class MultiAssetSyntheticParams(BaseModel):
    symbols: List[str] = Field(default=["EURUSD", "GBPUSD", "XAUUSD"])
    length_bars: int = Field(default=100, ge=10, le=500)
    regime: str = Field(default="volatile")
    jump_intensity: float = Field(default=0.05, ge=0.0, le=0.5)
    seed: int = Field(default=42)


class AlphaParams(BaseModel):
    symbol: str = Field(default="EURUSD")
    ohlcv_history: List[Dict[str, Any]] = Field(...)
    pred_len: int = Field(default=24, ge=1, le=120)
    model_key: str = Field(default="kronos-mini")


class MT5MultiDimParams(BaseModel):
    symbol: str = Field(default="EURUSD")
    mt5_rates: List[Dict[str, Any]] = Field(..., description="MT5 rates as list of dicts (time,open,high,low,close,tick_volume,spread,real_volume) or tuples")
    pred_len: int = Field(default=24, ge=1, le=120)
    sample_count: int = Field(default=10, ge=1, le=32)
    temperature: float = Field(default=1.0, ge=0.1, le=2.0)
    top_p: float = Field(default=0.9, ge=0.1, le=1.0)
    model_key: str = Field(default="kronos-mini")


class MT5VolParams(BaseModel):
    symbol: str = Field(default="EURUSD")
    mt5_rates: List[Dict[str, Any]] = Field(..., description="MT5 rates list")
    horizon_bars: int = Field(default=24, ge=1, le=240)
    model_key: str = Field(default="kronos-mini")
    account_balance: Optional[float] = Field(default=None)
    risk_per_trade_pct: Optional[float] = Field(default=None)


class StatusParams(BaseModel):
    model_key: str = Field(default="kronos-mini")
