# AUTONOMY MASTER PLAN — LLM Trader end-to-end

Status legend: ✅ shipped · 🔨 next up · 🧭 designed

## Multi-agent runtime (design §"MULTI-AGENT SYSTEM MASTER DESIGN")

| Phase | Deliverable | State |
|---|---|---|
| A | Contracts v2: 9 principals, PrincipalSpec, require_scope, ContractViolation; envelope v2 actor+trace_id | ✅ tests/test_contracts_v2.py |
| B | Executor registry `operations/executors.py` + scheduler entries + orchestrator seam | ✅ legacy chain untouched, new schedules route first |
| C | Personas: EventAgent blackout triage · PortfolioAgent caps · IncidentAgent circuit feed/runbooks · ResearchAgent sprints | ✅ tests/test_agent_personas.py |
| D | Task-graph delegation (`operations/delegation.py` child/fanout with parent auto-materialize) + per-principal supervisor freshness wired to executors + decision/validation sites | ✅ tests/test_delegation_freshness.py |
| E | **E1 shipped**: StageSpec semantics + runner + envelope (`blackout.gate → portfolio.consult → legacy body`), `PIPELINE_V2=1`, auto legacy fallback | ✅ |
| E | **E2 shipped**: maintenance prefix extracted to `_run_maintenance_prefix` and absorbed as 6 declared stages (velocity.poll · reconcile.pending · reconcile.open · memory.dispatch · position.tick · worker.heartbeat) with thread-local skip-token handoff; **call-order equivalence test proves V2 ≡ legacy** for paper + live modes | ✅ |
| E | **E3 designed**: absorb analysis half (context build → causal → llm.decide → validate → size-cap enforcement in RiskController → execute) into declared stages; golden-replay on recorded candles | 🧭 |

## 0. Where we were

| Layer | Component | State |
|---|---|---|
| Kernel | `operations/supervisor.py` FSM | ✅ boot→run→shutdown, guarded transitions |
| Doctrine | `operations/autonomy.py` L0-L3 + circuits + control plane | ✅ signature-gated L3, auto-demote/HALT |
| Telemetry | `operations/event_bridge.py` → harness_events SQLite | ✅ engine emits CYCLE_*/DECISION_*/VALIDATION_* |
| Governance | `operations/strategy_factory.py` gates + approvals files | ✅ PROMOTION/ROLLOUT_L3/CONFIG round-trip tested |
| API | `ui/gui_api.py` `/api/snapshot`, `/api/control/*`, WS producer | ✅ |
| Engine hooks | `llm_orchestrator.execute_cycle` gate + emissions; caller wrap | ✅ |
| Deck | 13 views incl. TASKS/STRATEGIES/ANALYTICS/APPROVALS/MAP | ✅ mock + live transports |

## 1. P3 — Workflow-engine migration of the main loop 🧭

**Goal**: replace the ~1,300-line imperative `execute_cycle` body with a declarative DAG executed by the existing `core/harness/workflows.QuantWorkflowEngine`, keeping `execute_cycle` as a thin adapter so all callers/tests stay valid.

Stage contract (new file `core/harness/stage_contracts.py`):

```python
@dataclass(frozen=True)
class StageSpec:
    key: str            # "data.poll"
    agent: str          # principal from agent_contracts (authority check)
    fn: str             # plugin callable name registered in kernel ctx
    timeout_s: float    # hard kill → stage error
    retries: int        # idempotent stages only
    fallback: str       # "skip" | "degrade" | "abort_cycle"
    sla_ms: int         # alert threshold (event STAGE_SLA_MISS)
    emits: tuple[str, ...]   # bridge events fired on success
```

Pipeline declaration (`core/harness/pipeline_def.py`):
`data.poll → reconcile → features → causal.check → llm.decide → validate → execute → position.manage`
with edges: `causal.check ─fail→ reject_sink`; `validate ─reject→ journal_reject`; `execute ─error→ flatten_guard`.

Acceptance:
1. Golden replay test: feed recorded candle → both old and new paths produce identical order intents (property test over 50 fixtures).
2. Every stage emits STAGE_STARTED/STAGE_DONE with duration; deck TASKS view consumes them unchanged.
3. Kill switch latency < 1 pulse (already guaranteed by autonomy gate at cycle top).

## 2. P4 — Strategy factory full automation 🧭

Already shipped: registry FSM + approvals round-trip. Remaining:

a) **Candidate generation** — schedule `candidate_sprint` (QuantitativeCoach, weekly): call `research.creator_engine.CreatorEngine.propose(n)` → each candidate runs `research.broker_replay` backtest → metrics into factory.evaluate(). Guarded imports; artifacts under `artifacts/factory/<id>/`.

b) **Paper-shadow runner** — `operations/shadow_runner.py`: FULL-candidate strategies trade in PaperBroker alongside production loop; shadow trades recorded to `shadow_trades` table; after N days clean (no recon flags, DD<10%) auto-open PROMOTION approval.

c) **Evidence binding** — every approval card embeds artifact hashes so the operator signs over exact evidence (sha256 of replay report + config).

d) **Demotion executor** — QUARANTINE transitions emit `STRATEGY_QUARANTINED`; orchestrator filters active set from factory.all() instead of static setup registry.

Acceptance: simulated 30-day tape promotes 1 candidate E2E with zero code changes (integration test with fake clock).

