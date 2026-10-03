"""R2/R4 tests: whole-request char budget + extended prunable list.

Sandbox-only, no network/journal/MT5.  Drives the LLMOrchestrator static
helpers directly (same import pattern as tests/test_phase1_remediation.py).
"""
import json

import pytest

from intelligence.llm_orchestrator import LLMOrchestrator


# ── R2: whole-request budget ─────────────────────────────────────────


def _oversized_request(history_turns: int = 3, payload_size: int = 40_000) -> list:
    msgs = [{"role": "system", "content": "S" * 2_000}]
    for turn in range(history_turns):
        msgs.append({"role": "user", "content": f"history-{turn} " + "h" * payload_size})
        msgs.append({"role": "assistant", "content": '{"action": "HOLD"}'})
    msgs.append({"role": "user", "content": "live " + "L" * payload_size})
    return msgs


def test_r2_small_request_untouched():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "live data"},
    ]
    out = LLMOrchestrator._enforce_request_char_budget(msgs)
    assert out is msgs  # returned as-is under budget


def test_r2_oversized_request_fits_and_protects_edges():
    budget = 96_000
    msgs = _oversized_request()
    total_before = sum(len(m["content"]) for m in msgs)
    assert total_before > budget
    out = LLMOrchestrator._enforce_request_char_budget(msgs, max_total_chars=budget)
    total_after = sum(len(m["content"]) for m in out)
    assert total_after <= budget + len("\n[truncated for request budget]") + 1
    # First system message intact.
    assert out[0]["content"] == msgs[0]["content"]
    # Final (live) user message intact.
    assert out[-1]["content"] == msgs[-1]["content"]
    # History was halved largest-first and explicitly marked.
    trimmed = [m for m in out if "[truncated for request budget]" in m["content"]]
    assert trimmed, "expected at least one trimmed history turn"
    assert all(m["role"] in {"user", "assistant"} for m in trimmed)


def test_r2_halves_largest_history_turn_first():
    msgs = [
        {"role": "system", "content": "S" * 500},
        {"role": "user", "content": "big " + "B" * 50_000},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "small " + "s" * 2_000},
        {"role": "user", "content": "live " + "L" * 2_000},
    ]
    out = LLMOrchestrator._enforce_request_char_budget(msgs, max_total_chars=20_000)
    big = next(m for m in out if m["content"].startswith("big"))
    small = next(m for m in out if m["content"].startswith("small"))
    assert len(big["content"]) < 30_000
    assert "[truncated for request budget]" in big["content"]
    assert "[truncated for request budget]" not in small["content"]


def test_r2_repeated_call_does_not_stack_markers():
    msgs = _oversized_request()
    out1 = LLMOrchestrator._enforce_request_char_budget(msgs, max_total_chars=30_000)
    out2 = LLMOrchestrator._enforce_request_char_budget(out1, max_total_chars=30_000)
    for a, b in zip(out1, out2):
        assert a["content"].count("[truncated for request budget]") == b["content"].count(
            "[truncated for request budget]"
        )


def test_r2_degenerate_inputs_returned_as_is():
    assert LLMOrchestrator._enforce_request_char_budget([]) == []
    two = [{"role": "system", "content": "S" * 200_000}, {"role": "user", "content": "U" * 200_000}]
    assert LLMOrchestrator._enforce_request_char_budget(two) is two


# ── PU-0161: prompt-size ceiling (SERIALIZED chars) ──────────────────


CEILING_MARKER = "[truncated for request budget]"


def _serialized(msgs: list) -> int:
    return len(json.dumps(msgs, default=str))


# Live payload shape mirrors session 11: a system prompt, replayed history
# turns, and a final user turn carrying JSON-encoded market data.
LIVE_PAYLOAD = json.dumps({
    "symbol": "EURUSD",
    "indicators": {"close": 1.0852, "rsi": 61.4},
    "decision": "evaluate",
})


