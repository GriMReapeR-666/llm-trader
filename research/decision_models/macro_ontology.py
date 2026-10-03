"""Macro Decision Ontology and Quantitative Feature Schema (D1–D8).

Defines the quantitative feature context blocks and discrete decision enums
for all macro-fundamental decisions across the autonomous trading system.

Core Guarantee:
Every scenario context carries deterministic quantitative anchors:
  ├─ Event: {name, currency, tier (HIGH/MED/LOW), timestamp}
  ├─ Surprise: {actual, consensus, σ_hist, Z-score}
  ├─ Market state: {VIX, DXY, 10Y Δ, credit OAS, oil 5d%}
  ├─ Liquidity: {WALCL-TGA-RRP Δ30d, reserves Δ, SOFR}
  ├─ Positioning: {COT percentile, OIS-implied path}
  └─ Time: {minutes_to_release, blackout_window}
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Decision Enums (D1–D8)
# ---------------------------------------------------------------------------
class EconomicSurprise(str, Enum):
    """D1: Economic Surprise Shock Classification."""
    EXTREME_HAWKISH = "EXTREME_HAWKISH"   # Z >= +2.0
    MODERATE_HAWKISH = "MODERATE_HAWKISH" # +0.8 <= Z < +2.0
    IN_LINE = "IN_LINE"                   # -0.8 < Z < +0.8
    MODERATE_DOVISH = "MODERATE_DOVISH"   # -2.0 < Z <= -0.8
    EXTREME_DOVISH = "EXTREME_DOVISH"     # Z <= -2.0


class BlackoutGate(str, Enum):
    """D2: Real-time News Blackout and De-risking Gate."""
    NORMAL_TRADING = "NORMAL_TRADING"
    PRE_EVENT_DELEVERAGE = "PRE_EVENT_DELEVERAGE"
    BLACKOUT_FREEZE = "BLACKOUT_FREEZE"
    POST_SHOCK_PULLBACK = "POST_SHOCK_PULLBACK"


class MacroRegime(str, Enum):
    """D3: Global Macro Regime State."""
    RISK_ON_EXPANSION = "RISK_ON_EXPANSION"
    NORMAL_RISK_OFF = "NORMAL_RISK_OFF"
    STAGFLATION_SHOCK = "STAGFLATION_SHOCK"
    CRISIS_PANIC = "CRISIS_PANIC"


class LiquidityStance(str, Enum):
    """D4: Central Bank & US Net Liquidity Stance."""
    EXPANDING_LIQUIDITY = "EXPANDING_LIQUIDITY"
    NEUTRAL_LIQUIDITY = "NEUTRAL_LIQUIDITY"
    CONTRACTIONARY_DRAIN = "CONTRACTIONARY_DRAIN"
    LIQUIDITY_CRUNCH = "LIQUIDITY_CRUNCH"


class CommodityShock(str, Enum):
    """D5: Commodity & Geopolitical Disruption Routing."""
    SUPPLY_DEFICIT_BULLISH = "SUPPLY_DEFICIT_BULLISH"
    SUPPLY_GLUT_BEARISH = "SUPPLY_GLUT_BEARISH"
    DEMAND_DESTRUCTION = "DEMAND_DESTRUCTION"
    NEUTRAL_NOISE = "NEUTRAL_NOISE"


class YieldCurveDivergence(str, Enum):
    """D6: Sovereign Yield Curve & Currency Divergence."""
    DOLLAR_DOMINANCE = "DOLLAR_DOMINANCE"
    EURO_CATCHUP = "EURO_CATCHUP"
    YIELD_CURVE_STEEPNESS_SPIKE = "YIELD_CURVE_STEEPNESS_SPIKE"
    RECESSIONARY_INVERSION = "RECESSIONARY_INVERSION"


class CaseFalsification(str, Enum):
    """D7: Case Study Analog Trajectory & Tripwires."""
    TRAJECTORY_CONFIRMED = "TRAJECTORY_CONFIRMED"
    EARLY_INVALIDATION = "EARLY_INVALIDATION"
    TRIPWIRE_TRIGGERED = "TRIPWIRE_TRIGGERED"
    AMBIGUOUS_DELAY = "AMBIGUOUS_DELAY"


class OpportunityArbitration(str, Enum):
    """D8: OpportunityBus Macro Arbitration (Macro-to-Execution Gate)."""
    STRONG_PROCEED = "STRONG_PROCEED"
    CONDITIONAL_HALF_RISK = "CONDITIONAL_HALF_RISK"
    REJECT_MACRO_CONFLICT = "REJECT_MACRO_CONFLICT"
    BLACKOUT_POSTPONE = "BLACKOUT_POSTPONE"


# ---------------------------------------------------------------------------
# Quantitative Macro Context Features
# ---------------------------------------------------------------------------
@dataclass
class MacroContextFeatures:
    """Explicit quantitative feature block attached to every decision scenario."""
    # 1. Event Identification
    event_name: str
    currency: str = "USD"
    tier: str = "HIGH"  # HIGH, MEDIUM, LOW
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # 2. Surprise Quant Data
    actual: Optional[float] = None
    consensus: Optional[float] = None
    previous: Optional[float] = None
    sigma_hist: float = 1.0
    z_score: Optional[float] = None

    # 3. Market State
    vix: float = 16.0
    dxy_change_pct: float = 0.0
    yield_10y_delta_bps: float = 0.0
    credit_oas_bps: float = 120.0
    oil_5d_pct: float = 0.0

    # 4. Liquidity Features
    net_liquidity_delta_30d_b: float = 0.0
    reserves_delta_b: float = 0.0
    sofr_rate: float = 5.30

    # 5. Positioning
    cot_percentile: float = 50.0
    ois_implied_path: str = "NEUTRAL"  # HAWKISH_HIKES, DOVISH_CUTS, NEUTRAL

    # 6. Time & Blackout Window
    minutes_to_release: float = 999.0  # negative = past release, positive = upcoming
    blackout_window_minutes: Tuple[float, float] = (-5.0, 2.0)

    # Descriptive Metadata
    headline: str = ""
    narrative: str = ""
    is_garbled: bool = False
    is_adversarial: bool = False

    # 7. Per-Article News Evidence Quality (from Layer-0 triage verdicts)
    # Shape mirrors news_triage.summarize_cycle_evidence(); absent = no data.
    article_evidence: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        """Compute Z-score if not explicitly provided."""
        if self.z_score is None and self.actual is not None and self.consensus is not None:
            denom = self.sigma_hist if self.sigma_hist > 1e-6 else 1.0
            self.z_score = round((self.actual - self.consensus) / denom, 3)

    def is_in_blackout_window(self) -> bool:
        """Check if current time is inside the blackout freeze window."""
        t_pre, t_post = self.blackout_window_minutes
        # e.g., minutes_to_release between -2.0 and +5.0 min (or T-5m to T+2m)
        return t_pre <= self.minutes_to_release <= abs(t_pre) or (-abs(t_post) <= self.minutes_to_release <= 0)

    def to_prompt_state(self) -> Dict[str, Any]:
        """Convert features into structured JSON format for System 1 decision models."""
        z_str = f"{self.z_score:+.2f}σ" if self.z_score is not None else "N/A"
        return {
            "event": {
                "name": self.event_name,
                "currency": self.currency,
                "tier": self.tier,
                "timestamp": self.timestamp,
            },
            "surprise": {
                "actual": self.actual,
                "consensus": self.consensus,
                "previous": self.previous,
                "sigma_hist": self.sigma_hist,
                "z_score": self.z_score,
                "z_string": z_str,
            },
            "market_state": {
                "vix": round(self.vix, 2),
                "dxy_change_pct": round(self.dxy_change_pct, 3),
                "yield_10y_delta_bps": round(self.yield_10y_delta_bps, 1),
                "credit_oas_bps": round(self.credit_oas_bps, 1),
                "oil_5d_pct": round(self.oil_5d_pct, 2),
            },
            "liquidity": {
                "net_liquidity_delta_30d_b": round(self.net_liquidity_delta_30d_b, 1),
                "reserves_delta_b": round(self.reserves_delta_b, 1),
                "sofr_rate": round(self.sofr_rate, 2),
            },
            "positioning": {
                "cot_percentile": round(self.cot_percentile, 1),
                "ois_implied_path": self.ois_implied_path,
            },
            **({"article_evidence": dict(self.article_evidence)} if self.article_evidence else {}),
            "time_context": {
                "minutes_to_release": round(self.minutes_to_release, 1),
                "is_blackout_active": self.is_in_blackout_window(),
            },
            "headline": self.headline,
            "narrative": self.narrative,
        }


# ---------------------------------------------------------------------------
# Standard Typed Questions for Laya / System 1 Models
# ---------------------------------------------------------------------------
def get_standard_macro_questions() -> Dict[str, Dict[str, Any]]:
    """Return standardized Laya typed question specifications for the full D0–D13, M1–M2 taxonomy."""
    return {
        # ── Layer 0: Source Verification ──
        "D0_1_source_credibility": {
            "type": "choice",
            "instructions": "Assess the credibility and reliability of the data source.",
            "criteria": {
                "OFFICIAL_VERIFIED": "Government/central bank official release with audit trail",
                "WIRE_SERVICE": "Bloomberg/Reuters wire with attributed source",
                "UNVERIFIED_LEAK": "Social media or unattributed rumor",
                "SPIN_NARRATIVE": "Clear editorial bias or political framing",
            }
        },
        "D0_2_duplicate_detection": {
            "type": "choice",
            "instructions": "Is this event a duplicate or already priced in?",
            "criteria": {
                "FRESH_EVENT": "New event not yet reflected in prices",
                "PARTIAL_OVERLAP": "Similar event occurred recently, partial pricing",
                "FULLY_PRICED": "Market has fully absorbed this information",
            }
        },
        "D0_3_fact_vs_opinion": {
            "type": "choice",
            "instructions": "Distinguish hard data from commentary.",
            "criteria": {
                "VERIFIED_FACT": "Hard print (CPI 3.2%, Rate cut 50 bps)",
                "OFFICIAL_STATEMENT": "Formal governor quote / press release",
                "ANALYST_OPINION": "Sell-side commentary / speculation",
                "RUMOR_UNCONFIRMED": "Unsubstantiated wire leak",
            }
        },
        # ── Layer 1: Event & Shock ──
        "D1_economic_surprise": {
            "type": "choice",
            "instructions": "Classify the macroeconomic surprise shock based on Z-score and actual vs consensus data.",
            "criteria": {
                "EXTREME_HAWKISH": "Z >= +2.0: Inflation, employment, or policy significantly hotter than expected",
                "MODERATE_HAWKISH": "+0.8 <= Z < +2.0: Higher than consensus, firm tightening pressure",
                "IN_LINE": "-0.8 < Z < +0.8: Negligible variance, fully priced into market",
                "MODERATE_DOVISH": "-2.0 < Z <= -0.8: Weaker than consensus, modest easing pressure",
                "EXTREME_DOVISH": "Z <= -2.0: Severe downside miss or emergency rate cut",
            }
        },
        "D1b_priced_in_asymmetry": {
            "type": "choice",
            "instructions": "Assess Z-score vs OIS/futures market-implied pricing before release.",
            "criteria": {
                "SURPRISE_UNDERPRICED": "Market under-positioned; strong follow-through likely",
                "FAIRLY_PRICED": "Print matches OIS-implied expectation",
                "SURPRISE_OVERPRICED": "'Sell the news' fade risk; market already extended",
            }
        },
        "D1c_revision_risk": {
            "type": "choice",
            "instructions": "Assess materiality of previous period historical revision.",
            "criteria": {
                "REVISION_NEGLIGIBLE": "Previous print unchanged or trivial adjustment",
                "REVISION_MATERIAL_HAWKISH": "Previous revised upward, amplifies hawkish signal",
                "REVISION_MATERIAL_DOVISH": "Previous revised downward, amplifies dovish signal",
            }
        },
        "D2_blackout_gate": {
            "type": "choice",
            "instructions": "Determine the news blackout and risk control state.",
            "criteria": {
                "NORMAL_TRADING": "Outside danger window, full execution authorized",
                "PRE_EVENT_DELEVERAGE": "T-60m to T-5m before high-impact release, scale risk down by 50%",
                "BLACKOUT_FREEZE": "T-5m to T+2m, cancel resting orders and freeze execution",
                "POST_SHOCK_PULLBACK": "T+2m to T+30m, trade confirmed directional post-shock trends",
            }
        },
        # ── Layer 1b: Geopolitical ──
        "D9_emergency_kill_switch": {
            "type": "noul",
            "instructions": "Is an emergency kill-switch active? (de-peg, exchange halt, -5sigma bar, spread blowup)",
        },
        "D5a_escalation_ladder": {
            "type": "choice",
            "instructions": "Classify geopolitical/war conflict escalation severity.",
            "criteria": {
                "RHETORIC_ONLY": "Verbal threats, zero physical action",
                "PROXY_CLASH": "Regional skirmish without trade disruption",
                "DIRECT_STRIKE": "Direct military engagement",
                "CHOKEPOINT_DISRUPTION": "Maritime/pipeline bottleneck threatened",
                "FULL_BLOCKADE": "Complete physical trade halt",
            }
        },
        "D5b_physical_supply_verification": {
            "type": "choice",
            "instructions": "Verify AIS tanker/pipeline data vs headline rumors.",
            "criteria": {
                "CONFIRMED_PHYSICAL_DISRUPTION": "Validated reduction in physical flow via AIS/pipeline data",
                "UNCONFIRMED_HEADLINE_ONLY": "Headlines only, physical flows unchanged",
                "INSUFFICIENT_DATA": "No AIS/pipeline data available to verify",
            }
        },
        "D5c_sanctions_impact": {
            "type": "choice",
            "instructions": "Classify sanctions transmission routing impact.",
            "criteria": {
                "SUPPLY_REROUTE": "Cargoes change destination, net volume unchanged",
                "SUPPLY_LOSS": "Net physical supply destroyed",
                "DEMAND_DESTROYED": "High prices destroy downstream demand",
                "SYMBOLIC_ONLY": "Zero material economic effect",
            }
        },
        # ── Layer 2: Regime & Liquidity ──
        "D3_macro_regime": {
            "type": "choice",
            "instructions": "Classify the global multi-asset macro regime.",
            "criteria": {
                "RISK_ON_EXPANSION": "Low VIX < 16, positive growth, tight credit spreads",
                "NORMAL_RISK_OFF": "VIX 18-25, defensive dollar bid, moderate equity pullback",
                "STAGFLATION_SHOCK": "Crude oil surging, gold/dollar positive correlation, growth slowing with inflation",
                "CRISIS_PANIC": "VIX > 30, equity collapse, correlation breakdown and liquidity stress",
            }
        },
        "D4_liquidity_stance": {
            "type": "choice",
            "instructions": "Classify the US Net Liquidity and central bank reserves stance.",
            "criteria": {
                "EXPANDING_LIQUIDITY": "Net liquidity rising > +$50B/month, tailwind for risk assets",
                "NEUTRAL_LIQUIDITY": "Net liquidity range-bound (+-$20B)",
                "CONTRACTIONARY_DRAIN": "QT and TGA rebuilding draining bank reserves",
                "LIQUIDITY_CRUNCH": "Sharp reserve drain, SOFR repo spike, money market stress",
            }
        },
        "D4b_central_bank_divergence": {
            "type": "choice",
            "instructions": "Assess cross-central bank monetary policy path divergence.",
            "criteria": {
                "FED_LEADS_HAWKISH": "Fed hiking/holding higher than ECB/BOJ",
                "ECB_LEADS_HAWKISH": "ECB hawkish repricing exceeding Fed",
                "BOJ_INTERRUPT": "BOJ surprise hike / YCC tweak disrupting carry",
                "SYNCHRONIZED_EASING": "Global coordinated rate cuts",
                "SYNCHRONIZED_TIGHTENING": "Global coordinated tightening",
            }
        },
        "D4c_fiscal_impulse": {
            "type": "choice",
            "instructions": "Assess Treasury issuance, TGA refill, and fiscal impulse.",
            "criteria": {
                "FISCAL_STIMULUS": "Government spending expansion, deficit widening",
                "NEUTRAL_BUDGET": "Balanced fiscal stance",
                "AUSTERITY_DRAIN": "Spending cuts, TGA refill draining liquidity",
                "DEBT_CEILING_CRISIS": "Political standoff threatening default",
            }
        },
        # ── Layer 3: Multi-Asset Transmission ──
        "D5_commodity_shock": {
            "type": "choice",
            "instructions": "Classify physical commodity and geopolitical disruption impact.",
            "criteria": {
                "SUPPLY_DEFICIT_BULLISH": "Physical chokepoint or quota cuts causing immediate deficit and price spike",
                "SUPPLY_GLUT_BEARISH": "Surplus production or quota breakdown flooding market",
                "DEMAND_DESTRUCTION": "High energy prices causing recessionary demand collapse",
                "NEUTRAL_NOISE": "Verbal rhetoric with zero physical shipping or refining disruption",
            }
        },
        "D6_yield_curve_divergence": {
            "type": "choice",
            "instructions": "Determine interest rate curve and currency divergence.",
            "criteria": {
                "DOLLAR_DOMINANCE": "US yield advantage expanding over foreign peers, bullish USD",
                "EURO_CATCHUP": "Foreign central bank hawkish repricing exceeding Fed, bullish EUR",
                "YIELD_CURVE_STEEPNESS_SPIKE": "Rapid curve steepening (bull or bear steepener)",
                "RECESSIONARY_INVERSION": "Yield curve inversion deepening on imminent recession",
            }
        },
        "D6b_cross_asset_confirmation": {
            "type": "choice",
            "instructions": "Check intermarket confirmation across equities, yields, and commodities.",
            "criteria": {
                "CONFIRMED_MACRO_FLOW": "USD, yields, and equities move in classical harmony",
                "DIVERGENT_FLOW": "Cross-asset divergence (e.g., Oil surging but Gold flat)",
                "DECOUPLING_NOISE": "Isolated single-asset idiosyncratic squeeze",
            }
        },
        "D6c_equity_credit_impulse": {
            "type": "choice",
            "instructions": "Assess high-yield OAS credit spread vs equity divergence.",
            "criteria": {
                "CREDIT_LEADS_RALLY": "Credit spreads tightening, confirming equity rally",
                "CREDIT_WARNING_DIVERGENCE": "Spreads widening while equities rally — divergence risk",
                "JUNK_STRESS_SPIKE": "High-yield stress spike, risk-off signal",
                "BENIGN_CALM": "Credit markets calm, no stress signals",
            }
        },
        # ── Layer 4 & 5: Flow, Positioning & Technical ──
        "D10_cot_extreme": {
            "type": "choice",
            "instructions": "Assess CFTC Commitments of Traders crowded positioning.",
            "criteria": {
                "CROWDED_LONG": "COT percentile >= 90%, unwind risk high",
                "CROWDED_SHORT": "COT percentile <= 10%, squeeze risk high",
                "NEUTRAL_POSITIONING": "Balanced positioning, no extreme",
                "UNWIND_RISK_HIGH": "2-week rapid positioning flip detected",
            }
        },
        "D10b_retail_sentiment_flow": {
            "type": "choice",
            "instructions": "Assess retail sentiment as contrarian indicator.",
            "criteria": {
                "RETAIL_FOMO_EXTREME": ">80% retail long — bearish contrarian signal",
                "RETAIL_PANIC_EXTREME": ">80% retail short — bullish contrarian signal",
                "BALANCED_FLOW": "Retail sentiment balanced, no contrarian signal",
            }
        },
        "D11_trend_state": {
            "type": "choice",
            "instructions": "Classify the quant trend regime.",
            "criteria": {
                "STRONG_TREND": "ADX > 25, Hurst > 0.55, directional momentum",
                "WEAK_TREND": "ADX 18-25, emerging or fading trend",
                "RANGE_BOUND": "ADX < 18, Hurst < 0.45, mean-reversion environment",
                "TRANSITION_BREAKOUT": "Volatility expansion from range, breakout imminent",
            }
        },
        "D11b_vol_regime": {
            "type": "choice",
            "instructions": "Classify the volatility regime.",
            "criteria": {
                "COMPRESSION_SQUEEZE": "ATR < 20th percentile, squeeze building",
                "NORMAL_VOLATILITY": "ATR 20th-80th percentile, normal conditions",
                "VOLATILITY_EXPANSION": "ATR > 80th percentile, expansion active",
                "EXTREME_VOL_SHOCK": "ATR > 98th percentile or VIX > 30, crisis volatility",
            }
        },
        # ── Layer 6: Arbitration & Execution ──
        "D7_analog_falsification": {
            "type": "choice",
            "instructions": "Validate historical archetype and tripwire status.",
            "criteria": {
                "TRAJECTORY_CONFIRMED": "Price strictly tracking historical analog",
                "EARLY_INVALIDATION": "Early price divergence from archetype",
                "TRIPWIRE_TRIGGERED": "Hard condition tripwire hit (e.g. OAS > 400)",
                "AMBIGUOUS_DELAY": "Stale narrative, awaiting confirmation",
            }
        },
        "D8_opportunity_arbitration": {
            "type": "choice",
            "instructions": "Arbitrate whether proposed technical setup is approved by macro conditions.",
            "criteria": {
                "STRONG_PROCEED": "Technical and macro alignments agree 100%, allocate full risk",
                "CONDITIONAL_HALF_RISK": "Technical valid, macro neutral or mild headwind, scale down 50%",
                "REJECT_MACRO_CONFLICT": "Setup directly fights macro regime or surprise trend, discard",
                "BLACKOUT_POSTPONE": "Setup valid but falls inside high-impact news blackout window",
            }
        },
        "D13_execution_micro_decision": {
            "type": "choice",
            "instructions": "Determine execution timing and order type.",
            "criteria": {
                "MARKET_NOW": "Clean liquidity, enter immediately",
                "LIMIT_AT_LEVEL": "Await retest / limit pull to key level",
                "WAIT_FOR_SPREAD_COMPRESSION": "Spread > 2x normal, pause for compression",
                "SKIP_SETUP": "Book void, skip execution entirely",
            }
        },
        # ── Meta-Decisions ──
        "M1_confidence_calibration": {
            "type": "choice",
            "instructions": "Calibrate meta-confidence band for the overall decision set.",
            "criteria": {
                "GROUNDED": "Entropy < 0.3, sharp feature alignment, high conviction",
                "PROBABILISTIC": "Entropy 0.3-0.7, well-behaved distribution",
                "SPECULATIVE": "Entropy 0.7-0.9, high ambiguity across inputs",
                "CONFLICTED": "Contradictory inputs across layers, low conviction",
            }
        },
        "M2_evidence_sufficiency": {
            "type": "choice",
            "instructions": "Is evidence sufficient to act, or should we wait?",
            "criteria": {
                "ACT_NOW": "All critical anchors verified, proceed with decision",
                "WAIT_CONFIRMATION": "Awaiting upcoming confirmation data within window",
                "INSUFFICIENT_DATA": "Abstain: insufficient data to form reliable judgment",
            }
        },
        # ── Boolean Gates ──
        "blackout_freeze_boolean": {
            "type": "noul",
            "instructions": "Must automated trading execution be frozen right now?"
        },
        "execution_veto_boolean": {
            "type": "noul",
            "instructions": "Should active risk proposals be vetoed due to adverse macro conditions?"
        },
        # ── Narrative & Routing (D12, R1) ──
        "D12_narrative_decay": {
            "type": "choice",
            "instructions": "Score narrative decay: is the active macro story still moving price, or is it stale and priced in?",
            "criteria": {
                "FRESH_CONFIRMED": "Decay >= 0.7 with confirming evidence balance, narrative actively repricing",
                "ACTIVE_FRESH": "Decay >= 0.4, narrative still tradeable with intact conviction",
                "STALE_FADING": "Decay >= 0.15, thinning conviction, reduce narrative-dependent risk",
                "EXHAUSTED_PRICED": "Decay < 0.15, story fully absorbed, fade candidate",
                "TRIPWIRE_INVALIDATED": "Falsification tripwire hit, narrative is dead",
                "INSUFFICIENT_DATA": "No active narrative or decay metrics available",
            }
        },
        "R1_headline_event_routing": {
            "type": "choice",
            "instructions": "Route this headline to its dominant macro event class (or classify as non-event noise).",
            "criteria": {
                "EVENT_CPI": "Inflation print: CPI/PCE/HICP release or revision",
                "EVENT_NFP": "Labor print: nonfarm payrolls, unemployment, jobless claims",
                "EVENT_CB_MEETING": "Central bank rate decision, minutes, or governor testimony",
                "EVENT_GEO_SHOCK": "War, missile strike, invasion, or geopolitical supply shock",
                "EVENT_BANKING_STRESS": "Bank failure, credit event, FDIC action, funding stress",
                "EVENT_TRADE_WAR": "Tariffs, sanctions, or trade conflict escalation",
                "EVENT_POLITICAL": "Election, impeachment, executive action, political shock",
                "EVENT_CB_GOLD": "Central bank gold accumulation or reserve reallocation",
                "NOT_EVENT_NOISE": "No routable event class: pure commentary, analysis, or noise",
                "INSUFFICIENT_DATA": "Headline too ambiguous to route with confidence",
            }
        },
    }
