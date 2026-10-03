"""R1 compact-context tests for brain.memory.ContextBuffer (payload audit fix).

Sandbox-only: no network, no journal DB, no MT5.  Each test restores the env
so the opt-in flag cannot leak across tests.
"""
import json
import os

import pytest

from brain.memory import ContextBuffer, compact_context_enabled


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "LLM_TRADER_COMPACT_CONTEXT",
        "LLM_TRADER_CONTEXT_HISTORY_WINDOW",
        "LLM_TRADER_CONTEXT_SUMMARY_CHARS",
        "LLM_TRADER_CONTEXT_SUMMARY_RETENTION",
    ):
        monkeypatch.delenv(name, raising=False)


def _payload(turn: int, size: int = 600) -> str:
    return f"Market Data JSON: cycle-{turn} " + ("x" * size)


def _reply(action: str = "HOLD", fallback: bool = False) -> str:
    body = {"action": action, "confidence": 0.42}
    if fallback:
        body["deterministic_fallback"] = True
    return json.dumps(body)


# ── Flag default / parsing ───────────────────────────────────────────


def test_flag_default_off():
    assert compact_context_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", " yes "])
def test_flag_truthy_values(monkeypatch, value):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", value)
    assert compact_context_enabled() is True


def test_flag_garbage_is_off(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "0")
    assert compact_context_enabled() is False


# ── Legacy behavior untouched when flag off ──────────────────────────


def test_legacy_mode_replays_full_history():
    buf = ContextBuffer(max_messages=10)
    assert buf.compact is False
    for turn in range(4):
        buf.set_system_prompt("SYS")
        buf.add_message("user", _payload(turn))
        buf.add_message("assistant", _reply())
    msgs = buf.get_messages()
    contents = "\n".join(m["content"] for m in msgs)
    assert "cycle-2" in contents and "cycle-3" in contents
    # Old behavior: up to max_messages live turns, older ones compressed but
    # full user payloads still occupy the window.
    user_turns = [m for m in msgs if m["role"] == "user"]
    assert len(user_turns) >= 2
    assert buf.size <= 10


def test_legacy_mode_no_env_dependency():
    # Constructing without the flag set must not create compact attrs weirdly.
    buf = ContextBuffer(max_messages=4)
    assert buf.history_window == 1  # default parsed even in legacy mode
    assert buf.compact is False


# ── Compact mode: window + summaries ─────────────────────────────────


