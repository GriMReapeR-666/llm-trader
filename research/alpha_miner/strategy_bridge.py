"""Strategy Factory Bridge for Alpha Mining.

Bridges alpha mining results to the existing strategy factory for
lifecycle management and promotion/demotion gates.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.file_utils import heal_strategy_registry_marker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alpha Strategy Data
# ---------------------------------------------------------------------------

@dataclass
class AlphaStrategy:
    """Alpha-mined strategy data."""
    strategy_id: str
    name: str
    expression: str
    ic_mean: Optional[float]
    ic_ir: Optional[float]
    ic_quality: str
    shap_values: Dict[int, float]
    created_at: float
    stage: str = "CANDIDATE"
    metrics: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.

        `ic_ir` is Optional because the pipeline passes `ic_ir=None` when the
        stability estimate could not be computed. Rounding an Optional crashed
        the whole registry write (`round(None, 4)` -> TypeError), which
        `_save_alpha_strategies` swallowed into a `logger.warning`, silently
        freezing `strategy_registry.json` at its pre-fix contents.
        """
        return {
            "id": self.strategy_id,
            "name": self.name,
            "expression": self.expression,
            "ic_mean": round(self.ic_mean, 6) if self.ic_mean is not None else None,
            "ic_ir": round(self.ic_ir, 4) if self.ic_ir is not None else None,
            "ic_quality": self.ic_quality,
            "stage": self.stage,
            "created_at": self.created_at,
            "metrics": self.metrics or {},
        }


# ---------------------------------------------------------------------------
# Alpha Strategy Bridge
# ---------------------------------------------------------------------------

