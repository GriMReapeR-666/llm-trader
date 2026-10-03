"""Kronos Financial Foundation Model Bridge — K-line GPT interface.

Architecture:
1. Tokenizer: Binary Spherical Quantization (BSQ) codebook discretizing OHLCV → discrete K-line tokens
2. Autoregressive Predictor: Multi-trajectory price & volatility forecasting
3. Synthetic Generator: Market regime scenario generation for Monte Carlo backtesting
4. Ensemble Integrator: Integrates Kronos prediction scores into MacroGraph / TradingMemory

Honesty contract
----------------
``KronosPredictor`` prefers a real torch/safetensors neural backend when local
weights load successfully.  If torch, vendor code, or weights are unavailable,
it falls back to a Gaussian-drift *heuristic* backend and labels every forecast
accordingly.  Callers must never treat this module as execution authority.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import threading
import time as _time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("KronosBridge")

BACKEND_NEURAL = "neural"
BACKEND_HEURISTIC = "heuristic"
EXECUTION_AUTHORITY = False

# The vendor Kronos modules keep shared mutable state (rotary cos/sin caches,
# buffers) that is not thread-safe: a concurrent caller can read a cache
# mid-update and get None where a Tensor is expected ("unsupported operand
# type(s) for *: Tensor and NoneType"). Strategy cycles run in a thread pool
# sharing one predictor, so official-predict calls are serialized.
_KRONOS_PREDICT_LOCK = threading.RLock()
_NEURAL_RETRY_COOLDOWN_SEC = 60.0

# Track logged heuristic warnings to avoid spam, auto-clear when fixed
_HEURISTIC_WARNED: set = set()
_HEURISTIC_WARN_CACHE: dict = {}
_HEURISTIC_FIRST_WARN_TS: dict = {}

def _should_warn_heuristic(key: str, ttl_sec: int = 3600) -> bool:
    """Dedup heuristic warnings per key, auto-clear after TTL or when fixed."""
    import time
    now = time.time()
    if key not in _HEURISTIC_WARNED:
        _HEURISTIC_WARNED.add(key)
        _HEURISTIC_FIRST_WARN_TS[key] = now
        return True
    # If still heuristic after an hour, remind once with fix guidance
    last = _HEURISTIC_WARN_CACHE.get(key, 0)
    if now - last > ttl_sec:
        _HEURISTIC_WARN_CACHE[key] = now
        return True
    return False

def clear_heuristic_error(model_name: str = ""):
    """Clear cached heuristic error when weights are repaired — auto-clear on fix."""
    keys_to_clear = [k for k in _HEURISTIC_WARNED if not model_name or model_name in k]
    for k in keys_to_clear:
        _HEURISTIC_WARNED.discard(k)
        _HEURISTIC_WARN_CACHE.pop(k, None)
        _HEURISTIC_FIRST_WARN_TS.pop(k, None)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODELS_DIR = _REPO_ROOT / "models"
_VENDOR_ROOT = _REPO_ROOT / "vendor" / "kronos_official"
# Fallback: also check research/models for legacy layouts
_LEGACY_MODELS_DIR = Path(__file__).resolve().parent / "models"

# Local weight folder → tokenizer folder.  kronos-small needs Tokenizer-base,
# which is not always present; load will fail closed into heuristic.
_MODEL_TOKENIZER_MAP = {
    "kronos-mini": "kronos-tokenizer-2k",
    "kronos-small": "kronos-tokenizer-base",
    "kronos-base": "kronos-tokenizer-base",
    "Kronos-base": "kronos-tokenizer-base",
    "Kronos-mini": "kronos-tokenizer-2k",
    "Kronos-small": "kronos-tokenizer-base",
}


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class CandleForecast:
    """Multi-trajectory candle forecast output from Kronos (close-only legacy view)."""
    symbol: str
    timeframe: str
    current_close: float
    predicted_close_mean: float
    predicted_close_std: float
    predicted_volatility_5d: float
    forecast_horizon_bars: int
    trajectories: List[List[float]] = field(default_factory=list)
    backend: str = BACKEND_HEURISTIC
    model_name: str = ""
    weights_loaded: bool = False
    execution_authority: bool = False

    @property
    def predicted_return_pct(self) -> float:
        if self.current_close <= 0:
            return 0.0
        return ((self.predicted_close_mean - self.current_close) / self.current_close) * 100.0

    @property
    def confidence_score(self) -> float:
        """Confidence score [0.0, 1.0] derived from trajectory dispersion."""
        if self.predicted_close_mean <= 0:
            return 0.0
        dispersion_ratio = self.predicted_close_std / self.predicted_close_mean
        score = max(0.0, min(1.0, 1.0 - (dispersion_ratio * 5.0)))
        # Heuristic forecasts are capped so they cannot look like calibrated neural confidence.
        if self.backend != BACKEND_NEURAL:
            score = min(score, 0.55)
        return score

    def as_advisory_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "predicted_return_pct": round(self.predicted_return_pct, 4),
            "predicted_close_mean": self.predicted_close_mean,
            "predicted_close_std": self.predicted_close_std,
            "predicted_volatility_5d": self.predicted_volatility_5d,
            "forecast_horizon_bars": self.forecast_horizon_bars,
            "confidence": round(self.confidence_score, 4),
            "backend": self.backend,
            "model_name": self.model_name,
            "weights_loaded": self.weights_loaded,
            "execution_authority": False,
            "trajectory_count": len(self.trajectories),
        }


@dataclass
class KronosMultiDimForecast:
    """Multi-dimensional Kronos forecast across OHLCVA (paper §3: hierarchical BSQ tokens).

    Each trajectory is a list of per-bar dicts with open/high/low/close/volume/amount.
    Means are aggregated across trajectories per horizon step.
    """
    symbol: str
    timeframe: str
    current_ohlcv: Dict[str, float]
    horizon_bars: int
    mean_path: List[Dict[str, float]]  # len == horizon_bars, each dict has OHLCVA
    std_path: List[Dict[str, float]]   # per-bar std across trajectories
    trajectories: List[List[Dict[str, float]]]  # sample_count × horizon_bars
    confidence: float
    predicted_volatility_5d: float
    predicted_volatility_10d: float
    predicted_volatility_20d: float
    vol_term_structure: Dict[str, float]  # {"5d":..., "10d":..., "20d":...}
    vol_regime: str
    risk_adjustment_factor: float
    backend: str = BACKEND_HEURISTIC
    model_name: str = ""
    weights_loaded: bool = False
    execution_authority: bool = False

    @property
    def predicted_close_mean(self) -> float:
        return float(self.mean_path[-1].get("close", 0.0)) if self.mean_path else 0.0

    @property
    def current_close(self) -> float:
        return float(self.current_ohlcv.get("close", 0.0))

    @property
    def predicted_return_pct(self) -> float:
        cc = self.current_close
        if cc <= 0:
            return 0.0
        return ((self.predicted_close_mean - cc) / cc) * 100.0

    def as_advisory_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "horizon_bars": self.horizon_bars,
            "current_ohlcv": self.current_ohlcv,
            "predicted_return_pct": round(self.predicted_return_pct, 4),
            "predicted_close_mean": round(self.predicted_close_mean, 4),
            "confidence": round(self.confidence, 4),
            "volatility": {
                "5d": round(self.predicted_volatility_5d, 5),
                "10d": round(self.predicted_volatility_10d, 5),
                "20d": round(self.predicted_volatility_20d, 5),
                "term_structure": {k: round(v, 5) for k, v in self.vol_term_structure.items()},
                "regime": self.vol_regime,
                "risk_adjustment_factor": self.risk_adjustment_factor,
            },
            "trajectory_count": len(self.trajectories),
            "backend": self.backend,
            "model_name": self.model_name,
            "weights_loaded": self.weights_loaded,
            "execution_authority": False,
            "mean_path": self.mean_path,
        }


# ── K-Line Tokenizer (Spherical Quantization) ───────────────────────────────

class KronosKLineTokenizer:
    """Discretizes continuous OHLCVA price action into discrete K-line codebook tokens.

    This is a lightweight local hash tokenizer used by the heuristic path and
    by callers that need discrete tokens without loading neural weights.
    """

    def __init__(self, codebook_size: int = 1024):
        self.codebook_size = codebook_size

    def tokenize_candle(self, open_p: float, high_p: float, low_p: float, close_p: float, volume: float) -> int:
        """Map OHLCV values to discrete token index [0, codebook_size-1]."""
        if open_p <= 0:
            return 0
        return_pct = (close_p - open_p) / open_p
        range_pct = (high_p - low_p) / open_p
        vol_scale = math.log1p(max(0.0, volume))

        # Hash feature tuple to codebook token
        val = hash((round(return_pct, 4), round(range_pct, 4), round(vol_scale, 2)))
        return abs(val) % self.codebook_size

    def tokenize_series(self, ohlcv_list: List[Dict[str, float]]) -> List[int]:
        """Convert OHLCV dictionary series to list of discrete tokens."""
        tokens = []
        for c in ohlcv_list:
            t = self.tokenize_candle(
                c.get("open", 1.0),
                c.get("high", 1.0),
                c.get("low", 1.0),
                c.get("close", 1.0),
                c.get("volume", 0.0),
            )
            tokens.append(t)
        return tokens


def _resolve_models_dir(models_dir: Optional[str] = None) -> Path:
    if models_dir:
        return Path(models_dir).expanduser().resolve()
    env = os.getenv("KRONOS_MODELS_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_MODELS_DIR


def _history_stats(ohlcv_history: List[Dict[str, float]]) -> Tuple[float, float, float]:
    last_close = float(ohlcv_history[-1].get("close", 100.0) or 100.0)
    returns: List[float] = []
    for i in range(1, len(ohlcv_history)):
        prev = float(ohlcv_history[i - 1].get("close", last_close) or last_close)
        curr = float(ohlcv_history[i].get("close", last_close) or last_close)
        if prev > 0:
            returns.append((curr - prev) / prev)
    avg_return = sum(returns) / len(returns) if returns else 0.0002
    volatility = (
        math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns))
        if len(returns) > 1
        else 0.01
    )
    return last_close, avg_return, max(1e-6, volatility)


def _vol_regime_and_risk(vol_5d: float) -> Tuple[str, float]:
    if vol_5d < 0.015:
        return "LOW", 1.0
    if vol_5d < 0.035:
        return "NORMAL", 0.9
    if vol_5d < 0.06:
        return "ELEVATED", 0.7
    return "HIGH", 0.5


def _advanced_vol_term_structure(base_vol: float, horizons_bars: List[int], hours_per_bar: int = 1) -> Dict[str, float]:
    """Paper Volatility Forecasting §4.2: term structure via sqrt(time) scaling.
    base_vol is per-bar log-return std; convert to nd-day vols.
    """
    out: Dict[str, float] = {}
    for h in horizons_bars:
        # horizon in hours then days (24h)
        days_equiv = max(1, h * hours_per_bar) / 24.0
        # fallback: if horizon <5d we still compute proportional
        vol = base_vol * math.sqrt(max(1.0, days_equiv * 24.0))  # per-bar -> period vol proxy
        # Normalize to standard 5/10/20d keys where applicable
        key = f"{h}b"
        out[key] = round(max(0.001, vol), 5)
    # Also provide canonical 5d/10d/20d scaled from base_vol * sqrt(days*24)
    for days, k in [(5, "5d"), (10, "10d"), (20, "20d")]:
        out[k] = round(max(0.001, base_vol * math.sqrt(days * 24.0)), 5)
    return out


def _ohlcv_stats_full(ohlcv_history: List[Dict[str, float]]) -> Dict[str, float]:
    last = ohlcv_history[-1] if ohlcv_history else {}
    cc = float(last.get("close", 100.0) or 100.0)
    return {
        "open": float(last.get("open", cc) or cc),
        "high": float(last.get("high", cc) or cc),
        "low": float(last.get("low", cc) or cc),
        "close": cc,
        "volume": float(last.get("volume", 1000.0) or 1000.0),
        "amount": float(last.get("amount", cc * 1000.0) or cc * 1000.0),
    }


def try_load_neural_bundle(
    model_name: str = "kronos-mini",
    models_dir: Optional[str] = None,
    device: str = "cpu",
) -> Tuple[Optional[Any], Optional[str]]:
    """Attempt to load local Kronos neural weights.

    Returns ``(official_predictor_or_None, reason)``.
    """
    models_root = _resolve_models_dir(models_dir)
    # Also probe legacy location if primary is empty
    if not models_root.exists() or not any(models_root.iterdir()):
        if _LEGACY_MODELS_DIR.exists() and any(_LEGACY_MODELS_DIR.iterdir()):
            models_root = _LEGACY_MODELS_DIR
    model_key = model_name.strip()
    model_folder = models_root / model_key.lower() if not (models_root / model_key).exists() else models_root / model_key
    # Prefer exact folder name, then lower-case alias used by downloader.
    for candidate in (models_root / model_key, models_root / model_key.lower()):
        if candidate.exists():
            model_folder = candidate
            break

    tok_key = _MODEL_TOKENIZER_MAP.get(model_key) or _MODEL_TOKENIZER_MAP.get(model_key.lower())
    if not tok_key:
        return None, f"no tokenizer mapping for model '{model_key}'"
    tok_folder = models_root / tok_key
    # fallback to legacy for tokenizer as well
    if not tok_folder.exists() and _LEGACY_MODELS_DIR.exists():
        legacy_tok = _LEGACY_MODELS_DIR / tok_key
        if legacy_tok.exists():
            tok_folder = legacy_tok
    if not model_folder.exists():
        return None, f"model folder missing: {model_folder}"
    if not tok_folder.exists():
        return None, f"tokenizer folder missing: {tok_folder}"

    model_weights = model_folder / "model.safetensors"
    tok_weights = tok_folder / "model.safetensors"
    model_cfg_path = model_folder / "config.json"
    tok_cfg_path = tok_folder / "config.json"
    for required in (model_weights, tok_weights, model_cfg_path, tok_cfg_path):
        if not required.exists():
            return None, f"required weight/config missing: {required}"

    try:
        import torch  # noqa: F401
        from safetensors.torch import load_file
    except Exception as exc:
        return None, f"torch/safetensors unavailable: {exc}"

    if not _VENDOR_ROOT.exists():
        return None, f"vendor kronos code missing: {_VENDOR_ROOT}"

    vendor_path = str(_VENDOR_ROOT)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)

    try:
        from model.kronos import Kronos, KronosTokenizer, KronosPredictor as OfficialPredictor
    except Exception as exc:
        return None, f"vendor import failed: {exc}"

    try:
        tok_cfg = json.loads(tok_cfg_path.read_text(encoding="utf-8"))
        model_cfg = json.loads(model_cfg_path.read_text(encoding="utf-8"))
        tokenizer = KronosTokenizer(**tok_cfg)
        model = Kronos(**model_cfg)
        tok_state = load_file(str(tok_weights))
        model_state = load_file(str(model_weights))
        tok_missing, tok_unexpected = tokenizer.load_state_dict(tok_state, strict=False)
        model_missing, model_unexpected = model.load_state_dict(model_state, strict=False)
        if tok_missing or model_missing:
            return None, (
                f"incomplete weight load (tok_missing={len(tok_missing)}, "
                f"model_missing={len(model_missing)})"
            )
        if tok_unexpected or model_unexpected:
            log.warning(
                "Kronos load unexpected keys tok=%s model=%s",
                len(tok_unexpected),
                len(model_unexpected),
            )
        tokenizer.eval()
        model.eval()
        max_context = 2048 if "mini" in model_key.lower() else 512
        official = OfficialPredictor(model, tokenizer, device=device, max_context=max_context)
        return official, (
            f"loaded neural weights model={model_folder.name} tokenizer={tok_folder.name} "
            f"device={device}"
        )
    except Exception as exc:
        return None, f"neural load failed: {exc}"


# ── Kronos Predictor ────────────────────────────────────────────────────────

class KronosPredictor:
    """Kronos forecast interface with honest neural/heuristic backend labeling.

    Prefer neural when local safetensors + torch + vendor code load cleanly.
    Otherwise use a clearly labeled Gaussian-drift heuristic.  Never claims
    execution authority.
    """

    def __init__(
        self,
        model_name: str = "kronos-mini",
        *,
        models_dir: Optional[str] = None,
        prefer_neural: bool = True,
        force_heuristic: bool = False,
        device: str = "cpu",
    ):
        self.model_name = model_name
        self.models_dir = str(_resolve_models_dir(models_dir))
        self.tokenizer = KronosKLineTokenizer()
        self.execution_authority = EXECUTION_AUTHORITY
        self._official = None
        self.backend = BACKEND_HEURISTIC
        self.weights_loaded = False
        self.load_reason = "heuristic default"
        self._neural_failed_at = 0.0

        env_force = os.getenv("KRONOS_FORCE_HEURISTIC", "").lower() in {"1", "true", "yes"}
        if force_heuristic or env_force or not prefer_neural:
            self.load_reason = "forced heuristic backend"
            log.info(
                "Kronos Predictor initialized (%s) backend=%s — %s",
                model_name,
                self.backend,
                self.load_reason,
            )
            return

        official, reason = try_load_neural_bundle(model_name, self.models_dir, device=device)
        self.load_reason = reason
        if official is not None:
            self._official = official
            self.backend = BACKEND_NEURAL
            self.weights_loaded = True
            clear_heuristic_error(model_name)
            log.info("✅ Kronos neural backend ready (%s): %s — error cleared", model_name, reason)
        else:
            # Provide actionable fix, not just reason
            fix_hint = ""
            if "model folder missing" in reason or "tokenizer folder missing" in reason:
                fix_hint = " → fix: python research/kronos_downloader.py --model kronos-mini  (or set KRONOS_MODELS_DIR)"
            elif "torch/safetensors unavailable" in reason:
                fix_hint = " → fix: pip install torch safetensors --index-url https://download.pytorch.org/whl/cpu"
            elif "vendor" in reason:
                fix_hint = " → fix: git submodule update --init vendor/kronos_official"
            full_msg = f"Kronos neural unavailable ({model_name}); HEURISTIC Gaussian-drift active. Reason: {reason}{fix_hint} · backend labeled heuristic, not execution authority"
            if _should_warn_heuristic(f"kronos:{model_name}:{reason[:40]}"):
                log.warning(full_msg)

    @property
    def is_neural(self) -> bool:
        return self.backend == BACKEND_NEURAL and self._official is not None

    def _should_retry_neural(self) -> bool:
        """True when a demoted backend is past its retry cooldown.

        The runtime-failure demotion used to be permanent for the process
        lifetime even though the log claimed "will retry next call". A short
        cooldown makes auto-heal real without hammering a genuinely broken
        backend (missing weights simply stay heuristic and quiet).
        """
        if self._official is None or self.weights_loaded:
            return False
        return (_time.time() - self._neural_failed_at) >= _NEURAL_RETRY_COOLDOWN_SEC

    def backend_status(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "backend": self.backend,
            "weights_loaded": self.weights_loaded,
            "load_reason": self.load_reason,
            "models_dir": self.models_dir,
            "execution_authority": False,
        }

    def predict(
        self,
        symbol: str,
        ohlcv_history: List[Dict[str, float]],
        pred_len: int = 24,
        sample_count: int = 10,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> CandleForecast:
        """Execute multi-trajectory price forecasting (advisory only)."""
        if not ohlcv_history:
            return CandleForecast(
                symbol=symbol,
                timeframe="H1",
                current_close=100.0,
                predicted_close_mean=100.0,
                predicted_close_std=0.0,
                predicted_volatility_5d=0.02,
                forecast_horizon_bars=pred_len,
                backend=self.backend,
                model_name=self.model_name,
                weights_loaded=self.weights_loaded,
                execution_authority=False,
            )

        retry_neural = self._should_retry_neural()
        if self.is_neural or retry_neural:
            try:
                # Serialize official-predict: vendor modules keep shared mutable
                # state (rotary caches) that breaks under the strategy thread pool.
                with _KRONOS_PREDICT_LOCK:
                    result = self._predict_neural(
                        symbol, ohlcv_history, pred_len, sample_count, temperature, top_p
                    )
                if retry_neural:
                    self.backend = BACKEND_NEURAL
                    self.weights_loaded = True
                    self.load_reason = "neural recovered after runtime failure"
                    log.info("✅ Kronos neural backend recovered for %s after runtime failure", symbol)
                return result
            except Exception as exc:
                self.backend = BACKEND_HEURISTIC
                self.weights_loaded = False
                self._neural_failed_at = _time.time()
                self.load_reason = f"runtime neural failure: {exc}"
                if _should_warn_heuristic(f"neural_fallback:{symbol}", ttl_sec=600):
                    tb_tail = "\n".join(traceback.format_exc().strip().splitlines()[-6:])
                    log.warning(
                        "⚠️ Kronos NEURAL→HEURISTIC fallback for %s: %s — retrying neural after %ss cooldown. Failing frames:\n%s",
                        symbol, exc, _NEURAL_RETRY_COOLDOWN_SEC, tb_tail,
                    )
                else:
                    log.warning(
                        "⚠️ Kronos NEURAL→HEURISTIC fallback for %s: %s (dedup; neural retry after %ss cooldown)",
                        symbol, exc, _NEURAL_RETRY_COOLDOWN_SEC,
                    )

        # Only log heuristic usage once per symbol+reason, auto-clear when neural recovers
        warn_key = f"heuristic_use:{symbol}:{self.load_reason[:30]}"
        if _should_warn_heuristic(warn_key, ttl_sec=3600):
            log.warning(
                "Kronos HEURISTIC Gaussian-drift for %s (backend=%s, reason=%s) — not foundation-model alpha. Fix: see startup warning. Error auto-clears when weights available.",
                symbol, self.backend, self.load_reason,
            )
        return self._predict_heuristic(
            symbol, ohlcv_history, pred_len, sample_count, temperature
        )

    def _predict_heuristic(
        self,
        symbol: str,
        ohlcv_history: List[Dict[str, float]],
        pred_len: int,
        sample_count: int,
        temperature: float,
    ) -> CandleForecast:
        last_close, avg_return, volatility = _history_stats(ohlcv_history)

        trajectories = []
        final_prices = []
        rng = random.Random(hash(symbol) + len(ohlcv_history))

        for _ in range(max(1, sample_count)):
            path = [last_close]
            curr_p = last_close
            for _ in range(pred_len):
                step_ret = rng.gauss(avg_return, volatility * temperature)
                curr_p = max(0.01, curr_p * (1.0 + step_ret))
                path.append(round(curr_p, 4))
            trajectories.append(path)
            final_prices.append(path[-1])

        mean_final = sum(final_prices) / len(final_prices)
        std_final = math.sqrt(sum((p - mean_final) ** 2 for p in final_prices) / len(final_prices))
        vol_5d = max(0.001, volatility * math.sqrt(5.0 * 24.0))

        return CandleForecast(
            symbol=symbol,
            timeframe="H1",
            current_close=last_close,
            predicted_close_mean=round(mean_final, 4),
            predicted_close_std=round(std_final, 4),
            predicted_volatility_5d=round(vol_5d, 4),
            forecast_horizon_bars=pred_len,
            trajectories=trajectories,
            backend=BACKEND_HEURISTIC,
            model_name=self.model_name,
            weights_loaded=False,
            execution_authority=False,
        )

    def _predict_neural(
        self,
        symbol: str,
        ohlcv_history: List[Dict[str, float]],
        pred_len: int,
        sample_count: int,
        temperature: float,
        top_p: float,
    ) -> CandleForecast:
        import pandas as pd

        last_close, _avg_return, volatility = _history_stats(ohlcv_history)
        rows = []
        for candle in ohlcv_history:
            open_p = float(candle.get("open", last_close) or last_close)
            high_p = float(candle.get("high", open_p) or open_p)
            low_p = float(candle.get("low", open_p) or open_p)
            close_p = float(candle.get("close", open_p) or open_p)
            volume = float(candle.get("volume", 0.0) or 0.0)
            amount = float(candle.get("amount", volume * close_p) or (volume * close_p))
            rows.append(
                {
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": volume,
                    "amount": amount,
                }
            )
        df = pd.DataFrame(rows)
        # Official predictor expects Series with .dt accessors, not DatetimeIndex.
        x_index = pd.date_range("2024-01-01", periods=len(df), freq="h")
        y_start = x_index[-1] + pd.DateOffset(hours=1)
        y_index = pd.date_range(y_start, periods=pred_len, freq="h")
        x_ts = pd.Series(x_index)
        y_ts = pd.Series(y_index)

        # Multiple sample_count=1 draws give trajectory dispersion without relying on
        # the official mean-pooling path alone.
        draws = max(1, min(int(sample_count), 8))
        trajectories: List[List[float]] = []
        final_prices: List[float] = []
        for _ in range(draws):
            pred_df = self._official.predict(
                df,
                x_ts,
                y_ts,
                pred_len=pred_len,
                T=float(temperature),
                top_k=0,
                top_p=float(top_p),
                sample_count=1,
                verbose=False,
            )
            path = [last_close] + [float(v) for v in pred_df["close"].tolist()]
            trajectories.append([round(p, 4) for p in path])
            final_prices.append(path[-1])

        mean_final = sum(final_prices) / len(final_prices)
        std_final = (
            math.sqrt(sum((p - mean_final) ** 2 for p in final_prices) / len(final_prices))
            if len(final_prices) > 1
            else max(1e-6, volatility * last_close * math.sqrt(pred_len))
        )
        vol_5d = max(0.001, volatility * math.sqrt(5.0 * 24.0))

        return CandleForecast(
            symbol=symbol,
            timeframe="H1",
            current_close=last_close,
            predicted_close_mean=round(mean_final, 4),
            predicted_close_std=round(std_final, 4),
            predicted_volatility_5d=round(vol_5d, 4),
            forecast_horizon_bars=pred_len,
            trajectories=trajectories,
            backend=BACKEND_NEURAL,
            model_name=self.model_name,
            weights_loaded=True,
            execution_authority=False,
        )

    # ── Multi-Dimensional Forecasting (Paper §3-§4: OHLCVA hierarchical tokens) ──

    def predict_multi_dim(
        self,
        symbol: str,
        ohlcv_history: List[Dict[str, float]],
        pred_len: int = 24,
        sample_count: int = 10,
        temperature: float = 1.0,
        top_p: float = 0.9,
        timeframe: str = "H1",
    ) -> KronosMultiDimForecast:
        """Multi-dimensional OHLCVA forecasting across all 6 K-line fields.

        Preserves paper's hierarchical BSQ tokenization: each bar's OHLCVA is
        tokenized into coarse+fine subtokens and autoregressively predicted.
        Returns per-bar mean/std OHLCVA paths plus full trajectories and
        advanced volatility term structure (5d/10d/20d) for risk management.
        """
        if not ohlcv_history:
            empty_ohlcv = {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0, "amount": 100000.0}
            return KronosMultiDimForecast(
                symbol=symbol, timeframe=timeframe, current_ohlcv=empty_ohlcv,
                horizon_bars=pred_len, mean_path=[], std_path=[], trajectories=[],
                confidence=0.0, predicted_volatility_5d=0.02, predicted_volatility_10d=0.028,
                predicted_volatility_20d=0.04, vol_term_structure={"5d": 0.02, "10d": 0.028, "20d": 0.04},
                vol_regime="NORMAL", risk_adjustment_factor=0.9,
                backend=self.backend, model_name=self.model_name,
                weights_loaded=self.weights_loaded, execution_authority=False,
            )
        retry_neural = self._should_retry_neural()
        if self.is_neural or retry_neural:
            try:
                with _KRONOS_PREDICT_LOCK:
                    result = self._predict_neural_multi_dim(symbol, ohlcv_history, pred_len, sample_count, temperature, top_p, timeframe)
                if retry_neural:
                    self.backend = BACKEND_NEURAL
                    self.weights_loaded = True
                    self.load_reason = "neural recovered after runtime failure"
                    log.info("✅ Kronos neural multi-dim recovered for %s after runtime failure", symbol)
                return result
            except Exception as exc:
                self.backend = BACKEND_HEURISTIC
                self.weights_loaded = False
                self._neural_failed_at = _time.time()
                self.load_reason = f"runtime neural multi-dim failure: {exc}"
                tb_tail = "\n".join(traceback.format_exc().strip().splitlines()[-6:])
                log.warning("⚠️ Kronos multi-dim NEURAL→HEURISTIC fallback for %s: %s — retrying after %ss. Failing frames:\n%s",
                            symbol, exc, _NEURAL_RETRY_COOLDOWN_SEC, tb_tail)
        return self._predict_heuristic_multi_dim(symbol, ohlcv_history, pred_len, sample_count, temperature, timeframe)

    def _predict_heuristic_multi_dim(
        self, symbol: str, ohlcv_history: List[Dict[str, float]],
        pred_len: int, sample_count: int, temperature: float, timeframe: str
    ) -> KronosMultiDimForecast:
        last_close, avg_return, volatility = _history_stats(ohlcv_history)
        current_ohlcv = _ohlcv_stats_full(ohlcv_history)
        intra_vol_scale = volatility * 0.6
        trajectories: List[List[Dict[str, float]]] = []
        rng = random.Random(hash(symbol) + len(ohlcv_history))
        for _ in range(max(1, sample_count)):
            path: List[Dict[str, float]] = []
            curr_p = last_close
            for _ in range(pred_len):
                step_ret = rng.gauss(avg_return, volatility * temperature)
                open_p = curr_p
                close_p = max(0.01, open_p * (1.0 + step_ret))
                high_p = max(open_p, close_p) * (1.0 + abs(rng.gauss(0, intra_vol_scale)))
                low_p = min(open_p, close_p) * (1.0 - abs(rng.gauss(0, intra_vol_scale)))
                volume = abs(rng.gauss(current_ohlcv["volume"] or 1000.0, (current_ohlcv["volume"] or 1000.0) * 0.25))
                amount = close_p * volume * rng.uniform(0.9, 1.1)
                bar = {
                    "open": round(open_p, 4), "high": round(high_p, 4), "low": round(low_p, 4),
                    "close": round(close_p, 4), "volume": round(volume, 2), "amount": round(amount, 2)
                }
                path.append(bar)
                curr_p = close_p
            trajectories.append(path)
        # Aggregate mean/std per bar across trajectories
        horizon = pred_len
        mean_path: List[Dict[str, float]] = []
        std_path: List[Dict[str, float]] = []
        for idx in range(horizon):
            vals = {k: [traj[idx][k] for traj in trajectories] for k in ("open", "high", "low", "close", "volume", "amount")}
            mean_bar = {k: round(sum(v) / len(v), 4) for k, v in vals.items()}
            std_bar = {}
            for k, v in vals.items():
                m = mean_bar[k]
                var = sum((x - m) ** 2 for x in v) / len(v) if len(v) > 1 else 0.0
                std_bar[k] = round(math.sqrt(var), 4)
            mean_path.append(mean_bar)
            std_path.append(std_bar)
        # Confidence from close dispersion at horizon end
        final_closes = [traj[-1]["close"] for traj in trajectories] if trajectories else [last_close]
        m_final = sum(final_closes) / len(final_closes)
        std_final = math.sqrt(sum((p - m_final) ** 2 for p in final_closes) / len(final_closes)) if len(final_closes) > 1 else volatility * last_close
        confidence = max(0.0, min(1.0, 1.0 - (std_final / max(m_final, 1e-6) * 5.0)))
        if self.backend != BACKEND_NEURAL:
            confidence = min(confidence, 0.55)
        # Advanced volatility term structure
        base_vol = volatility
        term = _advanced_vol_term_structure(base_vol, [12, 24, 48, 96], hours_per_bar=1)
        vol_5d = term["5d"]; vol_10d = term["10d"]; vol_20d = term["20d"]
        vol_regime, risk_adj = _vol_regime_and_risk(vol_5d)
        return KronosMultiDimForecast(
            symbol=symbol, timeframe=timeframe, current_ohlcv=current_ohlcv,
            horizon_bars=pred_len, mean_path=mean_path, std_path=std_path,
            trajectories=trajectories, confidence=confidence,
            predicted_volatility_5d=round(vol_5d, 5), predicted_volatility_10d=round(vol_10d, 5),
            predicted_volatility_20d=round(vol_20d, 5), vol_term_structure=term,
            vol_regime=vol_regime, risk_adjustment_factor=risk_adj,
            backend=BACKEND_HEURISTIC, model_name=self.model_name,
            weights_loaded=False, execution_authority=False,
        )

    def _predict_neural_multi_dim(
        self, symbol: str, ohlcv_history: List[Dict[str, float]],
        pred_len: int, sample_count: int, temperature: float, top_p: float, timeframe: str
    ) -> KronosMultiDimForecast:
        import pandas as pd
        last_stats = _ohlcv_stats_full(ohlcv_history)
        last_close, _avg_ret, volatility = _history_stats(ohlcv_history)
        rows = []
        for candle in ohlcv_history:
            open_p = float(candle.get("open", last_close) or last_close)
            high_p = float(candle.get("high", open_p) or open_p)
            low_p = float(candle.get("low", open_p) or open_p)
            close_p = float(candle.get("close", open_p) or open_p)
            volume = float(candle.get("volume", 0.0) or 0.0)
            amount = float(candle.get("amount", volume * close_p) or (volume * close_p))
            rows.append({"open": open_p, "high": high_p, "low": low_p, "close": close_p, "volume": volume, "amount": amount})
        df = pd.DataFrame(rows)
        x_index = pd.date_range("2024-01-01", periods=len(df), freq="h")
        y_start = x_index[-1] + pd.DateOffset(hours=1)
        y_index = pd.date_range(y_start, periods=pred_len, freq="h")
        x_ts = pd.Series(x_index); y_ts = pd.Series(y_index)
        draws = max(1, min(int(sample_count), 8))
        trajectories: List[List[Dict[str, float]]] = []
        for _ in range(draws):
            pred_df = self._official.predict(df, x_ts, y_ts, pred_len=pred_len, T=float(temperature), top_k=0, top_p=float(top_p), sample_count=1, verbose=False)
            path = []
            for _, row in pred_df.iterrows():
                bar = {
                    "open": round(float(row["open"]), 4), "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4), "close": round(float(row["close"]), 4),
                    "volume": round(float(row["volume"]), 2), "amount": round(float(row["amount"]), 2),
                }
                path.append(bar)
            trajectories.append(path)
        # aggregate
        mean_path: List[Dict[str, float]] = []; std_path: List[Dict[str, float]] = []
        for idx in range(pred_len):
            vals = {k: [traj[idx][k] for traj in trajectories] for k in ("open", "high", "low", "close", "volume", "amount")}
            mean_bar = {k: round(sum(v) / len(v), 4) for k, v in vals.items()}
            std_bar = {}
            for k, v in vals.items():
                m = mean_bar[k]
                var = sum((x - m) ** 2 for x in v) / len(v) if len(v) > 1 else 0.0
                std_bar[k] = round(math.sqrt(var), 4)
            mean_path.append(mean_bar); std_path.append(std_bar)
        final_closes = [traj[-1]["close"] for traj in trajectories]
        m_final = sum(final_closes) / len(final_closes)
        std_final = math.sqrt(sum((p - m_final) ** 2 for p in final_closes) / len(final_closes)) if len(final_closes) > 1 else volatility * last_close
        # Estimate base vol from trajectory dispersion + history vol (paper volatility MAE term)
        traj_vol = std_final / max(m_final, 1e-6) / math.sqrt(pred_len) if pred_len > 0 else volatility
        base_vol = max(1e-6, 0.6 * volatility + 0.4 * traj_vol)
        term = _advanced_vol_term_structure(base_vol, [12, 24, 48, 96], hours_per_bar=1)
        vol_5d = term["5d"]; vol_10d = term["10d"]; vol_20d = term["20d"]
        vol_regime, risk_adj = _vol_regime_and_risk(vol_5d)
        confidence = max(0.0, min(1.0, 1.0 - (std_final / max(m_final, 1e-6) * 5.0)))
        return KronosMultiDimForecast(
            symbol=symbol, timeframe=timeframe, current_ohlcv=last_stats,
            horizon_bars=pred_len, mean_path=mean_path, std_path=std_path,
            trajectories=trajectories, confidence=confidence,
            predicted_volatility_5d=round(vol_5d, 5), predicted_volatility_10d=round(vol_10d, 5),
            predicted_volatility_20d=round(vol_20d, 5), vol_term_structure=term,
            vol_regime=vol_regime, risk_adjustment_factor=risk_adj,
            backend=BACKEND_NEURAL, model_name=self.model_name,
            weights_loaded=True, execution_authority=False,
        )

    def get_advanced_volatility(self, symbol: str, ohlcv_history: List[Dict[str, float]], horizon_bars: int = 24) -> Dict[str, Any]:
        """Convenience: return only volatility term-structure advisory from multi-dim path."""
        md = self.predict_multi_dim(symbol, ohlcv_history, pred_len=horizon_bars)
        return {
            "symbol": symbol, "horizon_bars": horizon_bars,
            "volatility_5d": md.predicted_volatility_5d, "volatility_10d": md.predicted_volatility_10d,
            "volatility_20d": md.predicted_volatility_20d, "term_structure": md.vol_term_structure,
            "regime": md.vol_regime, "risk_adjustment_factor": md.risk_adjustment_factor,
            "confidence": round(md.confidence, 4), "backend": md.backend, "weights_loaded": md.weights_loaded,
            "execution_authority": False,
        }

    def generate_synthetic_regime(
        self,
        symbol: str,
        start_price: float = 100.0,
        length: int = 100,
        regime: str = "bull",
        seed: int = 42,
    ) -> List[Dict[str, float]]:
        """Generate synthetic market scenarios for Monte Carlo stress testing.

        Synthetic regimes remain heuristic even when neural weights are loaded;
        they are labeled via metadata on callers that wrap this output.
        ``seed`` keeps paths reproducible for offline RL research.
        """
        drift_map = {"bull": 0.0015, "bear": -0.0015, "volatile": 0.0001, "range": 0.0}
        vol_map = {"bull": 0.008, "bear": 0.015, "volatile": 0.025, "range": 0.005}

        drift = drift_map.get(regime, 0.0005)
        vol = vol_map.get(regime, 0.01)
        rng = random.Random(int(seed))

        candles = []
        curr_p = start_price

        for idx in range(length):
            ret = rng.gauss(drift, vol)
            open_p = curr_p
            close_p = max(0.01, open_p * (1.0 + ret))
            high_p = max(open_p, close_p) * (1.0 + abs(rng.gauss(0, vol * 0.5)))
            low_p = min(open_p, close_p) * (1.0 - abs(rng.gauss(0, vol * 0.5)))
            volume = abs(rng.gauss(1000, 300))

            candles.append({
                "bar": idx,
                "open": round(open_p, 4),
                "high": round(high_p, 4),
                "low": round(low_p, 4),
                "close": round(close_p, 4),
                "volume": round(volume, 2),
                "backend": BACKEND_HEURISTIC,
                "source": "kronos_synthetic_regime",
                "execution_authority": False,
            })
            curr_p = close_p

        return candles


# ── Kronos Memory Integrator ────────────────────────────────────────────────

class KronosMemoryIntegrator:
    """Bridge connecting Kronos model output → TradingMemory L1/L2 and MacroGraph."""

    @staticmethod
    def format_kronos_l1_atom(forecast: CandleForecast) -> Dict[str, Any]:
        """Convert Kronos forecast into a L1 Atomic Trading Fact for TradingMemory."""
        direction = (
            "BULLISH"
            if forecast.predicted_return_pct > 0.5
            else "BEARISH"
            if forecast.predicted_return_pct < -0.5
            else "NEUTRAL"
        )
        backend = getattr(forecast, "backend", BACKEND_HEURISTIC)
        return {
            "type": "fact",
            "subject_type": "KRONOS_FORECAST",
            "subject_key": f"kronos:{forecast.symbol}:{forecast.timeframe}",
            "content": (
                f"Kronos ({backend}) predicts {forecast.symbol} {direction} "
                f"({forecast.predicted_return_pct:+.2f}%) over {forecast.forecast_horizon_bars} bars"
            ),
            "confidence": forecast.confidence_score,
            "status": "CANDIDATE",
            "execution_authority": False,
            "metadata": {
                "predicted_return_pct": forecast.predicted_return_pct,
                "volatility_5d": forecast.predicted_volatility_5d,
                "trajectories": len(forecast.trajectories),
                "source": forecast.model_name or "kronos",
                "backend": backend,
                "weights_loaded": bool(getattr(forecast, "weights_loaded", False)),
                "execution_authority": False,
            },
        }
