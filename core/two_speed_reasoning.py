"""Two-Speed Reasoning Architecture & Deliberation Tax Guard (Cheng et al., 2026 / Snell et al., 2024).

Paper: "Agents Are Not Algorithms: The Tradeoffs of Decision-Time Reasoning in AI Trading"
(Cheng, Granger, Shi, Strela - Rotman / RBC Capital Markets, May 2026).

Key Insights:
1. Decision-time reasoning improves thesis selection ('reasoning dividend'), but consumes
   latency as the market evolves.
2. The 'deliberation tax' appears in stuck inventory and attempts to accept expired tenders
   (stale quotes / adverse price drift).
3. The two-speed architecture resolves this by compiling execution, unwinds, stops, and risk
   shields into deterministic sub-second fast paths, while reserving the LLM for tender
   judgment with pre-computed calculation inputs and strict deliberation TTL guards.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("TwoSpeedReasoning")


class TwoSpeedMode(str, Enum):
    COMPILED_FAST_PATH = "compiled_fast_path"
    DELIBERATIVE_SLOW_PATH = "deliberative_slow_path"


@dataclass
class DeliberationConfig:
    """Tunables for decision-time reasoning budgeting."""
    max_deliberation_ms: float = 5000.0  # Max allowable thinking latency before tender expires
    max_drift_pct: float = 0.002         # 0.20% maximum price drift from proposal to execution
    max_drift_atr_mult: float = 0.5      # Maximum price drift in ATR units
    enforce_drift_guard: bool = True
    enforce_latency_guard: bool = True


class DeliberationTaxGuard:
    """Enforces expiration and freshness bounds on tenders originating from decision-time reasoning."""

    def __init__(self, config: Optional[DeliberationConfig] = None) -> None:
        self.config = config or DeliberationConfig()
        self.history: List[Dict[str, Any]] = []

    def evaluate_tender(
        self,
        entry_price: float,
        current_market_price: float,
        latency_ms: float,
        atr: float = 0.0,
    ) -> Tuple[bool, str]:
        """Evaluate whether a deliberated tender is fresh or has expired."""
        if self.config.enforce_latency_guard and latency_ms > self.config.max_deliberation_ms:
            return False, f"expired_tender_latency: {latency_ms:.1f}ms > {self.config.max_deliberation_ms:.1f}ms"

        if self.config.enforce_drift_guard and entry_price > 0 and current_market_price > 0:
            drift = abs(current_market_price - entry_price)
            drift_pct = drift / entry_price
            if drift_pct > self.config.max_drift_pct:
                return False, f"expired_tender_price_drift: {drift_pct*100:.3f}% > {self.config.max_drift_pct*100:.3f}%"

            if atr > 0 and (drift / atr) > self.config.max_drift_atr_mult:
                return False, f"expired_tender_atr_drift: {drift/atr:.2f} ATR > {self.config.max_drift_atr_mult:.2f} ATR"

        return True, "admissible"

    def record_decision(self, latency_ms: float, approved: bool, tax_breach: bool) -> None:
        """Log deliberation event for ledger tracking."""
        self.history.append({
            "timestamp": time.time(),
            "latency_ms": latency_ms,
            "approved": approved,
            "tax_breach": tax_breach,
        })

    def summary(self) -> Dict[str, Any]:
        """Produce summary metrics of reasoning dividend and deliberation tax."""
        total = len(self.history)
        if total == 0:
            return {
                "total_deliberations": 0,
                "tenders_accepted": 0,
                "deliberation_tax_breaches": 0,
                "mean_latency_ms": 0.0,
            }
        accepted = sum(1 for d in self.history if d["approved"])
        breaches = sum(1 for d in self.history if d["tax_breach"])
        mean_lat = sum(d["latency_ms"] for d in self.history) / total
        return {
            "total_deliberations": total,
            "tenders_accepted": accepted,
            "deliberation_tax_breaches": breaches,
            "mean_latency_ms": round(mean_lat, 2),
        }
