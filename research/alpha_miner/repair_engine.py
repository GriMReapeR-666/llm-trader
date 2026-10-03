"""Convergence-Governed Repair Engine for Alpha Expressions.

Implements the repair engine from BrainAlpha with five formal stop conditions
that prevent wasted simulation budget on structurally unrecoverable expressions.

References:
    Wangpratham, N. (2026). "BrainAlpha: An Autonomous Multi-Agent System
    for Quantitative Alpha Discovery." Working Paper.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .failure_modes import (
    FailureMode,
    FailureDiagnostic,
    classify_failure,
    get_repair_actions,
    assess_recoverability,
)
from .expression_tree import AlphaExpression

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repair Configuration
# ---------------------------------------------------------------------------

@dataclass
class RepairConfig:
    """Configuration for the repair engine."""
    max_iterations: int = 5
    min_sharpe_improvement: float = 0.02
    divergence_threshold: float = 0.50
    temperature_repair: float = 0.3  # Conservative for targeted modification
    temperature_generation: float = 0.7  # Higher for creative exploration


# ---------------------------------------------------------------------------
# Repair Result
# ---------------------------------------------------------------------------

@dataclass
class RepairResult:
    """Result of the repair process."""
    original_expression: AlphaExpression
    repaired_expression: Optional[AlphaExpression]
    iterations: int
    final_diagnostic: Optional[FailureDiagnostic]
    stop_reason: str
    repair_history: List[Dict[str, Any]]
    success: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "iterations": self.iterations,
            "stop_reason": self.stop_reason,
            "success": self.success,
            "repair_history": self.repair_history,
            "final_diagnostic": self.final_diagnostic.to_dict() if self.final_diagnostic else None,
        }


# ---------------------------------------------------------------------------
# Repair Engine
# ---------------------------------------------------------------------------

class RepairEngine:
    """Convergence-governed repair engine with five stop conditions."""
    
    def __init__(self, config: Optional[RepairConfig] = None):
        self.config = config or RepairConfig()
    
    def repair(self, expression: AlphaExpression,
               evaluation_results: Dict[str, Any],
               original_thesis: str = "") -> RepairResult:
        """Attempt to repair a failed alpha expression.
        
        Implements the three-phase repair process from BrainAlpha:
        1. Recoverability Assessment (hard stops)
        2. Strategy Selection (deterministic)
        3. LLM Rewrite and Convergence Governance
        
        Args:
            expression: The failed alpha expression
            evaluation_results: Results from evaluation (metrics, checks)
            original_thesis: Original investment thesis
            
        Returns:
            RepairResult with repair history and outcome
        """
        repair_history = []
        current_expression = expression.copy()
        
        # Phase 1: Recoverability Assessment
        is_recoverable, stop_reason = self._assess_recoverability(
            expression, evaluation_results
        )
        
        if not is_recoverable:
            return RepairResult(
                original_expression=expression,
                repaired_expression=None,
                iterations=0,
                final_diagnostic=None,
                stop_reason=stop_reason,
                repair_history=[],
                success=False,
            )
        
        # Phase 2 & 3: Iterative Repair
        prev_sharpe = evaluation_results.get("sharpe", 0.0)
        no_improvement_count = 0
        
        for iteration in range(self.config.max_iterations):
            # Classify failure
            diagnostic = classify_failure(
                evaluation_results.get("checks", {}),
                evaluation_results.get("metrics", {})
            )
            
            # Record iteration
            iteration_record = {
                "iteration": iteration + 1,
                "failure_mode": diagnostic.failure_mode.value,
                "primary_check": diagnostic.primary_check,
                "gap_pct": diagnostic.gap_pct,
                "sharpe_before": prev_sharpe,
            }
            
            # Get repair actions
            repair_actions = get_repair_actions(
                diagnostic.failure_mode, 
                diagnostic.primary_check
            )
            
            # Apply first untried action (deterministic selection)
            action = self._select_repair_action(
                repair_actions, 
                repair_history
            )
            
            if action is None:
                # All actions exhausted
                iteration_record["stop_reason"] = "ACTIONS_EXHAUSTED"
                repair_history.append(iteration_record)
                return RepairResult(
                    original_expression=expression,
                    repaired_expression=None,
                    iterations=iteration + 1,
                    final_diagnostic=diagnostic,
                    stop_reason="ACTIONS_EXHAUSTED",
                    repair_history=repair_history,
                    success=False,
                )
            
            # Apply repair action
            new_expression = self._apply_repair_action(
                current_expression, action
            )
            
            iteration_record["action"] = action
            iteration_record["expression_after"] = new_expression.to_string()
            
            # Note: In real implementation, this would call the evaluation oracle
            # For now, we simulate improvement
            new_sharpe = prev_sharpe + 0.05  # Simulated improvement
            
            iteration_record["sharpe_after"] = new_sharpe
            iteration_record["improvement"] = new_sharpe - prev_sharpe
            
            # Check stop condition (iii): Sharpe improvement < δ for 3 consecutive
            if new_sharpe - prev_sharpe < self.config.min_sharpe_improvement:
                no_improvement_count += 1
                if no_improvement_count >= 3:
                    iteration_record["stop_reason"] = "NO_IMPROVEMENT"
                    repair_history.append(iteration_record)
                    return RepairResult(
                        original_expression=expression,
                        repaired_expression=None,
                        iterations=iteration + 1,
                        final_diagnostic=diagnostic,
                        stop_reason="NO_IMPROVEMENT",
                        repair_history=repair_history,
                        success=False,
                    )
            else:
                no_improvement_count = 0
            
            # Check stop condition (iv): Divergence
            if diagnostic.gap_pct > self.config.divergence_threshold:
                iteration_record["stop_reason"] = "DIVERGENCE"
                repair_history.append(iteration_record)
                return RepairResult(
                    original_expression=expression,
                    repaired_expression=None,
                    iterations=iteration + 1,
                    final_diagnostic=diagnostic,
                    stop_reason="DIVERGENCE",
                    repair_history=repair_history,
                    success=False,
                )
            
            # Check stop condition (i): Expression passes all checks
            if self._check_passing(evaluation_results):
                iteration_record["stop_reason"] = "PASSING"
                repair_history.append(iteration_record)
                return RepairResult(
                    original_expression=expression,
                    repaired_expression=new_expression,
                    iterations=iteration + 1,
                    final_diagnostic=None,
                    stop_reason="PASSING",
                    repair_history=repair_history,
                    success=True,
                )
            
            # Update for next iteration
            current_expression = new_expression
            prev_sharpe = new_sharpe
            repair_history.append(iteration_record)
        
        # Stop condition (ii): Maximum iterations reached
        return RepairResult(
            original_expression=expression,
            repaired_expression=current_expression,
            iterations=self.config.max_iterations,
            final_diagnostic=diagnostic,
            stop_reason="MAX_ITERATIONS",
            repair_history=repair_history,
            success=False,
        )
    
    def _assess_recoverability(self, expression: AlphaExpression,
                              evaluation_results: Dict[str, Any]) -> Tuple[bool, str]:
        """Assess whether the expression is recoverable."""
        complexity = self._calculate_complexity(expression)
        failure_count = evaluation_results.get("failure_count", 0)
        returns = evaluation_results.get("annualized_returns", 0.0)
        self_corr = evaluation_results.get("self_correlation", 0.0)
        gap_pct = evaluation_results.get("max_gap_pct", 0.0)
        
        return assess_recoverability(
            complexity, failure_count, returns, self_corr, gap_pct
        )
    
    def _calculate_complexity(self, expression: AlphaExpression) -> int:
        """Calculate expression complexity (AST node count)."""
        return expression.size()
    
    def _select_repair_action(self, actions: List[str],
                             repair_history: List[Dict[str, Any]]) -> Optional[str]:
        """Select first untried repair action."""
        tried_actions = {h.get("action") for h in repair_history}
        
        for action in actions:
            if action not in tried_actions:
                return action
        
        return None
    
    def _apply_repair_action(self, expression: AlphaExpression,
                            action: str) -> AlphaExpression:
        """Apply a repair action to the expression.
        
        This is a simplified implementation. In production, this would
        call an LLM to rewrite the expression based on the action.
        """
        # For now, return a copy (LLM rewrite would happen here)
        return expression.copy()
    
    def _check_passing(self, evaluation_results: Dict[str, Any]) -> bool:
        """Check if expression passes all quality gates."""
        checks = evaluation_results.get("checks", {})
        
        # All checks must pass
        return all(checks.values()) if checks else False


# ---------------------------------------------------------------------------
# Strategy Selector
# ---------------------------------------------------------------------------

class StrategySelector:
    """Deterministic strategy selector for repair actions."""
    
    def __init__(self):
        self.action_history: List[str] = []
    
    def select(self, diagnostic: FailureDiagnostic) -> str:
        """Select repair action based on diagnostic.
        
        Selection is fully deterministic: no LLM.
        """
        actions = get_repair_actions(
            diagnostic.failure_mode,
            diagnostic.primary_check
        )
        
        # Select first untried action
        for action in actions:
            if action not in self.action_history:
                self.action_history.append(action)
                return action
        
        # All actions tried
        return "MANUAL_REVIEW"
    
    def reset(self):
        """Reset action history."""
        self.action_history.clear()


# ---------------------------------------------------------------------------
# Convergence Governor
# ---------------------------------------------------------------------------

class ConvergenceGovernor:
    """Enforces convergence conditions for the repair loop."""
    
    def __init__(self, config: Optional[RepairConfig] = None):
        self.config = config or RepairConfig()
        self.sharpe_history: List[float] = []
        self.iteration_count: int = 0
    
    def update(self, sharpe: float) -> Tuple[bool, str]:
        """Update governor with new Sharpe value.
        
        Returns:
            Tuple of (should_continue, reason)
        """
        self.iteration_count += 1
        self.sharpe_history.append(sharpe)
        
        # Stop condition (ii): Maximum iterations
        if self.iteration_count >= self.config.max_iterations:
            return False, "MAX_ITERATIONS"
        
        # Stop condition (iii): No improvement for 3 consecutive
        if len(self.sharpe_history) >= 4:
            recent = self.sharpe_history[-3:]
            improvements = [recent[i] - recent[i-1] for i in range(1, len(recent))]
            if all(imp < self.config.min_sharpe_improvement for imp in improvements):
                return False, "NO_IMPROVEMENT"
        
        # Stop condition (iv): Divergence
        if len(self.sharpe_history) >= 2:
            if self.sharpe_history[-1] < self.sharpe_history[-2]:
                return False, "DIVERGENCE"
        
        return True, "CONTINUE"
    
    def reset(self):
        """Reset governor state."""
        self.sharpe_history.clear()
        self.iteration_count = 0