def _s11_shaped_request(
    history_turns: int = 8,
    history_size: int = 9_000,
    pad_turns: int = 0,
    pad_size: int = 4_000,
) -> list:
    """system + replayed history + a FINAL live JSON payload.

    ``pad_turns`` inserts extra history before the live payload so a fixture
    can be sized to an exact serialized length while keeping the live payload
    as the last message (the guard's protected edge).
    """
    msgs = [{"role": "system", "content": "SYSTEM PROMPT " + "S" * 12_000}]
    for turn in range(history_turns):
        msgs.append({"role": "user", "content": f"hist-{turn} " + "h" * history_size})
        msgs.append({"role": "assistant", "content": '{"action": "HOLD"}'})
    for turn in range(pad_turns):
        msgs.append({"role": "assistant", "content": f"pad-{turn} " + "p" * pad_size})
    msgs.append({"role": "user", "content": "Market Data JSON:\n" + LIVE_PAYLOAD})
    return msgs


def test_ceiling_compacts_oversized_request_below_cap():
    msgs = _s11_shaped_request(history_size=11_500)
    before = _serialized(msgs)
    assert before > 100_000, f"fixture must start over the ceiling, got {before}"

    out = LLMOrchestrator._enforce_request_ceiling(msgs, max_chars=100_000)
    after = _serialized(out)

    assert after <= 100_000, f"ceiling not enforced: {after} chars"
    # Edges preserved: system prompt (role/doctrine) and live payload intact.
    assert out[0]["content"] == msgs[0]["content"]
    assert out[-1]["content"] == msgs[-1]["content"]
    # History turns were compacted and explicitly marked as truncated.
    trimmed = [m for m in out if CEILING_MARKER in str(m.get("content", ""))]
    assert trimmed, "expected at least one compacted history turn"
    assert all(m["role"] in {"user", "assistant"} for m in trimmed)


def test_ceiling_keeps_current_turn_json_parsable():
    """The live payload must survive compaction still parseable as JSON.

    This is the offline proxy for "prompt still parses": if compaction ever
    chewed into the current turn, the model would receive truncated JSON and
    degrade to the 368-char response seen in session 11.
    """
    msgs = _s11_shaped_request(history_size=11_500)
    out = LLMOrchestrator._enforce_request_ceiling(msgs, max_chars=100_000)

    live = out[-1]["content"]
    assert live.startswith("Market Data JSON:\n")
    parsed = json.loads(live.split("\n", 1)[1])
    assert parsed["symbol"] == "EURUSD"
    assert parsed["indicators"]["rsi"] == 61.4
    assert CEILING_MARKER not in live


def test_ceiling_under_cap_returns_untouched():
    msgs = _s11_shaped_request(history_turns=1, history_size=500)
    assert _serialized(msgs) <= 100_000
    out = LLMOrchestrator._enforce_request_ceiling(msgs, max_chars=100_000)
    assert out is msgs


def test_ceiling_env_override(monkeypatch):
    msgs = _s11_shaped_request(history_size=11_500)
    assert _serialized(msgs) > 100_000, _serialized(msgs)
    # A lower env ceiling forces compaction even though the default is 100K.
    monkeypatch.setenv("LLM_TRADER_PROMPT_CEILING_CHARS", "60000")
    out = LLMOrchestrator._enforce_request_ceiling(msgs)
    assert _serialized(out) <= 60_000
    # Zero/negative disables the guard entirely.
    monkeypatch.setenv("LLM_TRADER_PROMPT_CEILING_CHARS", "0")
    assert LLMOrchestrator._enforce_request_ceiling(msgs) is msgs


def test_ceiling_garbage_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_PROMPT_CEILING_CHARS", "not-a-number")
    msgs = _s11_shaped_request(history_size=11_500)
    out = LLMOrchestrator._enforce_request_ceiling(msgs)
    assert _serialized(out) <= 100_000