## 3. P5 — Learning loop executors 🧭

Scheduler cadences exist (`session_review`, `daily_review`, `self_improvement`, `weekly_research`). Missing executors:

- `operations/learning_loop.py::run(kind)`
  - `daily_review`: aggregate day journal → `intelligence/reflection_graph` invoke → lessons appended to working_memory namespace `lessons:<date>` → top-3 injected into next prompt via existing context builder.
  - `self_improvement`: memory quality audit (`local_lll_memory` distill score) + optional `research.rl_trainer` offline batch gated by `TRADING_RL_ENABLE_UPDATES=1` AND shadow-A/B beat baseline by ≥5% expectancy over ≥100 shadow trades before weights swap via model_registry promotion package.
  - `weekly_research`: creator sprint (see P4a) + factory.evaluate sweep.

Wiring point: `LLMOrchestrator._process_due_schedules` dispatch table gains kind→callable map (one dict entry each; guarded try/except, never blocks fast lane).

Acceptance: unit test injects due schedule rows → asserts reflection invoked with day payload and lesson string appears in next `get_decision` prompt context.

## 4. P6 — Resilience & operations 🔨→ partial ✅

1. ✅ **Watchdog/service**: `ops/watchdog/install_nssm.bat` (NSSM service, restart 15s, log rotation, delayed auto-start). Install manually when ready.
2. ✅ Crash-only: lease recovery + supervisor RECOVER_FROM_* (pre-existing, tested).
3. ✅ **Secrets gate**: `operations/secrets_guard.py` scans accounts.json/config.ini; warn-only boot check `secrets_hygiene_warnonly` in mt5_trader boot.
4. ✅ **Metrics export**: `operations/metrics_exporter.py` → Prometheus textfile `artifacts/metrics/trader.prom`, refreshed from the cycle gate + registered as executor.
5. 🧭 CI gate: local script pending (`pytest && tsc && selftest`).

## 5. P7 — Deck on real telemetry ✅ (incidents pane wired 2026-09-20)

Shipped: WS `STAGE_*` → TASKS spans · `EXECUTOR_FAILED`/`STAGE_SLA_MISS` → alerts ·
`APPROVAL_DECISION` inbox sync · `GET /api/analytics` (equity/R/monthly/per-strategy
rollups from journal + rewards artifact + factory registry) polled into ANALYTICS ·
snapshot `strategy_registry` → STRATEGIES view.

Map real sources into existing views (transport-only changes):
- TASKS ← new STAGE_* events (+ AgentOperations.summary for queue health)
- ANALYTICS ← `trading_rewards.json` + `trades` table aggregation endpoint `/api/analytics` (server-side pandas rollups: equity curve, R series, monthly R, per-strategy stats from factory registry)
- STRATEGIES ← `strategy_registry.json` (already in snapshot as `strategy_registry`)
- Incidents: `health_alerts.evaluate()` enriches every alert with its
  `docs/INCIDENT_RUNBOOKS.md` section + first response (DATABASE_MISSING branch
  included); `gui_api._incidents_snapshot()` normalizes severity to the deck
  vocabulary and adds kill-switch state → `deck_snapshot()["incidents"]` →
  `mapIncidents` in live transport → INCIDENTS pane in SystemHealthScreen
  (↑/↓ selects a card, expansion shows runbook + first response). Pinned by
  `tests/test_incidents_pane.py` (7 tests: doc↔index invariant, enrichment,
  severity normalization, card shape).
- Operator surfaces (2026-09-20): `/incidents` command — jumps to the health
  screen and prints every active card's code + first response into the REPL
  feed (clean journal prints `no active incidents`); sidebar footer badge on
  every screen shows the worst active severity (`⛔ CRITICAL · ×3`, kill-switch
  engagement outranks the computed status), clickable → health screen.
  Pinned by `tui_opentui/src/command-incidents.test.tsx`.

## 6. P8 — Portfolio allocation layer ✅ (wired 2026-09-20)

Dormant `core/hierarchical_portfolio` + `intelligence/correlation_risk` now wired:
`LLMOrchestrator.apply_portfolio_allocation_cap()` runs after `arbiter.permit`,
before `_build_execution_plan`. Constraint set: regime allocation multiplier
(HierarchicalMetaPortfolioEngine), per-strategy DD state (factory registry:
>=20% halves, >=35% halts), pairwise ρ>0.7 notional overlap cap 40%
(`correlation_headroom`). Deterministic, reduction-only, fails open; pinned by
`tests/test_portfolio_allocation_p8.py` (14 tests).

## 7. Risk register

| Risk | Mitigation |
|---|---|
| Workflow migration regressions | golden-replay equivalence tests before cutover; feature flag `PIPELINE_V2=1` |
| File-based approvals tamper | sha256 chain in outbox lines; verifier checks prev-hash |
| LLM cost runaway in sprints | llm_budget reservations reused; sprint cap enforced in creator loop |
| Windows service silent death | NSSM throttle + external uptime ping (deck stale banner already) |

## 8. Sequencing & est.

P3 (2-3 d) → P4b/c (2 d) → P5 (1-2 d) → P6.1/6.4 (1 d) → P7 (2 d) → P8 (2 d) → P6.2/6.3/CI (1 d). Each phase lands behind a flag with its own acceptance suite.
