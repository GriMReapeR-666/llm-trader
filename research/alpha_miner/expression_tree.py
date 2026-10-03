"""Alpha Expression Tree Data Structure.

Implements a tree-based representation for alpha factor expressions.
Each node represents an operator, field, or constant that can be recursively
evaluated on OHLCV data to produce a alpha signal time series.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIELDS = ["open", "high", "low", "close", "volume"]
CONSTANTS = [0.001, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]
WINDOWS = [5, 10, 15, 20, 30, 50, 100]

# Operator categories
UNARY_ARITHMETIC = ["abs", "sign", "log", "negate", "square", "sqrt"]
BINARY_ARITHMETIC = ["add", "subtract", "multiply", "divide"]
TS_UNARY = ["ts_mean", "ts_std", "ts_rank", "ts_min", "ts_max", "ts_delay", "ts_delta", "ts_returns"]
CS_UNARY = ["cs_rank", "cs_scale", "cs_normalize"]

# Operator arity mapping
OPERATOR_ARITY = {
    "abs": 1, "sign": 1, "log": 1, "negate": 1, "square": 1, "sqrt": 1,
    "add": 2, "subtract": 2, "multiply": 2, "divide": 2,
    "ts_mean": 1, "ts_std": 1, "ts_rank": 1, "ts_min": 1, "ts_max": 1,
    "ts_delay": 1, "ts_delta": 1, "ts_returns": 1,
    "cs_rank": 1, "cs_scale": 1, "cs_normalize": 1,
}


# ---------------------------------------------------------------------------
# Expression Tree Node
# ---------------------------------------------------------------------------

@dataclass
class AlphaExpression:
    """Node in an alpha expression tree.
    
    Attributes:
        operator: The operation to perform (e.g., "ts_mean", "add", "close")
        children: Child expressions (for binary operators)
        field: Data field name (for leaf nodes, e.g., "close", "volume")
        constant: Constant value (for leaf nodes)
        window: Rolling window size (for time-series operators)
        depth: Depth of this node in the tree
    """
    operator: str
    children: List['AlphaExpression'] = field(default_factory=list)
    field: Optional[str] = None
    constant: Optional[float] = None
    window: Optional[int] = None
    depth: int = 0
    
    def evaluate(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Recursively evaluate the expression tree on OHLCV data.
        
        Args:
            ohlcv: DataFrame with columns [open, high, low, close, volume]
            
        Returns:
            pd.Series with the alpha signal values
        """
        # Leaf node: return field or constant
        if self.field is not None:
            if self.field not in ohlcv.columns:
                raise ValueError(f"Field '{self.field}' not in OHLCV columns")
            return ohlcv[self.field].copy()
        
        if self.constant is not None:
            return pd.Series(self.constant, index=ohlcv.index, dtype=float)
        
        # Evaluate children first
        child_values = [child.evaluate(ohlcv) for child in self.children]
        
        # Apply operator
        return self._apply_operator(child_values, ohlcv)
    
    def _apply_operator(self, child_values: List[pd.Series], 
                        ohlcv: pd.DataFrame) -> pd.Series:
        """Apply the operator to child values."""
        op = self.operator
        window = self.window or 20
        
        # Unary arithmetic operators
        if op == "abs":
            return child_values[0].abs()
        elif op == "sign":
            return np.sign(child_values[0])
        elif op == "log":
            return np.log(child_values[0].clip(lower=1e-10))
        elif op == "negate":
            return -child_values[0]
        elif op == "square":
            return child_values[0] ** 2
        elif op == "sqrt":
            return child_values[0].abs().apply(math.sqrt)
        
        # Binary arithmetic operators
        elif op == "add":
            return child_values[0] + child_values[1]
        elif op == "subtract":
            return child_values[0] - child_values[1]
        elif op == "multiply":
            return child_values[0] * child_values[1]
        elif op == "divide":
            return child_values[0] / (child_values[1] + 1e-10)
        
        # Time-series operators
        elif op == "ts_mean":
            return child_values[0].rolling(window, min_periods=1).mean()
        elif op == "ts_std":
            return child_values[0].rolling(window, min_periods=2).std()
        elif op == "ts_rank":
            return child_values[0].rolling(window, min_periods=1).apply(
                lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
            )
        elif op == "ts_min":
            return child_values[0].rolling(window, min_periods=1).min()
        elif op == "ts_max":
            return child_values[0].rolling(window, min_periods=1).max()
        elif op == "ts_delay":
            return child_values[0].shift(window)
        elif op == "ts_delta":
            return child_values[0].diff(window)
        elif op == "ts_returns":
            return child_values[0].pct_change(window)
        
        # Cross-sectional operators (single asset: normalize/rank entire series)
        elif op == "cs_rank":
            return child_values[0].rank(pct=True)
        elif op == "cs_scale":
            denom = child_values[0].abs().sum()
            return child_values[0] / (denom + 1e-10) if denom > 0 else child_values[0] * 0.0
        elif op == "cs_normalize":
            mean = child_values[0].mean()
            std = child_values[0].std()
            return (child_values[0] - mean) / (std + 1e-10)
        
        else:
            raise ValueError(f"Unknown operator: {op}")
    
    def to_string(self) -> str:
        """Generate human-readable expression string."""
        if self.field is not None:
            return self.field
        
        if self.constant is not None:
            return str(self.constant)
        
        child_strs = [child.to_string() for child in self.children]
        
        if self.operator in ["ts_mean", "ts_std", "ts_rank", "ts_min", "ts_max", 
                             "ts_delay", "ts_delta", "ts_returns"]:
            return f"{self.operator}({child_strs[0]}, {self.window})"
        
        if self.operator in ["abs", "sign", "log", "negate", "square", "sqrt",
                             "cs_rank", "cs_scale", "cs_normalize"]:
            return f"{self.operator}({child_strs[0]})"
        
        if self.operator in ["add", "subtract", "multiply", "divide"]:
            return f"{self.operator}({child_strs[0]}, {child_strs[1]})"
        
        return f"{self.operator}({', '.join(child_strs)})"
    
    def copy(self) -> 'AlphaExpression':
        """Deep copy the expression tree."""
        return copy.deepcopy(self)
    
    def size(self) -> int:
        """Return total number of nodes in the tree."""
        return 1 + sum(child.size() for child in self.children)
    
    def max_depth(self) -> int:
        """Return maximum depth of the tree."""
        if not self.children:
            return 0
        return 1 + max(child.max_depth() for child in self.children)
    
    def get_random_node(self) -> Tuple['AlphaExpression', Optional['AlphaExpression'], int]:
        """Get a random node and its parent.
        
        Returns:
            Tuple of (node, parent, child_index)
        """
        nodes = []
        self._collect_nodes(None, -1, nodes)
        return random.choice(nodes)
    
    def _collect_nodes(self, parent: Optional['AlphaExpression'], 
                       child_idx: int, 
                       result: List[Tuple['AlphaExpression', Optional['AlphaExpression'], int]]):
        """Recursively collect all nodes with their parents."""
        result.append((self, parent, child_idx))
        for i, child in enumerate(self.children):
            child._collect_nodes(self, i, result)
    
    def get_subtree_at(self, node: 'AlphaExpression') -> 'AlphaExpression':
        """Get the subtree rooted at the given node (identity for self)."""
        if self is node:
            return self
        for child in self.children:
            result = child.get_subtree_at(node)
            if result is not None:
                return result
        return None
    
    def replace_subtree(self, old_node: 'AlphaExpression', 
                        new_subtree: 'AlphaExpression') -> bool:
        """Replace a subtree with a new one.
        
        Returns:
            True if replacement was successful
        """
        for i, child in enumerate(self.children):
            if child is old_node:
                self.children[i] = new_subtree
                return True
            if child.replace_subtree(old_node, new_subtree):
                return True
        return False
    
    def __repr__(self) -> str:
        return f"AlphaExpression({self.to_string()})"
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AlphaExpression):
            return False
        return (self.operator == other.operator and
                self.children == other.children and
                self.field == other.field and
                self.constant == other.constant and
                self.window == other.window)
    
    def __hash__(self) -> int:
        return hash((self.operator, tuple(self.children), self.field, 
                     self.constant, self.window))


