# Economic Dynamics — Source of Truth & Integration Design

**Canonical machine SoT:** `economic_dynamics_catalog.py`  
**Narrative dump (raw):** `economic dynamics.txt` (~8770 lines of research chat)  
**Catalog version:** `1.0.0`  
**Rule:** every relationship is a *hypothesis*. `execution_authority` is always `False` until RiskController / live gates say otherwise.

---

## 1. What the raw document contains (organized)

| Block in `economic dynamics.txt` | Canonical home in this project |
|---|---|
| CB money S&D + contractionary/expansionary loops | `CAUSAL_EDGES` channels `interest_rate`, `demand`, `liquidity` |
| Gold ↔ DXY ↔ rates ↔ opportunity cost | edges + contradiction group `gold_dxy_safehaven` |
| Forex / commodity / USOIL causal chains | entities + `terms_of_trade`, `fx_pricing`, `petrodollar` |
| Dynamic correlation states (normal / crisis / stagflation…) | `CORRELATION_REGIMES` + `intermarket_regime.py` |
| CB meeting / options / bond transmission | `EVENT_TEMPLATES` + existing `event_policy.py` |
| Long-term yields ↔ growth duality | contradiction group `yield_growth_duality` |
| Universal event graph (war → tweet → contagion) | `EVENT_CLASS` / `GEOPOLITICAL` entities + lag metadata |
| Free-data stack (FRED / Yahoo / calendar) | `FREE_DATA_BINDINGS` → `openbb_data_bridge` / MT5 / Yahoo |
| 2016–2026 shock timeline | `DECADE_SHOCK_TEMPLATES` → `historical_regimes.py` |
| Self-learning “brain” pseudocode | **Do not port blindly** — map to existing learning loop (see §4) |

The chat dump is **not** executable truth. The catalog is.

---

## 2. Layered architecture (efficient across complexity)

```
T0 CORE (always on, cheap)
  economic_dynamics_catalog
        │ seed (CANDIDATE only)
        ▼
  macro_graph  ←── macro_source_policy (PRIMARY / RUNTIME_FEED)
        │
        ├── event_policy (blackout windows)
        └── mt5_trader TOON: macro_causal_context

T1 REGIME (medium)
  correlation_risk ──► intermarket_regime.classify
        │
        ▼
  filter CONDITIONAL / active_regimes edges
  lag_delay_engine (staleness / half-life)

T2 VALIDATE (medium-high)
  autonomous_causal_engine + edge_lifecycle + broker_replay
  promote CANDIDATE → STAT_PASS only with samples

T3 OPPORTUNITY (medium, advisory)
  causal_setup_map → opportunity_bus / scanner
  never creates order intent

T4 LEARN (high, research)
  reward_ledger / feature_builder / adaptive_research_service
  outcome feedback revises edges back to CANDIDATE
```

**Efficiency principle:** run T0 every cycle; T1 on completed bars; T2 on schedule; T3 only for approved/stat-pass edges; T4 offline/research.

---

## 3. Component integration map

| Project module | Role with Economic Dynamics |
|---|---|
| `macro_graph.py` | Stores seeded entities/edges, event reactions, history |
| `economic_dynamics_seed.py` | Idempotent catalog → CANDIDATE edge load |
| `intermarket_regime.py` | Live regime label from pair correlations |
| `causal_setup_map.py` | Relation → registered setup (advisory) |
| `event_policy.py` | Pre/post blackout for CPI/NFP/FX currencies |
| `lag_delay_engine.py` | Uses edge lag/half-life metadata in notes |
| `correlation_risk.py` | Supplies rolling corrs to regime classifier |
| `autonomous_causal_engine.py` | Empirically validates catalog priors |
| `historical_regimes.py` | Merges decade shock templates |
| `opportunity_bus.py` | Future: candidates from approved macro setups |
| `openbb_data_bridge.py` / Yahoo / MT5 | Free-data bindings for entities |
| `risk_manager.py` / execution path | Unchanged — still sole order authority |

---

## 4. Best approach (what to adopt vs reject from the dump)

### Adopt
- Typed entities + signed causal edges with confidence and lag.
- Regime-conditional signs (Gold↔DXY inverse **breaks** in crisis).
- Event playbooks as *watchlists + questions*, not auto-trades.
- Free-data bindings aligned to FRED / calendar / broker quotes.
- Decade shock templates for research ingestion.

### Reject / do not port as-is
- RandomForest “GlobalOpportunitySystem” as live signal authority.
- Hard-coded “buy DXY within 1h of tweet” style tips.
- Options matrices as execution recipes without broker replay proof.
- Any path that skips `execution_authority=False` lock.
- Unverified blog/news domains as causal edge sources.

### Adapt
- Document’s “self-learning brain” → already covered by `edge_lifecycle`, trade-outcome feedback on `macro_graph`, `reward_ledger`, `adaptive_research_service`.
- Document’s “war room dashboard” → GUI Brain / News views consume `macro_causal_context` + regime label (read-only).

---

## 5. Market dynamic relationships covered

**Policy core:** Policy rate → borrowing cost → demand → inflation/growth; QE/QT → liquidity → yields/DXY.

