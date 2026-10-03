# Knowledge Graph Schema: Macro-Fundamental Causal Shocks

This document outlines the ontology, entities, relationships, and temporal data properties required to model cascading macro-economic narrative shocks using a Hybrid Agentic Causal Architecture.

---

## 🏗️ 1. Core Ontology (Node Schema)

```
       [Actor] ──(initiates / reacts)──> [Event]
          │                                │
          │ (affected by)                  │ (triggers cascade)
          ▼                                ▼
[Macro-Fundamental Variable] <──── [Narrative State]
```

### 👤 Entity Node: `Actor`
Represents the decision-making entities capable of initiating events, shifting narrative tracks, or reacting to market shocks.
*   **Properties:**
    *   `id`: `String` (e.g., `ACTOR_FED`, `ACTOR_OPEC`)
    *   `name`: `String` (e.g., `Federal Reserve`, `OPEC+`)
    *   `type`: `Enum[Sovereign, CentralBank, Corporate Sector, TransnationalBody, MarketAggregate]`
    *   `sentiment_baseline`: `Float [-1.0 to 1.0]` (Current systemic stance: hawkish/dovish, protective/aggressive)

### 📢 Entity Node: `Event`
Represents localized structural breaks, shocks, policy announcements, or physical disruptions.
*   **Properties:**
    *   `id`: `String` (e.g., `EVT_MALACCA_BLOCK_2026`)
    *   `description`: `Text` (e.g., `Geopolitical naval blockade of Malacca Strait`)
    *   `type`: `Enum[SupplyDisruption, GeopoliticalShock, PolicyIntervention, MarketPanic]`
    *   `timestamp`: `ISO8601 DateTime`
    *   `magnitude_raw`: `Float` (Initial calculated intensity of the shock)

### 📈 Entity Node: `Macro-Fundamental Variable`
The quantifiable economic indicators influenced by actors and cascading events.
*   **Properties:**
    *   `id`: `String` (e.g., `VAR_BRENT_CRUDE`, `VAR_US_CPI`)
    *   `ticker`: `String` (e.g., `BZ=F`, `CPIYOY`)
    *   `unit`: `Enum[USD_BBL, Percentage, IndexPoints]`
    *   `frequency`: `Enum[RealTime, Daily, Monthly]`

### 💬 Entity Node: `Narrative State`
The semantic wrapper tracking how the market contextualizes the unfolding shock over time.
*   **Properties:**
    *   `id`: `String` (e.g., `NAR_TRANSITORY_SHOCK`)
    *   `theme`: `String` (e.g., `Transitory Supply Bottleneck vs Structural Inflation`)
    *   `llm_veracity_score`: `Float [0.0 to 1.0]` (Agentic confidence based on news cross-validation)

---

## 🔗 2. Directed Relationships (Edge Schema)

| Source Node | Edge Type (Relationship) | Target Node | Edge Properties |
| :--- | :--- | :--- | :--- |
| `Event` | `CASCADES_TO` | `Event` | `transmission_delay_days (Int)`, `structural_weight (Float)` |
| `Event` | `SHOCKS` | `Macro-Fundamental Variable` | `elasticity_coefficient (Float)`, `direction (Enum[UP, DOWN])` |
| `Actor` | `TRIGGERS` | `Event` | `intentionality (Float)` |
| `Actor` | `INTERVENES_IN` | `Macro-Fundamental Variable` | `policy_tool_used (String)` |
| `Event` | `FUELS` | `Narrative State` | `virality_index (Float)` |
| `Narrative State` | `ALTERS_BEHAVIOR` | `Actor` | `risk_premium_multiplier (Float)` |

---

## 📈 3. Visual Graph Representation (Anatomy of a Shock Cascade)

<layout>
chartPlaceholder(chartDescription="A Directed Acyclic Graph (DAG) unrolled across temporal intervals showing an initial Supply Disruption Event at the Malacca Strait cascading into Actor reactions, Variable shocks, and a shifting Narrative State framework.", results=[])
</layout>

---

## ⏱️ 4. Dynamic Handling: Chronological Unrolling
To prevent circular deadlocks inside standard Causal DAGs (e.g., *Inflation $ightarrow$ Wage Growth $ightarrow$ Inflation*), edges must be mathematically split across discrete temporal slices ($t_n$):

```
Time t=0:  [EVT_MALACCA_BLOCK] ───(shocks)───> [VAR_BRENT_CRUDE_t0]
                                                     │
                                             (transmits to t=1)
                                                     ▼
Time t=1:  [ACTOR_OPEC_t1] <───(reacts)─── [VAR_BRENT_CRUDE_t1]
                 │
             (triggers)
                 ▼
Time t=1:  [EVT_SUPPLY_RATION] ──(shocks)─> [VAR_INPUT_COST_t1]
                                                     │
                                             (transmits to t=2)
                                                     ▼
Time t=2:                                  [VAR_US_CPI_t2]
```

---

## 🛠️ 5. Implementation Strategy: Triplet Extraction Formats
For your **Semantic Extractor** layer, the LLM should parse raw news into strict structural records matching the Knowledge Graph ontology.

### Example JSON Payload Output from Semantic Agent:
```json
{
  "extraction_metadata": {
    "source_url": "https://reuters.com/example-macro-shock",
    "ingestion_time": "2026-08-18T05:30:00Z"
  },
  "nodes": [
    { "id": "EVT_MALACCA_BLOCK_2026", "label": "Event", "properties": { "type": "SupplyDisruption", "magnitude_raw": 8.5 } },
    { "id": "VAR_BRENT_CRUDE", "label": "Macro-Fundamental Variable", "properties": { "ticker": "BZ=F", "unit": "USD_BBL" } },
    { "id": "NAR_ENERGY_CRISIS_2026", "label": "Narrative State", "properties": { "theme": "Structural Deglobalization", "llm_veracity_score": 0.89 } }
  ],
  "edges": [
    { "source": "EVT_MALACCA_BLOCK_2026", "type": "SHOCKS", "target": "VAR_BRENT_CRUDE", "properties": { "elasticity_coefficient": 1.45, "direction": "UP" } },
    { "source": "EVT_MALACCA_BLOCK_2026", "type": "FUELS", "target": "NAR_ENERGY_CRISIS_2026", "properties": { "virality_index": 0.92 } }
  ]
}
```