# ---------------------------------------------------------------------------
# Expression Tree Builder Utilities
# ---------------------------------------------------------------------------

def create_leaf(field: Optional[str] = None, 
                constant: Optional[float] = None) -> AlphaExpression:
    """Create a leaf node (field or constant)."""
    if field is not None and constant is not None:
        raise ValueError("Cannot have both field and constant")
    return AlphaExpression(
        operator="leaf",
        field=field,
        constant=constant,
        depth=0
    )


def create_unary(op: str, child: AlphaExpression, 
                 window: Optional[int] = None) -> AlphaExpression:
    """Create a unary operator node."""
    return AlphaExpression(
        operator=op,
        children=[child],
        window=window,
        depth=child.depth + 1
    )


def create_binary(op: str, left: AlphaExpression, 
                  right: AlphaExpression) -> AlphaExpression:
    """Create a binary operator node."""
    return AlphaExpression(
        operator=op,
        children=[left, right],
        depth=max(left.depth, right.depth) + 1
    )


def create_random_expression(max_depth: int = 2, 
                             current_depth: int = 0) -> AlphaExpression:
    """Create a random expression tree.
    
    Args:
        max_depth: Maximum depth of the tree
        current_depth: Current depth (for recursion)
        
    Returns:
        Random alpha expression tree
    """
    # If at max depth or random chance, create leaf
    if current_depth >= max_depth or (current_depth > 0 and random.random() < 0.3):
        if random.random() < 0.8:
            return create_leaf(field=random.choice(FIELDS))
        else:
            return create_leaf(constant=random.choice(CONSTANTS))
    
    # Choose operator type
    op_type = random.choices(
        ["unary_arith", "binary_arith", "ts_unary", "cs_unary"],
        weights=[0.2, 0.3, 0.4, 0.1]
    )[0]
    
    if op_type == "unary_arith":
        op = random.choice(UNARY_ARITHMETIC)
        child = create_random_expression(max_depth, current_depth + 1)
        return create_unary(op, child)
    
    elif op_type == "binary_arith":
        op = random.choice(BINARY_ARITHMETIC)
        left = create_random_expression(max_depth, current_depth + 1)
        right = create_random_expression(max_depth, current_depth + 1)
        return create_binary(op, left, right)
    
    elif op_type == "ts_unary":
        op = random.choice(TS_UNARY)
        child = create_random_expression(max_depth, current_depth + 1)
        window = random.choice(WINDOWS)
        return create_unary(op, child, window=window)
    
    else:  # cs_unary
        op = random.choice(CS_UNARY)
        child = create_random_expression(max_depth, current_depth + 1)
        return create_unary(op, child)