def test_compact_mode_keeps_single_cycle_and_summarizes(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    buf = ContextBuffer(max_messages=10)
    assert buf.compact is True
    for turn in range(5):
        buf.set_system_prompt("SYS")
        buf.add_message("user", _payload(turn))
        buf.add_message("assistant", _reply("BUY" if turn == 3 else "HOLD"))
    msgs = buf.get_messages()
    roles = [m["role"] for m in msgs]
    # system + summaries-system + exactly one user/assistant pair
    assert roles[0] == "system"
    user_idx = [i for i, r in enumerate(roles) if r == "user"]
    assert user_idx == [len(roles) - 2]
    assert msgs[-1]["role"] == "assistant"
    # Latest payload must be intact, older payloads only in summaries.
    assert "cycle-4" in msgs[-2]["content"]
    for m in msgs:
        if m["role"] == "system" and m["content"].startswith("Previous context:"):
            assert "cycle-4" not in m["content"]
    assert any("action=HOLD" in m["content"] for m in msgs)


def test_compact_mode_summary_length_capped(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    monkeypatch.setenv("LLM_TRADER_CONTEXT_SUMMARY_CHARS", "300")
    buf = ContextBuffer(max_messages=10)
    buf.set_system_prompt("SYS")
    big = "Market Data JSON: " + ("y" * 5000)
    buf.add_message("user", big)
    buf.add_message("assistant", _reply())
    buf.add_message("user", _payload(2))
    buf.add_message("assistant", _reply())
    summaries = [m["content"] for m in buf.get_messages() if m["role"] == "system" and m["content"].startswith("Previous context:")]
    assert summaries, "expected a Previous-context summary"
    joined = summaries[-1]
    assert len(joined) < 2000  # raw payload is 5K; summary must be far shorter
    assert len([s for s in buf._summaries if len(s) > 300]) == 0


def test_compact_mode_history_window_two(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    monkeypatch.setenv("LLM_TRADER_CONTEXT_HISTORY_WINDOW", "2")
    buf = ContextBuffer(max_messages=10)
    for turn in range(4):
        buf.set_system_prompt("SYS")
        buf.add_message("user", _payload(turn))
        buf.add_message("assistant", _reply())
    msgs = buf.get_messages()
    user_turns = [m["content"] for m in msgs if m["role"] == "user"]
    assert user_turns == [_payload(2), _payload(3)]
    # Evicted cycles survive only as short summaries.
    prev = [m["content"] for m in msgs if m["role"] == "system" and m["content"].startswith("Previous context:")][-1]
    assert "cycle-1" in prev
    assert len(prev) < 1000


def test_compact_mode_fallback_cycles_do_not_consume_window(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    buf = ContextBuffer(max_messages=10)
    # Turn 0: real decision; turns 1-2: deterministic fallbacks; turn 3: real.
    buf.set_system_prompt("SYS")
    buf.add_message("user", _payload(0))
    buf.add_message("assistant", _reply("BUY"))
    buf.add_message("user", _payload(1))
    buf.add_message("assistant", _reply(fallback=True), metadata={"deterministic_fallback": True})
    buf.add_message("user", _payload(2))
    buf.add_message("assistant", _reply(fallback=True), metadata={"deterministic_fallback": True})
    buf.add_message("user", _payload(3))
    buf.add_message("assistant", _reply("SELL"))
    msgs = buf.get_messages()
    user_turns = [m["content"] for m in msgs if m["role"] == "user"]
    # Window=1 → only the latest cycle stays live; fallback cycles were
    # summarized (not silently dropped).
    assert user_turns == [_payload(3)]
    prev = [m["content"] for m in msgs if m["role"] == "system" and m["content"].startswith("Previous context:")][-1]
    assert "cycle-failed" in prev
    assert "action=BUY" in prev


def test_compact_mode_pinned_user_survives_compression(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    buf = ContextBuffer(max_messages=10)
    buf.set_system_prompt("SYS")
    buf.add_message("user", _payload(0), pinned=True)
    buf.add_message("assistant", _reply())
    buf.add_message("user", _payload(1))
    buf.add_message("assistant", _reply())
    # The pinned risk decision is never evicted; the unpinned cycle folds
    # into a summary instead.
    user_turns = [m["content"] for m in buf.get_messages() if m["role"] == "user"]
    assert user_turns == [_payload(0)]
    prev = [m["content"] for m in buf.get_messages() if m["role"] == "system" and m["content"].startswith("Previous context:")][-1]
    assert "cycle-1" in prev


# ── Persistence ──────────────────────────────────────────────────────


def test_save_load_roundtrip_trims_legacy_buffer_in_compact_mode(tmp_path, monkeypatch):
    # Legacy buffer saved with full replay window...
    monkeypatch.delenv("LLM_TRADER_COMPACT_CONTEXT", raising=False)
    buf = ContextBuffer(max_messages=10)
    buf.set_system_prompt("SYS")
    for turn in range(5):
        buf.add_message("user", _payload(turn))
        buf.add_message("assistant", _reply())
    path = tmp_path / "context_buffer.json"
    buf.save(str(path))
    # ...reloaded with compact mode on → trimmed to the window.
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    loaded = ContextBuffer.load(str(path))
    assert loaded.compact is True
    user_turns = [m["content"] for m in loaded.get_messages() if m["role"] == "user"]
    assert len(user_turns) == 1
    assert "cycle-4" in user_turns[0]
    assert len(loaded._summaries) <= loaded.summary_retention


def test_load_missing_file_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    buf = ContextBuffer.load(str(tmp_path / "nope.json"))
    assert buf.compact is True
    assert buf.size == 0


# ── R3: failed-cycle discard (legacy + compact modes) ───────────────


def test_r3_discard_failed_cycle_compact_mode(monkeypatch):
    monkeypatch.setenv("LLM_TRADER_COMPACT_CONTEXT", "1")
    # Window 2 so the earlier good cycle stays live while the latest fails.
    monkeypatch.setenv("LLM_TRADER_CONTEXT_HISTORY_WINDOW", "2")
    buf = ContextBuffer(max_messages=10)
    buf.set_system_prompt("SYS")
    # Cycle 0 succeeds; cycle 1 fails with a deterministic fallback.
    buf.add_message("user", _payload(0))
    buf.add_message("assistant", _reply("BUY"))
    buf.add_message("user", _payload(1))
    buf.add_message(
        "assistant", _reply(fallback=True),
        metadata={"deterministic_fallback": True},
    )
    buf.discard_failed_cycle()
    msgs = buf.get_messages()
    user_turns = [m["content"] for m in msgs if m["role"] == "user"]
    # The failed 30K payload is no longer replayed; only the good cycle is live.
    assert user_turns == [_payload(0)]
    prev = [
        m["content"] for m in msgs
        if m["role"] == "system" and m["content"].startswith("Previous context:")
    ][-1]
    assert "[cycle-failed]" in prev
    assert "cycle-1" in prev  # recorded, not silently dropped


def test_r3_discard_failed_cycle_legacy_mode(monkeypatch):
    monkeypatch.delenv("LLM_TRADER_COMPACT_CONTEXT", raising=False)
    buf = ContextBuffer(max_messages=10)
    buf.set_system_prompt("SYS")
    buf.add_message("user", _payload(0))
    buf.add_message("assistant", _reply("BUY"))
    buf.add_message("user", _payload(1))
    buf.add_message("assistant", _reply(fallback=True))
    buf.discard_failed_cycle()
    msgs = buf.get_messages()
    user_turns = [m["content"] for m in msgs if m["role"] == "user"]
    assert user_turns == [_payload(0)]
    prev = [
        m["content"] for m in msgs
        if m["role"] == "system" and m["content"].startswith("Previous context:")
    ][-1]
    # Assistant reply was a plain string (no metadata) — still recorded as a
    # summary, just without the failed marker.
    assert "cycle-1" in prev
    assert "[cycle-failed]" not in prev
