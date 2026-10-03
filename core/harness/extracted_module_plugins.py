"""HarnessPlugin adapters for the 9 extracted modules (01-09).

Maps the plugin interfaces from docs/extracted_modules/plugins/PLUGIN_INTERFACE.md
to existing codebase implementations.  Each mount function:
  - Uses lazy imports so missing optional deps never break startup
  - Provides a clean facade that the orchestrator and LLM prompt can call
  - Declares dependencies on existing plugins where needed

Module 01: Core Trading Metrics
Module 02: Strategy Validation Pipeline
Module 03: Alpha Sources & Market Inefficiencies
Module 04: Regime Detection & Dynamic Strategy Routing
Module 05: News Trading & Leading Indicators
Module 06: Risk Management & Position Sizing
Module 07: Autonomous Agent Architecture
Module 08: Alternative Data & Geospatial Analysis
Module 09: Research Frameworks & Self-Improvement
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.harness.kernel import HarnessPlugin, TradingContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module 01 — Core Trading Metrics
# ---------------------------------------------------------------------------
def _mount_extracted_metrics(ctx: TradingContext) -> None:
    """Module 01: Core Trading Metrics (EV, RoR, Sharpe, Sortino, Kelly, DSR, WFE, IC, R-Multiple, SQN).

    Wraps: core.metrics.*
    """
    from core.metrics import (
        calculate_sharpe_ratio,
        calculate_sortino_ratio,
        calculate_deflated_sharpe_ratio,
        calculate_kelly_criterion,
        calculate_hurst_exponent,
        calculate_wfe,
        calculate_expected_max_losing_streak,
        calculate_kelly_criterion as _kelly_fn,
    )
    calculate_kelly_fraction = _kelly_fn  # alias for interface compat

    def ev(trades: list) -> float:
        """Expected Value per trade in R-multiples."""
        if not trades:
            return 0.0
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        p_win = len(wins) / len(trades)
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean([abs(l) for l in losses])) if losses else 0.0
        return (p_win * avg_win) - ((1 - p_win) * avg_loss)

    def ror(edge: float, capital_units: int) -> float:
        """Risk of Ruin approximation."""
        if edge <= 0:
            return 1.0
        return ((1 - edge) / (1 + edge)) ** capital_units

    def r_multiple(entry: float, exit_price: float, stop: float) -> float:
        """Calculate R-Multiple for a trade."""
        risk = abs(entry - stop)
        if risk == 0:
            return 0.0
        return (exit_price - entry) / risk

    def quarter_kelly(win_rate: float, avg_win_r: float) -> float:
        """Conservative quarter-Kelly sizing fraction."""
        k = calculate_kelly_fraction(win_rate, avg_win_r)
        return max(0.0, k * 0.25)

    def sqn(n_trades: int, mean_r: float, std_r: float) -> float:
        """System Quality Number."""
        if std_r <= 0 or n_trades <= 0:
            return 0.0
        return (n_trades ** 0.5) * (mean_r / std_r)

    def streak_prob(win_rate: float, k: int) -> float:
        """Probability of k consecutive losses."""
        return (1 - win_rate) ** k

    def calmar(cagr: float, max_dd: float) -> float:
        """Calmar Ratio = CAGR / |Max Drawdown|."""
        if max_dd == 0:
            return 0.0
        return cagr / abs(max_dd)

    facade = {
        "ev": ev,
        "ror": ror,
        "r_multiple": r_multiple,
        "sharpe": calculate_sharpe_ratio,
        "sortino": calculate_sortino_ratio,
        "dsr": calculate_deflated_sharpe_ratio,
        "kelly": calculate_kelly_fraction,
        "quarter_kelly": quarter_kelly,
        "hurst": calculate_hurst_exponent,
        "wfe": calculate_wfe,
        "max_streak": calculate_expected_max_losing_streak,
        "sqn": sqn,
        "streak_prob": streak_prob,
        "calmar": calmar,
    }
    ctx.provide("extracted_metrics", facade)
    logger.info("ExtractedMetricsPlugin mounted — 14 metric functions available.")


# ---------------------------------------------------------------------------
# Module 02 — Strategy Validation Pipeline
# ---------------------------------------------------------------------------
def _mount_extracted_validation(ctx: TradingContext) -> None:
    """Module 02: 13-Stage Validation Gauntlet (Monte Carlo, CPCV, Stress, Holdout).

    Wraps: core.cpcv_backtester, core.walk_forward_validator, core.overfit_detector,
           core.trial_ledger, research.research_validation
    """
    from core.cpcv_backtester import run_combinatorial_purged_backtest
    from core.overfit_detector import detect_overfitting
    from core.trial_ledger import get_trial_ledger

    def monte_carlo_shuffle(returns: np.ndarray, n_sims: int = 1000) -> Dict[str, Any]:
        """Shuffle trade sequences to test path independence."""
        if len(returns) < 2:
            return {"prob_profitability": 0.0, "p5_sharpe": 0.0, "passed": False}
        base_sharpe = float(np.mean(returns) / np.std(returns)) if np.std(returns) > 0 else 0.0
        sharpes = []
        for _ in range(n_sims):
            shuffled = np.random.permutation(returns)
            s = float(np.mean(shuffled) / np.std(shuffled)) if np.std(shuffled) > 0 else 0.0
            sharpes.append(s)
        arr = np.array(sharpes)
        return {
            "base_sharpe": round(base_sharpe, 4),
            "prob_profitability": round(float(np.mean(arr > 0)), 4),
            "p5_sharpe": round(float(np.percentile(arr, 5)), 4),
            "median_sharpe": round(float(np.median(arr)), 4),
            "passed": bool(np.mean(arr > 0) >= 0.75 and np.percentile(arr, 5) > 0),
        }

    def stress_test_scenarios() -> List[Dict[str, Any]]:
        """Return historical stress scenario descriptions for manual application."""
        return [
            {"name": "2008_GFC", "description": "Global Financial Crisis — liquidity freeze, correlation spike"},
            {"name": "2020_COVID", "description": "COVID crash — VIX 80+, gap-down, circuit breakers"},
            {"name": "2022_CRYPTO_WINTER", "description": "2022 — rate hiking cycle, vol expansion"},
            {"name": "2023_BANKING_CRISIS", "description": "SVB/CS — regional bank contagion, safe-haven flows"},
            {"name": "FLASH_CRASH", "description": "Sudden liquidity removal — EURCHF 2015 style"},
        ]

    def cost_stress(returns: np.ndarray, cost_multiplier: float = 2.0) -> Dict[str, Any]:
        """Apply cost multiplier to returns and re-evaluate."""
        if len(returns) < 2:
            return {"stressed_sharpe": 0.0, "passed": False}
        stressed = returns - (cost_multiplier * 0.0001)  # assume 1bp base cost
        s = float(np.mean(stressed) / np.std(stressed)) if np.std(stressed) > 0 else 0.0
        return {"stressed_sharpe": round(s, 4), "cost_multiplier": cost_multiplier, "passed": s > 0}

    def parameter_jitter_test(params: Dict[str, float], strategy_fn, data, pct: float = 0.10) -> Dict[str, Any]:
        """Perturb each parameter ±pct and check robustness."""
        results = []
        for key, val in params.items():
            for sign in [-1, 1]:
                perturbed = dict(params)
                perturbed[key] = val * (1 + sign * pct)
                try:
                    r = strategy_fn(data, perturbed)
                    results.append({"param": key, "direction": "up" if sign > 0 else "down", "result": r})
                except Exception as exc:
                    results.append({"param": key, "direction": "up" if sign > 0 else "down", "error": str(exc)})
        return {"perturbations": results, "total": len(results)}

    facade = {
        "cpcv": run_combinatorial_purged_backtest,
        "overfit": detect_overfitting,
        "trial_ledger": get_trial_ledger,
        "monte_carlo_shuffle": monte_carlo_shuffle,
        "stress_scenarios": stress_test_scenarios,
        "cost_stress": cost_stress,
        "parameter_jitter": parameter_jitter_test,
    }
    ctx.provide("extracted_validation", facade)
    logger.info("ExtractedValidationPlugin mounted — CPCV, MC shuffle, stress, jitter available.")


# ---------------------------------------------------------------------------
# Module 03 — Alpha Sources & Market Inefficiencies
# ---------------------------------------------------------------------------
def _mount_extracted_alpha_source(ctx: TradingContext) -> None:
    """Module 03: Market Inefficiencies & Alpha Sources (Structural, Gamma, Microstructure, Behavioral).

    Wraps: core.alpha_factors, core.alpha_ensemble, core.alpha_decay_monitor,
           core.alpha_discovery, core.alpha_rotation
    """
    from core.alpha_ensemble import combine_alphas, compute_alpha_quality
    from core.alpha_decay_monitor import AlphaDecayMonitor
    from core.alpha_discovery import AlphaDiscoveryEngine
    from core.alpha_rotation import AlphaRotationEngine

    def detect_structural_inefficiency(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Detect index rebalancing, mandate-driven flows from price/volume anomalies."""
        df = data.get("df")
        if df is None or len(df) < 20:
            return []
        opportunities = []
        vol = df["tick_volume"].values if "tick_volume" in df.columns else df["volume"].values
        vol_ma = float(np.mean(vol[-20:])) if len(vol) >= 20 else 1.0
        vol_now = float(vol[-1]) if len(vol) > 0 else 0.0
        if vol_ma > 0 and vol_now > vol_ma * 2.5:
            opportunities.append({
                "type": "volume_spike",
                "signal": "structural_flow",
                "severity": "high" if vol_now > vol_ma * 4 else "medium",
                "ratio": round(vol_now / vol_ma, 2),
            })
        return opportunities

    def detect_gamma_regime(options_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Detect dealer gamma positioning from options open interest flow."""
        try:
            from core.market_structure_edges import detect_gamma_regime as _detect_gamma
            data = options_data or {}
            result = _detect_gamma(
                strikes=data.get("strikes"),
                open_interest=data.get("open_interest"),
                call_put_flag=data.get("call_put_flag"),
                spot_price=data.get("spot_price"),
                underlying_vol=data.get("underlying_vol", 0.01),
            )
            return result.to_dict()
        except Exception:
            return {
                "regime": "unknown",
                "gamma_exposure": 0.0,
                "note": "Requires live options data feed (CBOE/cryptocurrency options chain)",
            }

    def calculate_order_flow_imbalance(df: pd.DataFrame) -> float:
        """Bid/ask queue imbalance from tick data."""
        if "bid" not in df.columns or "ask" not in df.columns:
            return 0.0
        recent = df.tail(50)
        mid = (recent["bid"] + recent["ask"]) / 2
        delta = mid.diff().fillna(0)
        obi = float(np.mean(delta.values)) / float(np.std(delta.values)) if np.std(delta.values) > 0 else 0.0
        return round(np.clip(obi, -1.0, 1.0), 4)

    facade = {
        "combine_alphas": combine_alphas,
        "compute_alpha_quality": compute_alpha_quality,
        "decay_monitor": AlphaDecayMonitor,
        "discovery": AlphaDiscoveryEngine,
        "rotation": AlphaRotationEngine,
        "detect_structural": detect_structural_inefficiency,
        "gamma_regime": detect_gamma_regime,
        "order_flow_imbalance": calculate_order_flow_imbalance,
    }
    ctx.provide("extracted_alpha_source", facade)
    logger.info("ExtractedAlphaSourcePlugin mounted — alpha discovery, ensemble, decay, rotation available.")


# ---------------------------------------------------------------------------
# Module 04 — Regime Detection & Dynamic Strategy Routing
# ---------------------------------------------------------------------------
def _mount_extracted_regime(ctx: TradingContext) -> None:
    """Module 04: Regime Detection & Dynamic Strategy Routing (HMM, Hurst, Allocation Matrix).

    Wraps: intelligence.regime_classifier, core.hierarchical_portfolio,
           core.regime_duration_tracker, core.bocpd
    """
    from core.hierarchical_portfolio import RegimeDetectionEngine, RegimeState
    from core.regime_duration_tracker import RegimeDurationTracker

    ALLOCATION_MATRIX = {
        "LOW_VOL_TREND": {"momentum": 0.80, "mean_reversion": 0.00, "carry": 0.00, "cash": 0.20},
        "LOW_VOL_RANGE": {"momentum": 0.00, "mean_reversion": 0.80, "carry": 0.10, "cash": 0.10},
        "HIGH_VOL_TREND": {"momentum": 0.50, "mean_reversion": 0.00, "carry": 0.00, "cash": 0.50},
        "HIGH_VOL_CRISIS": {"momentum": 0.00, "mean_reversion": 0.00, "carry": 0.00, "cash": 1.00},
        "TRANSITIONAL": {"momentum": 0.30, "mean_reversion": 0.30, "carry": 0.00, "cash": 0.40},
    }

    def allocate_capital(regime: str) -> Dict[str, float]:
        """Route capital weights based on detected regime."""
        return ALLOCATION_MATRIX.get(regime, {"cash": 1.0})

    def regime_conditioned_ev(regime: str, strategy_evs: Dict[str, float]) -> float:
        """Calculate portfolio EV weighted by regime allocation."""
        alloc = ALLOCATION_MATRIX.get(regime, {"cash": 1.0})
        total = 0.0
        for strat, weight in alloc.items():
            ev = strategy_evs.get(strat, 0.0)
            total += weight * ev
        return round(total, 6)

    facade = {
        "engine": RegimeDetectionEngine,
        "state": RegimeState,
        "tracker": RegimeDurationTracker,
        "allocation_matrix": ALLOCATION_MATRIX,
        "allocate_capital": allocate_capital,
        "regime_conditioned_ev": regime_conditioned_ev,
    }
    ctx.provide("extracted_regime", facade)
    logger.info("ExtractedRegimePlugin mounted — HMM/Hurst regime + allocation matrix wired.")


# ---------------------------------------------------------------------------
# Module 05 — News Trading & Leading Indicators
# ---------------------------------------------------------------------------
def _mount_extracted_news(ctx: TradingContext) -> None:
    """Module 05: News Trading & Leading Indicators (Economic Calendar, ESI, Delta, Phase Detection).

    Wraps: core.macro_event_alpha_engine, intelligence.macrofundamental_pipeline,
           data.forexfactory_calendar
    """
    from core.macro_event_alpha_engine import (
        MacroEventAlphaEngine,
        RollingEconomicSurpriseIndex,
    )

    from core.news_trading import calculate_delta
    _calculate_delta = calculate_delta  # re-export

    from core.news_trading import detect_news_phase as _detect_news_phase
    def detect_news_phase(event_time: str, current_time: str) -> Dict[str, Any]:
        return _detect_news_phase(event_time, current_time)

    from core.news_trading import NewsTradingGate, EconomicSurpriseIndex, get_news_trading_context, predict_nfp, predict_cpi
    facade = {
        "engine": MacroEventAlphaEngine,
        "esi": RollingEconomicSurpriseIndex,
        "gate": NewsTradingGate,
        "esi_calc": EconomicSurpriseIndex,
        "calculate_delta": _calculate_delta,
        "detect_news_phase": detect_news_phase,
        "predict_nfp": predict_nfp,
        "predict_cpi": predict_cpi,
        "tier_1_events": [
            "NFP", "FOMC", "CPI", "ECB_DECISION", "BOJ_DECISION",
            "GDP_US", "GDP_EU",
        ],
        "tier_2_events": [
            "RETAIL_SALES", "UNEMPLOYMENT", "PPI", "CORE_PCE",
            "PMI_MFG", "PMI_SVC", "CONSUMER_CONFIDENCE",
        ],
    }
    ctx.provide("extracted_news", facade)
    logger.info("ExtractedNewsPlugin mounted — ESI, Delta, phase detection, NFP/CPI weights available.")


# ---------------------------------------------------------------------------
# Module 06 — Risk Management & Position Sizing
# ---------------------------------------------------------------------------
def _mount_extracted_risk(ctx: TradingContext) -> None:
    """Module 06: Risk Management & Position Sizing (3-Layer Architecture, Circuit Breakers, TCA).

    Wraps: core.risk_manager, core.trading_safety, core.execution_safety_supervisor,
           core.order_execution, core.position_manager
    """
    from core.risk_manager import RiskManager

    def position_size_1r(equity: float, risk_pct: float, entry: float, stop: float) -> float:
        """Fixed-fractional 1R position sizing."""
        risk_amount = equity * risk_pct
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            return 0.0
        return risk_amount / risk_per_unit

    def vol_target_size(equity: float, risk_target: float, atr: float, k: float = 2.0) -> float:
        """Volatility-targeted position size."""
        if atr <= 0:
            return 0.0
        return (equity * risk_target) / (k * atr)

    def portfolio_heat(positions: List[Dict[str, Any]]) -> float:
        """Total portfolio heat (active risk in R-units)."""
        return sum(abs(p.get("risk_amount", 0)) for p in positions)

    def correlation_gate(positions: List[Dict[str, Any]], threshold: float = 0.6) -> Dict[str, Any]:
        """Check if positions exceed correlation limits."""
        n = len(positions)
        if n <= 1:
            return {"exceeded": False, "pairs": [], "n_positions": n}
        risky_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                corr = positions[i].get("correlation", positions[j].get("correlation", 0.0))
                if abs(corr) > threshold:
                    risky_pairs.append({
                        "a": positions[i].get("symbol", f"pos_{i}"),
                        "b": positions[j].get("symbol", f"pos_{j}"),
                        "correlation": round(corr, 4),
                    })
        return {
            "exceeded": len(risky_pairs) > 0,
            "pairs": risky_pairs,
            "n_positions": n,
            "threshold": threshold,
        }

    LAYER_ARCHITECTURE = {
        "layer_1_strategy": {
            "description": "Individual strategy drawdown limits, 1R unit sizing, stop-loss at structural invalidation",
            "kill_trigger": "strategy_max_loss_exceeded",
        },
        "layer_2_portfolio": {
            "description": "Total portfolio heat cap (3-5R max), cross-strategy correlation, aggregate drawdown breakers",
            "kill_trigger": "portfolio_heat_5r_or_dd_15pct",
        },
        "layer_3_system": {
            "description": "Volatility kill-switches, emergency liquidation, human override",
            "kill_trigger": "extreme_market_or_constitutional_violation",
        },
    }

    facade = {
        "position_size_1r": position_size_1r,
        "vol_target_size": vol_target_size,
        "portfolio_heat": portfolio_heat,
        "correlation_gate": correlation_gate,
        "layer_architecture": LAYER_ARCHITECTURE,
        "risk_mgr": RiskManager,
    }
    ctx.provide("extracted_risk", facade)
    logger.info("ExtractedRiskPlugin mounted — 3-layer risk, 1R sizing, vol targeting, heat management.")


# ---------------------------------------------------------------------------
# Module 07 — Autonomous Agent Architecture
# ---------------------------------------------------------------------------
def _mount_extracted_autonomous(ctx: TradingContext) -> None:
    """Module 07: Autonomous Agent Architecture (Constitutional AI, Trust Levels, Seasons, Drift).

    Wraps: core.code_safety_agent, core.compliance_audit, core.self_improvement_loop,
           core.ewc_forgetting_prevention, core.task_queue
    """
    from core.code_safety_agent import CodeSafetyAgent
    from core.compliance_audit import get_audit
    from core.self_improvement_loop import RecursiveSelfImprovementEngine
    from core.ewc_forgetting_prevention import EWCForgettingPrevention
    from core.task_queue import TaskQueue

    CONSTITUTION = {
        "capital_preservation": {"max_drawdown": -0.10, "description": "Capital preservation first"},
        "leverage_limit": {"max_leverage": 2.0, "description": "No leverage beyond 2x"},
        "data_integrity": {"description": "All features computable from past data only"},
        "concentration_limit": {"max_single_asset_pct": 0.20, "description": "No concentration >20% in single asset"},
        "correlation_cap": {"max_correlated_positions": 3, "description": "Max 3 positions with r > 0.6"},
    }

    TRUST_LEVELS = {
        0: {"name": "Observe", "scope": "Data collection, hypothesis generation only", "human": "Approves all trades"},
        1: {"name": "Assist", "scope": "Recommends trades, human executes", "human": "Approves each execution"},
        2: {"name": "Delegate", "scope": "Auto-executes within tight guardrails", "human": "Reviews daily summary"},
        3: {"name": "Autonomous", "scope": "Full execution within constitutional bounds", "human": "Reviews weekly"},
        4: {"name": "Self-Managing", "scope": "Can propose meta-governance adjustments", "human": "Approves meta-changes only"},
    }

    SEASONS = {
        "exploration": {"duration_weeks": 2, "gate_looseness": "loose", "description": "High iteration, accepts more strategies"},
        "exploitation": {"duration_weeks": 4, "gate_looseness": "tight", "description": "Only top-quartile strategies pass"},
        "validation": {"duration_weeks": 1, "gate_looseness": "strict", "description": "No new strategies, holdout testing"},
        "review": {"duration_weeks": 0.5, "gate_looseness": "read_only", "description": "Meta-analysis, gate adjustment"},
    }

    def constitutional_critique(strategy: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a strategy against constitutional principles."""
        violations = []
        if strategy.get("max_drawdown", 0) < CONSTITUTION["capital_preservation"]["max_drawdown"]:
            violations.append("max_drawdown exceeds -10% constitutional limit")
        if strategy.get("leverage", 1.0) > CONSTITUTION["leverage_limit"]["max_leverage"]:
            violations.append("leverage exceeds 2x constitutional limit")
        if strategy.get("concentration", 0) > CONSTITUTION["concentration_limit"]["max_single_asset_pct"]:
            violations.append("single-asset concentration exceeds 20%")
        return {
            "approved": len(violations) == 0,
            "violations": violations,
            "constitution": CONSTITUTION,
        }

    def evaluate_promotion(current_level: int, perf: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate if agent should be promoted to next trust level."""
        min_sharpe_days = 30
        min_no_kill_days = 60
        sharpe_ok = perf.get("positive_sharpe_days", 0) >= min_sharpe_days
        kill_ok = perf.get("kill_switch_free_days", 0) >= min_no_kill_days
        can_promote = sharpe_ok and kill_ok and current_level < 4
        return {
            "current_level": current_level,
            "current_name": TRUST_LEVELS.get(current_level, {}).get("name", "Unknown"),
            "can_promote": can_promote,
            "next_level": current_level + 1 if can_promote else current_level,
            "next_name": TRUST_LEVELS.get(current_level + 1, {}).get("name", "Max") if can_promote else None,
            "sharpe_criterion_met": sharpe_ok,
            "no_kill_criterion_met": kill_ok,
        }

    facade = {
        "code_safety": CodeSafetyAgent,
        "audit": get_audit,
        "self_improvement": RecursiveSelfImprovementEngine,
        "ewc": EWCForgettingPrevention,
        "task_queue": TaskQueue,
        "constitution": CONSTITUTION,
        "trust_levels": TRUST_LEVELS,
        "seasons": SEASONS,
        "constitutional_critique": constitutional_critique,
        "evaluate_promotion": evaluate_promotion,
    }
    ctx.provide("extracted_autonomous", facade)
    logger.info("ExtractedAutonomousPlugin mounted — constitution, trust levels, seasons, drift detection.")


# ---------------------------------------------------------------------------
# Module 08 — Alternative Data & Geospatial Analysis
# ---------------------------------------------------------------------------
def _mount_extracted_alt_data(ctx: TradingContext) -> None:
    """Module 08: Alternative Data & Geospatial Analysis (Satellite, AIS, COT, Gold FV, Vol Regime).

    Wraps: data.cftc_cot_provider, data.data_source_pipeline (FRED, EIA),
           data.alternative_data_provider, data.fred_provider
    """
    def gold_fair_value(tips_yield: float, dxy: float, alpha: float = 0.0,
                        beta_tips: float = -0.5, beta_dxy: float = -0.8) -> Dict[str, Any]:
        """Rolling fair-value OLS model for XAUUSD.

        ln(P_fair) = alpha + beta_tips * r_TIPS + beta_dxy * ln(DXY)
        """
        ln_dxy = np.log(dxy) if dxy > 0 else 0.0
        ln_fair = alpha + beta_tips * tips_yield + beta_dxy * ln_dxy
        fair_value = float(np.exp(ln_fair))
        return {"fair_value": round(fair_value, 2), "ln_fair": round(ln_fair, 4)}

    def detect_vol_regime(vix: float, v3m: float) -> Dict[str, Any]:
        """Detect volatility regime from VIX term structure."""
        if v3m <= 0:
            return {"regime": "unknown", "vix_v3m_ratio": 0.0}
        ratio = vix / v3m
        if ratio < 0.95:
            regime = "contango"  # backwardation normal → trend following
        elif ratio >= 1.00:
            regime = "backwardation"  # backwardation inverted → defensive
        else:
            regime = "flat"
        return {"regime": regime, "vix_v3m_ratio": round(ratio, 4)}

    def carry_signal(base_rate: float, quote_rate: float) -> Dict[str, Any]:
        """Calculate macro carry for a currency pair."""
        carry = base_rate - quote_rate
        return {
            "carry": round(carry, 4),
            "direction": "long_base" if carry > 0 else "short_base",
            "annualized_pct": round(carry * 100, 2),
        }

    def synthetic_index_price(constituents: Dict[str, float], divisor: float = 1.0) -> float:
        """Calculate price-weighted synthetic index from constituent prices."""
        if divisor <= 0:
            return 0.0
        return sum(constituents.values()) / divisor

    facade = {
        "gold_fair_value": gold_fair_value,
        "detect_vol_regime": detect_vol_regime,
        "carry_signal": carry_signal,
        "synthetic_index": synthetic_index_price,
        "data_sources": {
            "satellite": "ESA Copernicus CDSE (free)",
            "vessel_tracking": "AISStream / OpenSky Network (free)",
            "institutional_holdings": "SEC EDGAR API (free)",
            "economic_data": "FRED API (free)",
            "volatility_data": "CBOE Historical Portal (free)",
            "commodity_data": "EIA API v2 (free)",
        },
    }
    ctx.provide("extracted_alt_data", facade)
    logger.info("ExtractedAltDataPlugin mounted — gold FV, vol regime, carry, synthetic index available.")


# ---------------------------------------------------------------------------
# Module 09 — Research Frameworks & Self-Improvement
# ---------------------------------------------------------------------------
def _mount_extracted_research(ctx: TradingContext) -> None:
    """Module 09: Research Frameworks (FactorMiner, AlphaAgent, AQuA, MARS, Self-Improvement).

    Wraps: core.alpha_discovery, core.meta_adaptive_engine, core.evolutionary_engine,
           core.cross_market_benchmark, core.self_improvement_loop
    """
    from core.alpha_discovery import AlphaDiscoveryEngine
    from core.meta_adaptive_engine import MetaAdaptiveEngine
    from core.self_improvement_loop import RecursiveSelfImprovementEngine

    def factorminer_config() -> Dict[str, Any]:
        """Configuration template for FactorMiner-style experience memory."""
        return {
            "experience_memory_size": 1000,
            "retrieval_top_k": 10,
            "novelty_threshold": 0.3,
            "max_active_hypotheses": 50,
            "decay_trigger_ic": 0.02,
            "retrain_trigger_ic": 0.01,
        }

    def aqua_config() -> Dict[str, Any]:
        """Configuration template for AQuA recursive self-improvement."""
        return {
            "sealed_sandbox": True,
            "independent_researchers": 2,
            "meta_hypothesis_validation_window": 30,  # days
            "shadow_deploy_before_apply": True,
            "min_dsr_for_meta_change": 0.95,
        }

    def mars_config() -> Dict[str, Any]:
        """Configuration template for MARS meta-adaptive portfolio management."""
        return {
            "meta_controller_update_freq": "weekly",
            "safety_critic_enabled": True,
            "agent_roles": ["alpha_generator", "risk_guardian", "execution_engine", "monitor"],
            "adaptation_method": "MAML",
            "fast_adaptation_shots": 5,
        }

    def challenge_to_solution_map() -> Dict[str, str]:
        """Map trading challenges to research framework solutions."""
        return {
            "alpha_decay": "FactorMiner experience memory + AlphaAgent regularization",
            "overfitting": "AQuA sealed sandbox + CPCV validation",
            "hallucination": "FinAgent multimodal grounding + AI-Trader minimal info",
            "cross_market": "AI-Trader benchmark + MARS meta-adaptation",
            "latency": "LA-NAS hardware-aware architecture search",
            "risk_management": "MARS safety-critic + 3-layer governance",
        }

    facade = {
        "alpha_discovery": AlphaDiscoveryEngine,
        "meta_adaptive": MetaAdaptiveEngine,
        "self_improvement": RecursiveSelfImprovementEngine,
        "factorminer_config": factorminer_config,
        "aqua_config": aqua_config,
        "mars_config": mars_config,
        "challenge_map": challenge_to_solution_map,
    }
    ctx.provide("extracted_research", facade)
    logger.info("ExtractedResearchPlugin mounted — FactorMiner, AQuA, MARS configs + challenge mapping.")


# ---------------------------------------------------------------------------
# Master Plugin — Registers all 9 extracted module facades
# ---------------------------------------------------------------------------
class ExtractedModulesPlugin(HarnessPlugin):
    """Master orchestrator that mounts all 9 extracted module facades.

    Usage:
        ctx.plugin(ExtractedModulesPlugin)
    """
    name = "extracted_modules"
    version = "1.0.0"
    dependencies: List[str] = []

    _ADAPTERS = [
        ("extracted_metrics", _mount_extracted_metrics, []),
        ("extracted_validation", _mount_extracted_validation, []),
        ("extracted_alpha_source", _mount_extracted_alpha_source, []),
        ("extracted_regime", _mount_extracted_regime, []),
        ("extracted_news", _mount_extracted_news, []),
        ("extracted_risk", _mount_extracted_risk, ["risk_manager"]),
        ("extracted_autonomous", _mount_extracted_autonomous, []),
        ("extracted_alt_data", _mount_extracted_alt_data, []),
        ("extracted_research", _mount_extracted_research, []),
    ]

    def apply(self, ctx: TradingContext) -> None:
        mounted = []
        for plugin_name, mount_fn, deps in self._ADAPTERS:
            try:
                # Skip dependencies that aren't in the context (graceful degradation)
                missing_deps = [d for d in deps if not ctx.has(d)]
                if missing_deps:
                    logger.warning(
                        "Skipping %s — missing deps: %s", plugin_name, missing_deps
                    )
                    continue
                mount_fn(ctx)
                mounted.append(plugin_name)
            except Exception as exc:
                logger.error("Failed to mount %s: %s", plugin_name, exc, exc_info=True)

        ctx.provide("extracted_modules_manifest", {
            "mounted": mounted,
            "total": len(self._ADAPTERS),
            "failed": len(self._ADAPTERS) - len(mounted),
        })
        logger.info(
            "ExtractedModulesPlugin mounted %d/%d adapters: %s",
            len(mounted), len(self._ADAPTERS), mounted,
        )
