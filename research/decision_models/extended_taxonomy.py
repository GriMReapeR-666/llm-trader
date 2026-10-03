"""Extended Macro-Fundamental & Cognitive Decision Taxonomy Registry.

Defines the complete unified taxonomy covering ~30 decisions across 7 functional layers:
- Layer 0: Ingestion & Source Verification (D0.1, D0.2, D0.3)
- Layer 1: Fast Event & Shock (D1, D1b, D1c, D2)
- Layer 1b: Geopolitical & Breaking (D5a, D5b, D5c, D9)
- Layer 2: Regime & Sovereign Liquidity (D3, D4, D4b, D4c)
- Layer 3: Multi-Asset Transmission (D5, D6, D6b, D6c)
- Layer 4: Positioning & Flow (D10, D10b, D10c)
- Layer 5: Technical & Quant Signals (D11, D11b, D11c, D11d)
- Layer 6: Opportunity Arbitration & Execution (D7, D8, D12, D13)
- Meta-Decisions: (M1, M2, M3, M4, M5, M6)
- Causal Attribution: (Q1, Q2, Q3, Q4, Q5, Q6, Q7)

Invariant:
Every Choice head includes INSUFFICIENT_DATA for safe abstention.
Every DecisionRecord and DecisionBundle strictly enforces execution_authority = False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional, Set, Tuple


# ===========================================================================
# 1. LAYER 0: INGESTION & SOURCE VERIFICATION
# ===========================================================================

class D0_1_SourceCredibility(str, Enum):
    """Source verification and spoofing protection."""
    TRUSTED_OFFICIAL = "TRUSTED_OFFICIAL"      # Central bank feed, official gov stat bureau
    VERIFIED_OUTLET = "VERIFIED_OUTLET"        # Bloomberg, Reuters, WSJ confirmed wire
    UNVERIFIED_SOCIAL = "UNVERIFIED_SOCIAL"    # Raw social media / unverified feed
    SPOOF_REJECTED = "SPOOF_REJECTED"          # Prompt injection, forged header, malicious RSS
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D0_2_DuplicateDetection(str, Enum):
    """Headline clustering and cascade deduplication."""
    FIRST_REPORT = "FIRST_REPORT"              # First receipt of novel event
    CONFIRMED_UPDATE = "CONFIRMED_UPDATE"      # Corroborating update with new data
    STALE_REPEAT = "STALE_REPEAT"              # Repeated identical headline (do not re-multiply)
    CONTRADICTION = "CONTRADICTION"            # Direct factual conflict between sources
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D0_3_FactVsOpinion(str, Enum):
    """Distinguishes hard data from commentary."""
    VERIFIED_FACT = "VERIFIED_FACT"            # Hard print (CPI 3.2%, Rate cut 50 bps)
    OFFICIAL_STATEMENT = "OFFICIAL_STATEMENT"  # Formal governor quote / press release
    ANALYST_OPINION = "ANALYST_OPINION"        # Sell-side commentary / speculation
    RUMOR_UNCONFIRMED = "RUMOR_UNCONFIRMED"    # Unsubstantiated wire leak
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 2. LAYER 1 & 1b: FAST NEWS, EVENT SHOCK & GEOPOLITICAL
# ===========================================================================

class D1_EconomicSurprise(str, Enum):
    """Z-score based macroeconomic surprise shock."""
    EXTREME_HAWKISH = "EXTREME_HAWKISH"        # Z >= +2.0
    MODERATE_HAWKISH = "MODERATE_HAWKISH"      # +0.8 <= Z < +2.0
    IN_LINE = "IN_LINE"                        # -0.8 < Z < +0.8
    MODERATE_DOVISH = "MODERATE_DOVISH"        # -2.0 < Z <= -0.8
    EXTREME_DOVISH = "EXTREME_DOVISH"          # Z <= -2.0
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D1b_PricedInAsymmetry(str, Enum):
    """Z-score vs OIS / futures market-implied pricing before release."""
    SURPRISE_UNDERPRICED = "SURPRISE_UNDERPRICED"  # Market under-positioned; strong follow-through
    FAIRLY_PRICED = "FAIRLY_PRICED"                # Print matches OIS-implied expectation
    SURPRISE_OVERPRICED = "SURPRISE_OVERPRICED"    # 'Sell the news' fade risk
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D1c_RevisionRisk(str, Enum):
    """Materiality of previous period historical revision."""
    REVISION_NEGLIGIBLE = "REVISION_NEGLIGIBLE"
    REVISION_MATERIAL_HAWKISH = "REVISION_MATERIAL_HAWKISH"
    REVISION_MATERIAL_DOVISH = "REVISION_MATERIAL_DOVISH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D2_BlackoutGate(str, Enum):
    """Real-time danger window execution state."""
    NORMAL_TRADING = "NORMAL_TRADING"
    PRE_EVENT_DELEVERAGE = "PRE_EVENT_DELEVERAGE"  # T-60m to T-5m: scale risk down by 50%
    BLACKOUT_FREEZE = "BLACKOUT_FREEZE"            # T-5m to T+2m: cancel resting orders, freeze entries
    POST_SHOCK_PULLBACK = "POST_SHOCK_PULLBACK"    # T+2m to T+30m: trade confirmed directional post-shock trend
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D9_EmergencyKillSwitch(str, Enum):
    """Flash crisis event kill-switch."""
    NORMAL_OPERATION = "NORMAL_OPERATION"
    EMERGENCY_HALT_ACTIVE = "EMERGENCY_HALT_ACTIVE"  # De-peg, exchange halt, -5σ bar, spread blowup
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D5a_EscalationLadder(str, Enum):
    """Geopolitical / war conflict escalation severity."""
    RHETORIC_ONLY = "RHETORIC_ONLY"                # Verbal threats, zero physical action
    PROXY_CLASH = "PROXY_CLASH"                    # Regional skirmish without trade disruption
    DIRECT_STRIKE = "DIRECT_STRIKE"                # Direct military engagement
    CHOKEPOINT_DISRUPTION = "CHOKEPOINT_DISRUPTION"# Maritime/pipeline bottleneck threatened
    FULL_BLOCKADE = "FULL_BLOCKADE"                # Complete physical trade halt
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D5b_PhysicalSupplyVerification(str, Enum):
    """AIS tanker / pipeline verification vs headline rumors."""
    CONFIRMED_PHYSICAL_DISRUPTION = "CONFIRMED_PHYSICAL_DISRUPTION" # Validated reduction in physical flow
    UNCONFIRMED_HEADLINE_ONLY = "UNCONFIRMED_HEADLINE_ONLY"         # Headlines only, physical flows unchanged
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D5c_SanctionsImpact(str, Enum):
    """Sanctions transmission routing."""
    SUPPLY_REROUTE = "SUPPLY_REROUTE"              # Cargoes change destination, net volume unchanged
    SUPPLY_LOSS = "SUPPLY_LOSS"                    # Net physical supply destroyed
    DEMAND_DESTROYED = "DEMAND_DESTROYED"          # High prices destroy downstream demand
    SYMBOLIC_ONLY = "SYMBOLIC_ONLY"                # Zero material economic effect
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 3. LAYER 2: REGIME & SOVEREIGN LIQUIDITY
# ===========================================================================

class D3_MacroRegime(str, Enum):
    """Global multi-asset macro regime."""
    RISK_ON_EXPANSION = "RISK_ON_EXPANSION"        # VIX < 16, tight credit spreads, earnings growth
    NORMAL_RISK_OFF = "NORMAL_RISK_OFF"            # VIX 18-25, defensive USD bid, moderate equity dip
    STAGFLATION_SHOCK = "STAGFLATION_SHOCK"        # Surging crude oil, slowing growth, sticky inflation
    CRISIS_PANIC = "CRISIS_PANIC"                  # VIX >= 30, systemic liquidity stress, equity plunge
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D4_LiquidityStance(str, Enum):
    """US Net Liquidity (WALCL - TGA - RRP) and bank reserve stance."""
    EXPANDING_LIQUIDITY = "EXPANDING_LIQUIDITY"    # Net liq delta > +$40B/mo
    NEUTRAL_LIQUIDITY = "NEUTRAL_LIQUIDITY"        # Range-bound (+-$40B)
    CONTRACTIONARY_DRAIN = "CONTRACTIONARY_DRAIN"  # QT and TGA rebuilding draining reserves
    LIQUIDITY_CRUNCH = "LIQUIDITY_CRUNCH"          # Severe drain: SOFR spike >= 5.5%, reserves <= -$100B
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D4b_CentralBankDivergence(str, Enum):
    """Cross-central bank monetary policy path divergence."""
    FED_LEADS_HAWKISH = "FED_LEADS_HAWKISH"        # Fed hiking/holding higher than ECB/BOJ
    ECB_LEADS_HAWKISH = "ECB_LEADS_HAWKISH"        # ECB hawkish repricing exceeding Fed
    BOJ_INTERRUPT = "BOJ_INTERRUPT"                # BOJ surprise hike / YCC tweak disrupting carry
    SYNCHRONIZED_EASING = "SYNCHRONIZED_EASING"    # Global coordinated rate cuts
    SYNCHRONIZED_TIGHTENING = "SYNCHRONIZED_TIGHTENING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D4c_FiscalImpulse(str, Enum):
    """Treasury issuance, TGA refill, and fiscal impulse."""
    FISCAL_STIMULUS = "FISCAL_STIMULUS"
    NEUTRAL_BUDGET = "NEUTRAL_BUDGET"
    AUSTERITY_DRAIN = "AUSTERITY_DRAIN"
    DEBT_CEILING_CRISIS = "DEBT_CEILING_CRISIS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 4. LAYER 3: MULTI-ASSET TRANSMISSION
# ===========================================================================

class D5_CommodityShock(str, Enum):
    """Physical commodity supply and demand shock routing."""
    SUPPLY_DEFICIT_BULLISH = "SUPPLY_DEFICIT_BULLISH" # Chokepoint/quota cut causing physical deficit
    SUPPLY_GLUT_BEARISH = "SUPPLY_GLUT_BEARISH"       # Quota breakdown/flood
    DEMAND_DESTRUCTION = "DEMAND_DESTRUCTION"         # High energy prices causing recessionary collapse
    NEUTRAL_NOISE = "NEUTRAL_NOISE"                   # Verbal noise with zero physical shipping impact
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D6_YieldCurveDivergence(str, Enum):
    """Interest rate curve and currency divergence."""
    DOLLAR_DOMINANCE = "DOLLAR_DOMINANCE"          # US yield spread widening, bullish USD
    EURO_CATCHUP = "EURO_CATCHUP"                  # European yield spread widening, bullish EUR
    YIELD_CURVE_STEEPNESS_SPIKE = "YIELD_CURVE_STEEPNESS_SPIKE" # Bull/bear steepener
    RECESSIONARY_INVERSION = "RECESSIONARY_INVERSION"           # Deepening 2s10s inversion
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D6b_CrossAssetConfirmation(str, Enum):
    """Intermarket confirmation across equities, yields, and commodities."""
    CONFIRMED_MACRO_FLOW = "CONFIRMED_MACRO_FLOW"  # USD, yields, and equities move in classical harmony
    DIVERGENT_FLOW = "DIVERGENT_FLOW"              # e.g., Oil surging but Gold flat and USD surging
    DECOUPLING_NOISE = "DECOUPLING_NOISE"          # Isolated single-asset idiosyncratic squeeze
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D6c_EquityCreditImpulse(str, Enum):
    """High-yield OAS credit spread vs equity divergence."""
    CREDIT_LEADS_RALLY = "CREDIT_LEADS_RALLY"
    CREDIT_WARNING_DIVERGENCE = "CREDIT_WARNING_DIVERGENCE" # Spreads widening while equities rally
    JUNK_STRESS_SPIKE = "JUNK_STRESS_SPIKE"
    BENIGN_CALM = "BENIGN_CALM"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 5. LAYER 4 & 5: FLOW, POSITIONING & TECHNICAL SIGNALS
# ===========================================================================

class D10_COTExtreme(str, Enum):
    """CFTC Commitments of Traders crowded positioning."""
    CROWDED_LONG = "CROWDED_LONG"                  # COT percentile >= 90% (unwind risk)
    CROWDED_SHORT = "CROWDED_SHORT"                # COT percentile <= 10% (squeeze risk)
    NEUTRAL_POSITIONING = "NEUTRAL_POSITIONING"
    UNWIND_RISK_HIGH = "UNWIND_RISK_HIGH"          # 2-week rapid positioning flip
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D10b_RetailSentimentFlow(str, Enum):
    """Retail sentiment contrarian indicator."""
    RETAIL_FOMO_EXTREME = "RETAIL_FOMO_EXTREME"    # >80% retail long (bearish contrarian)
    RETAIL_PANIC_EXTREME = "RETAIL_PANIC_EXTREME"  # >80% retail short (bullish contrarian)
    BALANCED_FLOW = "BALANCED_FLOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D11_TrendState(str, Enum):
    """Quant trend regime classification."""
    STRONG_TREND = "STRONG_TREND"                  # ADX > 25, Hurst > 0.55
    WEAK_TREND = "WEAK_TREND"                      # ADX 18-25
    RANGE_BOUND = "RANGE_BOUND"                    # ADX < 18, Hurst < 0.45
    TRANSITION_BREAKOUT = "TRANSITION_BREAKOUT"    # Volatility expansion from range
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D11b_VolRegime(str, Enum):
    """Volatility regime."""
    COMPRESSION_SQUEEZE = "COMPRESSION_SQUEEZE"    # ATR < 20th percentile
    NORMAL_VOLATILITY = "NORMAL_VOLATILITY"        # ATR 20th-80th percentile
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"  # ATR > 80th percentile
    EXTREME_VOL_SHOCK = "EXTREME_VOL_SHOCK"        # ATR > 98th percentile or VIX > 30
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 6. LAYER 6: OPPORTUNITY ARBITRATION & EXECUTION
# ===========================================================================

class D7_AnalogFalsification(str, Enum):
    """Historical archetype validation and tripwire status."""
    TRAJECTORY_CONFIRMED = "TRAJECTORY_CONFIRMED"  # Price strictly tracking historical analog
    EARLY_INVALIDATION = "EARLY_INVALIDATION"      # Early price divergence from archetype
    TRIPWIRE_TRIGGERED = "TRIPWIRE_TRIGGERED"      # Hard condition tripwire hit (e.g. OAS > 400)
    AMBIGUOUS_DELAY = "AMBIGUOUS_DELAY"            # Stale narrative, awaiting confirmation
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D8_OpportunityArbitration(str, Enum):
    """Final OpportunityBus macro gate verdict."""
    STRONG_PROCEED = "STRONG_PROCEED"              # 1.0x risk allocation: full macro & technical alignment
    CONDITIONAL_HALF_RISK = "CONDITIONAL_HALF_RISK"# 0.5x risk allocation: minor divergence or neutral macro
    REJECT_MACRO_CONFLICT = "REJECT_MACRO_CONFLICT"# 0.0x risk: trade directly opposes macro shock/regime
    BLACKOUT_POSTPONE = "BLACKOUT_POSTPONE"        # 0.0x risk: defer until danger window passes
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D13_ExecutionMicroDecision(str, Enum):
    """Execution timing and order type micro-decision."""
    MARKET_NOW = "MARKET_NOW"                      # Clean liquidity, enter immediately
    LIMIT_AT_LEVEL = "LIMIT_AT_LEVEL"              # Await retest / limit pull
    WAIT_FOR_SPREAD_COMPRESSION = "WAIT_FOR_SPREAD_COMPRESSION" # Spread > 2x normal, pause
    SKIP_SETUP = "SKIP_SETUP"                      # Book void, skip execution
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class D12_NarrativeDecay(str, Enum):
    """Narrative decay scoring: does the active story still move price?

    Scores the NarrativeStateMachine's active narrative (time-decay factor,
    evidence balance, falsification-tripwire status) into one typed head so
    cycle decisions inherit narrative staleness explicitly. Mirrors the
    deterministic decay_factor (2^(-age/half_life)) and current_confidence
    contract in narrative_state.py.
    """
    FRESH_CONFIRMED = "FRESH_CONFIRMED"            # decay >= 0.7 with confirming evidence balance
    ACTIVE_FRESH = "ACTIVE_FRESH"                  # decay >= 0.4, narrative still tradeable
    STALE_FADING = "STALE_FADING"                  # decay >= 0.15, thinning conviction
    EXHAUSTED_PRICED = "EXHAUSTED_PRICED"          # decay < 0.15, priced in / fade candidate
    TRIPWIRE_INVALIDATED = "TRIPWIRE_INVALIDATED"  # Falsification tripwire hit: narrative dead
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 6b. ROUTING: HEADLINE -> EVENT-CLASS (R1)
# ===========================================================================

class R1_HeadlineEventRouting(str, Enum):
    """Headline-to-event-class routing for causal mirroring and triage.

    Event classes mirror ``economic_dynamics_measure._EVENT_TOKEN_MAP`` so a
    model-routed headline can be post-validated against the lexical router
    (deterministic floor) before reaching the causal graph's event paths.
    """
    EVENT_CPI = "EVENT_CPI"                        # Inflation print: CPI/PCE/HICP
    EVENT_NFP = "EVENT_NFP"                        # Labor print: NFP/payrolls/claims
    EVENT_CB_MEETING = "EVENT_CB_MEETING"          # Central bank decision/speech: FOMC/ECB/BOJ
    EVENT_GEO_SHOCK = "EVENT_GEO_SHOCK"            # War/missile/strike/geopolitical shock
    EVENT_BANKING_STRESS = "EVENT_BANKING_STRESS"  # Bank failure/credit stress: SVB/FDIC
    EVENT_TRADE_WAR = "EVENT_TRADE_WAR"            # Tariffs/sanctions/trade conflict
    EVENT_POLITICAL = "EVENT_POLITICAL"            # Elections/executive action/political shock
    EVENT_CB_GOLD = "EVENT_CB_GOLD"                # Central bank gold accumulation
    NOT_EVENT_NOISE = "NOT_EVENT_NOISE"            # No routable event class (commentary/noise)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 7. META-DECISIONS (M1 - M6)
# ===========================================================================

class M1_ConfidenceCalibration(str, Enum):
    """Meta-confidence band."""
    GROUNDED = "GROUNDED"                          # Entropy < 0.3, sharp feature alignment
    PROBABILISTIC = "PROBABILISTIC"                # Entropy 0.3 - 0.7, well-behaved distribution
    SPECULATIVE = "SPECULATIVE"                    # Entropy 0.7 - 0.9, high ambiguity
    CONFLICTED = "CONFLICTED"                      # Contradictory inputs across layers
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class M2_EvidenceSufficiency(str, Enum):
    """Abstention gate."""
    ACT_NOW = "ACT_NOW"                            # All critical anchors verified
    WAIT_CONFIRMATION = "WAIT_CONFIRMATION"        # Awaiting upcoming confirmation data
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"        # Abstain: do not guess


class M4_ReasoningChainValidity(str, Enum):
    """Logical integrity of cross-layer inferences."""
    CHAIN_INTACT = "CHAIN_INTACT"
    BROKEN_LINK = "BROKEN_LINK"
    CONTRADICTED_BY_PRICE = "CONTRADICTED_BY_PRICE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 8. CAUSAL ATTRIBUTION (Q1 - Q7)
# ===========================================================================

class Q1_PrimaryDriver(str, Enum):
    """Causal source of price move."""
    MACRO_DATA = "MACRO_DATA"
    CB_POLICY = "CB_POLICY"
    GEO_EVENT = "GEO_EVENT"
    FLOW_POSITIONING = "FLOW_POSITIONING"
    TECHNICAL_ORDERFLOW = "TECHNICAL_ORDERFLOW"
    UNKNOWN = "UNKNOWN"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Q5_MoveQuality(str, Enum):
    """Sustainability and structural quality of price action."""
    FUNDAMENTAL_REPRICING = "FUNDAMENTAL_REPRICING" # High volume, cross-asset confirmation, trend persistence
    POSITIONING_SQUEEZE = "POSITIONING_SQUEEZE"     # Sharp fast move against crowded positioning, prone to fade
    STOP_CASCADE = "STOP_CASCADE"                   # Wick sweep through swing levels, high reversal risk
    LIQUIDITY_GHOST = "LIQUIDITY_GHOST"             # Empty book, wide spread jump on negligible volume
    NOISE = "NOISE"                                 # Random walk fluctuation within range
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Q7_CandlestickSignature(str, Enum):
    """Deterministic price action signature."""
    IMPULSIVE_BREAK = "IMPULSIVE_BREAK"             # Large body (>70%), close near high/low, volume Z > +1.5
    GRIND_TREND = "GRIND_TREND"                     # Consecutive small bodies, low wicks
    V_REVERSAL = "V_REVERSAL"                       # Long exhaustion wick (>60%), strong reject
    GAP_HOLD = "GAP_HOLD"                           # Session gap holding firm
    EXHAUSTION_FADE = "EXHAUSTION_FADE"             # Narrowing bodies into resistance
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ===========================================================================
# 9. UNIFIED DECISION RECORD & IMMUTABLE BUNDLE CONTRACT
# ===========================================================================

@dataclass(frozen=True)
class DecisionRecord:
    """Immutable record of an individual typed decision head outcome."""
    decision_id: str
    decision_layer: str
    head_type: str                            # 'choice', 'boolean', 'score'
    selected_option: str
    probabilities: Dict[str, float] = field(default_factory=dict)
    score: Optional[float] = None
    entropy: float = 0.0
    is_abstained: bool = False
    rule_label: Optional[str] = None
    model_label: Optional[str] = None
    is_overridden: bool = False
    override_reason: str = ""
    is_veto: bool = False
    feature_hash: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    model_version: str = "laya-modernbert-421m"
    latency_ms: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    execution_authority: bool = False          # CRITICAL INVARIANT: ALWAYS FALSE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision_layer": self.decision_layer,
            "head_type": self.head_type,
            "selected_option": self.selected_option,
            "probabilities": self.probabilities,
            "score": self.score,
            "entropy": round(self.entropy, 4),
            "is_abstained": self.is_abstained,
            "rule_label": self.rule_label,
            "model_label": self.model_label,
            "is_overridden": self.is_overridden,
            "override_reason": self.override_reason,
            "is_veto": self.is_veto,
            "feature_hash": self.feature_hash,
            "evidence_refs": self.evidence_refs,
            "model_version": self.model_version,
            "latency_ms": round(self.latency_ms, 2),
            "created_at": self.created_at,
            "execution_authority": self.execution_authority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DecisionRecord:
        d = dict(data)
        d["execution_authority"] = False  # Enforce invariant
        return cls(**d)


@dataclass(frozen=True)
class DecisionBundle:
    """Unified container of all decision heads for a given evaluation cycle."""
    bundle_id: str
    timestamp: str
    feature_hash: str
    decisions: Dict[str, DecisionRecord]
    fast_path_latency_ms: float
    total_cycle_latency_ms: float
    overrides_applied_count: int
    veto_enforced: bool
    arbitration_verdict: str                  # D8 result
    risk_multiplier: float                   # 1.0, 0.5, 0.0
    execution_authority: bool = False        # CRITICAL INVARIANT: ALWAYS FALSE

    def get(self, decision_id: str) -> Optional[DecisionRecord]:
        return self.decisions.get(decision_id)

    def get_option(self, decision_id: str, default: str = "INSUFFICIENT_DATA") -> str:
        rec = self.decisions.get(decision_id)
        return rec.selected_option if rec else default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "timestamp": self.timestamp,
            "feature_hash": self.feature_hash,
            "fast_path_latency_ms": round(self.fast_path_latency_ms, 2),
            "total_cycle_latency_ms": round(self.total_cycle_latency_ms, 2),
            "overrides_applied_count": self.overrides_applied_count,
            "veto_enforced": self.veto_enforced,
            "arbitration_verdict": self.arbitration_verdict,
            "risk_multiplier": self.risk_multiplier,
            "execution_authority": self.execution_authority,
            "decisions": {k: v.to_dict() for k, v in self.decisions.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DecisionBundle:
        d = dict(data)
        dec_map = {}
        for k, v in d.get("decisions", {}).items():
            if isinstance(v, dict):
                dec_map[k] = DecisionRecord.from_dict(v)
            elif isinstance(v, DecisionRecord):
                dec_map[k] = v
        return cls(
            bundle_id=d.get("bundle_id", ""),
            timestamp=d.get("timestamp", ""),
            feature_hash=d.get("feature_hash", ""),
            decisions=dec_map,
            fast_path_latency_ms=float(d.get("fast_path_latency_ms", 0.0)),
            total_cycle_latency_ms=float(d.get("total_cycle_latency_ms", 0.0)),
            overrides_applied_count=int(d.get("overrides_applied_count", 0)),
            veto_enforced=bool(d.get("veto_enforced", False)),
            arbitration_verdict=str(d.get("arbitration_verdict", "STRONG_PROCEED")),
            risk_multiplier=float(d.get("risk_multiplier", 1.0)),
            execution_authority=False,
        )

    @property
    def d1_surprise(self) -> D1_EconomicSurprise:
        val = self.get_option("D1_economic_surprise", D1_EconomicSurprise.IN_LINE.value)
        try:
            return D1_EconomicSurprise(val)
        except Exception:
            return D1_EconomicSurprise.IN_LINE

    @property
    def d2_blackout(self) -> D2_BlackoutGate:
        val = self.get_option("D2_blackout_gate", D2_BlackoutGate.NORMAL_TRADING.value)
        try:
            return D2_BlackoutGate(val)
        except Exception:
            return D2_BlackoutGate.NORMAL_TRADING

    @property
    def d3_regime(self) -> D3_MacroRegime:
        val = self.get_option("D3_macro_regime", D3_MacroRegime.RISK_ON_EXPANSION.value)
        try:
            return D3_MacroRegime(val)
        except Exception:
            return D3_MacroRegime.RISK_ON_EXPANSION

    @property
    def d4_liquidity(self) -> D4_LiquidityStance:
        val = self.get_option("D4_liquidity_stance", D4_LiquidityStance.NEUTRAL_LIQUIDITY.value)
        try:
            return D4_LiquidityStance(val)
        except Exception:
            return D4_LiquidityStance.NEUTRAL_LIQUIDITY

    @property
    def d9_kill_switch(self) -> D9_EmergencyKillSwitch:
        val = self.get_option("D9_emergency_kill_switch", D9_EmergencyKillSwitch.NORMAL_OPERATION.value)
        try:
            return D9_EmergencyKillSwitch(val)
        except Exception:
            return D9_EmergencyKillSwitch.NORMAL_OPERATION

    @property
    def d8_arbitration(self) -> D8_OpportunityArbitration:
        val = self.get_option("D8_opportunity_arbitration", D8_OpportunityArbitration.STRONG_PROCEED.value)
        try:
            return D8_OpportunityArbitration(val)
        except Exception:
            return D8_OpportunityArbitration.STRONG_PROCEED


# Per-head provenance for an applied override. The audit trail has to say which
# model actually decided a head, not just "a model" — a headline classifier and
# a candle forecaster are not interchangeable evidence.
OVERRIDE_REASONS: Dict[str, str] = {
    "D11_trend_state": "Kronos candle-forecast path efficiency",
    "D11b_vol_regime": "Kronos vol term structure (arXiv:2508.02739 §4)",
    "D6_yield_curve_divergence": "Kronos forecast with curve features",
}


def _is_allowed_option(decision_id: str, value: str) -> bool:
    """True when ``value`` is a member of this head's declared enum.

    Registry-driven so a head added to ``TAXONOMY_REGISTRY`` is validated
    automatically. An unregistered head accepts anything (it predates the
    registry and there is nothing to check against).
    """
    entry = TAXONOMY_REGISTRY.get(decision_id)
    if not entry:
        return True
    enum_cls = entry.get("enum_cls")
    if enum_cls is None:
        return True
    try:
        return value in {m.value for m in enum_cls}
    except TypeError:
        return True


def create_decision_bundle(
    features: Any,
    overrides: Optional[Dict[str, Any]] = None,
    proposed_direction: str = "BUY",
    symbol: str = "EURUSD",
    model_version: str = "laya-modernbert-421m",
    latency_ms: float = 0.0,
) -> DecisionBundle:
    from research.decision_models.deterministic_labeler_expanded import (
        label_d0_1_source_credibility,
        label_d0_2_duplicate_detection,
        label_d0_3_fact_vs_opinion,
        label_d1_economic_surprise,
        label_d1b_priced_in_asymmetry,
        label_d1c_revision_risk,
        label_d2_blackout_gate,
        label_d9_emergency_kill_switch,
        label_d5a_escalation_ladder,
        label_d5b_physical_supply_verification,
        label_d5c_sanctions_impact,
        label_d3_macro_regime,
        label_d4_liquidity_stance,
        label_d4b_central_bank_divergence,
        label_d4c_fiscal_impulse,
        label_d5_commodity_shock,
        label_d6_yield_curve_divergence,
        label_d6b_cross_asset_confirmation,
        label_d6c_equity_credit_impulse,
        label_d10_cot_extreme,
        label_d10b_retail_sentiment_flow,
        label_d11_trend_state,
        label_d11b_vol_regime,
        label_d7_analog_falsification,
        label_d8_opportunity_arbitration,
        label_d13_execution_micro_decision,
        label_d12_narrative_decay,
        label_m1_confidence_calibration,
        label_m2_evidence_sufficiency,
        label_q5_move_quality,
        label_q7_candlestick_signature,
    )

    feature_hash = getattr(features, "feature_hash", "")
    if not feature_hash:
        feature_hash = hashlib.sha256(str(features).encode("utf-8")).hexdigest()
    ts = datetime.now(timezone.utc).isoformat()
    bundle_id = f"BUNDLE_{feature_hash[:12]}_{int(datetime.now(timezone.utc).timestamp())}"

    rule_map = {
        "D0_1_source_credibility": ("Layer 0: Ingestion", "choice", label_d0_1_source_credibility(features)),
        "D0_2_duplicate_detection": ("Layer 0: Ingestion", "choice", label_d0_2_duplicate_detection(features)),
        "D0_3_fact_vs_opinion": ("Layer 0: Ingestion", "choice", label_d0_3_fact_vs_opinion(features)),
        "D1_economic_surprise": ("Layer 1: Event & Shock", "choice", label_d1_economic_surprise(features)),
        "D1b_priced_in_asymmetry": ("Layer 1: Event & Shock", "choice", label_d1b_priced_in_asymmetry(features)),
        "D1c_revision_risk": ("Layer 1: Event & Shock", "choice", label_d1c_revision_risk(features)),
        "D2_blackout_gate": ("Layer 1: Event & Shock", "choice", label_d2_blackout_gate(features)),
        "D9_emergency_kill_switch": ("Layer 1b: Emergency", "choice", label_d9_emergency_kill_switch(features)),
        "D5a_escalation_ladder": ("Layer 1b: Geopolitical", "choice", label_d5a_escalation_ladder(features)),
        "D5b_physical_supply_verification": ("Layer 1b: Geopolitical", "choice", label_d5b_physical_supply_verification(features)),
        "D5c_sanctions_impact": ("Layer 1b: Geopolitical", "choice", label_d5c_sanctions_impact(features)),
        "D3_macro_regime": ("Layer 2: Regime", "choice", label_d3_macro_regime(features)),
        "D4_liquidity_stance": ("Layer 2: Sovereign Liquidity", "choice", label_d4_liquidity_stance(features)),
        "D4b_central_bank_divergence": ("Layer 2: Sovereign Liquidity", "choice", label_d4b_central_bank_divergence(features)),
        "D4c_fiscal_impulse": ("Layer 2: Sovereign Liquidity", "choice", label_d4c_fiscal_impulse(features)),
        "D5_commodity_shock": ("Layer 3: Transmission", "choice", label_d5_commodity_shock(features)),
        "D6_yield_curve_divergence": ("Layer 3: Transmission", "choice", label_d6_yield_curve_divergence(features)),
        "D6b_cross_asset_confirmation": ("Layer 3: Transmission", "choice", label_d6b_cross_asset_confirmation(features)),
        "D6c_equity_credit_impulse": ("Layer 3: Transmission", "choice", label_d6c_equity_credit_impulse(features)),
        "D10_cot_extreme": ("Layer 4: Positioning & Flow", "choice", label_d10_cot_extreme(features)),
        "D10b_retail_sentiment_flow": ("Layer 4: Positioning & Flow", "choice", label_d10b_retail_sentiment_flow(features)),
        "D11_trend_state": ("Layer 5: Technical & Quant", "choice", label_d11_trend_state(features)),
        "D11b_vol_regime": ("Layer 5: Technical & Quant", "choice", label_d11b_vol_regime(features)),
        "D7_analog_falsification": ("Layer 6: Arbitration", "choice", label_d7_analog_falsification(features)),
        "D8_opportunity_arbitration": ("Layer 6: Arbitration", "choice", label_d8_opportunity_arbitration(features, proposed_direction, symbol)),
        "D13_execution_micro_decision": ("Layer 6: Arbitration", "choice", label_d13_execution_micro_decision(features)),
        "D12_narrative_decay": ("Layer 6: Arbitration", "choice", label_d12_narrative_decay(features)),
        "M1_confidence_calibration": ("Meta-Decisions", "choice", label_m1_confidence_calibration(features)),
        "M2_evidence_sufficiency": ("Meta-Decisions", "choice", label_m2_evidence_sufficiency(features)),
        "Q5_move_quality": ("Causal Attribution", "choice", label_q5_move_quality(features)),
        "Q7_candlestick_signature": ("Causal Attribution", "choice", label_q7_candlestick_signature(features)),
    }

    overrides_dict = overrides or {}
    dec_records: Dict[str, DecisionRecord] = {}
    overrides_applied_count = 0

    for dec_id, (layer, head_type, rule_res) in rule_map.items():
        rule_val = rule_res.value if hasattr(rule_res, "value") else str(rule_res)
        
        override_candidate = (
            overrides_dict.get(dec_id)
            or overrides_dict.get(dec_id.lower())
            or overrides_dict.get(dec_id.split("_")[0])
            or overrides_dict.get(dec_id.split("_")[0].lower())
        )
        
        is_overridden = False
        selected_option = rule_val
        model_label = None
        override_reason = ""

        if override_candidate is not None:
            cand_str = override_candidate.value if hasattr(override_candidate, "value") else str(override_candidate)
            model_label = cand_str
            if cand_str != rule_val:
                is_overridden = True
                selected_option = cand_str
                override_reason = OVERRIDE_REASONS.get(dec_id, "Model prediction validated by post-validator")
                overrides_applied_count += 1

        # Every override must name a value this head's own enum actually
        # contains. A validator or adapter that emits foreign vocabulary used to
        # be able to write arbitrary text into selected_option, which then
        # compared unequal to every real value downstream. This is what made
        # the D0_1/D0_2 heads inert: the model spoke a different vocabulary and
        # its labels were being carried as overrides that matched nothing.
        if is_overridden and not _is_allowed_option(dec_id, selected_option):
            is_overridden = False
            override_reason = ""
            selected_option = rule_val
            model_label = None
            overrides_applied_count -= 1

        is_veto = (
            (dec_id == "D2_blackout_gate" and selected_option == D2_BlackoutGate.BLACKOUT_FREEZE.value)
            or (dec_id == "D9_emergency_kill_switch" and selected_option == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE.value)
            or (dec_id == "D8_opportunity_arbitration" and selected_option == D8_OpportunityArbitration.REJECT_MACRO_CONFLICT.value)
            or (dec_id == "D4_liquidity_stance" and selected_option == D4_LiquidityStance.LIQUIDITY_CRUNCH.value)
            or (dec_id == "D0_1_source_credibility" and selected_option == D0_1_SourceCredibility.SPOOF_REJECTED.value)
        )

        dec_records[dec_id] = DecisionRecord(
            decision_id=dec_id,
            decision_layer=layer,
            head_type=head_type,
            selected_option=selected_option,
            rule_label=rule_val,
            model_label=model_label,
            is_overridden=is_overridden,
            override_reason=override_reason,
            is_veto=is_veto,
            feature_hash=feature_hash,
            model_version=model_version,
            latency_ms=latency_ms,
            created_at=ts,
            execution_authority=False,  # CRITICAL INVARIANT
        )

    veto_enforced = any(r.is_veto for r in dec_records.values())
    d8_opt = dec_records["D8_opportunity_arbitration"].selected_option

    if veto_enforced:
        risk_mult = 0.0
    elif d8_opt == D8_OpportunityArbitration.CONDITIONAL_HALF_RISK.value:
        risk_mult = 0.5
    else:
        risk_mult = 1.0

    return DecisionBundle(
        bundle_id=bundle_id,
        timestamp=ts,
        feature_hash=feature_hash,
        decisions=dec_records,
        fast_path_latency_ms=latency_ms,
        total_cycle_latency_ms=latency_ms,
        overrides_applied_count=overrides_applied_count,
        veto_enforced=veto_enforced,
        arbitration_verdict=d8_opt,
        risk_multiplier=risk_mult,
        execution_authority=False,  # CRITICAL INVARIANT
    )


generate_decision_bundle = create_decision_bundle


# ===========================================================================
# 10. CENTRAL TAXONOMY REGISTRY
# ===========================================================================

TAXONOMY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "D0_1_source_credibility": {
        "layer": "Layer 0: Ingestion",
        "enum_cls": D0_1_SourceCredibility,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["source_tier", "is_official_feed", "is_adversarial"],
        "default_abstain": D0_1_SourceCredibility.INSUFFICIENT_DATA.value,
    },
    "D0_2_duplicate_detection": {
        "layer": "Layer 0: Ingestion",
        "enum_cls": D0_2_DuplicateDetection,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["headline_hash", "time_since_first_report"],
        "default_abstain": D0_2_DuplicateDetection.INSUFFICIENT_DATA.value,
    },
    "D0_3_fact_vs_opinion": {
        "layer": "Layer 0: Ingestion",
        "enum_cls": D0_3_FactVsOpinion,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["has_numerical_data", "source_type"],
        "default_abstain": D0_3_FactVsOpinion.INSUFFICIENT_DATA.value,
    },
    "D1_economic_surprise": {
        "layer": "Layer 1: Fast Event Shock",
        "enum_cls": D1_EconomicSurprise,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["z_score", "actual", "consensus"],
        "default_abstain": D1_EconomicSurprise.INSUFFICIENT_DATA.value,
    },
    "D1b_priced_in_asymmetry": {
        "layer": "Layer 1: Fast Event Shock",
        "enum_cls": D1b_PricedInAsymmetry,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["z_score", "ois_implied_path"],
        "default_abstain": D1b_PricedInAsymmetry.INSUFFICIENT_DATA.value,
    },
    "D1c_revision_risk": {
        "layer": "Layer 1: Fast Event Shock",
        "enum_cls": D1c_RevisionRisk,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["previous_revision_delta"],
        "default_abstain": D1c_RevisionRisk.INSUFFICIENT_DATA.value,
    },
    "D2_blackout_gate": {
        "layer": "Layer 1: Fast Event Shock",
        "enum_cls": D2_BlackoutGate,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["minutes_to_release", "tier", "vix"],
        "default_abstain": D2_BlackoutGate.INSUFFICIENT_DATA.value,
    },
    "D9_emergency_kill_switch": {
        "layer": "Layer 1b: Geopolitical & Breaking",
        "enum_cls": D9_EmergencyKillSwitch,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["vix", "is_exchange_halt", "is_peg_broken"],
        "default_abstain": D9_EmergencyKillSwitch.INSUFFICIENT_DATA.value,
    },
    "D5a_escalation_ladder": {
        "layer": "Layer 1b: Geopolitical & Breaking",
        "enum_cls": D5a_EscalationLadder,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["military_incident_level", "chokepoint_impact"],
        "default_abstain": D5a_EscalationLadder.INSUFFICIENT_DATA.value,
    },
    "D5b_physical_supply_verification": {
        "layer": "Layer 1b: Geopolitical & Breaking",
        "enum_cls": D5b_PhysicalSupplyVerification,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["ais_tanker_speed_drop", "pipeline_flow_drop_pct"],
        "default_abstain": D5b_PhysicalSupplyVerification.INSUFFICIENT_DATA.value,
    },
    "D3_macro_regime": {
        "layer": "Layer 2: Regime & Liquidity",
        "enum_cls": D3_MacroRegime,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["vix", "credit_oas_bps", "oil_5d_pct"],
        "default_abstain": D3_MacroRegime.INSUFFICIENT_DATA.value,
    },
    "D4_liquidity_stance": {
        "layer": "Layer 2: Regime & Liquidity",
        "enum_cls": D4_LiquidityStance,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["net_liquidity_delta_30d_b", "sofr_rate", "reserves_delta_b"],
        "default_abstain": D4_LiquidityStance.INSUFFICIENT_DATA.value,
    },
    "D4b_central_bank_divergence": {
        "layer": "Layer 2: Regime & Liquidity",
        "enum_cls": D4b_CentralBankDivergence,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["fed_funds_rate", "ecb_deposit_rate", "rate_spread_delta_30d"],
        "default_abstain": D4b_CentralBankDivergence.INSUFFICIENT_DATA.value,
    },
    "D5_commodity_shock": {
        "layer": "Layer 3: Transmission",
        "enum_cls": D5_CommodityShock,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["oil_5d_pct", "physical_flow_confirmed"],
        "default_abstain": D5_CommodityShock.INSUFFICIENT_DATA.value,
    },
    "D6_yield_curve_divergence": {
        "layer": "Layer 3: Transmission",
        "enum_cls": D6_YieldCurveDivergence,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["yield_10y_delta_bps", "dxy_change_pct", "curve_2s10s_bps"],
        "default_abstain": D6_YieldCurveDivergence.INSUFFICIENT_DATA.value,
    },
    "D10_cot_extreme": {
        "layer": "Layer 4: Positioning & Flow",
        "enum_cls": D10_COTExtreme,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["cot_percentile", "cot_2wk_delta"],
        "default_abstain": D10_COTExtreme.INSUFFICIENT_DATA.value,
    },
    "D11_trend_state": {
        "layer": "Layer 5: Technical & Quant",
        "enum_cls": D11_TrendState,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["adx", "hurst"],
        "default_abstain": D11_TrendState.INSUFFICIENT_DATA.value,
    },
    "D7_analog_falsification": {
        "layer": "Layer 6: Arbitration & Execution",
        "enum_cls": D7_AnalogFalsification,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["analog_correlation", "tripwire_hit"],
        "default_abstain": D7_AnalogFalsification.INSUFFICIENT_DATA.value,
    },
    "D8_opportunity_arbitration": {
        "layer": "Layer 6: Arbitration & Execution",
        "enum_cls": D8_OpportunityArbitration,
        "head_type": "choice",
        "safety_severity": "CRITICAL",
        "required_features": ["proposed_direction", "symbol", "d1", "d2", "d3", "d4", "d6"],
        "default_abstain": D8_OpportunityArbitration.INSUFFICIENT_DATA.value,
    },
    "M1_confidence_calibration": {
        "layer": "Meta-Decisions",
        "enum_cls": M1_ConfidenceCalibration,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["decision_entropy", "missing_features_count"],
        "default_abstain": M1_ConfidenceCalibration.INSUFFICIENT_DATA.value,
    },
    "M2_evidence_sufficiency": {
        "layer": "Meta-Decisions",
        "enum_cls": M2_EvidenceSufficiency,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["critical_anchors_present", "source_credibility"],
        "default_abstain": M2_EvidenceSufficiency.INSUFFICIENT_DATA.value,
    },
    "Q1_primary_driver": {
        "layer": "Causal Attribution",
        "enum_cls": Q1_PrimaryDriver,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["macro_news_intensity", "orderflow_imbalance"],
        "default_abstain": Q1_PrimaryDriver.INSUFFICIENT_DATA.value,
    },
    "Q5_move_quality": {
        "layer": "Causal Attribution",
        "enum_cls": Q5_MoveQuality,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["volume_z", "cross_asset_confirmation", "wick_ratio"],
        "default_abstain": Q5_MoveQuality.INSUFFICIENT_DATA.value,
    },
    "Q7_candlestick_signature": {
        "layer": "Causal Attribution",
        "enum_cls": Q7_CandlestickSignature,
        "head_type": "choice",
        "safety_severity": "LOW",
        "required_features": ["body_ratio", "wick_ratio", "move_speed_pips_min"],
        "default_abstain": Q7_CandlestickSignature.INSUFFICIENT_DATA.value,
    },
    "D12_narrative_decay": {
        "layer": "Layer 6: Arbitration & Execution",
        "enum_cls": D12_NarrativeDecay,
        "head_type": "choice",
        "safety_severity": "HIGH",
        "required_features": ["narrative_decay_factor", "narrative_age_days", "narrative_tripwire_hit"],
        "default_abstain": D12_NarrativeDecay.INSUFFICIENT_DATA.value,
    },
    "R1_headline_event_routing": {
        "layer": "Layer 0: Ingestion",
        "enum_cls": R1_HeadlineEventRouting,
        "head_type": "choice",
        "safety_severity": "MEDIUM",
        "required_features": ["headline", "narrative"],
        "default_abstain": R1_HeadlineEventRouting.INSUFFICIENT_DATA.value,
    },
}