class AlphaStrategyBridge:
    """Bridge alpha mining results to strategy factory."""
    
    def __init__(self, factory=None, registry_file: str = "strategy_registry.json"):
        """Initialize bridge.
        
        Args:
            factory: StrategyFactory instance (optional)
            registry_file: Path to strategy registry file
        """
        self.factory = factory
        self.registry_file = registry_file
        self.alpha_strategies: Dict[str, AlphaStrategy] = {}
        
        # Load existing alpha strategies
        self._load_alpha_strategies()
    
    def _load_alpha_strategies(self):
        """Load alpha strategies from file."""
        try:
            if os.path.exists(self.registry_file):
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Load alpha-specific strategies
                for strategy in data.get("strategies", []):
                    if strategy.get("source") == "alpha_mining":
                        alpha_strategy = AlphaStrategy(
                            strategy_id=strategy["id"],
                            name=strategy["name"],
                            expression=strategy.get("expression", ""),
                            ic_mean=strategy.get("ic_mean", 0.0),
                            ic_ir=strategy.get("ic_ir", 0.0),
                            ic_quality=strategy.get("ic_quality", "weak"),
                            shap_values=strategy.get("shap_values", {}),
                            created_at=strategy.get("created_at", time.time()),
                            stage=strategy.get("stage", "CANDIDATE"),
                            metrics=strategy.get("metrics", {}),
                        )
                        self.alpha_strategies[strategy["id"]] = alpha_strategy
        
        except Exception as e:
            logger.warning(f"Failed to load alpha strategies: {e}")
    
    def register_alpha_strategy(self, expression: str,
                               ic_metrics: Dict[str, float],
                               shap_values: Dict[int, float],
                               name: Optional[str] = None) -> str:
        """Register mined alpha as a new strategy candidate.
        
        Args:
            expression: Alpha expression string
            ic_metrics: IC metrics (ic_mean, ic_ir, ic_quality)
            shap_values: SHAP values for explainability
            name: Optional strategy name
            
        Returns:
            Strategy ID
        """
        # Generate strategy ID
        strategy_id = f"alpha_{uuid.uuid4().hex[:8]}"
        
        # Create strategy name if not provided
        if name is None:
            ic_mean = ic_metrics.get("ic_mean", 0.0)
            ic_quality = ic_metrics.get("ic_quality", "weak")
            name = f"Alpha-{ic_quality.upper()[:3]}-{strategy_id[-6:]}"
        
        # Create alpha strategy
        alpha_strategy = AlphaStrategy(
            strategy_id=strategy_id,
            name=name,
            expression=expression,
            ic_mean=ic_metrics.get("ic_mean", 0.0),
            ic_ir=ic_metrics.get("ic_ir", 0.0),
            ic_quality=ic_metrics.get("ic_quality", "weak"),
            shap_values=shap_values,
            created_at=time.time(),
            stage="CANDIDATE",
            metrics=ic_metrics,
        )
        
        # Store
        self.alpha_strategies[strategy_id] = alpha_strategy
        
        # Register in factory if available
        if self.factory is not None:
            self._register_in_factory(alpha_strategy)
        
        # Persist to file
        self._save_alpha_strategies()
        
        logger.info(f"Registered alpha strategy: {strategy_id} ({name})")
        return strategy_id
    
    def _register_in_factory(self, alpha_strategy: AlphaStrategy):
        """Register strategy in the main factory."""
        try:
            strategy_record = {
                "id": alpha_strategy.strategy_id,
                "name": alpha_strategy.name,
                "stage": alpha_strategy.stage,
                "source": "alpha_mining",
                "expression": alpha_strategy.expression,
                "ic_mean": alpha_strategy.ic_mean,
                "ic_ir": alpha_strategy.ic_ir,
                "ic_quality": alpha_strategy.ic_quality,
                "trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "sharpe": 0.0,
                "max_dd": 0.0,
                "created_at": alpha_strategy.created_at,
                "notes": f"Alpha-mined strategy: {alpha_strategy.ic_quality}",
            }
            
            self.factory.upsert(strategy_record)
        
        except Exception as e:
            logger.warning(f"Failed to register in factory: {e}")
    
    def update_strategy_metrics(self, strategy_id: str,
                               backtest_results: Dict[str, float]) -> bool:
        """Update strategy metrics for factory gate evaluation.
        
        Args:
            strategy_id: Strategy ID
            backtest_results: Backtest metrics (trades, win_rate, profit_factor, etc.)
            
        Returns:
            True if successful
        """
        if strategy_id not in self.alpha_strategies:
            logger.warning(f"Strategy {strategy_id} not found")
            return False
        
        # Update alpha strategy
        alpha_strategy = self.alpha_strategies[strategy_id]
        alpha_strategy.metrics = {**(alpha_strategy.metrics or {}), **backtest_results}
        
        # Update in factory
        if self.factory is not None:
            try:
                strategy = self.factory.get(strategy_id)
                if strategy:
                    strategy.update(backtest_results)
                    self.factory._persist_locked()
            except Exception as e:
                logger.warning(f"Failed to update factory: {e}")
        
        # Persist
        self._save_alpha_strategies()
        
        return True
    
    def promote_strategy(self, strategy_id: str) -> bool:
        """Promote strategy through factory gates.
        
        Args:
            strategy_id: Strategy ID
            
        Returns:
            True if promotion was successful
        """
        if strategy_id not in self.alpha_strategies:
            logger.warning(f"Strategy {strategy_id} not found")
            return False
        
        alpha_strategy = self.alpha_strategies[strategy_id]
        
        # Check if eligible for promotion
        if alpha_strategy.stage == "CANDIDATE":
            # Check promotion requirements
            metrics = alpha_strategy.metrics or {}
            trades = metrics.get("trades", 0)
            win_rate = metrics.get("win_rate", 0.0)
            profit_factor = metrics.get("profit_factor", 0.0)
            
            # Simple promotion criteria
            if trades >= 30 and win_rate >= 48.0 and profit_factor >= 1.05:
                alpha_strategy.stage = "EVALUATION"
                logger.info(f"Promoted {strategy_id} to EVALUATION")
                
                # Update in factory
                if self.factory is not None:
                    self.factory.set_stage(strategy_id, "EVALUATION", 
                                          "Alpha mining promotion criteria met")
                
                self._save_alpha_strategies()
                return True
        
        return False
    
    def quarantine_strategy(self, strategy_id: str, reason: str = "Performance decay") -> bool:
        """Quarantine a strategy.
        
        Args:
            strategy_id: Strategy ID
            reason: Quarantine reason
            
        Returns:
            True if successful
        """
        if strategy_id not in self.alpha_strategies:
            return False
        
        alpha_strategy = self.alpha_strategies[strategy_id]
        alpha_strategy.stage = "QUARANTINE"
        
        # Update in factory
        if self.factory is not None:
            self.factory.set_stage(strategy_id, "QUARANTINE", reason)
        
        self._save_alpha_strategies()
        
        logger.info(f"Quarantined {strategy_id}: {reason}")
        return True
    
    def get_active_alpha_strategies(self) -> List[Dict[str, Any]]:
        """Get all active alpha strategies (not quarantined/retired)."""
        active = []
        for strategy in self.alpha_strategies.values():
            if strategy.stage not in ("QUARANTINE", "RETIRED"):
                active.append(strategy.to_dict())
        
        return active
    
    def get_strategy_expression(self, strategy_id: str) -> Optional[str]:
        """Get expression for a strategy."""
        if strategy_id in self.alpha_strategies:
            return self.alpha_strategies[strategy_id].expression
        return None
    
    def check_alpha_decay(self, strategy_id: str,
                         ic_history: List[float],
                         decay_threshold: float = 0.5) -> bool:
        """Check if alpha IC is decaying.
        
        Args:
            strategy_id: Strategy ID
            ic_history: Historical IC values
            decay_threshold: Threshold for decay detection
            
        Returns:
            True if decay detected
        """
        if len(ic_history) < 10:
            return False
        
        # Compare recent IC to initial IC
        initial_ic = np.mean(ic_history[:5])
        recent_ic = np.mean(ic_history[-5:])
        
        if abs(initial_ic) > 0:
            decay_ratio = recent_ic / initial_ic
            if decay_ratio < decay_threshold:
                logger.warning(f"Alpha decay detected for {strategy_id}: "
                             f"IC dropped from {initial_ic:.4f} to {recent_ic:.4f}")
                return True
        
        return False
    
    def _save_alpha_strategies(self):
        """Save alpha strategies to file."""
        try:
            # Load existing registry
            if os.path.exists(self.registry_file):
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {"strategies": []}
            
            # Remove existing alpha strategies
            data["strategies"] = [
                s for s in data["strategies"] 
                if s.get("source") != "alpha_mining"
            ]
            
            # Add current alpha strategies
            for strategy in self.alpha_strategies.values():
                data["strategies"].append({
                    **strategy.to_dict(),
                    "source": "alpha_mining",
                })
            
            # Save.  This rewrites the whole registry (it drops every
            # alpha_mining row first), so it MUST refresh the CRITICAL #10
            # invalidation marker's counts — otherwise the count silently goes
            # stale here and tests/test_evidence_artifacts.py fails on a file
            # this method wrote.
            heal_strategy_registry_marker(data)
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        
        except Exception:
            # Never let a persistence fault kill the mining run, but do not let
            # it hide either: a swallowed write here is how strategy_registry.json
            # stayed frozen at its pre-fix contents while every call site
            # reported success. exc_info keeps the traceback greppable.
            logger.warning("Failed to save alpha strategies to %s",
                           self.registry_file, exc_info=True)
    
    def get_diversity_score(self) -> float:
        """Calculate diversity score across alpha strategies."""
        if not self.alpha_strategies:
            return 0.0
        
        # Simple diversity: fraction of unique operators used
        operators = set()
        for strategy in self.alpha_strategies.values():
            expr = strategy.expression
            # Extract operators (simple parsing)
            for op in ["ts_mean", "ts_std", "ts_rank", "add", "subtract", 
                       "multiply", "divide", "cs_rank"]:
                if op in expr:
                    operators.add(op)
        
        # Normalize by total possible operators
        total_operators = 15  # Approximate
        return len(operators) / total_operators


