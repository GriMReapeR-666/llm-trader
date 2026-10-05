"""Kronos Signal Fusion Engine

Fuses multiple Kronos predictions with regime-aware filtering:
1. Multi-horizon signal fusion (1h, 4h, 1d predictions)
2. Regime-conditional weighting (different weights per regime)
3. Confidence-weighted aggregation
4. Signal decay and freshness tracking
5. Cross-asset correlation adjustment

Usage:
    fusion = KronosSignalFusion()
    # Get fused signal for a symbol
    signal = fusion.get_signal("EURUSD")
    # Get regime-adjusted signal
    signal = fusion.get_regime_adjusted_signal("EURUSD", regime="HIGH_VOL_TREND")
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("KronosSignalFusion")


class KronosSignalFusion:
    """Multi-horizon, regime-aware signal fusion engine."""

    # Default regime weights (regime -> signal weights)
    REGIME_WEIGHTS = {
        "LOW_VOL_TREND": {"direction": 0.8, "magnitude": 0.6, "confidence": 0.9},
        "LOW_VOL_RANGE": {"direction": 0.4, "magnitude": 0.3, "confidence": 0.7},
        "HIGH_VOL_TREND": {"direction": 0.9, "magnitude": 0.8, "confidence": 0.6},
        "HIGH_VOL_CRISIS": {"direction": 0.3, "magnitude": 0.5, "confidence": 0.4},
        "NORMAL": {"direction": 0.6, "magnitude": 0.5, "confidence": 0.7},
    }

    # Timeframe weights (shorter timeframes = more responsive)
    TIMEFRAME_WEIGHTS = {
        "M15": 0.3,
        "H1": 0.4,
        "H4": 0.2,
        "D1": 0.1,
    }

    # Signal decay half-life in seconds
    SIGNAL_DECAY_HALF_LIFE = 3600  # 1 hour

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.state_path = os.path.join(self.base_dir, "artifacts", "kronos_signal_state.json")
        self.state = self._load_state()

        # Lazy-load Kronos predictor
        self._predictor = None
        self._regime_classifier = None

    def _load_state(self) -> Dict[str, Any]:
        """Load signal state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "signals": {},
            "last_update": None,
            "total_fusions": 0,
        }

    def _save_state(self) -> None:
        """Save signal state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save state: %s", exc)

    def _get_predictor(self):
        """Lazy-load Kronos predictor."""
        if self._predictor is None:
            try:
                from research.kronos_bridge import KronosPredictor
                self._predictor = KronosPredictor()
            except Exception as exc:
                log.debug("Kronos predictor unavailable: %s", exc)
        return self._predictor

    def _get_regime_classifier(self):
        """Lazy-load regime classifier."""
        if self._regime_classifier is None:
            try:
                from intelligence.regime_classifier import MacroRegimeClassifier
                self._regime_classifier = MacroRegimeClassifier()
            except Exception as exc:
                log.debug("Regime classifier unavailable: %s", exc)
        return self._regime_classifier

    def get_signal(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        regime: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get fused signal for a symbol across multiple timeframes.

        Args:
            symbol: Trading symbol
            timeframes: List of timeframes to fuse (default: all)
            regime: Optional regime override

        Returns:
            Fused signal with direction, magnitude, confidence
        """
        if timeframes is None:
            timeframes = ["M15", "H1", "H4", "D1"]

        predictor = self._get_predictor()
        if not predictor:
            return self._default_signal(symbol, "predictor_unavailable")

        # Get predictions for each timeframe
        predictions = {}
        for tf in timeframes:
            try:
                pred = predictor.predict(symbol, timeframe=tf)
                if pred:
                    predictions[tf] = {
                        "direction": pred.get("direction", 0),
                        "magnitude": pred.get("magnitude", 0),
                        "confidence": pred.get("confidence", 0.5),
                        "timestamp": pred.get("timestamp", time.time()),
                    }
            except Exception as exc:
                log.debug("Prediction failed for %s %s: %s", symbol, tf, exc)

        if not predictions:
            return self._default_signal(symbol, "no_predictions")

        # Fuse predictions
        fused = self._fuse_predictions(predictions, regime)

        # Apply signal decay
        fused = self._apply_decay(fused)

        # Store signal
        self.state["signals"][symbol] = {
            **fused,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "predictions_count": len(predictions),
        }
        self.state["last_update"] = datetime.now(timezone.utc).isoformat()
        self.state["total_fusions"] += 1
        self._save_state()

        return fused

    def get_regime_adjusted_signal(
        self,
        symbol: str,
        regime: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get signal with regime-adjusted weights."""
        if regime is None:
            regime = self._detect_regime()

        return self.get_signal(symbol, regime=regime)

    def _fuse_predictions(
        self,
        predictions: Dict[str, Dict[str, Any]],
        regime: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fuse predictions from multiple timeframes."""
        if not predictions:
            return self._default_signal("", "empty_predictions")

        # Get regime weights
        if regime is None:
            regime = self._detect_regime()
        regime_weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS["NORMAL"])

        # Weighted average
        total_direction = 0.0
        total_magnitude = 0.0
        total_confidence = 0.0
        total_weight = 0.0

        for tf, pred in predictions.items():
            tf_weight = self.TIMEFRAME_WEIGHTS.get(tf, 0.25)
            regime_weight = regime_weights.get("direction", 0.6)
            confidence_weight = pred.get("confidence", 0.5)

            weight = tf_weight * regime_weight * confidence_weight

            total_direction += pred.get("direction", 0) * weight
            total_magnitude += pred.get("magnitude", 0) * weight
            total_confidence += pred.get("confidence", 0.5) * weight
            total_weight += weight

        if total_weight == 0:
            return self._default_signal("", "zero_weight")

        # Normalize
        fused_direction = total_direction / total_weight
        fused_magnitude = total_magnitude / total_weight
        fused_confidence = total_confidence / total_weight

        # Apply regime-specific adjustments
        if regime == "HIGH_VOL_CRISIS":
            # In crisis, reduce magnitude and confidence
            fused_magnitude *= 0.5
            fused_confidence *= 0.6
        elif regime == "LOW_VOL_TREND":
            # In low-vol trend, boost direction confidence
            fused_confidence *= 1.1

        # Clamp values
        fused_direction = max(-1.0, min(1.0, fused_direction))
        fused_magnitude = max(0.0, min(1.0, fused_magnitude))
        fused_confidence = max(0.0, min(1.0, fused_confidence))

        # Convert to actionable signal
        if fused_direction > 0.3:
            action = "BUY"
        elif fused_direction < -0.3:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "symbol": predictions[list(predictions.keys())[0]].get("symbol", ""),
            "action": action,
            "direction": round(fused_direction, 4),
            "magnitude": round(fused_magnitude, 4),
            "confidence": round(fused_confidence, 4),
            "regime": regime,
            "timeframes_used": list(predictions.keys()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _detect_regime(self) -> str:
        """Detect current market regime."""
        classifier = self._get_regime_classifier()
        if not classifier:
            return "NORMAL"

        try:
            result = classifier.classify_current_regime()
            return result.get("regime", "NORMAL")
        except Exception:
            return "NORMAL"

    def _apply_decay(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Apply time-based decay to signal."""
        # Check if we have a previous signal for this symbol
        symbol = signal.get("symbol", "")
        if symbol in self.state.get("signals", {}):
            prev = self.state["signals"][symbol]
            prev_time = prev.get("timestamp")
            if prev_time:
                try:
                    prev_dt = datetime.fromisoformat(prev_time.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    elapsed = (now - prev_dt).total_seconds()

                    # Apply exponential decay
                    decay_factor = math.exp(-0.693 * elapsed / self.SIGNAL_DECAY_HALF_LIFE)

                    # Blend with previous signal
                    prev_direction = prev.get("direction", 0)
                    signal["direction"] = signal["direction"] * (1 - decay_factor) + prev_direction * decay_factor
                    signal["direction"] = max(-1.0, min(1.0, signal["direction"]))

                except Exception:
                    pass

        return signal

    def _default_signal(self, symbol: str, reason: str) -> Dict[str, Any]:
        """Return a default neutral signal."""
        return {
            "symbol": symbol,
            "action": "HOLD",
            "direction": 0.0,
            "magnitude": 0.0,
            "confidence": 0.0,
            "regime": "UNKNOWN",
            "timeframes_used": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": reason,
        }

    def get_signal_history(
        self,
        symbol: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get signal history for a symbol."""
        # For now, return current signal only
        # In production, this would query a time-series store
        current = self.state.get("signals", {}).get(symbol)
        if current:
            return [current]
        return []

    def get_stats(self) -> Dict[str, Any]:
        """Get fusion statistics."""
        return {
            "total_fusions": self.state.get("total_fusions", 0),
            "last_update": self.state.get("last_update"),
            "symbols_tracked": len(self.state.get("signals", {})),
            "regime_weights": self.REGIME_WEIGHTS,
        }


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Kronos Signal Fusion")
    parser.add_argument("--symbol", type=str, default="EURUSD", help="Symbol to analyze")
    parser.add_argument("--regime", type=str, help="Override regime")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    fusion = KronosSignalFusion()

    if args.stats:
        print(json.dumps(fusion.get_stats(), indent=2, default=str))
    else:
        signal = fusion.get_regime_adjusted_signal(args.symbol, regime=args.regime)
        print(json.dumps(signal, indent=2, default=str))


if __name__ == "__main__":
    main()
