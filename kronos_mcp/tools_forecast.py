from __future__ import annotations
import sys
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Ensure research / vendor / core are importable when launched as `python -m kronos_mcp.main`
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RESEARCH = _REPO_ROOT / "research"
for p in (str(_REPO_ROOT), str(_RESEARCH)):
    if p not in sys.path:
        sys.path.insert(0, p)

from typing import Any, Dict, List

from .utils import mcp
from .schemas import (
    ForecastParams, BatchForecastParams, TokenizeParams, VolatilityParams,
    RiskSizingParams, SyntheticParams, MultiAssetSyntheticParams,
    AlphaParams, MT5MultiDimParams, MT5VolParams, StatusParams,
)


def _get_predictor(model_key: str = "kronos-mini"):
    # Lazy import to avoid heavy torch at server startup unless needed
    from kronos_bridge import KronosPredictor
    return KronosPredictor(model_name=model_key)


def _get_integrator(model_key: str = "kronos-mini"):
    from kronos_trading_integrator import KronosTradingIntegrator
    return KronosTradingIntegrator(model_key=model_key)


@mcp.tool
def kronos_status(params: StatusParams) -> Dict[str, Any]:
    """Paper-cited status + backend health for Kronos (arXiv:2508.02739).

    Returns model catalog, tokenizer mapping, backend (neural vs heuristic),
    load reason, context length, paper abstract highlights, and capability list
    (multi-dim OHLCVA forecasting, 9% lower MAE vol forecasting, synthetic
    fidelity +22%). All outputs advisory (execution_authority=False).
    """
    model_key = params.model_key.strip() or "kronos-mini"
    from kronos_downloader import MODELS
    from kronos_bridge import KronosPredictor
    predictor = KronosPredictor(model_name=model_key)
    status = predictor.backend_status()
    # Tokenizer info
    tok_info = {}
    try:
        from pathlib import Path as P
        repo = P(__file__).resolve().parent.parent
        models_dir = repo / "models"
        tok_map = {"kronos-mini": "kronos-tokenizer-2k", "kronos-small": "kronos-tokenizer-base", "kronos-base": "kronos-tokenizer-base"}
        tok_key = tok_map.get(model_key.lower(), "kronos-tokenizer-2k")
        tok_path = models_dir / tok_key
        tok_info = {"tokenizer_key": tok_key, "tokenizer_exists": tok_path.exists(), "path": str(tok_path)}
    except Exception:
        tok_info = {}
    return {
        "paper": {
            "title": "Kronos: A Foundation Model for the Language of Financial Markets",
            "arxiv": "2508.02739",
            "url": "https://arxiv.org/abs/2508.02739",
            "summary": "Two-phase BSQ hierarchical tokenizer (coarse+fine subtokens per K-line) + autoregressive decoder-only Transformer pretrained on 12B K-lines from 45 exchanges/7 granularities. SOTA price RankIC +93% vs TSFM/+87% vs non-pretrained, 9% lower MAE vol, 22% generative fidelity gain.",
            "framework": "discretize OHLCVA -> hierarchical coarse/fine tokens -> next-token prediction (autoregressive)",
            "input_features": ["open", "high", "low", "close", "volume", "amount"],
            "tokenizer_bits": "s1_bits + s2_bits hierarchical (e.g. 2k codebook for mini) - BSQuantizer with spherical quantization",
            "pretraining_corpus": "12B K-lines, 45 global exchanges, 7 temporal granularities",
        },
        "models_catalog": MODELS,
        "requested_model": model_key,
        "backend": status["backend"],
        "weights_loaded": status["weights_loaded"],
        "load_reason": status.get("load_reason", ""),
        "models_dir": status.get("models_dir", ""),
        "tokenizer": tok_info,
        "capabilities": [
            "multi_dimensional_ohlcv_forecasting (6-dim K-line: O/H/L/C/V/A per bar, hierarchical coarse->fine)",
            "volatility_forecasting (5d/10d/20d term structure, regime LOW/NORMAL/ELEVATED/HIGH, risk factor 0.5-1.0)",
            "synthetic_generation (regime-specific + correlated multi-asset GARCH+Merton jumps+copula)",
            "alpha_factor (tanh-scaled weighted by confidence [-1,1] for multi-factor ensemble)",
            "risk_adjusted_sizing (vol-aware lot sizing wired to RiskManager 1% rule)",
            "mt5_native (normalize copy_rates + DOM imbalance & spread aware)",
            "rl_simulator (KronosRLMarketSimulator / MT5KronosRLSimulator)",
        ],
        "execution_authority": False,
        "honesty_contract": "Neural only when safetensors+torch+vendor loads; else heuristic Gaussian-drift clearly labeled. Never claims execution authority.",
    }