# ---------------------------------------------------------------------------
# Factory Integration Helper
# ---------------------------------------------------------------------------

def integrate_alpha_mining_with_factory(factory, ga_result, ohlcv, forward_returns):
    """Integrate GA mining results with strategy factory.
    
    Args:
        factory: StrategyFactory instance
        ga_result: GAEvolutionResult
        ohlcv: OHLCV data
        forward_returns: Forward returns
    """
    from .dnn_aggregator import MegaAlphaAggregator
    
    bridge = AlphaStrategyBridge(factory)
    
    # Get top alphas
    top_alphas = ga_result.best_expressions[:50]
    top_ics = ga_result.best_ics[:50]
    
    # Create alpha panel
    from .expression_tree import evaluate_with_cache
    alpha_dict = {}
    for i, expr in enumerate(top_alphas):
        try:
            alpha_values = evaluate_with_cache(expr, ohlcv)
            alpha_dict[f"alpha_{i}"] = alpha_values
        except Exception:
            continue
    
    if not alpha_dict:
        logger.warning("No valid alphas for integration")
        return
    
    alpha_panel = pd.DataFrame(alpha_dict)
    
    # Construct MegaAlpha
    mega_aggregator = MegaAlphaAggregator(n_alphas=len(alpha_dict))
    mega_result = mega_aggregator.construct(alpha_panel, forward_returns)
    
    # Register composite strategy
    bridge.register_alpha_strategy(
        expression="MegaAlpha Composite",
        ic_metrics={
            "ic_mean": mega_result.ic_score,
            "ic_ir": 0.0,
            "ic_quality": "strong" if abs(mega_result.ic_score) > 0.05 else "good",
        },
        shap_values=mega_result.shap_values,
        name="MegaAlpha-Composite",
    )
    
    # Register individual top alphas
    for i, (expr, ic) in enumerate(zip(top_alphas[:10], top_ics[:10])):
        bridge.register_alpha_strategy(
            expression=expr.to_string(),
            ic_metrics={
                "ic_mean": ic,
                "ic_ir": 0.0,
                "ic_quality": "strong" if abs(ic) > 0.1 else "good" if abs(ic) > 0.05 else "significant",
            },
            shap_values={},
            name=f"Alpha-{i+1}",
        )
    
    logger.info(f"Integrated {len(top_alphas)} alphas with strategy factory")
