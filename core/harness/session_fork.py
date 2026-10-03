"""Session Forking & Counterfactual Hypothesis Branching Engine.

Enables the trading agent to spawn speculative, isolated execution branches
from the same market snapshot, test divergent scenarios (e.g. Hawkish vs Dovish,
Breakout vs Mean-Reversion), validate risk, and commit only the winning decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Dict, List, Optional
import copy

from core.harness.kernel import TradingContext

logger = logging.getLogger(__name__)


@dataclass
class Hypothesis:
    """A speculative scenario to evaluate in an isolated context fork."""
    name: str
    prompt_override: Optional[str] = None
    macro_regime_override: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class BranchResult:
    """Outcome of evaluating a speculative hypothesis within an isolated fork."""
    hypothesis_name: str
    proposal: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None
    confidence_score: float = 0.0
    passed_risk: bool = False
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class SessionForkEngine:
    """Manages speculative session forks with clean copy-on-write isolation."""

    @staticmethod
    def fork(
        parent_ctx: TradingContext,
        branch_name: str,
        state_overrides: Optional[Dict[str, Any]] = None,
    ) -> TradingContext:
        """Create an isolated child context inheriting parent services with overridden state."""
        branch_ctx = parent_ctx.create_child(name=f"Branch_{branch_name}")
        if state_overrides:
            for k, v in state_overrides.items():
                branch_ctx.set_state(k, copy.deepcopy(v))
        return branch_ctx

    def evaluate_branches(
        self,
        base_ctx: TradingContext,
        snapshot: Dict[str, Any],
        hypotheses: List[Hypothesis],
        eval_fn: Callable[[TradingContext, Hypothesis, Dict[str, Any]], BranchResult],
    ) -> List[BranchResult]:
        """Evaluate a collection of hypotheses in isolated session branches.
        
        Args:
            base_ctx: The active trading context.
            snapshot: Current market, feature, and macro snapshot.
            hypotheses: List of speculative scenarios to test.
            eval_fn: Function to execute inside the forked branch.
        """
        results: List[BranchResult] = []

        for hyp in hypotheses:
            branch_ctx = self.fork(
                base_ctx,
                branch_name=hyp.name,
                state_overrides={"snapshot": snapshot, "hypothesis": hyp},
            )
            try:
                result = eval_fn(branch_ctx, hyp, snapshot)
                results.append(result)
            except Exception as exc:
                logger.error("Error evaluating branch '%s': %s", hyp.name, exc, exc_info=True)
                results.append(
                    BranchResult(
                        hypothesis_name=hyp.name,
                        error=f"{type(exc).__name__}: {str(exc)}",
                        passed_risk=False,
                    )
                )
            finally:
                # Reversible cleanup: dispose isolated branch resources
                branch_ctx.dispose()

        return results

    def arbitrate_best_branch(self, results: List[BranchResult]) -> Optional[BranchResult]:
        """Select the highest-confidence branch that successfully passed all risk checks."""
        valid_candidates = [r for r in results if r.passed_risk and r.proposal is not None and not r.error]
        if not valid_candidates:
            logger.info("No speculative hypothesis passed deterministic risk checks.")
            return None

        # Sort by confidence score descending
        valid_candidates.sort(key=lambda x: x.confidence_score, reverse=True)
        winner = valid_candidates[0]
        logger.info(
            "Selected winning branch '%s' with confidence %.2f",
            winner.hypothesis_name,
            winner.confidence_score,
        )
        return winner
