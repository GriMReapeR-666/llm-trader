"""Universal Analytical Frameworks & Epistemology Suite (Volumes 01 to 07).

Exports:
- V01_GEOPOLITICAL_ENGINE: Geopolitical & Macro-Commodity Cross-Market Engine
- V02_QUANT_CHAOS_ENGINE: Quantitative, Statistical, Frequency & Chaos Engine
- V03_ECONOMIC_IO_ENGINE: Economic Systems, Input-Output & Feedback Dynamics Engine
- V04_STRATEGIC_ENGINE: Strategic Intelligence, Adversarial Modeling & Failure Forensics Engine
- V05_BEHAVIORAL_ENGINE: Behavioral, Cognitive & Socioeconomic Analysis Engine
- V06_EPISTEMOLOGICAL_ENGINE: Epistemology, Ontology & 5-Layer Filter Stacking Engine
- V07_SPECIALIZED_ENGINE: Specialized Domain & Hyper-Niche Analytical Engine
- GLOBAL_ANALYTICAL_REGISTRY: Master registry of all modules and tools
"""
from __future__ import annotations

from intelligence.analytical_frameworks.base_framework import (
    BaseAnalyticalModule,
    GLOBAL_ANALYTICAL_REGISTRY,
    analytical_tool,
    UniversalToolDefinition,
)
from intelligence.analytical_frameworks.v01_geopolitical_commodity_engine import (
    GeopoliticalCommodityEngine,
    V01_GEOPOLITICAL_ENGINE,
)
from intelligence.analytical_frameworks.v02_quantitative_chaos_engine import (
    QuantitativeChaosEngine,
    V02_QUANT_CHAOS_ENGINE,
)
from intelligence.analytical_frameworks.v03_economic_input_output_engine import (
    EconomicInputOutputEngine,
    V03_ECONOMIC_IO_ENGINE,
)
from intelligence.analytical_frameworks.v04_strategic_adversarial_engine import (
    StrategicAdversarialEngine,
    V04_STRATEGIC_ENGINE,
)
from intelligence.analytical_frameworks.v05_behavioral_cognitive_engine import (
    BehavioralCognitiveEngine,
    V05_BEHAVIORAL_ENGINE,
)
from intelligence.analytical_frameworks.v06_epistemological_filter_engine import (
    EpistemologicalFilterEngine,
    V06_EPISTEMOLOGICAL_ENGINE,
)
from intelligence.analytical_frameworks.v07_specialized_niche_engine import (
    SpecializedNicheEngine,
    V07_SPECIALIZED_ENGINE,
)

ALL_ANALYTICAL_ENGINES = [
    V01_GEOPOLITICAL_ENGINE,
    V02_QUANT_CHAOS_ENGINE,
    V03_ECONOMIC_IO_ENGINE,
    V04_STRATEGIC_ENGINE,
    V05_BEHAVIORAL_ENGINE,
    V06_EPISTEMOLOGICAL_ENGINE,
    V07_SPECIALIZED_ENGINE,
]

__all__ = [
    "BaseAnalyticalModule",
    "GLOBAL_ANALYTICAL_REGISTRY",
    "analytical_tool",
    "UniversalToolDefinition",
    "GeopoliticalCommodityEngine",
    "V01_GEOPOLITICAL_ENGINE",
    "QuantitativeChaosEngine",
    "V02_QUANT_CHAOS_ENGINE",
    "EconomicInputOutputEngine",
    "V03_ECONOMIC_IO_ENGINE",
    "StrategicAdversarialEngine",
    "V04_STRATEGIC_ENGINE",
    "BehavioralCognitiveEngine",
    "V05_BEHAVIORAL_ENGINE",
    "EpistemologicalFilterEngine",
    "V06_EPISTEMOLOGICAL_ENGINE",
    "SpecializedNicheEngine",
    "V07_SPECIALIZED_ENGINE",
    "ALL_ANALYTICAL_ENGINES",
]
