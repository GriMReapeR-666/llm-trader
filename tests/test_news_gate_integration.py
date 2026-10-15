"""Permanent integration test for the news-gate wiring round-trip.

Targets the doctrinal blind spot that existed before the news execution
engine + macro routing wiring landed: the orchestrator could publish the news
gate and news trading context, but nothing produced a news execution plan or
the macro_routed_verdicts observational fields, so two checklists were
vacuously FALSE at runtime.

This test exercises the orchestrator's OWN writes into `toon` in one
news-cycle path: the provider's OHLC fetch is replaced with a no-OHLC stub so
the cycle path can proceed past the instrument-availability guard, then the
released-event calendar entry is stamped into the SAME dict the orchestrator
consumes. Assertions inspect the orchestrator's published keys, not any
smoke-injected values.

Marker: `integration`. Run with `pytest -m integration -q` or as part of the
normal suite; the marker exists in `pytest.ini`.
"""
from __future__ import annotations

import io as _io
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

import pytest

# Single import root: repo root only.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_EVENT_STREAM_MESSAGE_FIELDS = ("msg", "message", "text")

EVENT_TIME_ISO = "2024-05-03T13:30:00+00:00"

# ---------------------------------------------------------------------------
# Tiny stub fixture so the orchestrator's cycle path can exercise the news
# wiring without any live broker / network / LLM.
# ---------------------------------------------------------------------------


def _mini_fetch_ohlc(symbol, timeframe, count=100):
    now = pd.Timestamp("2024-05-03 13:30:00+0000", tz="UTC")
    n = 260
    closes = pd.Series([1.0800 - i * 0.0001 for i in range(n)], name="close")
    return pd.DataFrame({
        "time": pd.date_range(end=now, periods=n, freq="D", tz="UTC"),
        "open": closes - 0.0002,
        "high": closes + 0.0002,
        "low": closes - 0.0002,
        "close": closes,
        "tick_volume": [1000] * n,
        "spread": [1] * n,
        "real_volume": [1000] * n,
    })


def _mini_dom_snapshot():
    return {"available": False}


class _MiniMT5:
    """Broker stub satisfying the harness's client/terminal contract.

    `client` is wired to this on the provider so the OHLC guard does not
    raise before the news wiring runs. The news path does not use live
    prices; we provide a DOWN candle so downstream OHLC consumers (when any)
    see a coherent shape.
    """

    def __init__(self) -> None:
        self._terminal = type("T", (), {
            "company": "MiniTerminal",
            "connected": True,
            "reconnect_count": 0,
        })()
        self._positions: list[object] = []
        self._orders: list[object] = []

    def terminal_info(self) -> object:
        return self._terminal

    def positions_get(self) -> list[object]:
        return self._positions

    def orders_get(self) -> list[object]:
        return self._orders

    def symbol_info(self, name: str) -> object:
        class _SI:
            visible = True

        return _SI()

    def symbol_select(self, name: str, force: bool) -> bool:
        return True

    def symbols_get(self) -> list[object]:
        return []

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int):
        # Return None downstream of fetch_ohlc; the news-cycle path does not
        # read OHLC, and a real DataFrame would require a real broker. Explicit
        # None keeps this test broker-free and deterministic.
        return None

    def market_book_add(self, symbol: str) -> bool:
        return False

    def market_book_release(self, symbol: str) -> bool:
        return True


_MiniMT5.market_book_add = lambda self, symbol: False
_MiniMT5.market_book_release = lambda self, symbol: True


