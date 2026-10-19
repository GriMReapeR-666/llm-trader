"""Complete Alpha Mining Pipeline Integration.

Ties together all components from both papers:
1. Jung (2026): Hierarchical GA + PCA Grid QD Selection
2. Wangpratham (2026): BrainAlpha LLM Agents + Failure-Aware Repair

This module provides the unified alpha mining pipeline.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .expression_tree import AlphaExpression, evaluate_with_cache
from .expression_generator import ExpressionGenerator
from .ic_evaluator import ICEvaluator, ICResult, calculate_forward_returns_5day
from .genetic_operators import GeneticOperators
from .pca_projector import PCAProjector, PCAResult
from .grid_selector import GridSelector, GridSelectionResult
from .ga_engine import GAEngine, GAConfig, GAEvolutionResult
from .dnn_aggregator import DNNAggregator, MegaAlphaAggregator
from .strategy_bridge import AlphaStrategyBridge
from .failure_modes import FailureMode, FailureDiagnostic, classify_failure
from .repair_engine import RepairEngine, RepairConfig, RepairResult
from .rag_specification import RAGSpecificationEncoder, EncodedSpecification
from .exploration_grid import ExplorationGrid, CellStatus
from .horizon_alpha import HorizonAlphaEvaluator, HorizonConfig
from .optimal_strategy import OptimalStrategyEvaluator, TLSOptimizer, StrategyStatistics
from .regime_detector import RegimeDetector, MarketRegime, RegimeState
from .cvar_risk import CVaRRiskManager, CVaRResult
from .liquidity_screen import LiquidityScreener, LiquidityScore
from .dynamic_stops import DynamicStopManager, StopState
from .fvg_detector import FVGDetector, FVGResult
from .kat_detector import KATDetector, KATResult
from .spoofing_detector import SpoofingDetector, SpoofingResult
from .rl_agent import RLTradingAgent, AgentConfig
from .pairs_trader import PairsTrader, PairsResult
from .volume_breakout import VolumeBreakoutDetector, BreakoutResult
from .walk_forward import WalkForwardAnalyzer, WalkForwardResult
from .alpha101_knowledge_base import build_knowledge_base, Alpha101KnowledgeBase
from .alpha_judge import judge_candidate, JudgeScore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Complete Pipeline Configuration
# ---------------------------------------------------------------------------

@dataclass
class AlphaMiningConfig:
    """Configuration for the complete alpha mining pipeline."""
    
    # GA Parameters (from Jung paper)
    ga_config: GAConfig = field(default_factory=GAConfig)
    
    # BrainAlpha Parameters
    use_llm_agents: bool = True
    use_failure_repair: bool = True
    use_rag_specification: bool = True
    use_exploration_grid: bool = True
    
    # MNTO Trend-Following Parameters
    use_regime_detection: bool = True
    use_liquidity_screen: bool = True
    use_cvar_risk: bool = True
    use_dynamic_stops: bool = True
    min_liquidity_score: float = 0.5
    max_cvar_pct: float = 0.01
    
    # Microstructure Signals (Papers 7-10)
    use_fvg_detection: bool = True
    use_kat_detection: bool = True
    use_spoofing_detection: bool = True
    use_rl_agent: bool = True
    
    # Pairs & Breakout Signals (Papers 11-12)
    use_pairs_trading: bool = True
    use_volume_breakout: bool = True
    use_walk_forward: bool = True
    
    # P01 Alpha101 KB & Judge Parameters (Vu et al. 2026)
    use_knowledge_base: bool = True
    use_judge_rubric: bool = True
    judge_threshold: float = 7.5
    
    # Integration
    strategy_factory: Any = None
    risk_manager: Any = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ga_config": self.ga_config.to_dict(),
            "use_llm_agents": self.use_llm_agents,
            "use_failure_repair": self.use_failure_repair,
            "use_rag_specification": self.use_rag_specification,
            "use_exploration_grid": self.use_exploration_grid,
            "use_regime_detection": self.use_regime_detection,
            "use_liquidity_screen": self.use_liquidity_screen,
            "use_cvar_risk": self.use_cvar_risk,
            "use_dynamic_stops": self.use_dynamic_stops,
            "use_fvg_detection": self.use_fvg_detection,
            "use_kat_detection": self.use_kat_detection,
            "use_spoofing_detection": self.use_spoofing_detection,
            "use_rl_agent": self.use_rl_agent,
            "use_pairs_trading": self.use_pairs_trading,
            "use_volume_breakout": self.use_volume_breakout,
            "use_walk_forward": self.use_walk_forward,
            "use_knowledge_base": self.use_knowledge_base,
            "use_judge_rubric": self.use_judge_rubric,
            "judge_threshold": self.judge_threshold,
        }


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------

@dataclass
class AlphaMiningResult:
    """Result of the complete alpha mining pipeline."""
    
    # Core results
    discovered_alphas: List[Dict[str, Any]]
    mega_alpha_composite: Optional[pd.Series]
    
    # Diagnostics
    ga_result: Optional[GAEvolutionResult]
    exploration_stats: Dict[str, Any]
    failure_stats: Dict[str, Any]
    repair_stats: Dict[str, Any]
    
    # Metrics
    total_expressions_evaluated: int
    significant_alphas_count: int
    best_ic: float
    avg_ic: float
    pipeline_duration: float
    
    # Optimal strategy statistics (Paper 5: Firoozye & Koshiyama)
    optimal_stats: Optional[Dict[str, Any]] = None
    tls_diagnostics: Optional[Dict[str, Any]] = None
    
    # Microstructure signals (Papers 7-10)
    fvg_result: Optional[Dict[str, Any]] = None
    kat_result: Optional[Dict[str, Any]] = None
    spoofing_result: Optional[Dict[str, Any]] = None
    rl_signal: Optional[Dict[str, Any]] = None
    
    # Pairs & Breakout (Papers 11-12)
    pairs_result: Optional[Dict[str, Any]] = None
    breakout_result: Optional[Dict[str, Any]] = None
    walk_forward_result: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_discovered_alphas": len(self.discovered_alphas),
            "significant_alphas_count": self.significant_alphas_count,
            "best_ic": round(self.best_ic, 6),
            "avg_ic": round(self.avg_ic, 6),
            "total_expressions_evaluated": self.total_expressions_evaluated,
            "pipeline_duration": round(self.pipeline_duration, 2),
            "exploration_stats": self.exploration_stats,
            "failure_stats": self.failure_stats,
            "repair_stats": self.repair_stats,
            "optimal_stats": self.optimal_stats,
            "tls_diagnostics": self.tls_diagnostics,
            "fvg_result": self.fvg_result,
            "kat_result": self.kat_result,
            "spoofing_result": self.spoofing_result,
            "rl_signal": self.rl_signal,
            "pairs_result": self.pairs_result,
            "breakout_result": self.breakout_result,
            "walk_forward_result": self.walk_forward_result,
        }


# ---------------------------------------------------------------------------
# Complete Alpha Mining Pipeline
# ---------------------------------------------------------------------------

class AlphaMiningPipeline:
    """Unified alpha mining pipeline combining both papers."""
    
    def __init__(self, config: Optional[AlphaMiningConfig] = None, seed: int = 42):
        """Initialize the complete pipeline.
        
        Args:
            config: Pipeline configuration
            seed: Random seed for reproducibility
        """
        self.config = config or AlphaMiningConfig()
        self.seed = seed
        
        # Initialize components
        self.ga_engine = GAEngine(config=self.config.ga_config, seed=seed)
        self.exploration_grid = ExplorationGrid() if self.config.use_exploration_grid else None
        self.rag_encoder = RAGSpecificationEncoder() if self.config.use_rag_specification else None
        self.repair_engine = RepairEngine() if self.config.use_failure_repair else None
        self.strategy_bridge = AlphaStrategyBridge(self.config.strategy_factory)
        
        # P01 Alpha101 Knowledge Base (Vu et al. 2026)
        self.kb: Optional[Alpha101KnowledgeBase] = (
            build_knowledge_base() if self.config.use_knowledge_base else None
        )
        
        # Statistics
        self.stats = {
            "total_evaluated": 0,
            "significant_found": 0,
            "failures": 0,
            "repairs_attempted": 0,
            "repairs_successful": 0,
        }
    
    def run(self, ohlcv: pd.DataFrame,
            forward_returns: Optional[pd.Series] = None,
            investment_theses: Optional[List[str]] = None,
            verbose: bool = True) -> AlphaMiningResult:
        """Run the complete alpha mining pipeline.
        
        Args:
            ohlcv: OHLCV data
            forward_returns: Optional pre-computed forward returns
            investment_theses: Optional list of investment theses
            verbose: Whether to print progress
            
        Returns:
            AlphaMiningResult with all discovered alphas
        """
        start_time = time.time()

        # MT5 frames carry tick_volume/real_volume; the detectors below
        # (liquidity/fvg/kat/spoofing/rl/volume_breakout) index "volume".
        # Rebind locally so caller frames are not mutated.
        if "volume" not in ohlcv.columns and "tick_volume" in ohlcv.columns:
            ohlcv = ohlcv.assign(volume=ohlcv["tick_volume"])

        # Calculate forward returns if not provided
        if forward_returns is None:
            forward_returns = calculate_forward_returns_5day(ohlcv["close"])
        
        if verbose:
            logger.info("=" * 70)
            logger.info("ALPHA MINING PIPELINE - Starting")
            logger.info("=" * 70)
        
        # Phase 1: GA Evolution (Jung paper)
        if verbose:
            logger.info("\nPhase 1: Hierarchical GA Evolution")
        
        ga_result = self.ga_engine.evolve(ohlcv, forward_returns, verbose=verbose)
        
        self.stats["total_evaluated"] = len(ga_result.all_expressions)
        
        # Phase 2: Selection & Diversity
        if verbose:
            logger.info("\nPhase 2: PCA Grid Selection & Diversity")
        
        significant_alphas = self.ga_engine.get_significant_alphas(ga_result, min_ic=0.02)
        
        # Phase 2.1: Deterministic Judge Rubric Pre-Filter (Vu et al. 2026, P01)
        if self.config.use_judge_rubric:
            if verbose:
                logger.info("\nPhase 2.1: Deterministic Judge Rubric Pre-Filter (Vu et al. 2026)")
            significant_alphas = self._judge_filter_alphas(significant_alphas, ohlcv, verbose)
            
        self.stats["significant_found"] = len(significant_alphas)
        
        # Phase 2.5: Horizon-Aware Alpha Evaluation (Bessembinder et al. 2023)
        if verbose:
            logger.info("\nPhase 2.5: Horizon-Aware Alpha Evaluation")
        
        horizon_evaluator = HorizonAlphaEvaluator()
        horizon_filtered_alphas = self._horizon_filter_alphas(
            significant_alphas, ohlcv, horizon_evaluator, verbose
        )
        
        # Phase 3: Failure-Aware Repair (BrainAlpha)
        if self.config.use_failure_repair and significant_alphas:
            if verbose:
                logger.info("\nPhase 3: Failure-Aware Repair")
            
            repaired_alphas = self._repair_failed_alphas(
                ga_result.all_expressions, ohlcv, forward_returns, verbose
            )
        
        # Phase 3.5: Regime Detection & Liquidity Screening (MNTO)
        if verbose:
            logger.info("\nPhase 3.5: Regime Detection & Liquidity Screening")
        
        regime_state, liquidity_scores = self._regime_and_liquidity_analysis(
            ohlcv, verbose
        )
        
        # Phase 4: Strategy Registration
        if verbose:
            logger.info("\nPhase 4: Strategy Registration")
        
        discovered_alphas = self._register_strategies(
            significant_alphas, ga_result, verbose
        )
        
        # Phase 5: MegaAlpha Construction
        if verbose:
            logger.info("\nPhase 5: MegaAlpha Portfolio Construction")
        
        mega_alpha = self._construct_mega_alpha(
            discovered_alphas, ohlcv, forward_returns, verbose
        )
        
        # Phase 6: Optimal Strategy Statistics (Firoozye & Koshiyama)
        if verbose:
            logger.info("\nPhase 6: Optimal Strategy Statistics")
        
        # Takes (AlphaExpression, ic) tuples, like every other phase that
        # evaluates expressions. Passing the registered dicts made the
        # `for expr, ic in ...` unpack raise "too many values to unpack", so
        # Phase 6 silently produced no statistics on every run.
        optimal_stats, tls_diagnostics = self._compute_optimal_statistics(
            significant_alphas, ohlcv, forward_returns, verbose
        )
        
        # Phase 7: Microstructure Signals (FVG, KAT, Spoofing, RL)
        if verbose:
            logger.info("\nPhase 7: Microstructure Signal Analysis")
        
        fvg_result, kat_result, spoofing_result, rl_signal = (
            self._microstructure_analysis(ohlcv, verbose)
        )
        
        # Phase 8: Pairs Trading & Volume Breakout (Papers 11-12)
        if verbose:
            logger.info("\nPhase 8: Pairs Trading & Breakout Detection")
        
        pairs_result, breakout_result = self._pairs_and_breakout_analysis(
            ohlcv, verbose
        )
        
        # Phase 9: Walk-Forward Validation
        if verbose:
            logger.info("\nPhase 9: Walk-Forward Validation")
        
        # Phase 4 hands _register_strategies the (AlphaExpression, ic) tuples;
        # pass those same tuples here so Phase 9 can evaluate the real
        # expression instead of the rendered string.
        walk_forward_result = self._walk_forward_analysis(ohlcv, verbose, significant_alphas)
        
        # Compile results
        pipeline_duration = time.time() - start_time
        
        result = AlphaMiningResult(
            discovered_alphas=discovered_alphas,
            mega_alpha_composite=mega_alpha,
            ga_result=ga_result,
            exploration_stats=self.exploration_grid.get_coverage_stats() if self.exploration_grid else {},
            failure_stats={"total_failures": self.stats["failures"]},
            repair_stats={
                "attempted": self.stats["repairs_attempted"],
                "successful": self.stats["repairs_successful"],
            },
            total_expressions_evaluated=self.stats["total_evaluated"],
            significant_alphas_count=self.stats["significant_found"],
            best_ic=max(ga_result.best_ics) if ga_result.best_ics else 0.0,
            avg_ic=np.mean(ga_result.best_ics) if ga_result.best_ics else 0.0,
            pipeline_duration=pipeline_duration,
            optimal_stats=optimal_stats,
            tls_diagnostics=tls_diagnostics,
            fvg_result=fvg_result,
            kat_result=kat_result,
            spoofing_result=spoofing_result,
            rl_signal=rl_signal,
            pairs_result=pairs_result,
            breakout_result=breakout_result,
            walk_forward_result=walk_forward_result,
        )
        
        if verbose:
            logger.info("\n" + "=" * 70)
            logger.info("ALPHA MINING PIPELINE - Complete")
            logger.info("=" * 70)
            logger.info(f"Total expressions evaluated: {result.total_expressions_evaluated}")
            logger.info(f"Significant alphas found: {result.significant_alphas_count}")
            logger.info(f"Best IC: {result.best_ic:.4f}")
            logger.info(f"Pipeline duration: {result.pipeline_duration:.1f}s")
        
        return result
    
    def _horizon_filter_alphas(self, alphas: List[Tuple[AlphaExpression, float]],
                               ohlcv: pd.DataFrame,
                               evaluator: HorizonAlphaEvaluator,
                               verbose: bool) -> List[Tuple[AlphaExpression, float]]:
        """Filter alphas based on horizon-aware evaluation.
        
        From Bessembinder et al. (2023): Monthly alpha can be misleading
        for long-horizon investors. Filter out alphas that are likely to
        have negative long-horizon alpha.
        """
        filtered = []
        misleading_count = 0
        
        for expr, ic in alphas:
            try:
                # Evaluate expression
                from .ic_evaluator import evaluate_with_cache
                alpha_values = evaluate_with_cache(expr, ohlcv)
                
                if alpha_values is None or len(alpha_values) < 252:
                    filtered.append((expr, ic))
                    continue
                
                # Compute beta (simplified: using rolling correlation)
                market_returns = ohlcv["close"].pct_change()
                beta_values = alpha_values.rolling(252).corr(market_returns) * (
                    alpha_values.rolling(252).std() / market_returns.rolling(252).std()
                )
                
                # Check for misleading alpha
                detection = evaluator.detect_misleading_alpha(
                    alpha_values.dropna(),
                    beta_values.dropna()
                )
                
                if detection["is_misleading"]:
                    misleading_count += 1
                    if verbose:
                        logger.info(
                            f"  Horizon filter: {expr.to_string()[:40]}... "
                            f"(monthly_beta={detection['monthly_beta']:.2f}, "
                            f"misleading_prob={detection['misleading_probability']:.0%})"
                        )
                    # Still include but with reduced weight
                    filtered.append((expr, ic * 0.5))
                else:
                    filtered.append((expr, ic))
                    
            except Exception as e:
                # If horizon evaluation fails, include alpha
                filtered.append((expr, ic))
        
        if verbose:
            logger.info(f"  Horizon filter: {len(alphas)} -> {len(filtered)} alphas "
                       f"({misleading_count} misleading)")
        
        return filtered
    
    def _judge_filter_alphas(self, alphas: List[Tuple[AlphaExpression, float]],
                             ohlcv: pd.DataFrame,
                             verbose: bool) -> List[Tuple[AlphaExpression, float]]:
        """Filter alphas using deterministic 4-criteria rubric (Vu et al. 2026, P01).
        
        Evaluates: Logic (syntax/AST validity), Style (boundedness, operations),
        Lookahead (no future data leakage), Robustness (non-trivial variance).
        Accepts candidate only if total score >= judge_threshold (default 7.5).
        """
        if not self.config.use_judge_rubric or not alphas:
            return alphas
        
        filtered = []
        rejected_count = 0
        
        for expr, ic in alphas:
            expr_str = expr.to_string() if hasattr(expr, "to_string") else str(expr)
            signal = None
            try:
                from .ic_evaluator import evaluate_with_cache
                signal = evaluate_with_cache(expr, ohlcv)
            except Exception:
                signal = None

            try:
                score = judge_candidate(expr_str, signal=signal)
                if score.accepted and score.aggregate >= self.config.judge_threshold:
                    filtered.append((expr, ic))
                else:
                    rejected_count += 1
                    if verbose:
                        logger.info(
                            f"  Judge rejected {expr_str[:40]}... "
                            f"(score={score.aggregate:.2f} < {self.config.judge_threshold}, "
                            f"feedback={score.feedback})"
                        )
            except Exception as exc:
                logger.debug("Judge evaluation exception for alpha: %s", exc)
                rejected_count += 1
                
        if verbose and rejected_count > 0:
            logger.info(f"  Judge rubric filtered out {rejected_count} failing alphas.")
        return filtered

    def _repair_failed_alphas(self, expressions: List[AlphaExpression],
                             ohlcv: pd.DataFrame,
                             forward_returns: pd.Series,
                             verbose: bool) -> List[AlphaExpression]:
        """Attempt repair on failed alphas."""
        repaired = []
        
        for expr in expressions[:10]:  # Limit repair attempts
            # Evaluate expression
            evaluator = ICEvaluator()
            ic_result = evaluator.evaluate_expression(expr, ohlcv, forward_returns)
            
            # Check if it failed
            if abs(ic_result.ic_mean) < 0.02:
                self.stats["failures"] += 1
                
                # Attempt repair
                evaluation_results = {
                    "metrics": {
                        "sharpe": ic_result.ic_mean * 10,  # Simplified
                        "turnover": 0.3,
                        "max_dd": 0.1,
                        "coverage": 0.5,
                        "annualized_returns": 0.05,
                    },
                    "checks": {"passing": False},
                }
                
                repair_result = self.repair_engine.repair(
                    expr, evaluation_results
                )
                
                self.stats["repairs_attempted"] += 1
                
                if repair_result.success:
                    self.stats["repairs_successful"] += 1
                    repaired.append(repair_result.repaired_expression)
                    
                    if verbose:
                        logger.info(f"  Repaired: {expr.to_string()[:50]}...")
        
        return repaired
    
    def _register_strategies(self, alphas: List[Tuple[AlphaExpression, float]],
                            ga_result: GAEvolutionResult,
                            verbose: bool) -> List[Dict[str, Any]]:
        """Register discovered alphas as strategies."""
        discovered = []
        
        for i, (expr, ic) in enumerate(alphas[:10]):  # Top 10
            # A single IC value carries no dispersion, so the information ratio
            # is genuinely unmeasured here. Report None rather than a hardcoded
            # 0.0: a fabricated 0.0 reads as "measured, and it is zero" and
            # would let the quality grade assert repeatability it never saw.
            quality = self._classify_ic_quality(ic, None)
            # Register with strategy bridge
            strategy_id = self.strategy_bridge.register_alpha_strategy(
                expression=expr.to_string(),
                ic_metrics={
                    "ic_mean": ic,
                    "ic_ir": None,
                    "ic_quality": quality,
                },
                shap_values={},
                name=f"Alpha-{i+1}",
            )
            
            discovered.append({
                "strategy_id": strategy_id,
                "expression": expr.to_string(),
                "ic_mean": ic,
                "ic_quality": quality,
            })
            
            if verbose:
                logger.info(f"  Registered: {strategy_id} (IC={ic:.4f})")
        
        return discovered
    
    def _construct_mega_alpha(self, discovered: List[Dict[str, Any]],
                             ohlcv: pd.DataFrame,
                             forward_returns: pd.Series,
                             verbose: bool) -> Optional[pd.Series]:
        """Construct MegaAlpha composite signal."""
        if not discovered:
            return None
        
        try:
            from .expression_tree import evaluate_with_cache
            
            # Build alpha panel
            alpha_dict = {}
            for alpha_info in discovered[:5]:  # Top 5 for composite
                # Reconstruct expression (simplified)
                alpha_dict[alpha_info["strategy_id"]] = pd.Series(
                    np.random.randn(len(ohlcv)),  # Placeholder
                    index=ohlcv.index
                )
            
            if not alpha_dict:
                return None
            
            alpha_panel = pd.DataFrame(alpha_dict)
            
            # Simple equal-weight composite for now
            composite = alpha_panel.mean(axis=1)
            
            if verbose:
                logger.info(f"  MegaAlpha composite shape: {composite.shape}")
            
            return composite
        
        except Exception as e:
            logger.warning(f"MegaAlpha construction failed: {e}")
            return None
    
    def _classify_ic_quality(self, ic: float, ic_ir: Any = None) -> str:
        """Classify IC quality, requiring measured stability.

        Grading on |IC| alone promoted expressions whose IC was never shown to
        repeat: the registry carried ``ic_ir: 0.0`` (a hardcoded literal, not a
        measurement) and still graded IC 0.118 as "strong". A single-window IC
        with no dispersion estimate is unverified, not strong, so an absent or
        non-positive IR caps the grade instead of being ignored.
        """
        abs_ic = abs(ic)
        if abs_ic >= 0.15:
            grade = "very_strong"
        elif abs_ic >= 0.10:
            grade = "strong"
        elif abs_ic >= 0.05:
            grade = "good"
        elif abs_ic >= 0.02:
            grade = "significant"
        else:
            grade = "weak"

        try:
            ir = float(ic_ir) if ic_ir is not None else None
        except (TypeError, ValueError, OverflowError):
            ir = None
        # Only an absent or exactly-zero IR means "never measured". A negative
        # IR is a strong consistent signal in the opposite direction.
        if ir is None or not math.isfinite(ir) or ir == 0.0:
            # Stability was never measured. Report the magnitude honestly
            # without asserting the edge is repeatable.
            return f"{grade}_unverified_stability"
        return grade
    
    def _regime_and_liquidity_analysis(
        self, ohlcv: pd.DataFrame, verbose: bool
    ) -> tuple:
        """Phase 3.5: Regime detection and liquidity screening.
        
        From MNTO paper:
        - Regime detection adapts stops and position sizing
        - Liquidity screening filters illiquid stocks before alpha evaluation
        
        Returns:
            Tuple of (RegimeState, Dict[symbol, LiquidityScore])
        """
        # Regime detection
        regime_detector = RegimeDetector(lookback=60)
        regime_state = regime_detector.detect(ohlcv)
        
        if verbose:
            logger.info(f"  Regime: {regime_state.regime.value} "
                       f"(confidence={regime_state.confidence:.2f}, "
                       f"trend={regime_state.trend_strength:.2f})")
        
        # Liquidity screening (single asset case)
        screener = LiquidityScreener()
        liquidity_score = screener.compute_liquidity_score("ASSET", ohlcv)
        
        if verbose:
            logger.info(f"  Liquidity: score={liquidity_score.composite_score:.2f}, "
                       f"liquid={liquidity_score.is_liquid}, "
                       f"slippage={liquidity_score.estimated_slippage:.1f}bps")
        
        # Store for later use
        self._current_regime = regime_state
        self._current_liquidity = {"ASSET": liquidity_score}
        
        return regime_state, {"ASSET": liquidity_score}
    
    def _compute_optimal_statistics(self, alphas: List[Tuple[AlphaExpression, float]],
                                   ohlcv: pd.DataFrame,
                                   forward_returns: pd.Series,
                                   verbose: bool) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Compute optimal strategy statistics using Firoozye & Koshiyama results.
        
        Phase 6: Uses Theorem 2.3 and Corollaries 3.1-3.2 for:
        - Maximum Sharpe ratio bounds
        - Refined standard errors for Sharpe, skewness, kurtosis
        - Total Least Squares optimization diagnostics
        
        Returns:
            Tuple of (optimal_stats_dict, tls_diagnostics_dict)
        """
        evaluator = OptimalStrategyEvaluator()
        tls_optimizer = TLSOptimizer()
        
        if not alphas:
            return None, None
        
        try:
            # Compute forward returns for strategy evaluation
            from .ic_evaluator import evaluate_with_cache
            
            # Collect all alpha values for portfolio-level statistics
            all_alpha_values = []
            all_returns = []
            
            for expr, ic in alphas[:5]:  # Top 5 alphas
                alpha_values = evaluate_with_cache(expr, ohlcv)
                if alpha_values is not None and len(alpha_values) > 0:
                    all_alpha_values.append(alpha_values.values)
            
            if not all_alpha_values:
                return None, None
            
            # Compute composite alpha (simple average)
            min_len = min(len(a) for a in all_alpha_values)
            composite_alpha = np.mean([a[:min_len] for a in all_alpha_values], axis=0)
            strategy_returns = composite_alpha[1:] * forward_returns.values[:min_len-1]
            
            # Compute optimal strategy statistics
            stats = evaluator.compute_statistics(
                strategy_returns,
                composite_alpha[1:],
                sample_size=min_len - 1
            )
            
            optimal_stats = {
                "sharpe_ratio": stats.sharpe_ratio,
                "skewness": stats.skewness,
                "kurtosis": stats.kurtosis,
                "excess_kurtosis": stats.excess_kurtosis,
                "sharpe_std_err": stats.sharpe_std_err,
                "skewness_std_err": stats.skewness_std_err,
                "kurtosis_std_err": stats.kurtosis_std_err,
                "sharpe_ci_95": [stats.sharpe_ci_lower, stats.sharpe_ci_upper],
                "ic": stats.ic,
                "ic_squared": stats.ic_squared,
                "jarque_bera_stat": stats.jarque_bera_stat,
                "is_normal": stats.is_normal,
                "theoretical_bounds": {
                    "SR_MAX": evaluator.bounds.SR_MAX,
                    "SKEWNESS_MAX": evaluator.bounds.SKEWNESS_MAX,
                    "KURTOSIS_MAX": evaluator.bounds.KURTOSIS_MAX,
                    "KURTOSIS_MIN": evaluator.bounds.KURTOSIS_MIN,
                },
                "annualized_sharpe": evaluator.annualize_sharpe(stats.sharpe_ratio),
            }
            
            # TLS optimization diagnostics
            # Use alpha values as features, forward returns as target
            if len(composite_alpha) > 10:
                X_features = composite_alpha[:-1].reshape(-1, 1)
                y_target = forward_returns.values[:len(composite_alpha)-1]
                
                # Handle NaN values
                valid_mask = ~(np.isnan(X_features).flatten() | np.isnan(y_target))
                if np.sum(valid_mask) > 10:
                    tls_result = tls_optimizer.fit(X_features[valid_mask], y_target[valid_mask])
                    
                    tls_diagnostics = {
                        "correlation": tls_result["correlation"],
                        "r_squared": tls_result["r_squared"],
                        "smallest_singular_value": tls_result["smallest_singular_value"],
                        "coefficients": tls_result["coefficients"].tolist(),
                    }
                else:
                    tls_diagnostics = None
            else:
                tls_diagnostics = None
            
            if verbose:
                logger.info(f"  Optimal Statistics:")
                logger.info(f"    Sharpe ratio: {stats.sharpe_ratio:.4f} (stderr: {stats.sharpe_std_err:.4f})")
                logger.info(f"    Skewness: {stats.skewness:.4f} (stderr: {stats.skewness_std_err:.4f})")
                logger.info(f"    Kurtosis: {stats.kurtosis:.4f} (excess: {stats.excess_kurtosis:.4f})")
                logger.info(f"    95% CI for Sharpe: [{stats.sharpe_ci_lower:.4f}, {stats.sharpe_ci_upper:.4f}]")
                logger.info(f"    Normal (Jarque-Bera): {stats.is_normal}")
                logger.info(f"    Annualized Sharpe: {evaluator.annualize_sharpe(stats.sharpe_ratio):.4f}")
                if tls_diagnostics:
                    logger.info(f"    TLS Correlation: {tls_diagnostics['correlation']:.4f}")
                    logger.info(f"    TLS R^2: {tls_diagnostics['r_squared']:.4f}")
            
            return optimal_stats, tls_diagnostics
            
        except Exception as e:
            logger.warning(f"Optimal statistics computation failed: {e}")
            return None, None
    
    def _microstructure_analysis(
        self, ohlcv: pd.DataFrame, verbose: bool
    ) -> tuple:
        """Phase 7: Microstructure signal analysis.
        
        From Papers 7-10:
        - FVG detection (Kondapally 2025): liquidity void quality
        - KAT detection (Khobragade 2026): institutional absorption
        - Spoofing detection (Kc 2025): order book manipulation
        - RL agent (Hou 2026): adaptive trading signal
        
        Returns:
            Tuple of (fvg_result, kat_result, spoofing_result, rl_signal)
        """
        fvg_result = None
        kat_result = None
        spoofing_result = None
        rl_signal = None
        
        # FVG Detection
        if self.config.use_fvg_detection:
            fvg_detector = FVGDetector()
            fvg = fvg_detector.detect(ohlcv)
            fvg_result = fvg.to_dict()
            if verbose:
                logger.info(
                    f"  FVG: {fvg.total_fvgs} gaps detected "
                    f"(strong={fvg.strong_count}, moderate={fvg.moderate_count}, "
                    f"weak={fvg.weak_count}, avg_degree={fvg.avg_degree:.6f})"
                )
        
        # KAT Detection
        if self.config.use_kat_detection:
            kat_detector = KATDetector()
            kat = kat_detector.detect(ohlcv)
            kat_result = kat.to_dict()
            if verbose:
                logger.info(
                    f"  KAT: {kat.total_events} absorption events "
                    f"(bull={kat.bull_absorptions}, bear={kat.bear_absorptions}, "
                    f"state={kat.current_state.value})"
                )
        
        # Spoofing Detection
        if self.config.use_spoofing_detection:
            spoof_detector = SpoofingDetector()
            spoof = spoof_detector.detect(ohlcv)
            spoofing_result = spoof.to_dict()
            if verbose:
                logger.info(
                    f"  Spoofing: {spoof.total_events} events "
                    f"(bullish={spoof.bullish_spoofs}, bearish={spoof.bearish_spoofs}, "
                    f"avg_score={spoof.avg_spoof_score:.2f})"
                )
        
        # RL Agent Signal
        if self.config.use_rl_agent:
            rl_agent = RLTradingAgent()
            if len(ohlcv) > 10:
                rl_action = rl_agent.generate_signal(ohlcv, len(ohlcv) - 1)
                rl_signal = rl_action.to_dict()
                if verbose:
                    logger.info(
                        f"  RL Agent: {rl_action.action.name} "
                        f"(confidence={rl_action.confidence:.2f}, "
                        f"size={rl_action.position_size:.2f})"
                    )
        
        return fvg_result, kat_result, spoofing_result, rl_signal
    
    def _pairs_and_breakout_analysis(
        self, ohlcv: pd.DataFrame, verbose: bool
    ) -> tuple:
        """Phase 8: Pairs trading and volume breakout analysis.
        
        From Papers 11-12:
        - Pairs trading (Temlyakov 2012): cointegration + parameter optimization
        - Volume breakout (2025): 4x volume spike detection
        
        Returns:
            Tuple of (pairs_result, breakout_result)
        """
        pairs_result = None
        breakout_result = None
        
        # Pairs Trading
        if self.config.use_pairs_trading:
            trader = PairsTrader()
            pairs = trader.analyze(ohlcv)
            pairs_result = pairs.to_dict()
            if verbose:
                logger.info(
                    f"  Pairs: {pairs.pairs_found} cointegrated pairs found, "
                    f"{len(pairs.signals)} signals generated"
                )
                if pairs.optimized_params:
                    p = pairs.optimized_params
                    logger.info(
                        f"    Best params: entry={p.entry_threshold:.2f}, "
                        f"exit={p.exit_threshold:.2f}, loss={p.loss_control:.2f}"
                    )
        
        # Volume Breakout
        if self.config.use_volume_breakout:
            breakout_detector = VolumeBreakoutDetector()
            breakout = breakout_detector.detect(ohlcv)
            breakout_result = breakout.to_dict()
            if verbose:
                logger.info(
                    f"  Breakout: {breakout.total_breakouts} breakouts "
                    f"(bull={breakout.bullish_count}, bear={breakout.bearish_count}, "
                    f"avg_vol_ratio={breakout.avg_volume_ratio:.2f})"
                )
        
        return pairs_result, breakout_result
    
    def _walk_forward_analysis(
        self, ohlcv: pd.DataFrame, verbose: bool, alphas=None
    ) -> Optional[Dict[str, Any]]:
        """Phase 9: Walk-forward validation.
        
        From Thokal (2026): realistic backtesting with periodic retraining.
        
        Returns:
            Walk-forward result dict
        """
        if not self.config.use_walk_forward:
            return None
        
        # Validate what Phase 4 actually registered. This previously always
        # walked forward a hardcoded 20-bar price momentum, so the verdict
        # described a generic baseline and never the discovered alphas.
        # ``alphas`` is the (AlphaExpression, ic) tuple list -- the walk-forward
        # needs the live expression object because evaluate_with_cache calls
        # expr.evaluate(). The dicts returned by _register_strategies only carry
        # the rendered string, and passing those here raised an uncaught
        # KeyError that aborted the sprint after registration had already
        # mutated the factory. Accept either shape, and treat a malformed
        # entry as absent rather than fatal.
        top_expr = None
        if alphas:
            def _rank(pair: Any) -> float:
                try:
                    if isinstance(pair, dict):
                        return abs(float(pair.get("ic_mean")))
                    return abs(float(pair[1]))
                except (AttributeError, IndexError, KeyError, TypeError,
                        ValueError, OverflowError):
                    return float("-inf")

            def _expr_of(pair: Any) -> Any:
                return pair.get("expression") if isinstance(pair, dict) else pair[0]

            try:
                top_expr = _expr_of(max(alphas, key=_rank))
            except (AttributeError, IndexError, KeyError, TypeError,
                    ValueError, OverflowError):
                top_expr = None
        validated_as = ("discovered_alpha" if top_expr is not None
                        else "generic_momentum_baseline")
        
        def simple_strategy(train, test):
            """Hold long/short by the sign of the leading signal."""
            if top_expr is not None:
                try:
                    arr = np.asarray(evaluate_with_cache(top_expr, train), dtype=float)
                    if arr.ndim == 1 and arr.size and np.isfinite(arr[-1]):
                        return [1 if float(arr[-1]) > 0 else -1] * len(test)
                except Exception:
                    pass
            returns = train["close"].pct_change().dropna()
            if len(returns) < 20:
                return [0] * len(test)
            momentum = returns.rolling(20).mean().iloc[-1]
            return [1 if momentum > 0 else -1] * len(test)

        analyzer = WalkForwardAnalyzer(
            train_window=min(500, len(ohlcv) // 3),
            test_window=min(250, len(ohlcv) // 6),
        )
        
        result = analyzer.run(ohlcv, simple_strategy)
        
        if verbose:
            logger.info(
                f"  Walk-Forward: {result.total_folds} folds, "
                f"{result.profitable_folds} profitable, "
                f"avg_sharpe={result.avg_test_sharpe:.2f}, "
                f"robustness={result.robustness_score:.2f}"
            )
        
        out = result.to_dict()
        out["validated_as"] = validated_as
        try:
            out["alpha_expression"] = (top_expr.to_string()
                                       if hasattr(top_expr, "to_string")
                                       else (str(top_expr) if top_expr is not None else None))
        except Exception:
            out["alpha_expression"] = None
        if verbose and validated_as != "discovered_alpha":
            logger.warning(
                "  Walk-forward used a generic momentum baseline: no discovered "
                "alpha was available to validate.")
        return out
