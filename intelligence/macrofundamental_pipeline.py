"""Autonomous Macrofundamental Analysis, Zero-Lag Nowcasting & Case Study Matching Pipeline.

Implements the complete zero-manual-intervention closed loop:
1. Multi-Source Ingestion: MT5, FRED, ForexFactory, Exness, COT, EIA, SeekingAlpha, SEC EDGAR.
2. Zero-Lag Physical Proxies: 22-Pillar nowcasting, synthetic commodity spreads, US Net Liquidity.
3. Case-Based Cognition: Matches unfolding events to 16 master historical case study archetypes.
4. Cross-Source Detective & Causal Graph: Maps entities, layers, and transmission shocks.
5. Autonomous Fact-Checking: 4-stage validation (triangulation, sanity bounds, provenance).
6. Dual-Engine Synthesis: 134 dynamics rules & narrative contextualization with blackout gating.
7. Systematic Opportunity Identification: Multi-asset scoring & OpportunityBus arbitration.
8. Immutable Multi-Tier Journaling: Full analytical reasoning recorded in SQLite TradingMemory.

Guarantees:
- execution_authority=False
- Continuous zero-manual-intervention daemon execution
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from data.global_macro_bridge import GlobalMacroBridge, GlobalMacroSnapshot
from data.sec_edgar_provider import SECEDGARProvider
from data.zero_lag_macro_provider import ZeroLagMacroProvider, ZeroLagMacroSnapshot, get_zero_lag_provider
from intelligence.case_study_matcher import CaseStudyMatcher, CaseMatchResult, get_case_study_matcher
from intelligence.fact_checker import AutonomousFactChecker, FactValidationResult
from intelligence.regime_classifier import MacroRegimeClassifier, MacroRegimeResult
from intelligence.meta_reflector import SystemMetaReflector, HealthAuditReport
from intelligence.causal_setup_map import resolve_causal_setup
from intelligence.behavioral_macro_catalog import get_behavioral_edges
from intelligence.opportunity_bus import publish as publish_opportunity
from intelligence.opportunity_fusion import score_opportunity
from core.trading_safety import TradingStore

log = logging.getLogger("AutonomousMacroPipeline")


class AutonomousMacroPipeline:
    """Fully Autonomous Macrofundamental Analysis, Nowcasting, and Opportunity Discovery Pipeline."""

    def __init__(self, store: Optional[TradingStore] = None) -> None:
        self.store = store or TradingStore()
        self.global_bridge = GlobalMacroBridge()
        self.sec_provider = SECEDGARProvider()
        self.zero_lag_provider = get_zero_lag_provider()
        self.case_matcher = get_case_study_matcher()
        self.fact_checker = AutonomousFactChecker()
        self.regime_classifier = MacroRegimeClassifier()
        self.meta_reflector = SystemMetaReflector()

    def run_pipeline_cycle(
        self,
        symbols: Sequence[str] = ("EURUSD", "XAUUSD", "USOIL"),
    ) -> Dict[str, Any]:
        """Execute a complete end-to-end autonomous macrofundamental analysis cycle."""
        now_utc = datetime.now(timezone.utc).isoformat()
        cycle_results: Dict[str, Any] = {
            "timestamp": now_utc,
            "status": "SUCCESS",
            "symbols_analyzed": list(symbols),
            "regime_state": {},
            "architectural_health": {},
            "zero_lag_nowcasts": {},
            "top_matched_case_studies": [],
            "verified_claims": [],
            "opportunities": [],
            "execution_authority": False,
        }

        # 1. Architectural Health Audit (Self-Diagnosis)
        health_report = self.meta_reflector.audit_system_health()
        cycle_results["architectural_health"] = health_report.to_dict()

        # 2. Global Macro Regime Classification (HMM + BMA)
        regime_result = self.regime_classifier.classify_current_regime()
        cycle_results["regime_state"] = regime_result.to_dict()

        # 3. Zero-Lag Physical Proxy & Synthetic Spreads Bundle
        zero_lag_snapshot: ZeroLagMacroSnapshot = self.zero_lag_provider.get_snapshot()
        cycle_results["zero_lag_nowcasts"] = zero_lag_snapshot.to_dict()

        # 4. Analogical Case Study Pattern Matching
        active_regime = regime_result.active_regime
        matched_cases = self.case_matcher.match_event(
            event_domain="MARITIME_CHOKEPOINT" if "USOIL" in symbols else "SOVEREIGN_LIQUIDITY",
            affected_entities=["WTI_OIL", "LIQUIDITY_STRESS", "INFLATION", "GROWTH"],
            top_k=3,
        )
        cycle_results["top_matched_case_studies"] = [
            {
                "case_id": c.matched_case_id,
                "label": c.label,
                "similarity": c.similarity_score,
                "tau_horizon": c.tau_horizon,
                "trajectory": c.projected_trajectory,
                "tripwires": list(c.falsification_tripwires),
            }
            for c in matched_cases
        ]

        # 5. Per-Symbol Ingestion, Fact-Checking, Causal Analysis & Opportunity Discovery
        discovered_opportunities: List[Dict[str, Any]] = []

        for symbol in symbols:
            # A. Ingest Unified Snapshot & Corporate Filings
            snapshot: GlobalMacroSnapshot = self.global_bridge.get_macro_snapshot(symbol)
            filings = self.sec_provider.fetch_filings(symbol=symbol, limit=2)

            # B. Fact-Check Ingested Catalyst Claims
            fact_res = self.fact_checker.verify_macro_claim(
                claim_text=f"Macro snapshot catalyst for {symbol}: {snapshot.macro_regime}",
                entity=symbol,
                reported_value=snapshot.cot_percentile_52w,
                source_id="global_macro_bridge",
                corroborating_sources=["forex_factory", "exness_insights"],
            )
            cycle_results["verified_claims"].append(fact_res.to_dict())

            # C. Dual-Engine Causal Mapping
            causal_setup = resolve_causal_setup(
                relation="SAFE_HAVEN_BID" if "XAU" in symbol else ("SUPPLY_SHOCK" if "OIL" in symbol else "DRIVES_CURRENCY_STRENGTH"),
                source_type="MONETARY_POLICY" if "USD" in symbol else "COMMODITY",
                target_type="COMMODITY" if "XAU" in symbol or "OIL" in symbol else "CURRENCY",
                source_name="CENTRAL_BANK" if "USD" in symbol else "WTI_OIL",
                target_name="GOLD" if "XAU" in symbol else ("WTI_OIL" if "OIL" in symbol else "USD"),
            )

            # D. Opportunity Scoring
            is_blackout = snapshot.high_impact_near
            analyst_bias = snapshot.analyst_sentiment
            cot_percentile = snapshot.cot_percentile_52w

            # Score proposal
            direction = "BUY" if analyst_bias == "BULLISH" or cot_percentile > 65.0 else ("SELL" if analyst_bias == "BEARISH" or cot_percentile < 35.0 else "HOLD")
            
            if direction != "HOLD" and not is_blackout:
                fusion_res = score_opportunity(
                    setup_id=causal_setup.get("setup_id", "macro_continuation_v1"),
                    side=direction,
                    symbol=symbol,
                    event_state={"blackout": is_blackout},
                    ta_eligible=True,
                    ta_confidence=0.75,
                )

                candidate = {
                    "candidate_id": f"opp_{symbol}_{now_utc[:10]}",
                    "symbol": symbol,
                    "direction": direction,
                    "score": fusion_res.get("score", 0.75),
                    "fusion_gate": fusion_res.get("gate", "PASS"),
                    "macro_regime": active_regime,
                    "causal_setup": causal_setup.get("setup_id", "macro_continuation_v1"),
                    "analyst_sentiment": analyst_bias,
                    "cot_percentile": cot_percentile,
                    "top_analogue_case": matched_cases[0].label if matched_cases else "None",
                    "created_at": now_utc,
                    "execution_authority": False,
                }
                discovered_opportunities.append(candidate)

        # 6. Opportunity Bus Publication
        if discovered_opportunities:
            publish_opportunity(self.store, discovered_opportunities, agent="MacroAnalyst")
            cycle_results["opportunities"] = discovered_opportunities

        log.info("Completed Autonomous Macro Pipeline cycle. Scored %d opportunities.", len(discovered_opportunities))
        return cycle_results