**FX–commodity nexus:** DXY ↔ Gold (inverse / crisis break); DXY ↔ Oil; Oil → CAD; Gold → AUD; petrodollar link marked **CONDITIONAL** (US net-exporter regime).

**Risk hierarchy:** Uncertainty → VIX → risk sentiment → equities / JPY / CHF / AUD / NZD / EM FX; credit spreads widen with vol.

**Yield–growth:** Slope predicts growth; level can **constrain** or **reflect optimism** (dual edges + contradiction group).

**Events:** CB meeting, CPI, NFP, war, trade war, political shock, banking stress → first/second order watch lists.

**Feedback:** Inflation/employment/growth → policy; extreme VIX → conditional emergency easing.

---

## 6. Operational runbook

```bash
# 1) Integrity
python -c "from economic_dynamics_catalog import validate_catalog, catalog_summary; print(validate_catalog()); print(catalog_summary())"

# 2) Seed into local MacroGraph (CANDIDATE only)
python economic_dynamics_seed.py --db trading_state.db

# 3) Classify a live regime from correlations (example)
python -c "from intermarket_regime import classify_intermarket_regime; print(classify_intermarket_regime({'DXY|GOLD': 0.6, 'VIX|EQUITIES': -0.8, 'GOLD|EQUITIES': -0.5}))"
```

Then let `AutonomousCausalValidator` / lifecycle promote edges with enough `event_reactions` samples. Do **not** bulk-approve.

---

## 7. Implementation phases

1. **Done:** catalog SoT, seed tool, regime classifier, setup-map extensions, decade regimes merge, tests, design doc.
2. **Done:** `intermarket_regime` wired into `mt5_trader` TOON (`intermarket_regime` + `macro_causal_context_regime`) using `correlation_risk` pair correlations; proxy OHLC capture is rate-limited and fail-closed.
3. **Done:** `approved_edges` includes `AUTO_APPROVED`; startup + daily catalog seed; 15m `causal_edge_validate` schedule; contradiction-group resolution; event playbooks in TOON; journal-scoped macro/correlation DB path.
4. **Done:** opportunity_bus emitter from `STAT_PASS` / approved tradable macro edges (`publish_from_stat_pass_edges`, MacroAnalyst contract).
5. **Done:** catalog measurement pass — mirror runtime surprises onto seed source + scheduled `catalog_measurement_pass` / auto-validate.
6. **Done:** historical regime observation batches + broker-cost replay (`historical_regime_replay.py`, daily research snapshot schedule).
7. **Later:** GUI Brain view surfaces regime + top approved edges.

### Verified gap closures
| Gap | Fix |
|---|---|
| Auto-validated edges invisible to TOON | `macro_graph.approved_edges` includes `AUTO_APPROVED` |
| Catalog never loaded unless manual CLI | Seed on orchestrator init + daily schedule |
| No scheduled validation pass | `causal_edge_validate` every 900s |
| Macro/correlation DB split from journal | Both use `store.path` |
| Crisis vs inverse gold-DXY both shown | Contradiction-group resolver |
| UNKNOWN regime over-included edges | Keep ALL-tagged only until classified |
| Event playbooks unused | `event_playbooks` on TOON + lite prompt |
| Setup-map “holes” for transmission nodes | Explicit research-transmission (not tradable) |
| Regime pairs missing yields | REAL_YIELD / YIELD_CURVE_SLOPE proxies |

---

## 9. Realtime news extraction (governed, CANDIDATE only)

| Source | Module | Role |
|---|---|---|
| MT5 / Exness calendar | `collect_calendar_event_rows` | **Only** path that schedules event-reaction measurement + blackout |
| FRED / NewsAPI / SA | `data_source_pipeline.py` | Observations + candidate `macro_sources` |
| Reuters / Fed RSS | `realtime_news_ingest.py` | Allow-listed RSS → candidate sources |
| NewsAPI + Seeking Alpha | `realtime_news_ingest.py` | Normalized headlines → candidate sources |
| LLM claim extraction | `macro_extraction.py` | CANDIDATE edges/facts when `TRADING_REALTIME_NEWS_EXTRACT=true` |

**Doctrine**
- Live news expands **candidate graph evidence** and **watch entities** (`macro_entities_extra`).
- Measured market effects still require **calendar reaction floors** + edge lifecycle promotion.
- Twitter / Truth Social remain **UNVERIFIED** in `macro_source_policy.py` (no auto causal promotion).
- Exness is covered via **MT5 calendar**, not a separate article scraper.
- Paid Bloomberg terminal feeds are out of scope.

**Operator env**
- `TRADING_REALTIME_NEWS_EXTRACT=true` — optional LLM extraction on `event_monitor` pulse only (still CANDIDATE).

---

## 10. Non-goals

- Replacing RiskController / order path.
- Treating chat narratives as measured edge evidence.
- Guaranteeing P&L from catalog priors.
- Tweet autopilot or “buy within 1h of tweet” execution recipes.
- Exness article scraping beyond broker/MT5 calendar.
- Paid Bloomberg terminal as live causal authority.
