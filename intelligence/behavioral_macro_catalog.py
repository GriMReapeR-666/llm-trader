"""Behavioral Finance, Socioeconomic Dynamics & AI Paradigm Knowledge Engine.

Expands the Macro Causal Knowledge Graph with:
1. Behavioral Finance & PsyOps Phenomena:
   - Retail FOMO, Capitulation, Narrative Propaganda / PsyOps, Reflexivity Loops,
     Herd Behavior, Disposition Effect, Short Squeeze Cascades, Liquidity Hunts.
2. Socioeconomic & Class Dynamics (Capital vs. Labor / Rich vs. Poor):
   - Cantillon Effect, Wealth Inequality Skew, Real Wage Compression,
     Consumer Debt Stress, Capital Flight Havens, Labor Strikes.
3. AI Industrial Revolution & Tech Paradigm:
   - AI Capex Supercycle, AI Productivity Dividend, AI Labor Displacement,
     Mega-Cap Tech Monopolies, Power Grid Constraints.
4. Academic & Complex Adaptive Systems Formulations:
   - Minsky Financial Instability Moments, Keynesian Beauty Contests,
     Prospect Theory Asymmetries, Cascading Contagion Risks.

All edges are typed, directional, and machine-auditable with execution_authority=False.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("BehavioralMacroCatalog")

BEHAVIORAL_CATALOG_VERSION = "2.0.0"
EXECUTION_AUTHORITY = False

# ---------------------------------------------------------------------------
# Entity Types
# ---------------------------------------------------------------------------
BEHAVIORAL_ENTITY_TYPES = (
    "BEHAVIORAL_PSYCH",
    "SOCIOECONOMIC",
    "AI_TECHNOLOGY",
    "ACADEMIC_PARADIGM",
    "MARKET_MICROSTRUCTURE",
    "POLICY_TRANSMISSION",
)

# ---------------------------------------------------------------------------
# Typed Entities
# ---------------------------------------------------------------------------
BEHAVIORAL_ENTITIES: List[Dict[str, Any]] = [
    # 1. Behavioral & Psychological Phenomena
    {"name": "RETAIL_FOMO", "entity_type": "BEHAVIORAL_PSYCH", "layer": 3, "aliases": ["EUPHORIA", "SPECULATIVE_MANIA", "BUYING_CLIMAX"]},
    {"name": "MARKET_CAPITULATION", "entity_type": "BEHAVIORAL_PSYCH", "layer": 3, "aliases": ["PANIC_SELLING", "FORCED_LIQUIDATION", "MARGIN_CALL_CASCADE"]},
    {"name": "NARRATIVE_PROPAGANDA", "entity_type": "BEHAVIORAL_PSYCH", "layer": 3, "aliases": ["PSYOPS", "MEDIA_SPIN", "DISINFORMATION", "SENTIMENT_MANIPULATION"]},
    {"name": "HERD_BEHAVIOR", "entity_type": "BEHAVIORAL_PSYCH", "layer": 3, "aliases": ["CROWDED_TRADE", "MOMENTUM_STAMPEDE", "CONSENSUS_BIAS"]},
    {"name": "REFLEXIVITY_LOOP", "entity_type": "BEHAVIORAL_PSYCH", "layer": 3, "aliases": ["SOROS_REFLEXIVITY", "FEEDBACK_LOOP", "SELF_FULFILLING_PROPHECY"]},
    {"name": "DISPOSITION_EFFECT", "entity_type": "BEHAVIORAL_PSYCH", "layer": 3, "aliases": ["LOSS_AVERSION", "BAGHOLDING", "PROFIT_CLIPPING"]},
    {"name": "SHORT_SQUEEZE_CASCADE", "entity_type": "MARKET_MICROSTRUCTURE", "layer": 3, "aliases": ["GAMMA_SQUEEZE", "BORROW_EXHAUSTION", "BEAR_TRAP"]},
    {"name": "LIQUIDITY_HUNT", "entity_type": "MARKET_MICROSTRUCTURE", "layer": 3, "aliases": ["STOP_RUN", "ALGO_SWEEP", "FALSE_BREAKOUT"]},

    # 2. Socioeconomic Class & Wealth Dynamics (Capital vs Labor)
    {"name": "CANTILLON_EFFECT", "entity_type": "SOCIOECONOMIC", "layer": 1, "aliases": ["FIRST_RECEIVER_ADVANTAGE", "MONETARY_DISTORTION"]},
    {"name": "WEALTH_INEQUALITY_SKEW", "entity_type": "SOCIOECONOMIC", "layer": 2, "aliases": ["TOP_TEN_PERCENT_WEALTH", "GINI_EXPANSION"]},
    {"name": "REAL_WAGE_COMPRESSION", "entity_type": "SOCIOECONOMIC", "layer": 2, "aliases": ["INFLATION_TAX", "PURCHASING_POWER_COLLAPSE", "WAGE_STAGNATION"]},
    {"name": "CONSUMER_DEBT_STRESS", "entity_type": "SOCIOECONOMIC", "layer": 2, "aliases": ["CREDIT_CARD_DELINQUENCY", "AUTO_LOAN_DEFAULT", "BNPL_STRAIN"]},
    {"name": "CAPITAL_FLIGHT_HAVEN", "entity_type": "SOCIOECONOMIC", "layer": 3, "aliases": ["HNW_OFFSHORING", "SOVEREIGN_WEALTH_ALLOCATION", "ASSET_PROTECTION"]},
    {"name": "LABOR_DISPUTES_STRIKES", "entity_type": "SOCIOECONOMIC", "layer": 2, "aliases": ["UNION_STRIKES", "WAGE_PRESSURE_PUSH", "SUPPLY_CHAIN_BOTTLENECK"]},

    # 3. AI Industrial Revolution & Tech Paradigms
    {"name": "AI_CAPEX_SUPERCYCLE", "entity_type": "AI_TECHNOLOGY", "layer": 1, "aliases": ["GPU_INFRASTRUCTURE", "DATA_CENTER_BUILD", "COMPUTE_INVESTMENT"]},
    {"name": "AI_PRODUCTIVITY_DIVIDEND", "entity_type": "AI_TECHNOLOGY", "layer": 2, "aliases": ["CORPORATE_AUTOMATION", "MARGIN_EXPANSION", "SGA_EFFICIENCY"]},
    {"name": "AI_LABOR_DISPLACEMENT", "entity_type": "AI_TECHNOLOGY", "layer": 2, "aliases": ["WHITE_COLLAR_AUTOMATION", "STRUCTURAL_UNEMPLOYMENT"]},
    {"name": "MEGA_CAP_TECH_MONOPOLY", "entity_type": "AI_TECHNOLOGY", "layer": 4, "aliases": ["MAG_SEVEN", "INDEX_CONCENTRATION", "HYPERSCALER_DOMINANCE"]},
    {"name": "POWER_GRID_CONSTRAINTS", "entity_type": "AI_TECHNOLOGY", "layer": 2, "aliases": ["ENERGY_BOTTLENECK", "NUCLEAR_POWER_DEMAND", "UTILITY_LOAD_SURGE"]},

    # 4. Academic & Complex Adaptive Systems Formulations
    {"name": "MINSKY_INSTABILITY_MOMENT", "entity_type": "ACADEMIC_PARADIGM", "layer": 3, "aliases": ["PONZI_FINANCE_COLLAPSE", "STABILITY_BREEDS_INSTABILITY"]},
    {"name": "KEYNESIAN_BEAUTY_CONTEST", "entity_type": "ACADEMIC_PARADIGM", "layer": 3, "aliases": ["SECOND_ORDER_EXPECTATIONS", "NARRATIVE_MIMICRY"]},
    {"name": "PROSPECT_THEORY_ASYMMETRY", "entity_type": "ACADEMIC_PARADIGM", "layer": 3, "aliases": ["KAHNEMAN_TVERSKY_BIAS", "ASYMMETRIC_LOSS_PAIN"]},
    {"name": "CASCADE_CONTAGION_RISK", "entity_type": "ACADEMIC_PARADIGM", "layer": 3, "aliases": ["DOMINO_DEFAULT", "INTERBANK_COUNTERPARTY_STRESS"]},
]

# ---------------------------------------------------------------------------
# Causal Directed Edges & Transmission Mechanisms
# ---------------------------------------------------------------------------
BEHAVIORAL_CAUSAL_EDGES: List[Dict[str, Any]] = [
    # Cantillon Effect & Wealth Inequality Dynamics
    {
        "source_name": "QE_QT",
        "target_name": "CANTILLON_EFFECT",
        "relation": "TRIGGERS",
        "sign": "POSITIVE",
        "confidence": 0.88,
        "mechanism": "Central bank liquidity injections reach financial asset holders and institutional prime brokers first, inflating capital assets before wages adjust.",
        "channel": "LIQUIDITY_TRANSMISSION",
        "execution_authority": False,
    },
    {
        "source_name": "CANTILLON_EFFECT",
        "target_name": "WEALTH_INEQUALITY_SKEW",
        "relation": "EXPANDS",
        "sign": "POSITIVE",
        "confidence": 0.85,
        "mechanism": "Asset prices surge faster than real median earnings, widening the wealth gap and concentrating purchasing power in the top decile.",
        "channel": "SOCIOECONOMIC_FLOW",
        "execution_authority": False,
    },
    {
        "source_name": "INFLATION",
        "target_name": "REAL_WAGE_COMPRESSION",
        "relation": "DRIVES",
        "sign": "POSITIVE",
        "confidence": 0.90,
        "mechanism": "High headline inflation acts as a regressive tax, severely shrinking the disposable income and savings cushion of low-to-middle income households.",
        "channel": "REAL_ECONOMY_FLOW",
        "execution_authority": False,
    },
    {
        "source_name": "REAL_WAGE_COMPRESSION",
        "target_name": "CONSUMER_DEBT_STRESS",
        "relation": "ACCELERATES",
        "sign": "POSITIVE",
        "confidence": 0.84,
        "mechanism": "Compressed households rely on revolving credit cards, personal loans, and BNPL to sustain living standards, spiking delinquency rates.",
        "channel": "CREDIT_TRANSMISSION",
        "execution_authority": False,
    },
    {
        "source_name": "CONSUMER_DEBT_STRESS",
        "target_name": "AGGREGATE_DEMAND",
        "relation": "SUPPRESSES",
        "sign": "NEGATIVE",
        "confidence": 0.82,
        "mechanism": "Rising debt servicing burdens force consumption curtailment, dragging down broad consumer discretionary retail spending.",
        "channel": "REAL_ECONOMY_FLOW",
        "execution_authority": False,
    },
    {
        "source_name": "WEALTH_INEQUALITY_SKEW",
        "target_name": "CAPITAL_FLIGHT_HAVEN",
        "relation": "STIMULATES",
        "sign": "POSITIVE",
        "confidence": 0.80,
        "mechanism": "Concentrated high-net-worth capital seeks jurisdictional diversification, hoarding physical gold, Swiss Francs, and defensive sovereign debt.",
        "channel": "CAPITAL_FLOW",
        "execution_authority": False,
    },
    {
        "source_name": "CAPITAL_FLIGHT_HAVEN",
        "target_name": "GOLD",
        "relation": "SAFE_HAVEN_BID",
        "sign": "POSITIVE",
        "confidence": 0.85,
        "mechanism": "Global ultra-wealthy capital reallocations create structural, price-inelastic physical gold demand.",
        "channel": "COMMODITY_DEMAND",
        "execution_authority": False,
    },

    # Behavioral Psychology & PsyOps Dynamics
    {
        "source_name": "NARRATIVE_PROPAGANDA",
        "target_name": "RETAIL_FOMO",
        "relation": "INDUCES",
        "sign": "POSITIVE",
        "confidence": 0.78,
        "mechanism": "Coordinated financial media narratives and influencer hype cycles generate viral fear of missing out, driving retail buying at structural market tops.",
        "channel": "SENTIMENT_PROPAGATION",
        "execution_authority": False,
    },
    {
        "source_name": "RETAIL_FOMO",
        "target_name": "SHORT_SQUEEZE_CASCADE",
        "relation": "IGNITES",
        "sign": "POSITIVE",
        "confidence": 0.75,
        "mechanism": "Aggressive retail call buying forces institutional option dealers to hedge by aggressively buying underlying shares, triggering explosive gamma squeezes.",
        "channel": "MICROSTRUCTURE_FLOW",
        "execution_authority": False,
    },
    {
        "source_name": "REFLEXIVITY_LOOP",
        "target_name": "EQUITIES",
        "relation": "AMPLIFIES",
        "sign": "POSITIVE",
        "confidence": 0.80,
        "mechanism": "Rising stock prices lower corporate cost of capital, allowing share buybacks and acquisitions that temporarily validate elevated valuations.",
        "channel": "FINANCIAL_FEEDBACK",
        "execution_authority": False,
    },
    {
        "source_name": "LIQUIDITY_HUNT",
        "target_name": "MARKET_CAPITULATION",
        "relation": "TRIGGERS",
        "sign": "POSITIVE",
        "confidence": 0.82,
        "mechanism": "Institutional smart money sweeps stops resting beyond obvious swing highs/lows, triggering cascading retail margin calls before reversing price.",
        "channel": "MICROSTRUCTURE_FLOW",
        "execution_authority": False,
    },
    {
        "source_name": "MARKET_CAPITULATION",
        "target_name": "VIX",
        "relation": "SPIKES",
        "sign": "POSITIVE",
        "confidence": 0.89,
        "mechanism": "Forced institutional deleveraging and panic puts bidding drive severe volatility spikes (VIX expansion).",
        "channel": "VOLATILITY_FLOW",
        "execution_authority": False,
    },

    # AI Paradigm & Industrial Revolution Dynamics
    {
        "source_name": "AI_CAPEX_SUPERCYCLE",
        "target_name": "POWER_GRID_CONSTRAINTS",
        "relation": "CREATES",
        "sign": "POSITIVE",
        "confidence": 0.91,
        "mechanism": "Massive exponential data center expansion drives unprecedented electricity load requirements, straining legacy utility grids and boosting natural gas/nuclear demand.",
        "channel": "ENERGY_DEMAND",
        "execution_authority": False,
    },
    {
        "source_name": "AI_CAPEX_SUPERCYCLE",
        "target_name": "MEGA_CAP_TECH_MONOPOLY",
        "relation": "CONCENTRATES",
        "sign": "POSITIVE",
        "confidence": 0.88,
        "mechanism": "Only hyperscalers with massive operating cash flow can afford frontier compute infrastructure, reinforcing structural market capitalization monopolies.",
        "channel": "EQUITY_STRUCTURE",
        "execution_authority": False,
    },
    {
        "source_name": "AI_PRODUCTIVITY_DIVIDEND",
        "target_name": "EQUITIES",
        "relation": "EXPANDS_MARGINS",
        "sign": "POSITIVE",
        "confidence": 0.79,
        "mechanism": "Enterprise AI adoption reduces labor overhead and accelerates software execution, expanding corporate operating margins.",
        "channel": "CORPORATE_EARNINGS",
        "execution_authority": False,
    },
    {
        "source_name": "AI_LABOR_DISPLACEMENT",
        "target_name": "EMPLOYMENT",
        "relation": "DAMPENS",
        "sign": "NEGATIVE",
        "confidence": 0.74,
        "mechanism": "Rapid generative automation of white-collar cognitive tasks creates structural employment friction and hiring slowdowns in services sectors.",
        "channel": "LABOR_MARKET",
        "execution_authority": False,
    },

    # Academic & Complex Systems Phenomena
    {
        "source_name": "MINSKY_INSTABILITY_MOMENT",
        "target_name": "CASCADE_CONTAGION_RISK",
        "relation": "UNLEASHES",
        "sign": "POSITIVE",
        "confidence": 0.86,
        "mechanism": "Prolonged low volatility breeds reckless speculative leverage (Ponzi finance); a sudden rate shock converts tranquil markets into liquidity freezes.",
        "channel": "SYSTEMIC_RISK",
        "execution_authority": False,
    },
    {
        "source_name": "CASCADE_CONTAGION_RISK",
        "target_name": "CREDIT_SPREAD",
        "relation": "WIDENS",
        "sign": "POSITIVE",
        "confidence": 0.90,
        "mechanism": "Counterparty uncertainty causes interbank credit spreads (HY OAS, FRA-OIS) to blowout as liquidity dries up.",
        "channel": "CREDIT_TRANSMISSION",
        "execution_authority": False,
    },
    {
        "source_name": "KEYNESIAN_BEAUTY_CONTEST",
        "target_name": "HERD_BEHAVIOR",
        "relation": "REINFORCES",
        "sign": "POSITIVE",
        "confidence": 0.83,
        "mechanism": "Market participants price assets based on what they anticipate other participants will think the consensus opinion is, forming powerful self-fulfilling trends.",
        "channel": "BEHAVIORAL_REASONING",
        "execution_authority": False,
    },
]


def get_behavioral_entities() -> List[Dict[str, Any]]:
    """Return all behavioral, socioeconomic, and AI entities."""
    return list(BEHAVIORAL_ENTITIES)


def get_behavioral_edges() -> List[Dict[str, Any]]:
    """Return all behavioral causal edges."""
    return list(BEHAVIORAL_CAUSAL_EDGES)


def seed_behavioral_dynamics_into_graph(graph: Any) -> Dict[str, Any]:
    """Seed behavioral, socioeconomic, and tech paradigm nodes into SQLite MacroGraph."""
    entities_seeded = 0
    edges_seeded = 0

    with graph._con() as con:
        # 1. Insert entities
        for ent in BEHAVIORAL_ENTITIES:
            con.execute(
                """INSERT OR IGNORE INTO macro_entities(name, entity_type, created_at)
                   VALUES (?, ?, datetime('now'))""",
                (ent["name"], ent["entity_type"])
            )
            entities_seeded += 1

        # Build name -> entity_id map
        rows = con.execute("SELECT entity_id, name FROM macro_entities").fetchall()
        id_map = {str(r["name"]).upper(): r["entity_id"] for r in rows}

        # 2. Insert sources if needed
        con.execute(
            """INSERT OR IGNORE INTO macro_sources(
               source_id, captured_at, title, source_url, publisher, content_hash, version, payload
            ) VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)""",
            (
                "src_behavioral_v2",
                "Behavioral Macro & Socioeconomic Dynamics Source of Truth",
                "https://www.nber.org/papers",
                "National Bureau of Economic Research / Behavioral Economics",
                "sha256_behavioral_catalog_v2",
                BEHAVIORAL_CATALOG_VERSION,
                "{}",
            )
        )

        # 3. Insert directed edges
        for edge in BEHAVIORAL_CAUSAL_EDGES:
            src_id = id_map.get(str(edge["source_name"]).upper())
            tgt_id = id_map.get(str(edge["target_name"]).upper())
            if not src_id or not tgt_id:
                continue

            con.execute(
                """INSERT OR IGNORE INTO macro_edges(
                   created_at, updated_at, source_entity, target_entity,
                   relation, sign, confidence, source_id, status, notes
                ) VALUES (
                   datetime('now'), datetime('now'), ?, ?, ?, ?, ?, ?, 'MEASURED', ?
                )""",
                (
                    src_id,
                    tgt_id,
                    edge["relation"],
                    edge["sign"],
                    float(edge["confidence"]),
                    "src_behavioral_v2",
                    edge.get("mechanism", "Behavioral transmission mechanism"),
                )
            )
            edges_seeded += 1

    return {
        "status": "SUCCESS",
        "entities_seeded": entities_seeded,
        "edges_seeded": edges_seeded,
        "version": BEHAVIORAL_CATALOG_VERSION,
    }