class _MiniAPI:
    """Stubbed provider whose calendar row carries a RELEASED event.

    Upcoming calendar rows in the cached fixture did NOT carry both `actual`
    and `consensus`, which is why the post-release plan/router path never
    fired historically. This stub provides both floats.
    """

    def get_calendar(self) -> list[dict[str, object]]:
        return [{
            "title": "Non-Farm Payrolls",
            "datetime": EVENT_TIME_ISO,
            "actual": 350.0,
            "forecast": 180.0,
            "value": 350.0,
            "consensus": 180.0,
            "previous": 200.0,
            "importance": "HIGH",
        }]

    def get_news(self, *, page_size: int | None = None, page: int = 1) -> list[dict[str, object]]:
        return []

    def get_hot_topics(self, limit: int = 3) -> list[dict[str, object]]:
        return []

    def is_holiday(self) -> bool:
        return False

    def get_status(self) -> dict[str, str]:
        return {
            "NewsAPI": "OFFLINE",
            "FRED": "OFFLINE",
            "SEC": "OFFLINE",
        }


class _MockRouter:
    def generate(self, messages, model=None, **kwargs):
        return _mock_generate(messages, model=model, **kwargs)

    def generate_with_tools(self, messages, tools, tool_executor, model=None, **kwargs):
        return _mock_generate(messages, model=model, **kwargs)


def _mock_generate(messages, model=None, **kwargs) -> str:
    return json.dumps({
        "action": "HOLD",
        "confidence": 0.55,
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "position_size": None,
        "rationale": ["stub"],
    })


def _build_orch(db_path: Path, perf_path: Path):
    from core.trading_safety import TradingStore
    from core.risk_manager import RiskManager
    from data.market_data_provider import PerformanceMonitor
    from intelligence.llm_orchestrator import LLMOrchestrator

    store = TradingStore(path=str(db_path), mode="PAPER")
    risk = RiskManager(
        balance=100000.0,
        store=store,
        risk_per_trade=0.01,
        max_drawdown=0.20,
    )
    orch = LLMOrchestrator(
        client=_MiniMT5(),
        risk_manager=risk,
        perf_monitor=PerformanceMonitor(history_file=str(perf_path), store=store),
        api_key="fake",
        model_name="meta/llama-3.2-11b-vision-instruct",
        enable_rl=False,
        agent_name="news-gate-hardening",
        journal_mode="PAPER",
        store=store,
        llm_router=_MockRouter(),
    )
    # Paper-mode fetch_data requires a client OR a live MT5 terminal.
    # This hardening test is broker-free by design, so we keep the stub
    # api_manager AND drop the client so the stub stays authoritative.
    orch.data_provider.api_manager = _MiniAPI()
    import data.market_data_provider as _mdp
    _orig_mt5 = _mdp._get_mt5
    _mdp._get_mt5 = lambda: _MiniMT5()
    # Override the module-level fetch_ohlc so the provider's DATA path sees a
    # real DataFrame from our stub instead of trying the broker's copy_rates.
    _mdp.fetch_ohlc = _mini_fetch_ohlc
    orch._news_gate_test_mt5_backend = (_mdp, _orig_mt5)
    return orch, store


