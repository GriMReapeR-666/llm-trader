"""PU-0093: live payload-flow measurement (read-only).

Opens trading_state.db strictly mode=ro and measures what the LLM context
assembly actually persisted recently: llm_requests payload/response sizes,
per-section char contributions from the newest payload, raw_traces sizes,
and working_memory footprint. No writes anywhere.
"""
import json
import sqlite3
import statistics

DB = "trading_state.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
con.row_factory = sqlite3.Row
results = []


def check(name, value):
    results.append(name)
    print(f"{name}: {value}")


def _p95(values):
    if not values:
        return 0
    vals = sorted(values)
    idx = min(len(vals) - 1, int(0.95 * len(vals)))
    return vals[idx]


# --- llm_requests over the last 7 days -------------------------------------
rows = con.execute(
    "SELECT payload, response_chars, status, parse_status, model, created_at "
    "FROM llm_requests WHERE created_at >= datetime('now', '-7 days') "
    "ORDER BY created_at DESC LIMIT 500"
).fetchall()
payload_sizes = []
resp_sizes = []
statuses = {}
models = {}
newest = None
for r in rows:
    p = r["payload"] or ""
    payload_sizes.append(len(p))
    rc = r["response_chars"]
    if rc is not None:
        resp_sizes.append(int(rc))
    statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    models[r["model"]] = models.get(r["model"], 0) + 1
    if newest is None:
        try:
            newest = (r["created_at"], json.loads(p))
        except Exception:
            newest = None

check("llm_requests_7d_count", len(rows))
if payload_sizes:
    check("payload_chars_min_avg_p95_max",
          f"{min(payload_sizes)} / {int(statistics.mean(payload_sizes))} / {_p95(payload_sizes)} / {max(payload_sizes)}")
    check("payload_tokens_est_avg", int(statistics.mean(payload_sizes) / 4))
    check("response_chars_avg", int(statistics.mean(resp_sizes)) if resp_sizes else 0)
    check("status_dist", statuses)
    check("models", models)

# --- per-section contribution from the newest payload ----------------------
KNOWN_SECTIONS = [
    "account", "recent_performance", "multi_timeframe", "indicators",
    "alpha_ensemble", "alpha_decay_health", "alpha_rotation", "regime_allocation",
    "regime_duration", "auto_quant_tools", "cpcv_escalation", "meta_adaptive_override",
    "evolutionary_suggestions", "cross_market_robustness", "alpha_discovery_enrichment",
    "multimodal_patterns", "crash_detector_signal", "causal_filter_report",
    "walk_forward_decay", "optimal_execution_cost", "ewc_forgetting_status",
    "alpha_stability", "canonical_mastery", "rl_analytics", "grpo_train", "ppo_train",
    "regime_autocorr", "meta_loop", "zero_lag_macro", "alpha_mining_results",
    "knowledge_base_context", "advanced_auto_tools_summary", "completed_feature_snapshot",
    "volume_analysis", "microstructure", "dom", "news_events", "news_trading_context",
    "news_gate", "event_state", "event_playbooks", "api_news", "hot_topics", "macro",
    "macro_causal_context", "intermarket_regime", "macro_causal_context_regime",
    "opportunity_fusion", "causal_propagation", "causal_main_driver",
    "macro_state_vector", "laya_decision_bundle", "macro_opportunity_bus",
    "open_positions", "playbook", "similar_past_regimes", "scenario_memory",
    "reviewed_core_memory", "working_memory", "learned_patterns",
]
if newest and isinstance(newest[1], dict):
    created_at, data = newest
    print(f"\nnewest payload at {created_at}: top-level chars={len(json.dumps(data, default=str))}")
    # Include unknown top-level keys too (context dict may have grown).
    contributions = []
    for key, value in data.items():
        size = len(json.dumps(value, default=str))
        contributions.append((size, key, key in KNOWN_SECTIONS))
    contributions.sort(reverse=True)
    print("top sections by chars (newest payload):")
    for size, key, known in contributions[:20]:
        print(f"  {size:>7}  {'known' if known else 'UNKNOWN':<7}  {key}")
    unknown = [k for _, k, known in contributions if not known]
    if unknown:
        print("unknown top-level keys:", unknown[:15])
    empty = [k for k in KNOWN_SECTIONS if k not in data]
    if empty:
        print("known sections absent from newest payload:", empty[:15])

# --- raw_traces recent -------------------------------------------------------
rows = con.execute(
    "SELECT LENGTH(payload) AS n FROM raw_traces "
    "WHERE created_at >= datetime('now', '-7 days') LIMIT 500"
).fetchall()
sizes = [r["n"] for r in rows if r["n"] is not None]
check("\nraw_traces_7d_count", len(sizes))
if sizes:
    check("raw_traces_chars_avg_p95_max",
          f"{int(statistics.mean(sizes))} / {_p95(sizes)} / {max(sizes)}")

# --- working_memory footprint ------------------------------------------------
rows = con.execute(
    "SELECT namespace, COUNT(*) AS n, AVG(LENGTH(payload)) AS avg_chars "
    "FROM working_memory WHERE status = 'ACTIVE' GROUP BY namespace ORDER BY n DESC LIMIT 10"
).fetchall()
check("\nworking_memory_ACTIVE_by_namespace",
      [(r["namespace"], r["n"], int(r["avg_chars"] or 0)) for r in rows])

total = con.execute(
    "SELECT COUNT(*) AS n, AVG(LENGTH(payload)) AS avg_chars FROM working_memory WHERE status='ACTIVE'"
).fetchone()
check("working_memory_total_ACTIVE", f"{total['n']} items, avg {int(total['avg_chars'] or 0)} chars/item")

con.close()
print("\nPROBE-DONE")