@mcp.tool
def kronos_tokenize(params: TokenizeParams) -> Dict[str, Any]:
    """Tokenize OHLCVA K-lines into BSQ codebook indices (paper §3.1 hierarchical tokenizer).

    Uses vendor BSQuantizer tokenizer when neural weights are present, otherwise
    the heuristic hash tokenizer. Returns per-bar tokens and vocab info.
    Advisory only.
    """
    from kronos_bridge import KronosKLineTokenizer
    tok = KronosKLineTokenizer(codebook_size=params.codebook_size)
    tokens = tok.tokenize_series(params.ohlcv_history)
    return {
        "token_count": len(tokens),
        "tokens": tokens[:200],  # cap for payload size
        "codebook_size": params.codebook_size,
        "sample_tokens_full": len(tokens) <= 200,
        "paper_note": "Paper tokenizer: Transformer encoder -> BSQuantizer (s1_bits coarse + s2_bits fine) -> decoder; each K-line quantized into dual sub-tokens with hierarchical reconstruction loss.",
        "execution_authority": False,
    }


@mcp.tool
def kronos_forecast(params: ForecastParams) -> Dict[str, Any]:
    """Legacy close-only forecast (back-compat) - use kronos_forecast_multi_dim for full OHLCVA."""
    predictor = _get_predictor(params.model_key)
    forecast = predictor.predict(
        symbol=params.symbol,
        ohlcv_history=params.ohlcv_history,
        pred_len=params.pred_len,
        sample_count=params.sample_count,
        temperature=params.temperature,
        top_p=params.top_p,
    )
    return {
        **forecast.as_advisory_dict(),
        "paper_alignment": "Price forecasting RankIC +93% vs TSFM; zero-shot hierarchical BSQ.",
        "note": "For full 6-dim OHLCVA use kronos_forecast_multi_dim.",
    }


@mcp.tool
def kronos_forecast_multi_dim(params: ForecastParams) -> Dict[str, Any]:
    """Multi-dimensional OHLCVA forecasting - paper core: 6-dim K-line per bar.

    Hierarchical BSQ tokens (coarse s1 + fine s2) autoregressively predicted.
    Returns per-bar OHLCVA means/stds, confidence from trajectory dispersion,
    advanced vol term structure (5d/10d/20d), vol regime, risk factor,
    and full trajectories (sample_count × horizon). Advisory only.
    """
    predictor = _get_predictor(params.model_key)
    md = predictor.predict_multi_dim(
        symbol=params.symbol,
        ohlcv_history=params.ohlcv_history,
        pred_len=params.pred_len,
        sample_count=params.sample_count,
        temperature=params.temperature,
        top_p=params.top_p,
        timeframe=params.timeframe,
    )
    d = md.as_advisory_dict()
    d["trajectories"] = md.trajectories  # include full OHLCVA trajectories for agents
    d["std_path"] = md.std_path
    d["paper_alignment"] = "Hierarchical coarse->fine subtokens preserve price-volume interactions; autoregressively predicts both levels per future step."
    return d


