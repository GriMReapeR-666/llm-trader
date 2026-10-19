"""Alpha Expression Generator.

Generates random alpha expressions of specified depth and complexity.
Implements the initial population generation for the genetic algorithm.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .expression_tree import (
    AlphaExpression,
    FIELDS,
    CONSTANTS,
    WINDOWS,
    UNARY_ARITHMETIC,
    BINARY_ARITHMETIC,
    TS_UNARY,
    CS_UNARY,
    create_leaf,
    create_unary,
    create_binary,
    create_random_expression,
)


class ExpressionGenerator:
    """Generate random alpha expressions for initial population."""
    
    def __init__(self, seed: Optional[int] = None):
        """Initialize generator with optional random seed.
        
        Args:
            seed: Random seed for reproducibility
        """
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)
        
        self.fields = FIELDS
        self.constants = CONSTANTS
        self.windows = WINDOWS
    
    def generate_leaf(self) -> AlphaExpression:
        """Generate a random leaf node (field or constant)."""
        if random.random() < 0.85:
            return create_leaf(field=random.choice(self.fields))
        else:
            return create_leaf(constant=random.choice(self.constants))
    
    def generate_depth_1(self) -> AlphaExpression:
        """Generate a depth-1 expression (operator applied to leaf)."""
        # Choose operator type
        op_type = random.choices(
            ["unary_arith", "binary_arith", "ts_unary", "cs_unary"],
            weights=[0.25, 0.25, 0.4, 0.1]
        )[0]
        
        if op_type == "unary_arith":
            op = random.choice(UNARY_ARITHMETIC)
            child = self.generate_leaf()
            return create_unary(op, child)
        
        elif op_type == "binary_arith":
            op = random.choice(BINARY_ARITHMETIC)
            left = self.generate_leaf()
            right = self.generate_leaf()
            return create_binary(op, left, right)
        
        elif op_type == "ts_unary":
            op = random.choice(TS_UNARY)
            child = self.generate_leaf()
            window = random.choice(self.windows)
            return create_unary(op, child, window=window)
        
        else:  # cs_unary
            op = random.choice(CS_UNARY)
            child = self.generate_leaf()
            window = random.choice(self.windows)
            return create_unary(op, child, window=window)
    
    def generate_expression(self, max_depth: int = 2) -> AlphaExpression:
        """Generate a random expression tree up to max_depth.
        
        Args:
            max_depth: Maximum depth of the expression tree
            
        Returns:
            Random alpha expression
        """
        if max_depth <= 1:
            return self.generate_depth_1()
        
        return create_random_expression(max_depth=max_depth)
    
    def generate_population(self, size: int = 3000, 
                           depth: int = 1) -> List[AlphaExpression]:
        """Generate initial population of alpha expressions.
        
        Args:
            size: Number of expressions to generate
            depth: Maximum depth of expressions
            
        Returns:
            List of alpha expressions
        """
        population = []
        
        # Generate depth-1 expressions (as per paper)
        for _ in range(size):
            expr = self.generate_depth_1()
            population.append(expr)
        
        return population
    
    def generate_diverse_population(self, size: int = 3000,
                                   depth: int = 1,
                                   diversity_factor: float = 0.3) -> List[AlphaExpression]:
        """Generate diverse population with guaranteed operator coverage.
        
        Args:
            size: Number of expressions to generate
            depth: Maximum depth of expressions
            diversity_factor: Fraction of population to force diverse operators
            
        Returns:
            Diverse list of alpha expressions
        """
        population = []
        
        # Ensure all operators are represented
        all_operators = UNARY_ARITHMETIC + BINARY_ARITHMETIC + TS_UNARY + CS_UNARY
        n_diverse = int(size * diversity_factor)
        per_operator = max(1, n_diverse // len(all_operators))
        
        # Add diverse expressions
        for op in all_operators:
            for _ in range(per_operator):
                if op in UNARY_ARITHMETIC:
                    child = self.generate_leaf()
                    expr = create_unary(op, child)
                elif op in BINARY_ARITHMETIC:
                    left = self.generate_leaf()
                    right = self.generate_leaf()
                    expr = create_binary(op, left, right)
                elif op in TS_UNARY:
                    child = self.generate_leaf()
                    window = random.choice(self.windows)
                    expr = create_unary(op, child, window=window)
                else:  # cs_unary
                    child = self.generate_leaf()
                    window = random.choice(self.windows)
                    expr = create_unary(op, child, window=window)
                
                population.append(expr)
        
        # Fill remaining with random expressions
        remaining = size - len(population)
        for _ in range(remaining):
            expr = self.generate_expression(max_depth=depth)
            population.append(expr)
        
        return population
    
    def generate_from_template(self, template: str, 
                              n_variations: int = 10) -> List[AlphaExpression]:
        """Generate variations of a template expression.
        
        Args:
            template: Template expression string (e.g., "ts_mean(close, 20)")
            n_variations: Number of variations to generate
            
        Returns:
            List of expression variations
        """
        # This is a placeholder for template-based generation
        # In practice, you would parse the template and mutate parameters
        variations = []
        for _ in range(n_variations):
            expr = self.generate_depth_1()
            variations.append(expr)
        return variations


# ---------------------------------------------------------------------------
# Expression Templates (Common Patterns)
# ---------------------------------------------------------------------------

TEMPLATES = {
    "momentum": [
        "ts_returns(close, {window})",
        "subtract(close, ts_delay(close, {window}))",
        "ts_delta(close, {window})",
    ],
    "mean_reversion": [
        "subtract(close, ts_mean(close, {window}))",
        "divide(subtract(close, ts_mean(close, {window})), ts_std(close, {window}))",
    ],
    "volatility": [
        "ts_std(ts_returns(close, 1), {window})",
        "divide(ts_max(high, {window}) - ts_min(low, {window}), close)",
    ],
    "volume_price": [
        "multiply(ts_returns(close, 1), volume)",
        "correlation(close, volume, {window})",  # Not implemented but template
    ],
    "trend": [
        "subtract(ts_mean(close, {fast_window}), ts_mean(close, {slow_window}))",
        "sign(ts_delta(ts_mean(close, {window}), 1))",
    ],
    "momentum": [
        "ts_returns(close, {window})",
        "divide(subtract(close, ts_min(low, {window})), subtract(ts_max(high, {window}), ts_min(low, {window})))",
        "ts_max(high, {window}) - close",
        "divide(ts_sum(ts_returns(close, 1), {window}), ts_std(close, {window}))",
    ],
    "reversal": [
        "negate(ts_returns(close, {window}))",
        "subtract(ts_mean(close, {window}), close)",
        "divide(subtract(ts_mean(close, {window}), close), ts_std(close, {window}))",
    ],
    "volume": [
        "divide(volume, ts_mean(volume, {window}))",
        "multiply(ts_returns(close, 1), divide(volume, ts_mean(volume, {window})))",
        "ts_corr(volume, ts_returns(close, 1), {window})",
    ],
    "breakout": [
        "divide(subtract(close, ts_min(low, {window})), subtract(ts_max(high, {window}), ts_min(low, {window})))",
        "sign(subtract(close, ts_max(high, {window})))",
        "subtract(close, ts_max(high, {window}))",
    ],
    "trend_strength": [
        "divide(ts_mean(ts_abs(ts_returns(close, 1)), {window}), ts_std(close, {window}))",
        "divide(subtract(ts_max(high, {window}), ts_min(low, {window})), ts_mean(volume, {window}))",
    ],
}


def generate_from_templates(n_per_template: int = 100) -> List[AlphaExpression]:
    """Generate expressions from common templates.
    
    Args:
        n_per_template: Number of expressions per template category
        
    Returns:
        List of template-based expressions
    """
    generator = ExpressionGenerator()
    expressions = []
    
    for category, templates in TEMPLATES.items():
        for _ in range(n_per_template):
            template = random.choice(templates)
            # For now, just generate random expressions
            # Template parsing would be implemented here
            expr = generator.generate_depth_1()
            expressions.append(expr)
    
    return expressions


# ---------------------------------------------------------------------------
# Population Quality Metrics
# ---------------------------------------------------------------------------

def compute_population_diversity(population: List[AlphaExpression]) -> float:
    """Compute diversity score of a population.
    
    Diversity is measured as the fraction of unique operators used.
    
    Args:
        population: List of alpha expressions
        
    Returns:
        Diversity score between 0 and 1
    """
    if not population:
        return 0.0
    
    all_operators = set()
    for expr in population:
        _collect_operators(expr, all_operators)
    
    total_possible = len(UNARY_ARITHMETIC) + len(BINARY_ARITHMETIC) + len(TS_UNARY) + len(CS_UNARY)
    return len(all_operators) / total_possible if total_possible > 0 else 0.0


def _collect_operators(expr: AlphaExpression, operators: set):
    """Recursively collect all operators in an expression tree."""
    if expr.operator != "leaf":
        operators.add(expr.operator)
    for child in expr.children:
        _collect_operators(child, operators)


def compute_population_depth_distribution(population: List[AlphaExpression]) -> dict:
    """Compute depth distribution of a population.
    
    Args:
        population: List of alpha expressions
        
    Returns:
        Dictionary mapping depth to count
    """
    depth_counts = {}
    for expr in population:
        depth = expr.max_depth()
        depth_counts[depth] = depth_counts.get(depth, 0) + 1
    return depth_counts


def compute_operator_distribution(population: List[AlphaExpression]) -> dict:
    """Compute operator usage distribution.
    
    Args:
        population: List of alpha expressions
        
    Returns:
        Dictionary mapping operator to count
    """
    op_counts = {}
    for expr in population:
        _count_operators(expr, op_counts)
    return op_counts


def _count_operators(expr: AlphaExpression, counts: dict):
    """Recursively count operators in an expression tree."""
    if expr.operator != "leaf":
        counts[expr.operator] = counts.get(expr.operator, 0) + 1
    for child in expr.children:
        _count_operators(child, counts)
