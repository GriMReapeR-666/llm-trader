"""RAG-Enhanced Specification Encoder for Alpha Expressions.

Implements the M1 module from BrainAlpha that grounds every hypothesis
in verified field catalogues, eliminating field hallucination at zero
simulation cost.

References:
    Wangpratham, N. (2026). "BrainAlpha: An Autonomous Multi-Agent System
    for Quantitative Alpha Discovery." Working Paper.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verified Field Catalogue
# ---------------------------------------------------------------------------

# These are the verified fields from MT5 and our data providers
VERIFIED_FIELDS: Dict[str, Dict[str, Any]] = {
    # Price fields
    "open": {"type": "price", "source": "mt5", "description": "Opening price"},
    "high": {"type": "price", "source": "mt5", "description": "Highest price"},
    "low": {"type": "price", "source": "mt5", "description": "Lowest price"},
    "close": {"type": "price", "source": "mt5", "description": "Closing price"},
    "volume": {"type": "volume", "source": "mt5", "description": "Trading volume"},
    
    # Derived fields
    "returns": {"type": "derived", "source": "computed", "description": "Simple returns"},
    "log_returns": {"type": "derived", "source": "computed", "description": "Log returns"},
    "vwap": {"type": "derived", "source": "computed", "description": "Volume-weighted average price"},
    
    # Technical indicators
    "rsi_14": {"type": "indicator", "source": "ta", "description": "RSI 14-period"},
    "macd": {"type": "indicator", "source": "ta", "description": "MACD signal"},
    "atr_14": {"type": "indicator", "source": "ta", "description": "ATR 14-period"},
    "ema_50": {"type": "indicator", "source": "ta", "description": "EMA 50-period"},
    "sma_20": {"type": "indicator", "source": "ta", "description": "SMA 20-period"},
    
    # Microstructure
    "spread": {"type": "microstructure", "source": "dom", "description": "Bid-ask spread"},
    "imbalance": {"type": "microstructure", "source": "dom", "description": "Order imbalance"},
    
    # Fundamental (if available)
    "pe_ratio": {"type": "fundamental", "source": "provider", "description": "P/E ratio"},
    "market_cap": {"type": "fundamental", "source": "provider", "description": "Market capitalization"},
}

# Verified operators from our expression tree
VERIFIED_OPERATORS: Dict[str, Dict[str, Any]] = {
    # Time-series operators
    "ts_mean": {"arity": 1, "params": ["window"], "description": "Rolling mean"},
    "ts_std": {"arity": 1, "params": ["window"], "description": "Rolling standard deviation"},
    "ts_rank": {"arity": 1, "params": ["window"], "description": "Rolling rank"},
    "ts_min": {"arity": 1, "params": ["window"], "description": "Rolling minimum"},
    "ts_max": {"arity": 1, "params": ["window"], "description": "Rolling maximum"},
    "ts_delay": {"arity": 1, "params": ["period"], "description": "Lag operator"},
    "ts_delta": {"arity": 1, "params": ["period"], "description": "Momentum"},
    "ts_returns": {"arity": 1, "params": ["period"], "description": "Percentage change"},
    
    # Rolling normalizers. Historically these were FULL-SAMPLE (whole-series)
    # normalizers, which read future bars on single-symbol data. They now take a
    # window and read bars [t-window, t] only. `cs_rank` is now equivalent to
    # `ts_rank`; with one symbol there is no cross-section to rank over.
    # ponytail: descriptions are fed to an LLM as the operator contract, so they
    # must not claim semantics the evaluator does not implement.
    "cs_rank": {"arity": 1, "params": ["window"], "description": "Rolling percentile rank (causal)"},
    "cs_scale": {"arity": 1, "params": ["window"], "description": "Rolling absolute-scaled (causal)"},
    "cs_normalize": {"arity": 1, "params": ["window"], "description": "Rolling z-score (causal)"},
    
    # Arithmetic operators
    "add": {"arity": 2, "params": [], "description": "Addition"},
    "subtract": {"arity": 2, "params": [], "description": "Subtraction"},
    "multiply": {"arity": 2, "params": [], "description": "Multiplication"},
    "divide": {"arity": 2, "params": [], "description": "Division"},
    
    # Unary operators
    "abs": {"arity": 1, "params": [], "description": "Absolute value"},
    "sign": {"arity": 1, "params": [], "description": "Sign function"},
    "log": {"arity": 1, "params": [], "description": "Natural logarithm"},
    "negate": {"arity": 1, "params": [], "description": "Negation"},
}


# ---------------------------------------------------------------------------
# Specification Validation Rules
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of specification validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class SpecificationValidator:
    """Six-rule deterministic validator for alpha specifications."""
    
    def validate(self, spec: Dict[str, Any]) -> ValidationResult:
        """Validate a specification against all rules.
        
        Rules (from BrainAlpha M1):
        1. Non-empty primary fields
        2. Lookback range validity (1 ≤ min ≤ max ≤ 120)
        3. Recognized neutralization strategy
        4. Non-empty operator family
        5. Non-empty direction
        6. Hypothesis length ≥ 20 characters
        """
        errors = []
        warnings = []
        
        # Rule 1: Non-empty primary fields
        primary_fields = spec.get("primary_fields", [])
        if not primary_fields:
            errors.append("Rule 1: primary_fields cannot be empty")
        
        # Validate all fields exist
        for field_name in primary_fields:
            if field_name not in VERIFIED_FIELDS:
                errors.append(f"Rule 1: Unknown field '{field_name}'")
        
        # Rule 2: Lookback range validity
        lookback = spec.get("lookback_range", {})
        min_lookback = lookback.get("min", 1)
        max_lookback = lookback.get("max", 20)
        
        if not (1 <= min_lookback <= 120):
            errors.append(f"Rule 2: min_lookback must be 1-120, got {min_lookback}")
        if not (1 <= max_lookback <= 120):
            errors.append(f"Rule 2: max_lookback must be 1-120, got {max_lookback}")
        if min_lookback > max_lookback:
            errors.append(f"Rule 2: min_lookback ({min_lookback}) > max_lookback ({max_lookback})")
        
        # Rule 3: Recognized neutralization strategy
        valid_neutralizations = {"sector", "subindustry", "none", "market", "country"}
        neutralization = spec.get("neutralization", "none")
        if neutralization not in valid_neutralizations:
            warnings.append(f"Rule 3: Unknown neutralization '{neutralization}'")
        
        # Rule 4: Non-empty operator family
        operator_family = spec.get("operator_family", [])
        if not operator_family:
            errors.append("Rule 4: operator_family cannot be empty")
        
        for op in operator_family:
            if op not in VERIFIED_OPERATORS:
                warnings.append(f"Rule 4: Unknown operator '{op}'")
        
        # Rule 5: Non-empty direction
        direction = spec.get("direction", "")
        if direction not in ("long", "short", "both"):
            errors.append(f"Rule 5: direction must be long/short/both, got '{direction}'")
        
        # Rule 6: Hypothesis length ≥ 20 characters
        hypothesis = spec.get("statement", "")
        if len(hypothesis) < 20:
            errors.append(f"Rule 6: hypothesis must be ≥20 chars, got {len(hypothesis)}")
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# RAG-Enhanced Specification Encoder
# ---------------------------------------------------------------------------

@dataclass
class EncodedSpecification:
    """RAG-enhanced encoded specification."""
    original_thesis: str
    validated_fields: List[str]
    validated_operators: List[str]
    lookback_range: Dict[str, int]
    neutralization: str
    direction: str
    validation_result: ValidationResult
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_thesis": self.original_thesis,
            "validated_fields": self.validated_fields,
            "validated_operators": self.validated_operators,
            "lookback_range": self.lookback_range,
            "neutralization": self.neutralization,
            "direction": self.direction,
            "validation": self.validation_result.to_dict(),
        }


class RAGSpecificationEncoder:
    """RAG-enhanced specification encoder (M1 from BrainAlpha).
    
    Embeds the verified field catalogue and retrieves relevant fields
    for each incoming thesis, eliminating field hallucination.
    """
    
    def __init__(self):
        self.validator = SpecificationValidator()
        self.field_catalogue = VERIFIED_FIELDS
        self.operator_catalogue = VERIFIED_OPERATORS
    
    def encode(self, thesis: str, 
               requested_fields: Optional[List[str]] = None,
               lookback_range: Optional[Dict[str, int]] = None,
               neutralization: str = "none",
               operator_family: Optional[List[str]] = None,
               direction: str = "long") -> EncodedSpecification:
        """Encode a thesis into a validated specification.
        
        Args:
            thesis: Investment thesis statement
            requested_fields: Requested data fields
            lookback_range: Lookback window range
            neutralization: Neutralization strategy
            operator_family: Requested operator families
            direction: Trading direction (long/short/both)
            
        Returns:
            EncodedSpecification with validation
        """
        # Retrieve relevant fields (RAG simulation)
        if requested_fields is None:
            requested_fields = self._retrieve_relevant_fields(thesis)
        
        # Validate fields against catalogue
        validated_fields = [
            f for f in requested_fields if f in self.field_catalogue
        ]
        
        # Validate operators
        validated_operators = [
            op for op in (operator_family or []) 
            if op in self.operator_catalogue
        ]
        
        # Default lookback
        if lookback_range is None:
            lookback_range = {"min": 5, "max": 20}
        
        # Build specification
        spec = {
            "statement": thesis,
            "primary_fields": validated_fields,
            "lookback_range": lookback_range,
            "neutralization": neutralization,
            "operator_family": validated_operators,
            "direction": direction,
        }
        
        # Validate
        validation = self.validator.validate(spec)
        
        return EncodedSpecification(
            original_thesis=thesis,
            validated_fields=validated_fields,
            validated_operators=validated_operators,
            lookback_range=lookback_range,
            neutralization=neutralization,
            direction=direction,
            validation_result=validation,
        )
    
    def _retrieve_relevant_fields(self, thesis: str) -> List[str]:
        """Retrieve relevant fields for a thesis (RAG simulation).
        
        In production, this would use vector similarity search against
        the verified field catalogue.
        """
        # Simple keyword matching for now
        thesis_lower = thesis.lower()
        relevant = []
        
        # Map keywords to fields
        keyword_field_map = {
            "price": ["close", "open", "high", "low"],
            "volume": ["volume"],
            "momentum": ["returns", "ts_returns", "ts_delta"],
            "volatility": ["atr_14", "ts_std"],
            "trend": ["ema_50", "sma_20"],
            "reversal": ["rsi_14"],
            "spread": ["spread", "imbalance"],
        }
        
        for keyword, fields in keyword_field_map.items():
            if keyword in thesis_lower:
                relevant.extend(fields)
        
        # Default to basic fields if nothing matched
        if not relevant:
            relevant = ["close", "volume"]
        
        return list(set(relevant))  # Deduplicate
    
    def get_field_info(self, field_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a verified field."""
        return self.field_catalogue.get(field_name)
    
    def get_operator_info(self, operator_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a verified operator."""
        return self.operator_catalogue.get(operator_name)
    
    def list_fields(self) -> List[str]:
        """List all verified fields."""
        return list(self.field_catalogue.keys())
    
    def list_operators(self) -> List[str]:
        """List all verified operators."""
        return list(self.operator_catalogue.keys())