@mcp.tool
def kronos_forecast_batch(params: BatchForecastParams) -> Dict[str, Any]:
    """Batch multi-dim forecasting for correlated multi-asset / autonomous fleet.

    Runs Kronos per-symbol sequentially (shared-model sequential batch) and
    returns per-symbol mean_path + vol term structure for cross-asset risk.
    """
    from kronos_bridge import KronosPredictor
    predictor = KronosPredictor(model_name=params.model_key)
    results: List[Dict[str, Any]] = []
    for sym, hist in zip(params.symbols, params.ohlcv_histories):
        md = predictor.predict_multi_dim(
            symbol=sym, ohlcv_history=hist, pred_len=params.pred_len,
            sample_count=params.sample_count, temperature=params.temperature,
            top_p=params.top_p,
        )
        d = md.as_advisory_dict()
        # trim for batch payload
        d_trim = {k: d[k] for k in ("symbol", "timeframe", "horizon_bars", "predicted_return_pct", "predicted_close_mean", "confidence", "volatility", "trajectory_count", "backend", "weights_loaded")}
        d_trim["horizon_close_mean"] = d["predicted_close_mean"]
        results.append(d_trim)
    return {
        "batch_size": len(results),
        "pred_len": params.pred_len,
        "results": results,
        "execution_authority": False,
    }


@mcp.tool
def kronos_volatility_forecast(params: VolatilityParams) -> Dict[str, Any]:
    """Advanced volatility forecasting - paper §4: 9% lower MAE vs baselines.

    Returns 5d/10d/20d predicted vol, per-horizon term_structure, vol_regime
    (LOW/NORMAL/ELEVATED/HIGH), and risk_adjustment_factor (0.5-1.0) to gate
    RiskManager sizing. Also returns confidence and backend label.
    """
    integrator = _get_integrator(params.model_key)
    bundle = integrator.get_volatility_term_structure(params.symbol, params.ohlcv_history, horizon_bars=params.horizon_bars)
    bundle["paper_alignment"] = "Volatility forecasting via trajectory dispersion + history GARCH proxy; 9% MAE improvement, 22% synthetic fidelity gain."
    return bundle


@mcp.tool
def kronos_volatility_term_structure(params: VolatilityParams) -> Dict[str, Any]:
    """Alias for kronos_volatility_forecast - explicit term-structure view."""
    return kronos_volatility_forecast(params)


@mcp.tool
def kronos_risk_sizing(params: RiskSizingParams) -> Dict[str, Any]:
    """Volatility-aware position sizing wired to RiskManager 1% rule + vol regime.

    Uses Kronos predicted vol term structure to compute dollar_risk_allowed,
    estimated SL distance, and recommended MT5 lots (1 lot = 100k). Respects
    risk_adjustment_factor (0.5-1.0) from HIGH->LOW vol regimes. Advisory only.
    """
    integrator = _get_integrator(params.model_key)
    return integrator.get_risk_adjusted_sizing(
        symbol=params.symbol,
        ohlcv_history=params.ohlcv_history,
        account_balance=params.account_balance,
        risk_per_trade_pct=params.risk_per_trade_pct,
        entry_price=params.entry_price,
        stop_loss=params.stop_loss,
    )


@mcp.tool
def kronos_alpha_factor(params: AlphaParams) -> Dict[str, Any]:
    """Kronos alpha signal for multi-factor ensemble [-1,1].

    tanh(predicted_return/2) * confidence -> weighted_alpha; direction BUY/SELL/NEUTRAL
    at ±0.15 thresholds. Use as one sleeve in ensemble with DOM/TA/Macro.
    Paper RankIC +93% vs TSFM establishes alpha sleeve.
    """
    integrator = _get_integrator(params.model_key)
    base = integrator.get_alpha_factor(params.symbol, params.ohlcv_history, pred_len=params.pred_len)
    # also attach multi-dim context for richer ensemble
    try:
        md = integrator.get_multi_dim_forecast(params.symbol, params.ohlcv_history, pred_len=params.pred_len)
        base["multi_dim"] = {"predicted_return_pct": md["predicted_return_pct"], "confidence": md["confidence"], "volatility_regime": md["volatility"]["regime"]}
    except Exception as exc:
        logger.debug("DOM data failed: %s", exc)
    base["paper_alignment"] = "Ensemble sleeve; micro model contributed to RankIC lift."
    return base


@mcp.tool
def kronos_synthetic_generate(params: SyntheticParams) -> Dict[str, Any]:
    """Single-asset synthetic K-line generation - paper generative fidelity +22%.

    Produces regime-specific OHLCVA bars for Monte-Carlo/CPCV stress testing.
    Heuristic regime generator (Merton-like drift/vol per regime). Advisory.
    """
    predictor = _get_predictor("kronos-mini")
    candles = predictor.generate_synthetic_regime(
        symbol=params.symbol, start_price=params.start_price, length=params.length, regime=params.regime, seed=params.seed,
    )
    return {
        "symbol": params.symbol,
        "regime": params.regime,
        "length": len(candles),
        "bars": candles,
        "paper_alignment": "Generative fidelity measured via distribution distance; Kronos synthetic +22% vs baselines.",
        "execution_authority": False,
    }