# ---------------------------------------------------------------------------
# Expression Cache for Performance
# ---------------------------------------------------------------------------

class ExpressionCache:
    """Cache for expression evaluation results to avoid recomputation."""
    
    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self.cache: Dict[str, pd.Series] = {}
        self.access_order: List[str] = []
    
    def _key(self, expr: AlphaExpression, ohlcv_hash: str) -> str:
        """Generate cache key from expression and data hash."""
        return f"{expr.to_string()}_{ohlcv_hash}"
    
    def get(self, expr: AlphaExpression, ohlcv: pd.DataFrame) -> Optional[pd.Series]:
        """Get cached result if available."""
        ohlcv_hash = str(hash(ohlcv.values.tobytes()))
        key = self._key(expr, ohlcv_hash)
        
        if key in self.cache:
            self.access_order.remove(key)
            self.access_order.append(key)
            return self.cache[key]
        
        return None
    
    def put(self, expr: AlphaExpression, ohlcv: pd.DataFrame, 
            result: pd.Series) -> None:
        """Cache an evaluation result."""
        ohlcv_hash = str(hash(ohlcv.values.tobytes()))
        key = self._key(expr, ohlcv_hash)
        
        # Evict if at capacity
        if len(self.cache) >= self.max_size and key not in self.cache:
            oldest = self.access_order.pop(0)
            del self.cache[oldest]
        
        self.cache[key] = result
        if key in self.access_order:
            self.access_order.remove(key)
        self.access_order.append(key)
    
    def clear(self) -> None:
        """Clear the cache."""
        self.cache.clear()
        self.access_order.clear()


# Module-level cache instance
_expression_cache = ExpressionCache()


def evaluate_with_cache(expr: AlphaExpression, ohlcv: pd.DataFrame) -> pd.Series:
    """Evaluate expression with caching."""
    cached = _expression_cache.get(expr, ohlcv)
    if cached is not None:
        return cached
    
    result = expr.evaluate(ohlcv)
    _expression_cache.put(expr, ohlcv, result)
    return result