def _dict_repr(obj):
    if isinstance(obj, dict):
        return {str(k): _dict_repr(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dict_repr(it) for it in obj]
    if hasattr(obj, "__dict__"):
        return _dict_repr(obj.__dict__)
    try:
        return json.loads(json.dumps(obj, default=lambda o: _dict_repr(o)))
    except Exception:
        return str(obj)


# ---------------------------------------------------------------------------
# Shared cycle harness: one news-cycle path, capturing the toon the
# orchestrator actually consumed.
# ---------------------------------------------------------------------------

PUB_KEYS = {
    "news",
    "news_gate",
    "news_trading_context",
    "macro_routed_verdicts",
    "news_execution_plan",
}


def _iter_plans(news_execution_plan):
    """Normalize the plan slot to a list of plan dicts.

    The orchestrator currently stores a single plan dict per cycle
    (`toon["news_execution_plan"] = plan.to_dict()`), so accept both a plain
    dict and a list of dicts.
    """
    if not news_execution_plan:
        return []
    if isinstance(news_execution_plan, dict):
        return [news_execution_plan]
    if isinstance(news_execution_plan, list):
        if news_execution_plan and isinstance(news_execution_plan[0], dict):
            return news_execution_plan
        return [
            p.to_dict() if hasattr(p, "to_dict") else p.__dict__
            for p in news_execution_plan
        ]
    return []


def _run_one_cycle(orch, *, symbol="EURUSD", timeframe="M15", cycle_num=1):
    """Run one news-cycle path and return (decision, toon, raw_log).

    `toon` is the SAME dict the orchestrator consumed (the provider stub
    returns it verbatim after we stamp the released event into it), so
    assertions below inspect the orchestrator's own published keys.
    """
    intercept = _io.StringIO()
    reporter = logging.StreamHandler(intercept)
    reporter.setLevel(logging.DEBUG)
    root = logging.getLogger()
    before_level = root.level
    before_handlers = root.handlers[:]
    root.addHandler(reporter)
    root.setLevel(logging.DEBUG)

    prepared = orch.data_provider.fetch_data(
        symbol=symbol,
        timeframe=timeframe,
        cycle_num=cycle_num,
    )
    # Give the engine a regime so macro routing can resolve macro_regime.
    prepared.setdefault("regime", {"regime_id": "EXPANSION"})
    prepared.setdefault("intermarket_regime", {"regime_id": "EXPANSION"})

    # Stamp the RELEASED NFP print into the SAME dict the orchestrator consumes
    # (parity with the original smoke: cached calendar fixtures only carry
    # upcoming rows without actual/consensus floats, which never fired the
    # post-release plan/router path, and stale cached events leak in as
    # unroutable verdicts). Numbers, not strings, so float() inside the
    # orchestrator cannot choke.
    prepared["news"] = [{
        "title": "Non-Farm Payrolls",
        "datetime": EVENT_TIME_ISO,
        "actual": 350.0,
        "forecast": 180.0,
        "previous": 200.0,
        "importance": "HIGH",
    }]

    # Pin the orchestrator's fetch to this exact dict so its writes land where
    # we can observe them.
    real_fetch = orch.data_provider.fetch_data
    orch.data_provider.fetch_data = lambda *a, **k: prepared
    try:
        result = orch.execute_cycle(
            symbol=symbol,
            timeframe=timeframe,
            cycle_num=cycle_num,
            total_cycles=1,
            strategy="parametric",
        )
    finally:
        orch.data_provider.fetch_data = real_fetch

    raw_log = intercept.getvalue()
    root.removeHandler(reporter)
    root.handlers[:] = before_handlers
    root.setLevel(before_level)

    decision = result[0] if isinstance(result, tuple) else result
    return decision, prepared, raw_log


# ---------------------------------------------------------------------------
# Hard assertions
# ---------------------------------------------------------------------------


def _assert_news_gate(toon):
    news_gate = toon.get("news_gate")
    assert news_gate, "news_gate was not published into toon"
    assert isinstance(news_gate, dict), "news_gate must be a dict"
    assert "phase" in news_gate, "news_gate missing phase"
    assert "allowed" in news_gate, "news_gate missing allowed"
    assert news_gate.get("ablated") is False, "news_gate.ablated must be False"
    allowed = news_gate.get("allowed")
    assert isinstance(allowed, bool), "news_gate.allowed must be a bool"
    assert allowed is True, "expected news_gate allowed=True in the released-event fixture"


def _assert_news_execution_plan_present_and_clear(toon):
    plan = toon.get("news_execution_plan")
    assert plan, "news_execution_plan was not published into toon"
    plans = _iter_plans(plan)
    assert plans, "news_execution_plan resolved to no plan entries"
    clear_plan = next(
        (p for p in plans if p.get("phase") == "CLEAR" and p.get("allowed") is True),
        None,
    )
    assert clear_plan is not None, (
        "expected at least one news execution plan with phase=CLEAR and allowed=True"
    )
    assert clear_plan.get("event_name"), "CLEAR plan missing event_name"
    assert clear_plan.get("tier") >= 1, "CLEAR plan tier should be >= 1 for NFP"


def _assert_macro_routed_verdicts_present_and_observational(toon):
    verdicts = toon.get("macro_routed_verdicts", [])
    assert verdicts, "macro_routed_verdicts was not emitted into toon"
    assert isinstance(verdicts, list), "macro_routed_verdicts must be a list"
    assert all(isinstance(v, dict) for v in verdicts), (
        "each macro_routed_verdict entry must be a dict"
    )
    # Observational invariant: NO routed verdict may carry execution authority.
    bad = [v.get("event_title", "?") for v in verdicts if v.get("execution_authority") is not False]
    assert not bad, f"routed verdict entries must stay observational; got authority on {bad}"

    # The routed verdict shapes a dict either flat on the entry or nested under
    # entry['verdict']; resolve fields for shape assertions either way.
    def _fields(entry):
        nested = entry.get("verdict")
        if isinstance(nested, dict):
            merged = dict(entry)
            merged.pop("verdict", None)
            nested = dict(nested)
            return {**merged, **nested}
        return entry

    # The stubbed released NFP print must itself get a routed verdict, with
    # the router-composed observational fields it owns.
    nfp = next(
        (
            v for v in verdicts
            if str(v.get("event_title", "")).lower().startswith("non-farm")
        ),
        None,
    )
    assert nfp is not None, "no routed verdict for the released Non-Farm Payrolls fixture"
    assert "verdict" in nfp, "NFP routed verdict entry missing verdict field"
    fields = _fields(nfp)
    for key in ("event_title", "phase", "tier", "surprise", "alignment", "conviction"):
        assert key in fields, f"NFP macro_routed_verdict missing field {key!r}"


# ---------------------------------------------------------------------------
# Tests — one orchestrator build + cycle shared module-wide (the build is
# minutes-class on a low-RAM box; four independent builds would exceed the
# normal suite budget for no extra signal: every test asserts on the SAME
# cycle's toon).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def news_cycle(tmp_path_factory):
    """Build the orchestrator once, run one released-event news cycle."""
    base = tmp_path_factory.mktemp("news_gate")
    orch, _ = _build_orch(base / "trading_state.db", base / "rewards.json")
    decision, toon, _ = _run_one_cycle(orch)
    return decision, toon


@pytest.mark.integration
def test_news_gate_ablated_is_false(news_cycle):
    """news_gate must be published with ablated=False in the released-event path."""
    _, toon = news_cycle
    _assert_news_gate(toon)


@pytest.mark.integration
def test_news_execution_plan_present_and_clear(news_cycle):
    """A released calendar event must produce a CLEAR/allowed execution plan."""
    _, toon = news_cycle
    _assert_news_execution_plan_present_and_clear(toon)
    assert toon.get("news_execution_plan") is not None


@pytest.mark.integration
def test_macro_routed_verdicts_emitted_and_observational(news_cycle):
    """macro_routed_verdicts must be emitted and remain observational.

    This is the key blind-spot check: route_event is wired, the event carries
    actual + consensus, and the emission should survive in toon.
    """
    _, toon = news_cycle
    _assert_macro_routed_verdicts_present_and_observational(toon)


@pytest.mark.integration
def test_news_gate_wiring_round_trip(news_cycle):
    """End-to-end: gate + plan + verdicts all land in the same toon.

    This is the integration smoke's contract, made permanent and explicit.
    """
    _, toon = news_cycle
    assert set(PUB_KEYS).issubset(toon.keys()), (
        "expected all news-related keys present in the orchestrator's toon"
    )
    _assert_news_gate(toon)
    _assert_news_execution_plan_present_and_clear(toon)
    _assert_macro_routed_verdicts_present_and_observational(toon)

    # Contextual sanity: the news list carried a released event.
    news_list = toon.get("news", [])
    assert news_list, "news list empty in the released-event fixture"
    assert any(
        str(ev.get("title", "")).lower() == "non-farm payrolls"
        for ev in news_list
    ), "released NFP event not found in toon['news']"
