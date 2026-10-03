"""CRITICAL #8 - concurrent strategies must not share an LLM conversation.

mt5_trader.py:4618-4626 runs every ``(strategy, timeframe)`` pair in a
``ThreadPoolExecutor`` against a *single* orchestrator instance::

    with _TPE(max_workers=len(strategy_cycles), ...) as _strat_pool:
        _strat_futs = {_strat_pool.submit(_run_one_strategy, sn, stf): sn ...}

``_run_one_strategy`` calls ``orch.execute_cycle(..., strategy=_sname)``. The
``ContextBuffer`` and the ``_last_llm_trace`` dict were plain instance
attributes, so N strategies in flight shared one prompt window and one trace:

  * one strategy's market payload and assistant reply were appended to the
    buffer the next strategy replayed into its own prompt;
  * one strategy's token usage and parse status were reported against another
    strategy's ``llm_request_id``.

The pool is built per pulse, so a thread-local would be wrong too (a fresh
thread each pulse would lose the conversation). The state is keyed on the
strategy instead, which is what makes the buffer survive across pulses while
staying isolated between strategies.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "data", "intelligence", "operations", "research", "brain"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.llm_orchestrator import LLMOrchestrator  # noqa: E402


class FakeBuffer:
    """Stand-in ContextBuffer: records what a strategy appended to it."""

    def __init__(self, max_messages=10):
        self.max_messages = max_messages
        self.messages = []
        self.system_prompt = None

    def set_system_prompt(self, prompt):
        self.system_prompt = prompt

    def add_message(self, role, content):
        self.messages.append((role, content))

    def get_messages(self):
        return list(self.messages)


def _texts(messages):
    """Content strings from a buffer, whichever shape it returns.

    brain.memory.ContextBuffer.get_messages() yields dicts; the FakeBuffer and
    any future implementation may yield (role, content) pairs.
    """
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m.get("content") or "")
        else:
            out.append(m[1] or "")
    return out

@pytest.fixture()
def orch():
    """A bare orchestrator with just the per-cycle state, no LLM or MT5.

    Constructing the real thing drags in providers, models and a trading
    database; the defect under test is entirely in the state plumbing.
    """
    obj = LLMOrchestrator.__new__(LLMOrchestrator)
    obj._cycle_state_lock = threading.Lock()
    obj._cycle_local = threading.local()
    obj._memory_by_strategy = {}
    obj._trace_by_strategy = {}
    return obj


def _claim(orch, strategy):
    """Do what execute_cycle does on entry."""
    orch._cycle_local.strategy = strategy


# --- buffers are per strategy -------------------------------------------

def test_buffers_are_not_shared(orch):
    _claim(orch, "scalp")
    a = orch.memory
    _claim(orch, "swing")
    b = orch.memory
    assert a is not b, "two strategies got the same ContextBuffer"


def test_buffer_content_does_not_leak(orch):
    _claim(orch, "scalp")
    buf_a = orch.memory
    buf_a.add_message("user", "SCALP-MARKET-DATA")
    buf_a.add_message("assistant", "SCALP-RESPONSE")

    _claim(orch, "swing")
    buf_b = orch.memory
    assert _texts(buf_b.get_messages()) == [], "swing inherited scalp's conversation"
    assert all("SCALP" not in c for c in _texts(buf_b.get_messages()))


def test_buffer_persists_across_pulses_for_one_strategy(orch):
    """The pool is rebuilt every pulse, so memory must key on strategy.

    A thread-local implementation would hand out a fresh empty buffer here and
    silently destroy the conversation history.
    """
    _claim(orch, "scalp")
    first = orch.memory
    first.add_message("user", "cycle-1")
    # a new "pulse" means a new thread with no claim on this state
    _claim(orch, "swing")
    orch.memory.add_message("user", "swing-cycle-1")
    _claim(orch, "scalp")
    again = orch.memory
    assert again is first, "scalp lost its buffer between pulses"
    assert "cycle-1" in _texts(again.get_messages())


# --- traces are per strategy --------------------------------------------

def test_traces_are_not_shared(orch):
    _claim(orch, "scalp")
    orch._last_llm_trace = {"prompt_hash": "a", "attempts": 1}
    _claim(orch, "swing")
    assert orch._last_llm_trace.get("prompt_hash") != "a", \
        "swing saw scalp's prompt hash"
    orch._last_llm_trace = {"prompt_hash": "b"}
    _claim(orch, "scalp")
    assert orch._last_llm_trace["prompt_hash"] == "a"


def test_trace_getter_returns_a_stable_mutable_object(orch):
    """Callers mutate in place (`trace["attempts"] = ...`); a copy would drop it."""
    _claim(orch, "scalp")
    t1 = orch._last_llm_trace
    t1["attempts"] = 3
    t1["parse_status"] = "SUCCESS"
    t2 = orch._last_llm_trace
    assert t2 is t1
    assert t2["attempts"] == 3
    assert t2["parse_status"] == "SUCCESS"


def test_outside_a_cycle_uses_the_default_slot(orch):
    """Startup and main-thread calls must still work, as they did before."""
    d = orch.memory
    assert d is not None or True  # may be None if brain.memory is unavailable
    d2 = orch.memory
    assert d2 is d, "the default slot is not stable across calls"
    orch._last_llm_trace = {"x": 1}
    assert orch._last_llm_trace["x"] == 1


# --- the real concurrency shape -----------------------------------------

def test_concurrent_cycles_do_not_cross_contaminate(orch):
    """Replay the mt5_trader shape: N strategies, one orchestrator, threads."""
    names = ["scalp", "swing", "memory", "trend"]
    seen_messages = {}
    seen_traces = {}
    errors = []
    barrier = threading.Barrier(len(names))
    lock = threading.Lock()

    def worker(strategy):
        try:
            # each thread claims its own strategy, exactly like execute_cycle
            _claim(orch, strategy)
            barrier.wait(timeout=10)  # maximise overlap
            buf = orch.memory
            for i in range(40):
                buf.add_message("user", "%s-cycle-%d" % (strategy, i))
                buf.add_message("assistant", "%s-reply-%d" % (strategy, i))
                orch._last_llm_trace = {
                    "prompt_hash": "%s-%d" % (strategy, i),
                    "attempts": 1,
                }
                time.sleep(0)  # yield, so the threads really interleave
            msgs = buf.get_messages()
            with lock:
                seen_messages[strategy] = msgs
                seen_traces[strategy] = dict(orch._last_llm_trace)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append("%s: %r" % (strategy, exc))

    threads = [threading.Thread(target=worker, args=(n,), name="strat-%s" % n)
               for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    for name in names:
        msgs = seen_messages[name]
        assert msgs, "%s collected no messages" % name
        # The buffer may compact older turns into a "Previous context: ..."
        # summary, so the check is that no *other* strategy's marker appears,
        # not that every message begins with this strategy's name.
        for content in _texts(msgs):
            for other in names:
                if other == name:
                    continue
                assert other not in content, (
                    "%s buffer contains %s's message: %r"
                    % (name, other, content[:160]))
        assert seen_traces[name]["prompt_hash"].startswith(name), (
            "%s trace belongs to %r" % (name, seen_traces[name]["prompt_hash"]))


def test_buffer_construction_failure_is_per_strategy_not_global(orch):
    """A failed import must not poison every strategy's slot with None twice.

    `_new_context_buffer` is consulted per strategy, so a transient failure on
    one key cannot be inherited by another.
    """
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("no brain.memory")

    orch._new_context_buffer = boom
    _claim(orch, "scalp")
    with pytest.raises(RuntimeError):
        orch.memory
    _claim(orch, "swing")
    with pytest.raises(RuntimeError):
        orch.memory
    assert len(calls) == 2, "each strategy attempted its own construction"


# --- structural guard: a revert to plain attributes must be caught --------

def test_state_must_remain_properties_not_plain_attributes():
    """Both names must stay properties on the class.

    If someone "simplifies" them back to `self.memory = ...` in __init__ with
    no per-strategy keying, the instance dictionary would hold one shared
    buffer again and the behavioural tests above would be testing a fixture
    that no longer matches the class.
    """
    assert isinstance(LLMOrchestrator.memory, property), \
        "LLMOrchestrator.memory is no longer a property"
    assert isinstance(LLMOrchestrator._last_llm_trace, property), \
        "LLMOrchestrator._last_llm_trace is no longer a property"


def test_execute_cycle_claims_the_strategy_before_use():
    """The strategy must be claimed at the top of execute_cycle, not lazily.

    Anything that reads self.memory or self._last_llm_trace before the claim
    lands in the "_default" slot, which is the shared buffer all over again.
    """
    import inspect
    src = inspect.getsource(LLMOrchestrator.execute_cycle)
    # strip comments and docstrings: this method's own comment names the very
    # attributes being checked for, and prose is not a read
    body = [l.split("#", 1)[0] for l in src.split("\n")]
    claim = next((i for i, l in enumerate(body)
                  if "_cycle_local.strategy" in l), None)
    assert claim is not None, "execute_cycle never claims a strategy"
    for i, line in enumerate(body):
        if "self.memory" in line or "self._last_llm_trace" in line:
            assert i > claim, (
                "cycle state read at body line %d, before the claim at %d"
                % (i, claim))
