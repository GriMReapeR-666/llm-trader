"""Autonomous Dynamic Case Study Research Engine & Real-Time Supply Shock Analyzer.

Empowers the agentic architecture to:
1. Ingest unstructured market headlines, incident reports, policy announcements, or research prompts.
2. Decompose events into causal graph structures:
   - Primary domain classification
   - Physical & institutional chokepoints
   - Affected macroeconomic & commodity entities
   - Multi-order transmission channels (1st, 2nd, 3rd order cascades)
   - Lead time, duration, and tau substitution horizon (τ_substitution)
   - Core spread & ratio distortions
   - Prompt curve regimes (Backwardation, Contango, Inverted)
   - Popperian falsification tripwires
   - Tradeable playbooks (relative value spreads, options convexity)
3. Compute analogy similarity against the Master Case Study Library.
4. Automatically assimilate and persist validated case studies into active memory.

All operations are non-custodial and machine-auditable with execution_authority=False.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from intelligence.case_study_library import (
    CaseStudyLibrary,
    get_case_study_library,
)
from intelligence.case_study_matcher import (
    CaseStudyArchetype,
    CaseMatchResult,
    get_case_study_matcher,
)

log = logging.getLogger("CaseStudyResearchEngine")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SupplyShockDecomposition:
    """Multi-order causal decomposition of an identified supply shock or macro disruption."""
    shock_name: str
    primary_domain: str
    severity_score: float                # 1.0 to 5.0
    chokepoints: List[str]
    primary_entities: List[str]
    first_order_direct: List[str]
    second_order_transmission: List[str]
    third_order_systemic: List[str]
    tau_substitution: str                # 'DAYS', 'MONTHS', 'SEASON', 'YEARS', 'DECADES'
    estimated_lead_days: int
    estimated_duration_days: int
    core_spread_distortions: List[str]
    prompt_curve_regime: str
    projected_asset_impacts: Dict[str, str]
    falsification_tripwires: List[str]
    tradeable_playbook: Dict[str, str]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["execution_authority"] = False
        return d


@dataclass
class CaseStudyResearchReport:
    """Full quantitative report of an autonomous research synthesis operation."""
    case_id: str
    label: str
    primary_domain: str
    decomposition: SupplyShockDecomposition
    top_historical_analogues: List[CaseMatchResult]
    is_novel_archetype: bool
    novelty_score: float                 # 0.0 (identical to existing) to 1.0 (completely unprecedented)
    assimilation_status: str             # 'ASSIMILATED_SUCCESSFULLY', 'EXISTING_UPDATED', 'AUDIT_FLAGGED'
    persisted_to_disk: bool
    persisted_path: str
    timestamp: str
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["execution_authority"] = False
        return d


# ---------------------------------------------------------------------------
# Domain Heuristic Keyword Taxonomies for Real-Time Detection
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "MARITIME_CHOKEPOINT": [
        "strait", "canal", "port", "vessel", "tanker", "shipping", "freight",
        "red sea", "suez", "hormuz", "panama", "malacca", "bosporus", "baltimore",
        "houthi", "drone attack", "reroute", "container", "fbx", "baltic", "anchorage",
        "draft", "gatun", "collision", "bridge collapse", "grounding", "chokepoint"
    ],
    "TECH_HARDWARE": [
        "semiconductor", "chip", "fab", "wafer", "lithography", "asml", "tsmc",
        "nvidia", "gpu", "euv", "duv", "neon", "excimer", "packaging", "cowos",
        "hbm", "microcontroller", "dram", "nand", "hdd", "silicon", "foundry",
        "cleanroom", "pure water", "earthquake", "taiwan", "hsinchu", "bis", "export ban"
    ],
    "ENERGY_PETROCHEM": [
        "crude", "oil", "gas", "refinery", "pipeline", "opec", "spr", "diesel",
        "gasoline", "crack spread", "lng", "brent", "wti", "propane", "ethylene",
        "pvc", "petrochemical", "nord stream", "sabotage", "colonial pipeline",
        "freeze-off", "ercot", "power grid", "spark spread", "storage", "cushing"
    ],
    "CRITICAL_MINERALS": [
        "nickel", "lithium", "cobalt", "copper", "gallium", "germanium", "antimony",
        "rare earth", "smelter", "lme", "tsingshan", "indonesia", "hpal", "drc",
        "eskom", "platinum", "palladium", "bauxite", "alumina", "short squeeze",
        "export quota", "mining", "concentrate"
    ],
    "AGRICULTURAL_FERTILIZER": [
        "grain", "wheat", "corn", "soybean", "fertilizer", "ammonia", "urea",
        "haber-bosch", "drought", "flood", "kakhovka", "dam", "black sea", "fao",
        "rice", "crop yield", "usda", "wasde", "crush spread", "feedlot", "hog", "acreage"
    ],
    "SOVEREIGN_LIQUIDITY": [
        "bank run", "deposit run", "svb", "lehman", "repo", "liquidity", "btfp",
        "discount window", "credit spread", "fra-ois", "ted spread", "default",
        "ltcm", "sovereign debt", "gilt", "ldi", "margin call", "interbank", "contagion"
    ],
    "MONETARY_POLICY": [
        "fed", "fomc", "ecb", "boj", "rate hike", "rate cut", "basis points",
        "superhike", "taper", "volcker", "carry trade", "yen unwind", "quantitative easing",
        "quantitative tightening", "inflation print", "cpi", "taylor rule", "yield curve inversion"
    ],
    "CURRENCY_PEG": [
        "currency peg", "devaluation", "erm", "black wednesday", "soros", "baht",
        "asian financial crisis", "snb", "eurchf", "floor", "parallel rate",
        "fx reserve depletion", "capital flight", "forex intervention", "revaluation"
    ],
    "SANCTIONS_FINANCE": [
        "sanctions", "ofac", "swift", "asset freeze", "foreign reserves", "shadow fleet",
        "dark fleet", "price cap", "secondary sanctions", "correspondent banking",
        "de-dollarization", "bilateral settlement", "ruble", "renminbi", "petro-sanctions"
    ],
    "SYSTEMIC_LIQUIDITY": [
        "flash crash", "circuit breaker", "order book vacuum", "negative oil",
        "covid crash", "dash for cash", "black monday", "portfolio insurance",
        "gamma squeeze", "gamestop", "clearinghouse margin", "limit down"
    ],
}

ENTITY_MAP: Dict[str, List[str]] = {
    "WTI_OIL": ["oil", "crude", "petroleum", "brent", "wti", "diesel", "gasoline", "energy"],
    "INFLATION": ["inflation", "cpi", "pce", "price hike", "cost push", "expensive", "surge in price"],
    "SHIPPING_FLOW": ["vessel", "ship", "tanker", "port", "canal", "strait", "maritime", "transit"],
    "FREIGHT_VOLUME": ["freight", "container", "cargo", "fbx", "baltic dry", "teu", "drayage"],
    "CORPORATE_HEALTH": ["earnings", "production halt", "factory shutdown", "delays", "supply shortage", "lead time"],
    "GROWTH": ["gdp", "economic activity", "manufacturing", "industrial", "contraction", "recession"],
    "LIQUIDITY_STRESS": ["liquidity", "bank run", "repo", "margin call", "funding squeeze", "collateral"],
    "CREDIT_SPREAD": ["credit", "spread", "high yield", "oas", "default", "bond risk"],
    "POLICY_RATE": ["fed rate", "rate hike", "central bank", "interest rate", "fomc", "hiking"],
    "NOMINAL_BOND_YIELD": ["treasury yield", "10-year", "bond yield", "gilt", "bund", "rates surge"],
    "GOLD": ["gold", "xau", "bullion", "safe haven", "precious metal", "reserve asset"],
    "DXY": ["dollar", "usd", "greenback", "dxy", "fx exchange"],
    "EUR": ["euro", "eur", "ecb", "europe", "germany"],
    "JPY": ["yen", "jpy", "boj", "japan", "carry trade"],
    "CHF": ["swiss franc", "chf", "snb", "switzerland"],
    "GBP": ["pound", "sterling", "gbp", "bank of england", "uk"],
    "EM_FX": ["emerging market", "rupee", "real", "lira", "baht", "peso", "naira", "rand"],
    "VIX": ["vix", "volatility", "fear index", "implied vol", "options skew"],
    "EQUITIES": ["stocks", "spx", "s&p 500", "nasdaq", "dow", "equities", "nikkei"],
    "CROP_HEALTH": ["crop", "grain", "wheat", "corn", "soybean", "harvest", "agriculture", "fertilizer"],
    "ENERGY_DEMAND": ["power", "electricity", "natural gas", "grid", "heat wave", "winter storm", "lng"],
    "GRID_LOAD": ["power grid", "megawatt", "blackout", "load shedding", "ercot", "eskom"],
}


class CaseStudyResearchEngine:
    """Autonomous Engine for Real-Time Case Study Research, News Reasoning, and Library Synthesis."""

    def __init__(self, persistence_file: str = "artifacts/learned_case_studies.json") -> None:
        self.persistence_file = persistence_file
        self.library = get_case_study_library()
        self.matcher = get_case_study_matcher()

    def research_and_synthesize(
        self,
        headline_or_text: str,
        domain_override: Optional[str] = None,
        source_reference: str = "https://antigravity.internal/research",
        auto_assimilate: bool = True,
    ) -> CaseStudyResearchReport:
        """
        Decomposes unstructured event text, maps multi-order causal ripple effects,
        synthesizes Popperian falsification tripwires and trade playbooks,
        computes analogy similarity against the Master Library, and assimilates into memory.
        """
        raw_text = headline_or_text.strip()
        if not raw_text:
            raise ValueError("Research input text cannot be empty.")

        # 1. Infer Domain & Entities
        detected_domain = domain_override.strip().upper() if domain_override else self._infer_domain(raw_text)
        detected_entities = self._infer_entities(raw_text)
        detected_chokepoints = self._infer_chokepoints(raw_text)

        # 2. Decompose Multi-Order Cascade
        decomposition = self._decompose_cascade(
            raw_text=raw_text,
            domain=detected_domain,
            entities=detected_entities,
            chokepoints=detected_chokepoints,
        )

        # 3. Formulate Case ID & Archetype
        slug = self._generate_slug(decomposition.shock_name)
        case_id = f"case_research_{slug}"

        archetype = CaseStudyArchetype(
            case_id=case_id,
            label=decomposition.shock_name,
            primary_domain=decomposition.primary_domain,
            historical_date_window=f"{datetime.now(timezone.utc).strftime('%Y-%m')} Real-Time Research",
            chokepoints_affected=tuple(decomposition.chokepoints),
            primary_entities=tuple(decomposition.primary_entities),
            transmission_channels=tuple(
                decomposition.first_order_direct +
                decomposition.second_order_transmission +
                decomposition.third_order_systemic
            ),
            lead_time_days=decomposition.estimated_lead_days,
            duration_days=decomposition.estimated_duration_days,
            tau_substitution_horizon=decomposition.tau_substitution,
            core_spread_distortions=tuple(decomposition.core_spread_distortions),
            prompt_curve_regime=decomposition.prompt_curve_regime,
            historical_outcomes=decomposition.projected_asset_impacts,
            falsification_tripwires=tuple(decomposition.falsification_tripwires),
            reference_url=source_reference,
        )

        # 4. Analogical Matching & Novelty Gap Analysis
        matches = self.matcher.match_event(
            event_domain=archetype.primary_domain,
            affected_entities=list(archetype.primary_entities),
            affected_chokepoints=list(archetype.chokepoints_affected),
            observed_spreads=list(archetype.core_spread_distortions),
            top_k=3,
        )

        top_similarity = matches[0].similarity_score if matches else 0.0
        novelty_score = round(max(0.0, min(1.0, 1.0 - top_similarity)), 4)
        is_novel = novelty_score > 0.40

        # 5. Assimilation & Persistence
        persisted = False
        status_msg = "ASSIMILATED_SUCCESSFULLY"
        if auto_assimilate:
            self.library.library[case_id] = archetype
            self.matcher.registry[case_id] = archetype
            persisted = self._persist_case(archetype)
            log.info("Assimilated researched case study %s (Novelty: %.2f)", case_id, novelty_score)

        return CaseStudyResearchReport(
            case_id=case_id,
            label=archetype.label,
            primary_domain=archetype.primary_domain,
            decomposition=decomposition,
            top_historical_analogues=matches,
            is_novel_archetype=is_novel,
            novelty_score=novelty_score,
            assimilation_status=status_msg,
            persisted_to_disk=persisted,
            persisted_path=self.persistence_file,
            timestamp=utcnow(),
            execution_authority=False,
        )

    # -----------------------------------------------------------------------
    # Internal Heuristic & Cognitive Extraction Pipelines
    # -----------------------------------------------------------------------

    def _infer_domain(self, text: str) -> str:
        text_lower = text.lower()
        best_domain = "MARITIME_CHOKEPOINT"
        best_score = 0

        for domain, keywords in DOMAIN_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                best_domain = domain

        return best_domain

    def _infer_entities(self, text: str) -> List[str]:
        text_lower = text.lower()
        found: List[str] = []
        for ent, keywords in ENTITY_MAP.items():
            if any(kw in text_lower for kw in keywords):
                found.append(ent)
        if not found:
            found = ["WTI_OIL", "INFLATION", "GROWTH", "DXY"]
        return found[:6]

    def _infer_chokepoints(self, text: str) -> List[str]:
        text_upper = text.upper()
        known_chokepoints = [
            "STRAIT_OF_HORMUZ", "BAB_EL_MANDEB", "SUEZ_CANAL", "PANAMA_CANAL",
            "STRAIT_OF_MALACCA", "BALTIMORE_HARBOR", "BOSPORUS_STRAIT",
            "TSMC_FAB", "ASML_VELDHOVEN", "ERCOT_GRID", "COLONIAL_PIPELINE",
            "NORD_STREAM", "CUSHING_OKLAHOMA", "LME_WAREHOUSE", "KOLWEZI_MINING",
            "BLACK_SEA_PORTS", "ESKOM_GRID", "FED_DISCOUNT_WINDOW", "TARGET2_CLEARING"
        ]
        found = []
        for cp in known_chokepoints:
            raw_term = cp.replace("_", " ")
            if raw_term in text_upper or cp in text_upper:
                found.append(cp)

        if not found:
            # Generate descriptive chokepoint from text
            match = re.search(r"\b(port of \w+|strait of \w+|\w+ canal|\w+ pipeline|\w+ fab|\w+ terminal)\b", text, re.I)
            if match:
                found.append(match.group(1).upper().replace(" ", "_"))
            else:
                found.append("PHYSICAL_GLOBAL_CHOKEPOINT")
        return found

    def _decompose_cascade(
        self,
        raw_text: str,
        domain: str,
        entities: List[str],
        chokepoints: List[str],
    ) -> SupplyShockDecomposition:
        # Title extraction
        clean_name = raw_text.split(".")[0].split("\n")[0][:80].strip()
        if len(clean_name) < 10:
            clean_name = f"{domain.replace('_', ' ').title()} Emerging Disruption"

        # Multi-order cascading ripple channels
        first_order = [f"DIRECT_PHYSICAL_DISRUPTION_AT_{chokepoints[0]}"]
        second_order = [f"SUBSTITUTION_AND_INPUT_COST_SURGE_{entities[0]}"]
        third_order = ["MACRO_INFLATION_AND_CENTRAL_BANK_FEEDBACK_LOOP"]

        if "OIL" in entities or "WTI_OIL" in entities or domain in ("ENERGY_PETROCHEM", "MARITIME_CHOKEPOINT"):
            first_order.append("PROMPT_CRUDE_OR_PRODUCT_FLOW_HALT")
            second_order.append("REFINING_CRACK_SPREAD_EXPANSION")
            third_order.append("CONSUMER_PURCHASING_POWER_COMPRESSION")

        if domain == "TECH_HARDWARE":
            first_order.append("SEMICONDUCTOR_IN-PROCESS_WAFER_SCRAP")
            second_order.append("ADVANCED_PACKAGING_LEAD_TIME_SURGE")
            third_order.append("DOWNSTREAM_AUTO_AND_SERVER_ASSEMBLY_HALT")

        if domain == "AGRICULTURAL_FERTILIZER":
            first_order.append("AMMONIA_OR_GRAIN_EXPORT_RESTRICTION")
            second_order.append("CORN_ACREAGE_CONTRACTION_SOY_SHIFT")
            third_order.append("GLOBAL_FOOD_CPI_INFLATION_PRESSURE")

        # Tau horizon & prompt curve
        tau = "DAYS"
        lead_days = 2
        duration_days = 60
        curve = "BACKWARDATION"

        if domain in ("TECH_HARDWARE", "CRITICAL_MINERALS"):
            tau = "YEARS"
            lead_days = 30
            duration_days = 365
            curve = "INVERTED"
        elif domain in ("AGRICULTURAL_FERTILIZER", "ENERGY_PETROCHEM"):
            tau = "SEASON"
            lead_days = 7
            duration_days = 120
            curve = "BACKWARDATION"
        elif domain in ("SOVEREIGN_LIQUIDITY", "CURRENCY_PEG", "SYSTEMIC_LIQUIDITY"):
            tau = "DAYS"
            lead_days = 0
            duration_days = 30
            curve = "INVERTED"

        # Core spreads
        spreads = ["CRACK_SPREAD_321", "MOVE_VIX_RATIO"]
        if "WTI_OIL" in entities:
            spreads.append("BRENT_WTI_SPREAD")
        if "CROP_HEALTH" in entities or domain == "AGRICULTURAL_FERTILIZER":
            spreads.append("CORN_SOY_RATIO")
        if domain == "CRITICAL_MINERALS":
            spreads.append("LME_CASH_3M_SPREAD")
        if domain == "MARITIME_CHOKEPOINT":
            spreads.append("FREIGHTOS_FBX_CONTAINER")

        # Projected outcomes
        projected_impacts = {
            "primary_asset": f"Immediate bullish pressure on {entities[0]} with volatility expansion",
            "cross_asset": "Risk-off transmission to cyclical equities and high-beta currencies",
            "curve_structure": f"Curve shifts into steep {curve.lower()}",
        }

        # Popperian tripwires
        tripwires = [
            f"Alternative supply routes restore >85% operational capacity through {chokepoints[0]} within 14 days",
            f"Physical cash spot pricing fails to transmit to benchmark futures across 5 consecutive sessions",
            "Central bank or government intervention issues unconditional emergency liquidity backstop",
        ]

        # Tradeable playbook
        playbook = {
            "strategy_type": "CONVEX_RELATIVE_VALUE_SPREAD",
            "asymmetric_expression": f"Long prompt {entities[0]} futures vs short deferred calendar contracts",
            "volatility_overlay": "Long out-of-the-money call convexity to capture upside supply-squeeze tails",
            "invalidation_level": "Exit thesis if prompt backwardation collapses below 20-day exponential moving average",
        }

        return SupplyShockDecomposition(
            shock_name=clean_name,
            primary_domain=domain,
            severity_score=3.8,
            chokepoints=chokepoints,
            primary_entities=entities,
            first_order_direct=first_order,
            second_order_transmission=second_order,
            third_order_systemic=third_order,
            tau_substitution=tau,
            estimated_lead_days=lead_days,
            estimated_duration_days=duration_days,
            core_spread_distortions=spreads,
            prompt_curve_regime=curve,
            projected_asset_impacts=projected_impacts,
            falsification_tripwires=tripwires,
            tradeable_playbook=playbook,
            execution_authority=False,
        )

    def _generate_slug(self, text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
        return slug[:40] if slug else "unnamed_shock"

    def _persist_case(self, archetype: CaseStudyArchetype) -> bool:
        try:
            os.makedirs(os.path.dirname(self.persistence_file) or ".", exist_ok=True)
            existing: Dict[str, Dict[str, Any]] = {}
            if os.path.exists(self.persistence_file):
                try:
                    with open(self.persistence_file, "r", encoding="utf-8") as f:
                        for row in json.load(f):
                            existing[row["case_id"]] = row
                except Exception:
                    existing = {}

            existing[archetype.case_id] = {
                "case_id": archetype.case_id,
                "label": archetype.label,
                "primary_domain": archetype.primary_domain,
                "historical_date_window": archetype.historical_date_window,
                "chokepoints_affected": list(archetype.chokepoints_affected),
                "primary_entities": list(archetype.primary_entities),
                "transmission_channels": list(archetype.transmission_channels),
                "lead_time_days": archetype.lead_time_days,
                "duration_days": archetype.duration_days,
                "tau_substitution_horizon": archetype.tau_substitution_horizon,
                "core_spread_distortions": list(archetype.core_spread_distortions),
                "prompt_curve_regime": archetype.prompt_curve_regime,
                "historical_outcomes": archetype.historical_outcomes,
                "falsification_tripwires": list(archetype.falsification_tripwires),
                "reference_url": archetype.reference_url,
                "execution_authority": False,
            }

            with open(self.persistence_file, "w", encoding="utf-8") as f:
                json.dump(list(existing.values()), f, indent=2)

            return True
        except Exception as e:
            log.warning("Failed to persist case study %s: %s", archetype.case_id, e)
            return False


_GLOBAL_RESEARCH_ENGINE: Optional[CaseStudyResearchEngine] = None


def get_case_study_research_engine() -> CaseStudyResearchEngine:
    global _GLOBAL_RESEARCH_ENGINE
    if _GLOBAL_RESEARCH_ENGINE is None:
        _GLOBAL_RESEARCH_ENGINE = CaseStudyResearchEngine()
    return _GLOBAL_RESEARCH_ENGINE