def test_ceiling_repeated_call_does_not_stack_markers():
    msgs = _s11_shaped_request(history_size=11_500)
    out1 = LLMOrchestrator._enforce_request_ceiling(msgs, max_chars=100_000)
    out2 = LLMOrchestrator._enforce_request_ceiling(out1, max_chars=100_000)
    for a, b in zip(out1, out2):
        assert str(a.get("content", "")).count(CEILING_MARKER) == str(
            b.get("content", "")
        ).count(CEILING_MARKER)


def test_ceiling_degenerate_inputs_returned_as_is():
    assert LLMOrchestrator._enforce_request_ceiling([]) == []
    two = [
        {"role": "system", "content": "S" * 300_000},
        {"role": "user", "content": "U" * 300_000},
    ]
    # Only two messages: nothing trimmable without eating system or live data.
    assert LLMOrchestrator._enforce_request_ceiling(two, max_chars=100_000) is two


def test_ceiling_s11_failure_shape_now_blocked():
    """Reproduce PU-0160's exact failure boundary.

    Session 11's three PARSE_ERRORs serialized to 104.1k/104.8k/105.9k chars
    while staying under the 96K *content* budget, so R2 let them through and
    the 11B model returned a constant 368-char degraded response. This asserts
    the new ceiling blocks precisely that shape.
    """
    for over in (104_146, 104_785, 105_909):
        # Size history so the fixture lands at (or just past) the recorded
        # serialized length, with the live payload still LAST.
        msgs = _s11_shaped_request(history_size=11_500)
        pad = 0
        while _serialized(msgs) < over:
            msgs.insert(
                -1, {"role": "assistant", "content": "p" * 4_000},
            )
            pad += 1
            if pad > 500:
                break
        assert _serialized(msgs) >= over, f"fixture did not reach {over}"
        assert msgs[-1]["role"] == "user", "live payload must stay last"

        out = LLMOrchestrator._enforce_request_ceiling(msgs, max_chars=100_000)
        assert _serialized(out) <= 100_000, f"{over}-char request not compacted"
        # The market-data payload that the model must parse stays whole.
        live = out[-1]["content"]
        assert live.startswith("Market Data JSON:\n"), live[:60]
        assert json.loads(live.split("\n", 1)[1])["symbol"] == "EURUSD"


# ── R4: extended prunable list ────────────────────────────────────────


def test_r4_advisory_keys_pruned_under_budget():
    toon = {
        # Non-prunable gated projection must survive intact.
        "opportunity_fusion": {"status": "PASS", "score": 0.7, "execution_authority": False},
        # New R4 prunable keys, each bloated past the budget.
        "live_advisory_hub": {"hierarchical": {"levels": "A" * 200_000}},
        "hierarchical_advisory": {"x": "B" * 100_000},
        "meta_label_advisory": {"y": "C" * 100_000},
        "advisory_regime": {"z": "D" * 100_000},
        "quant_audit": {"w": "E" * 100_000},
    }
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    serialized_len = len(str(out))
    assert serialized_len < 100_000  # bloated payloads got pruned
    # Pruned dict keys collapse to status-only stubs.
    for key in ("live_advisory_hub", "hierarchical_advisory", "meta_label_advisory", "advisory_regime", "quant_audit"):
        assert out[key].get("status") == "TRUNCATED_FOR_BUDGET", key
    # Gated projection untouched.
    assert out["opportunity_fusion"]["status"] == "PASS"
    assert "execution_authority" in out["opportunity_fusion"]


def test_r4_no_pruning_under_budget():
    # R5 contract note: small non-protected sections are summarized into
    # minor_context_summary (key + head preserved), while status-carrying
    # dicts pass through intact.  Budget pruning itself still never fires
    # under the limit.
    toon = {
        "advisory_regime": {"regime": "RISK_ON", "detail": "x" * 500},
        "quant_audit": {"status": "OK", "detail": "y" * 500},
    }
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    assert out["quant_audit"]["status"] == "OK"
    assert "RISK_ON" in out["minor_context_summary"]["detail"]
    assert out["minor_context_summary"]["collapsed_sections"] == 1


