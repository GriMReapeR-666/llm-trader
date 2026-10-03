"""Alpha Factor Mining Module.

Implements hierarchical genetic algorithm with quality-diversity selection
for autonomous alpha factor discovery.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from .expression_tree import (
    AlphaExpression,
    ExpressionCache,
    evaluate_with_cache,
    create_leaf,
    create_unary,
    create_binary,
    create_random_expression,
)
from .expression_generator import (
    ExpressionGenerator,
    generate_from_templates,
    compute_population_diversity,
    compute_operator_distribution,
)
from .ic_evaluator import (
    ICEvaluator,
    ICResult,
    calculate_forward_returns,
    calculate_forward_returns_5day,
    batch_evaluate_ic,
    select_significant_alphas,
)
from .genetic_operators import GeneticOperators
from .pca_projector import PCAProjector, PCAResult
from .grid_selector import GridSelector, GridSelectionResult, ParentPoolManager
from .ga_engine import GAEngine, GAConfig, GAEvolutionResult
from .dnn_aggregator import DNNAggregator, MegaAlphaAggregator, SHAPExplainer
from .strategy_bridge import AlphaStrategyBridge, AlphaStrategy
from .failure_modes import FailureMode, FailureDiagnostic, classify_failure
from .repair_engine import RepairEngine, RepairConfig, RepairResult
from .rag_specification import RAGSpecificationEncoder, SpecificationValidator
from .exploration_grid import ExplorationGrid, GridCell, CellStatus
from .alpha_risk import AlphaRiskManager, AlphaRiskConfig, AlphaRiskProfile
from .pipeline import AlphaMiningPipeline, AlphaMiningConfig, AlphaMiningResult
from .horizon_alpha import HorizonAlphaEvaluator, HorizonAlphaResult, HorizonConfig
from .optimal_strategy import (
    OptimalStrategyEvaluator,
    TLSOptimizer,
    StrategyStatistics,
    TheoreticalBounds,
    compute_strategy_quality,
    compute_optimal_ic_target,
)
from .regime_detector import RegimeDetector, MarketRegime, RegimeState
from .cvar_risk import CVaRRiskManager, CVaRResult, PositionSizing
from .liquidity_screen import LiquidityScreener, LiquidityScore
from .dynamic_stops import DynamicStopManager, StopState, StopType
from .fvg_detector import FVGDetector, FVGResult, FVGType, FVGQuality
from .kat_detector import KATDetector, KATResult, KATSignal
from .spoofing_detector import SpoofingDetector, SpoofingResult, SpoofingBias
from .rl_agent import RLTradingAgent, AgentConfig, TradeAction, ActionType
from .pairs_trader import PairsTrader, PairsResult, TradingParams, PairSignal
from .volume_breakout import VolumeBreakoutDetector, BreakoutResult, BreakoutSignal, BreakoutDirection
from .walk_forward import WalkForwardAnalyzer, WalkForwardResult, WalkForwardFold
from .alpha101_knowledge_base import (
    Alpha101KnowledgeBase,
    Alpha101Record,
    NestedLoopParams,
    PAPER_DEFAULTS,
    build_knowledge_base,
    extract_operators,
    extract_windows,
)
from .alpha_judge import JudgeScore, judge_candidate

__all__ = [
    # Expression Tree
    "AlphaExpression",
    "ExpressionCache",
    "evaluate_with_cache",
    "create_leaf",
    "create_unary",
    "create_binary",
    "create_random_expression",
    
    # Expression Generator
    "ExpressionGenerator",
    "generate_from_templates",
    "compute_population_diversity",
    "compute_operator_distribution",
    
    # IC Evaluator
    "ICEvaluator",
    "ICResult",
    "calculate_forward_returns",
    "calculate_forward_returns_5day",
    "batch_evaluate_ic",
    "select_significant_alphas",
    
    # Genetic Operators
    "GeneticOperators",
    
    # PCA Projector
    "PCAProjector",
    "PCAResult",
    
    # Grid Selector
    "GridSelector",
    "GridSelectionResult",
    "ParentPoolManager",
    
    # GA Engine
    "GAEngine",
    "GAConfig",
    "GAEvolutionResult",
    
    # DNN Aggregator
    "DNNAggregator",
    "MegaAlphaAggregator",
    "SHAPExplainer",
    
    # Strategy Bridge
    "AlphaStrategyBridge",
    "AlphaStrategy",
    
    # Failure Modes
    "FailureMode",
    "FailureDiagnostic",
    "classify_failure",
    
    # Repair Engine
    "RepairEngine",
    "RepairConfig",
    "RepairResult",
    
    # RAG Specification
    "RAGSpecificationEncoder",
    "SpecificationValidator",
    
    # Exploration Grid
    "ExplorationGrid",
    "GridCell",
    "CellStatus",
    
    # Alpha Risk
    "AlphaRiskManager",
    "AlphaRiskConfig",
    "AlphaRiskProfile",
    
    # Pipeline
    "AlphaMiningPipeline",
    "AlphaMiningConfig",
    "AlphaMiningResult",
    
    # Horizon Alpha (Bessembinder et al. 2023)
    "HorizonAlphaEvaluator",
    "HorizonAlphaResult",
    "HorizonConfig",
    
    # Optimal Strategy (Firoozye & Koshiyama 2023)
    "OptimalStrategyEvaluator",
    "TLSOptimizer",
    "StrategyStatistics",
    "TheoreticalBounds",
    "compute_strategy_quality",
    "compute_optimal_ic_target",
    
    # Regime Detection (MNTO)
    "RegimeDetector",
    "MarketRegime",
    "RegimeState",
    
    # CV@R Risk (MNTO)
    "CVaRRiskManager",
    "CVaRResult",
    "PositionSizing",
    
    # Liquidity Screen (MNTO)
    "LiquidityScreener",
    "LiquidityScore",
    
    # Dynamic Stops (MNTO)
    "DynamicStopManager",
    "StopState",
    "StopType",
    
    # FVG Detection (Kondapally 2025)
    "FVGDetector",
    "FVGResult",
    "FVGType",
    "FVGQuality",
    
    # KAT Detection (Khobragade 2026)
    "KATDetector",
    "KATResult",
    "KATSignal",
    
    # Spoofing Detection (Kc 2025)
    "SpoofingDetector",
    "SpoofingResult",
    "SpoofingBias",
    
    # RL Agent (Hou 2026)
    "RLTradingAgent",
    "AgentConfig",
    "TradeAction",
    "ActionType",
    
    # Pairs Trading (Temlyakov 2012)
    "PairsTrader",
    "PairsResult",
    "TradingParams",
    "PairSignal",
    
    # Volume Breakout (2025)
    "VolumeBreakoutDetector",
    "BreakoutResult",
    "BreakoutSignal",
    "BreakoutDirection",
    
    # Walk-Forward (Thokal 2026)
    "WalkForwardAnalyzer",
    "WalkForwardResult",
    "WalkForwardFold",

    # 101 Alpha Knowledge Base Psi (Vu et al. 2026, P01 Sec. 3.5)
    "Alpha101KnowledgeBase",
    "Alpha101Record",
    "NestedLoopParams",
    "PAPER_DEFAULTS",
    "build_knowledge_base",
    "extract_operators",
    "extract_windows",

    # Deterministic Judge rubric (Vu et al. 2026, P01 Sec. 3.6.2)
    "JudgeScore",
    "judge_candidate",
]
