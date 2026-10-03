"""Persona unit tests for the four deterministic principals (Phase C)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.agents.event_agent import EventWindowManager  # noqa: E402
from operations.agents.portfolio_agent import cap_size, correlation_headroom, level_cap  # noqa: E402
from operations.agents.incident_agent import IncidentRecorder  # noqa: E402
from operations.agents.research_agent import sweep  # noqa: E402
from operations.strategy_factory import StrategyFactory  # noqa: E402
from operations.autonomy import AutonomyManager  # noqa: E402


# ---------- EventAgent ----------
def test_high_impact_event_creates_blackout(tmp_path):
    mgr = EventWindowManager(str(tmp_path / "windows.json"))
    start = mgr.__class__ and __import__("time").time() + 3600
    res = mgr.triage([
        {"symbol": "XAUUSD", "title": "NFP", "impact": "high",
         "time": __import__("datetime").datetime.fromtimestamp(
             start, tz=__import__("datetime").timezone.utc).isoformat()},
        {"symbol": "EURUSD", "title": "low-impact speech", "impact": "low",
         "time": __import__("datetime").datetime.fromtimestamp(
             start, tz=__import__("datetime").timezone.utc).isoformat()},
    ])
    assert res["added"] == 1
    gated, why = mgr.is_blackout("XAUUSD")
    assert gated is True and "NFP" in why
    gated_eur, _ = mgr.is_blackout("EURUSD")
    assert gated_eur is True  # wildcard window covers all symbols near NFP


def test_blackout_expires_after_window(tmp_path):
    mgr = EventWindowManager(str(tmp_path / "w.json"))
    now = __import__("time").time()
    mgr.triage([{"symbol": "*", "title": "past event", "impact": "high",
                 "time": now - 3600}], now=now)  # window ended 30m+ ago? (start -90m .. +30m => ended)
    gated, _ = mgr.is_blackout("*", now=now + 31 * 60)
    assert gated is False


def test_duplicate_events_dedupe(tmp_path):
    mgr = EventWindowManager(str(tmp_path / "w.json"))
    ev = [{"symbol": "*", "title": "CPI", "impact": "high",
           "time": __import__("datetime").datetime.fromtimestamp(
               1_800_000_000, tz=__import__("datetime").timezone.utc).isoformat()}]
    first = mgr.triage(ev)
    second = mgr.triage(ev)
    assert first["added"] == 1 and second["added"] == 0


# ---------- PortfolioAgent ----------
def test_l2_micro_caps_risk_pct():
    res = cap_size({"level": "L2_MICRO", "volume_risk_pct": 1.0})
    assert res["capped_pct"] == 0.25
    assert any("autonomy level" in c for c in res["constraints"])
    assert res["order_authority"] is False


def test_heat_headroom_shrinks_size():
    res = cap_size({"level": "L3_FULL", "volume_risk_pct": 1.0,
                    "heat_used_r": 5.4, "heat_cap_r": 6.0})
    assert res["capped_pct"] < 1.0
    assert any("heat" in c for c in res["constraints"])


def test_correlation_overlap_cap():
    positions = [
        {"symbol": "XAUUSD", "volume": 50},
        {"symbol": "EURUSD", "volume": 40},
    ]
    corr = {("XAUUSD", "XAGUSD"): 0.9}
    capped, why = correlation_headroom("XAGUSD", proposed=100,
                                       positions=positions, correlations=corr)
    assert capped < 100 and "overlap" in why
    uncapped, why2 = correlation_headroom("AUDUSD", proposed=10,
                                          positions=positions,
                                          correlations={(("XAUUSD", "XAGUSD")): 0.9} and {})
    assert uncapped == 10 and why2 == ""


# ---------- IncidentAgent ----------
def test_circuit_feed_records_incident_and_halts(tmp_path):
    rec = IncidentRecorder(str(tmp_path / "incidents.jsonl"))
    state, control = str(tmp_path / "a.json"), str(tmp_path / "c.jsonl")
    from operations.autonomy import get_autonomy as _g
    autonomy = AutonomyManager(state, control)

    original = _g()
    try:
        import operations.autonomy as mod
        mod._default_manager = autonomy  # feed_circuits uses get_autonomy()
        trips = rec.feed_circuits({"drawdown_pct": 7.7})
    finally:
        mod._default_manager = original

    assert trips and trips[0]["action"] == "HALT"
    rows = rec.recent()
    assert any(r["kind"] == "drawdown" for r in rows)
    assert all(r["runbook"].endswith("#drawdown-breaker")
               for r in rows if r["kind"] == "drawdown")


def test_runbook_known_for_every_circuit_key():
    from operations.agents.incident_agent import RUNBOOKS
    from operations.autonomy import AutonomyManager
    keys = {c["key"] for c in AutonomyManager.CIRCUITS}
    missing = keys - set(RUNBOOKS)
    assert not missing, f"runbooks missing for {missing}"


# ---------- ResearchAgent ----------
def test_sprint_feeds_factory_and_promotes_via_approval_only(tmp_path):
    reg, outbox, decisions = (
        str(tmp_path / "r.json"), str(tmp_path / "o.jsonl"), str(tmp_path / "d.jsonl"))
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "sprint1", "name": "eps_v1", "stage": "CANDIDATE"})

    metrics = {"sprint1": {"trades": 60, "win_rate": 56.0,
                           "profit_factor": 1.5, "max_dd": 7.0}}
    result = sweep(factory, lambda sid: metrics.get(sid))

    assert result["evaluated"] == 1
    assert factory.get("sprint1")["gate_progress"] == 1.0
    pending = [a for a in factory.pending_approvals() if a.get("strategyId") == "sprint1"]
    assert pending and pending[0]["status"] == "PENDING"
    # ResearchAgent cannot promote directly:
    assert factory.get("sprint1")["stage"] == "CANDIDATE"


def test_sprint_skips_strategies_without_evidence(tmp_path):
    reg, outbox, decisions = (
        str(tmp_path / "r.json"), str(tmp_path / "o.jsonl"), str(tmp_path / "d.jsonl"))
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "quiet", "name": "no_data_v0"})
    result = sweep(factory, lambda sid: None)
    assert result == {"evaluated": 0, "transitions": []}


def test_evidence_provider_never_replays_a_records_own_metrics(tmp_path):
    """A record's stored metrics are claims, not evidence — they must not be
    fed back in, or the factory re-validates itself forever."""
    from operations.agents.research_agent import _evidence_only_provider

    reg, outbox, decisions = (
        str(tmp_path / "r.json"), str(tmp_path / "o.jsonl"), str(tmp_path / "d.jsonl"))
    factory = StrategyFactory(reg, outbox, decisions)
    factory.upsert({"id": "claimed", "name": "claims_v1", "stage": "FULL",
                    "trades": 342, "win_rate": 66.4, "profit_factor": 2.45,
                    "sharpe": 2.14})

    # No fresh evidence supplied for this sprint.
    result = sweep(factory, _evidence_only_provider({}))

    assert result == {"evaluated": 0, "transitions": []}
    assert factory.get("claimed").get("last_eval_ts") is None
    assert factory.get("claimed")["gate_progress"] == 0.0
    assert not [a for a in factory.pending_approvals()
                if a.get("strategyId") == "claimed"]

    # Fresh evidence for a different id still drives evaluation normally.
    fresh = {"other": {"trades": 60, "win_rate": 56.0, "profit_factor": 1.5,
                       "max_dd": 7.0}}
    factory.upsert({"id": "other", "name": "other_v1", "stage": "CANDIDATE"})
    assert sweep(factory, _evidence_only_provider(fresh))["evaluated"] == 1


# ---------- Setup registry ----------
def test_setup_registry_reports_never_supplied_features():
    """A rejection must name the features it never received.

    `macro_continuation` has no producer anywhere in the codebase, so
    macro_continuation_v1 can never match. A bare "no match" reason hid that.
    """
    from research.setup_registry import SETUPS, classify

    res = classify({"adx": 13.5, "ema_alignment": -1, "mtf_alignment": 0,
                    "near_key_level": True, "confirmed_candle": True,
                    "volume_confirmed": False, "liquidity_sweep": False,
                    "asian_range_breakout": True})
    assert res["setup_id"] == "unclassified_v1"
    assert res["eligible"] is False
    assert "macro_continuation" in res["missing_features"]
    assert "features never supplied" in res["reason"]
    assert "macro_continuation" in res["reason"]


def test_setup_registry_still_classifies_when_features_present():
    """The added diagnostics must not change any classification outcome."""
    from research.setup_registry import classify

    # asian_range_breakout AND confirmed AND volume
    res = classify({"adx": 13.5, "ema_alignment": -1, "mtf_alignment": -1,
                    "near_key_level": True, "confirmed_candle": True,
                    "volume_confirmed": True, "liquidity_sweep": False,
                    "asian_range_breakout": True, "macro_continuation": False})
    assert res["setup_id"] == "asian_range_breakout_v1", res

    # adx >= 20 AND aligned AND near_level AND confirmed
    res = classify({"adx": 25.0, "ema_alignment": 1, "mtf_alignment": 1,
                    "near_key_level": True, "confirmed_candle": True,
                    "volume_confirmed": False, "liquidity_sweep": False,
                    "asian_range_breakout": False, "macro_continuation": False})
    assert res["setup_id"] == "trend_pullback_v1", res

    # macro_continuation remains reachable when the feature is supplied
    res = classify({"adx": 25.0, "ema_alignment": 1, "mtf_alignment": 1,
                    "near_key_level": True, "confirmed_candle": True,
                    "volume_confirmed": True, "liquidity_sweep": False,
                    "asian_range_breakout": False, "macro_continuation": True})
    assert res["setup_id"] == "macro_continuation_v1", res


# ---------- Bracket fallback for engine-mode signals ----------
def test_scalp_engine_mode_falls_back_to_bracket_setup():
    """`scalp` is an engine mode, not a structural setup, so its signals used to
    be unclassifiable and rejected. It now falls back to a registered bracket
    spec - but only after every structural setup has been tried."""
    from research.setup_registry import classify

    weak = {"adx": 13.5, "ema_alignment": -1, "mtf_alignment": 0,
            "near_key_level": True, "confirmed_candle": True,
            "volume_confirmed": False, "liquidity_sweep": False,
            "asian_range_breakout": True, "macro_continuation": False}

    res = classify(dict(weak), strategy="scalp")
    assert res["setup_id"] == "scalp_bracket_v1", res
    assert res["eligible"] is True, res
    assert res["fallback"] is True
    assert "structural fallback" in res["reason"], res

    # Without the engine mode it is still unclassified.
    assert classify(dict(weak))["setup_id"] == "unclassified_v1"
    # A different engine mode gets no bracket fallback.
    assert classify(dict(weak), strategy="swing")["setup_id"] == "unclassified_v1"
    # Case-insensitive.
    assert classify(dict(weak), strategy="SCALP")["setup_id"] == "scalp_bracket_v1"


def test_bracket_fallback_never_outranks_a_structural_setup():
    """A scalp-mode signal with a real structural match must take the setup."""
    from research.setup_registry import classify

    strong = {"adx": 25.0, "ema_alignment": 1, "mtf_alignment": 1,
              "near_key_level": True, "confirmed_candle": True,
              "volume_confirmed": False, "liquidity_sweep": False,
              "asian_range_breakout": False, "macro_continuation": False}
    res = classify(dict(strong), strategy="scalp")
    assert res["setup_id"] == "trend_pullback_v1", res
    assert res["fallback"] is False

    brk = {"adx": 13.5, "ema_alignment": -1, "mtf_alignment": -1,
           "near_key_level": True, "confirmed_candle": True,
           "volume_confirmed": True, "liquidity_sweep": False,
           "asian_range_breakout": True, "macro_continuation": False}
    res = classify(dict(brk), strategy="scalp")
    assert res["setup_id"] == "asian_range_breakout_v1", res
    assert res["fallback"] is False


# ---------- Provider outage must keep the safety HOLD absolute ----------
def test_rl_bridge_cannot_override_provider_outage_hold():
    """When every LLM provider is down, llm_router returns a deterministic HOLD.

    The RL bridge used to merge its own BUY/SELL on top of that, so a live
    cycle logged "safety HOLD engaged" and then acted on ACTION: BUY - opening
    new risk with no model judgement at all.
    """
    import inspect
    from intelligence import llm_orchestrator as orch

    src = inspect.getsource(orch.LLMOrchestrator.execute_cycle)
    assert "provider_outage" in src, "execute_cycle lost the provider_outage guard"
    assert "not provider_outage" in src, "RL bridge is no longer gated on it"

    # And the router must still be the thing that produces that flag.
    from data.llm_router import LLMRouter
    payload = __import__("json").loads(LLMRouter.deterministic_hold_fallback("trader"))
    assert payload["action"] == "HOLD"
    assert payload["deterministic_fallback"] is True
    assert payload["confidence"] == 0.0


def test_deterministic_hold_fallback_blocks_buy():
    """A BUY decision is only acceptable when a real model produced it."""
    from data.llm_router import LLMRouter
    import json
    outage = json.loads(LLMRouter.deterministic_hold_fallback("trader"))
    assert outage["action"] not in ("BUY", "SELL")