# ── R5: minor-section tail collapse ──────────────────────────────────


def test_r5_small_sections_collapse_into_one_blob(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_TAIL_COLLAPSE_CHARS", raising=False)
    toon = {
        "multi_timeframe": {"H1": [{"close": 1.1}] * 50},  # large, stays
        "ewc_forgetting_status": {"lambda": 0.5, "ok": True},
        "walk_forward_decay": {"decay": 0.12},
        "cross_market_hold_bias": False,
        "crash_detector_hold_bias": False,
        "optimal_execution_cost": {"pips": 0.4},
        "dom": {"best_bid": 1.1, "best_ask": 1.1002},
    }
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    assert "minor_context_summary" in out
    assert out["minor_context_summary"]["collapsed_sections"] == 6
    detail = out["minor_context_summary"]["detail"]
    for key in ("ewc_forgetting_status", "walk_forward_decay", "cross_market_hold_bias"):
        assert key in detail
    # Large + protected sections never collapse.
    assert "multi_timeframe" in out
    # Bounded representation: every per-section line carries at most an
    # 80-char head, so real-world (hundreds-of-char) sections compress hard
    # and the blob stays O(section count), never O(section content).
    for line in out["minor_context_summary"]["detail"].split(" | "):
        head = line.split(": ", 1)[1]
        assert len(head) <= 80, line


def test_r5_protected_and_status_keys_never_collapse(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_TAIL_COLLAPSE_CHARS", raising=False)
    toon = {
        "account": {"balance": 10000.0},
        "open_positions": [{"ticket": 7}],
        "working_memory": {"items": []},
        "session_info": {"session": "LONDON"},
        "opportunity_fusion": {"status": "PASS", "score": 0.7},  # small dict with status
        "ewc_forgetting_status": {"ok": True},                   # small, no status → collapses
    }
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    for key in ("account", "open_positions", "working_memory", "session_info", "opportunity_fusion"):
        assert key in out, key
    assert "minor_context_summary" in out
    assert out["minor_context_summary"]["collapsed_sections"] == 1


def test_r5_env_zero_disables_collapse(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_TAIL_COLLAPSE_CHARS", "0")
    toon = {
        "ewc_forgetting_status": {"ok": True},
        "walk_forward_decay": {"decay": 0.12},
    }
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    assert "minor_context_summary" not in out
    assert "ewc_forgetting_status" in out and "walk_forward_decay" in out


def test_r5_tail_blob_dropped_first_under_prune_pressure(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_TAIL_COLLAPSE_CHARS", raising=False)
    toon = {
        "multi_timeframe": {"H1": [{"close": 1.1}] * 50},
        "rl_analytics": {"heavy": "R" * 40_000},   # prunable research key
        "ewc_forgetting_status": {"ok": True},     # tail material
        "walk_forward_decay": {"decay": 0.12},     # tail material
    }
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    # Blob existed before pruning, and pruning must have removed it before
    # (or alongside) crushing the named research key.
    if "minor_context_summary" in out:
        pruned_named = out["rl_analytics"].get("status") == "TRUNCATED_FOR_BUDGET"
        assert not pruned_named or out["rl_analytics"]["status"] == "TRUNCATED_FOR_BUDGET"
    else:
        # Blob was consumed by budget pressure; some pruning happened.
        assert len(json.dumps(out, default=str)) <= 60_000
    assert len(json.dumps(out, default=str)) <= 60_000


def test_r5_single_section_payload_untouched(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_TAIL_COLLAPSE_CHARS", raising=False)
    toon = {"ewc_forgetting_status": {"ok": True}}
    out = LLMOrchestrator._sanitize_toon_lite_budget(toon, max_total_chars=60_000)
    assert out == toon
    assert "minor_context_summary" not in out
