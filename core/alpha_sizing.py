"""Alpha-Driven Position Sizing Model.

Maps the alpha ensemble composite score to a position size multiplier
that replaces the LLM's subjective sizing with a quantitative signal.

The composite score ranges from ~-2 to +2:
  +2 = strong bullish (all alphas agree upward)
   0 = neutral (no alpha edge)
  -2 = strong bearish (all alphas agree downward)

Sizing logic (direction-aware):
  When composite aligns with trade direction → increase size
  When composite contradicts trade direction → decrease size
  When composite is neutral → standard sizing

This is a PURELY QUANTITATIVE decision — no LLM involved.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class AlphaSizingResult:
    """Result of alpha-driven position sizing."""
    multiplier: float          # Final multiplier to apply to base risk
    composite: float           # Raw composite score
    alignment: str             # "aligned", "contradicts", "neutral"
    confidence_band: str       # "high", "medium", "low", "none"
    reason: str                # Human-readable explanation
    raw_multiplier: float      # Before clamping
    ic_quality: str            # "strong", "moderate", "weak", "unknown"


# Alignment thresholds
STRONG_SIGNAL = 1.0    # |composite| > 1.0 = strong alpha edge
MODERATE_SIGNAL = 0.5  # |composite| > 0.5 = moderate edge
WEAK_SIGNAL = 0.2      # |composite| > 0.2 = weak edge

# Sizing multipliers
ALIGN_STRONG = 1.35     # Strong alignment → 35% size boost
ALIGN_MODERATE = 1.15   # Moderate alignment → 15% boost
ALIGN_WEAK = 1.05       # Weak alignment → 5% boost
NEUTRAL = 1.0           # No edge → standard size
CONTRA_WEAK = 0.80      # Weak contradiction → 20% reduction
CONTRA_MODERATE = 0.55  # Moderate contradiction → 45% reduction
CONTRA_STRONG = 0.25    # Strong contradiction → 75% reduction

# IC quality thresholds (from ensemble overall_ic)
IC_STRONG = 0.05        # |IC| > 0.05 = strong alpha quality
IC_MODERATE = 0.02      # |IC| > 0.02 = moderate quality
IC_WEAK = 0.01          # |IC| > 0.01 = weak quality

# IC quality discount (reduces multiplier when alpha quality is low)
IC_STRONG_DISCOUNT = 1.0     # No discount
IC_MODERATE_DISCOUNT = 0.90  # 10% discount
IC_WEAK_DISCOUNT = 0.75      # 25% discount
IC_UNKNOWN_DISCOUNT = 0.60   # 40% discount (no IC data)

# Hard limits
MIN_MULTIPLIER = 0.1   # Never go below 10% of base risk
MAX_MULTIPLIER = 2.0   # Never exceed 200% of base risk


def compute_alpha_sizing(
    composite: float,
    trade_direction: str,
    *,
    overall_ic: float = 0.0,
    n_alphas_used: int = 0,
    composite_confidence: str = "unknown",
) -> AlphaSizingResult:
    """Compute position size multiplier from alpha ensemble composite.

    Args:
        composite: Alpha ensemble composite score (-2 to +2)
        trade_direction: "BUY" or "SELL"
        overall_ic: Mean IC across all alphas (quality indicator)
        n_alphas_used: Number of alphas contributing to composite
        composite_confidence: "high", "medium", "low", or "unknown"

    Returns:
        AlphaSizingResult with multiplier and diagnostics
    """
    try:
        composite = float(composite)
    except (TypeError, ValueError):
        composite = 0.0
    if not math.isfinite(composite):
        composite = 0.0
    composite = max(-2.0, min(2.0, composite))
    direction = trade_direction.upper()

    # --- Step 1: Determine alignment ---
    if direction == "BUY":
        alignment_sign = 1.0  # positive composite = aligned
    elif direction == "SELL":
        alignment_sign = -1.0  # negative composite = aligned
    else:
        alignment_sign = 0.0

    # Signed alignment: positive = aligned, negative = contradicts
    signed_alignment = composite * alignment_sign

    if signed_alignment >= STRONG_SIGNAL:
        alignment = "aligned"
        raw_mult = ALIGN_STRONG
        band = "high"
    elif signed_alignment >= MODERATE_SIGNAL:
        alignment = "aligned"
        raw_mult = ALIGN_MODERATE
        band = "medium"
    elif signed_alignment >= WEAK_SIGNAL:
        alignment = "aligned"
        raw_mult = ALIGN_WEAK
        band = "low"
    elif signed_alignment <= -STRONG_SIGNAL:
        alignment = "contradicts"
        raw_mult = CONTRA_STRONG
        band = "high"
    elif signed_alignment <= -MODERATE_SIGNAL:
        alignment = "contradicts"
        raw_mult = CONTRA_MODERATE
        band = "medium"
    elif signed_alignment <= -WEAK_SIGNAL:
        alignment = "contradicts"
        raw_mult = CONTRA_WEAK
        band = "low"
    else:
        alignment = "neutral"
        raw_mult = NEUTRAL
        band = "none"

    # --- Step 2: IC quality discount ---
    abs_ic = abs(overall_ic)
    if abs_ic >= IC_STRONG:
        ic_quality = "strong"
        ic_discount = IC_STRONG_DISCOUNT
    elif abs_ic >= IC_MODERATE:
        ic_quality = "moderate"
        ic_discount = IC_MODERATE_DISCOUNT
    elif abs_ic >= IC_WEAK:
        ic_quality = "weak"
        ic_discount = IC_WEAK_DISCOUNT
    else:
        ic_quality = "unknown"
        ic_discount = IC_UNKNOWN_DISCOUNT

    # --- Step 3: Confidence discount ---
    conf_discount = {
        "high": 1.0,
        "medium": 0.95,
        "low": 0.85,
        "unknown": 0.80,
    }.get(composite_confidence, 0.80)

    # --- Step 4: Combine ---
    multiplier = raw_mult * ic_discount * conf_discount
    multiplier = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))

    # --- Build reason ---
    if alignment == "aligned":
        reason = (f"Alpha composite {composite:+.2f} aligns with {direction} "
                  f"→ {raw_mult:.2f}x base risk (IC={overall_ic:.4f}, "
                  f"quality={ic_quality}, confidence={composite_confidence})")
    elif alignment == "contradicts":
        reason = (f"Alpha composite {composite:+.2f} contradicts {direction} "
                  f"→ {raw_mult:.2f}x base risk (reduced)")
    else:
        reason = (f"Alpha composite {composite:+.2f} is neutral for {direction} "
                  f"→ standard sizing (1.0x)")

    return AlphaSizingResult(
        multiplier=round(multiplier, 4),
        composite=composite,
        alignment=alignment,
        confidence_band=band,
        reason=reason,
        raw_multiplier=raw_mult,
        ic_quality=ic_quality,
    )


def alpha_sizing_from_ensemble(ensemble: Dict, trade_direction: str) -> AlphaSizingResult:
    """Convenience: extract composite/IC from an ensemble result dict and size.

    The ensemble dict is the output of market_data_provider's alpha_ensemble block:
    {
        "composite_value": float,
        "composite_signal": "BUY"|"SELL"|"HOLD",
        "composite_confidence": "high"|"medium"|"low",
        "overall_ic": float,
        "n_alphas_used": int,
    }
    """
    return compute_alpha_sizing(
        composite=float(ensemble.get("composite_value", 0.0)),
        trade_direction=trade_direction,
        overall_ic=float(ensemble.get("overall_ic", 0.0)),
        n_alphas_used=int(ensemble.get("n_alphas_used", 0)),
        composite_confidence=str(ensemble.get("composite_confidence", "unknown")),
    )