@mcp.tool
def kronos_synthetic_multi_asset(params: MultiAssetSyntheticParams) -> Dict[str, Any]:
    """Correlated multi-asset synthetic dataset (GARCH+copula+Merton jumps).

    Uses intelligence.kronos_synthetic_generator: Cholesky-correlated normals,
    GARCH(1,1) volatility clustering, Merton Poisson jumps, regime-specific
    drift/vol. For cross-asset contagion, heat-budget, and tail-risk tests.
    """
    try:
        from intelligence.kronos_synthetic_generator import generate_correlated_multi_asset_synthetic_dataset
    except Exception as exc:
        # fallback via research path
        sys.path.insert(0, str((_REPO_ROOT / "intelligence").resolve()))
        from kronos_synthetic_generator import generate_correlated_multi_asset_synthetic_dataset  # type: ignore
    dataset = generate_correlated_multi_asset_synthetic_dataset(
        symbols=params.symbols,
        length_bars=params.length_bars,
        regime=params.regime,
        jump_intensity=params.jump_intensity,
        seed=params.seed,
    )
    # serialize with preview
    preview = {k: v[:5] for k, v in dataset.items()}
    return {
        "symbols": list(params.symbols),
        "regime": params.regime,
        "length_bars": params.length_bars,
        "asset_count": len(dataset),
        "preview_bars": preview,
        "full_bars_per_asset": {k: len(v) for k, v in dataset.items()},
        "bars": dataset,  # full payload for agents
        "paper_alignment": "Joint generative fidelity via copula-aware simulation; validates heat & tail.",
        "execution_authority": False,
    }


@mcp.tool
def kronos_mt5_forecast_multi_dim(params: MT5MultiDimParams) -> Dict[str, Any]:
    """MT5-native multi-dim forecast: normalize MT5 copy_rates -> OHLCVA -> Kronos.

    Accepts raw MT5 rates (dict tuple schema) and DOM-aware via MT5 adapter.
    Returns same payload as kronos_forecast_multi_dim with MT5 provenance.
    """
    # import locally to keep sys.path handling consistent
    sys.path.insert(0, str((_REPO_ROOT / "research").resolve()))
    from kronos_mt5_adapter import KronosMT5Adapter
    adapter = KronosMT5Adapter(model_key=params.model_key)
    # Normalize tolerates both list[dict] and list[tuple]
    return adapter.get_mt5_multi_dim_forecast(
        symbol=params.symbol, mt5_rates=params.mt5_rates, pred_len=params.pred_len,
        sample_count=params.sample_count, temperature=params.temperature, top_p=params.top_p,
    )


@mcp.tool
def kronos_mt5_advanced_volatility(params: MT5VolParams) -> Dict[str, Any]:
    """MT5-native advanced volatility + risk sizing.

    Returns 5d/10d/20d term structure + regime + risk_adjustment_factor + optional
    lot sizing if account_balance provided. MT5 spread-aware. Advisory only.
    """
    sys.path.insert(0, str((_REPO_ROOT / "research").resolve()))
    from kronos_mt5_adapter import KronosMT5Adapter
    from kronos_trading_integrator import KronosTradingIntegrator
    from kronos_bridge import KronosPredictor  # noqa
    adapter = KronosMT5Adapter(model_key=params.model_key)
    base = adapter.get_mt5_advanced_volatility(params.symbol, params.mt5_rates, horizon_bars=params.horizon_bars)
    # optionally attach sizing
    if params.account_balance is not None and params.risk_per_trade_pct is not None:
        sizing = adapter.get_mt5_risk_adjusted_sizing(
            params.symbol, params.mt5_rates, account_balance=float(params.account_balance),
            risk_per_trade_pct=float(params.risk_per_trade_pct),
        )
        base["risk_sizing"] = sizing
    return base
