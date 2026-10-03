from types import SimpleNamespace
import threading

from mt5_trader import LLMOrchestrator


class _Provider:
    def __init__(self, calendar=None, manager=None):
        self.calendar = calendar
        self.api_manager = manager

    def check_data_sources(self):
        return {"MT5 Calendar": "ONLINE" if self.calendar else "OFFLINE"}


class _Calendar:
    def get_events(self, **kwargs):
        return [SimpleNamespace(to_dict=lambda: {"event": "CPI", "importance": 3, "currency": "USD"})]


class _Manager:
    newsapi = True
    fred = True

    def get_hot_topics(self, limit=5):
        return [{"title": "rates", "sentiment": "NEUTRAL"}]

    def get_fred_data(self, series):
        return {name: {"value": 1.0} for name in series}


class _LLM:
    def __init__(self, response=None, error=None, usage=None):
        self.response = response
        self.error = error
        self.last_usage = usage or {}

    def generate(self, messages):
        if self.error:
            raise RuntimeError(self.error)
        return self.response


class _Ops:
    def __init__(self):
        self.reservations = []
        self.completions = []

    def reserve_llm(self, **kwargs):
        self.reservations.append(kwargs)
        return True, "pair-request-1"

    def finish_llm(self, request_id, **kwargs):
        self.completions.append((request_id, kwargs))


class _Circuit:
    def __init__(self):
        self.records = []

    def record(self, success, error=""):
        self.records.append((success, error))
        return {"tier": 1, "success": success}


def _bare_orchestrator():
    # Bypasses __init__, so the per-strategy cycle-state plumbing that the
    # memory/_last_llm_trace properties require must be installed explicitly.
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance._cycle_state_lock = threading.Lock()
    instance._cycle_local = threading.local()
    instance._memory_by_strategy = {}
    instance._trace_by_strategy = {}
    return instance


def _orchestrator(provider):
    instance = _bare_orchestrator()
    instance.data_provider = provider
    return instance


def test_pair_selection_context_blocks_missing_calendar():
    context = LLMOrchestrator._pair_selection_context(_orchestrator(_Provider()))
    assert context["selection_blocked"] is True
    assert context["calendar"]["status"] == "UNAVAILABLE"


def test_pair_selection_context_records_calendar_macro_and_news():
    context = LLMOrchestrator._pair_selection_context(_orchestrator(_Provider(_Calendar(), _Manager())))
    assert context["selection_blocked"] is False
    assert context["calendar"]["high_impact_count"] == 1
    assert context["macro"]["status"] == "AVAILABLE"
    assert context["news"]["topics"]


def test_pair_selection_blocks_when_news_or_macro_source_is_missing():
    context = LLMOrchestrator._pair_selection_context(_orchestrator(_Provider(_Calendar())))
    assert context["calendar"]["status"] == "AVAILABLE"
    assert context["news"]["status"] == "UNAVAILABLE"
    assert context["macro"]["status"] == "UNAVAILABLE"
    assert context["selection_blocked"] is True


def test_rl_state_uses_completed_snapshot_and_non_placeholder_macro_fields():
    instance = LLMOrchestrator.__new__(LLMOrchestrator)
    instance._get_market_session = lambda: "LONDON"
    toon = {
        "ohlc": [{"close": 100.0}, {"close": 999.0}],
        "indicators": {"rsi_14": [40.0, 90.0], "ema_50": [99.0, 998.0],
                        "atr_14": [1.0, 9.0], "adx_14": [25.0, 45.0],
                        "macd": {"histogram": [0.5, 8.0]}},
        "feature_snapshot": {"ready": True, "values": {"close": 100.0, "rsi_14": 40.0,
                              "ema_50": 99.0, "atr_14": 1.0, "adx_14": 25.0, "macd_hist": 0.5}},
        "technical_analysis": {"key_levels": [100.05], "confluence": {"score": 4},
                                "htf_context": {"trend": "BULLISH"}},
        "news": [{"sentiment": "BULLISH"}], "api_news": [],
        "event_state": {"state": "POST_EVENT"},
    }
    state = LLMOrchestrator._build_rl_state(instance, toon)
    assert state["rsi_14"] == 40.0
    assert state["ma_alignment"] == 1
    assert state["news_sentiment"] == 1.0
    assert state["htf_trend"] == 1
    assert state["near_sr"] is True
    assert state["macro_event_state"] == "POST_EVENT"


def test_pair_selector_fails_closed_on_malformed_llm_response():
    instance = _orchestrator(_Provider(_Calendar(), _Manager()))
    instance.llm = _LLM("not-json")
    assert LLMOrchestrator.select_pairs_with_llm(instance, count=3) == []
    assert instance._last_llm_trace["parse_status"] == "ERROR"


def test_pair_selector_filters_unknown_and_duplicate_symbols():
    instance = _orchestrator(_Provider(_Calendar(), _Manager()))
    instance.llm = _LLM('["eurusd", "BADPAIR", "EURUSD", "xauusd"]')
    assert LLMOrchestrator.select_pairs_with_llm(instance, count=3) == ["EURUSD", "XAUUSD"]


def test_pair_selector_fails_closed_on_provider_exception():
    instance = _orchestrator(_Provider(_Calendar(), _Manager()))
    instance.llm = _LLM(error="provider outage")
    assert LLMOrchestrator.select_pairs_with_llm(instance, count=3) == []


def test_pair_selector_records_durable_llm_request_telemetry():
    instance = _orchestrator(_Provider(_Calendar(), _Manager()))
    instance.llm = _LLM('["EURUSD"]', usage={"total_tokens": 37, "cost": 0.0025})
    instance.operations = _Ops()
    instance.provider_circuit = _Circuit()
    instance._llm_router = None
    instance._agent_name = "trader"
    instance.model_name = "test-model"
    instance.run_id = "run-1"
    assert LLMOrchestrator.select_pairs_with_llm(instance, count=1) == ["EURUSD"]
    assert instance.operations.reservations[0]["strategy"] == "scanner"
    assert instance.operations.completions[0][0] == "pair-request-1"
    assert instance.operations.completions[0][1]["parse_status"] == "SUCCESS"
    assert instance.operations.completions[0][1]["tokens"] == 37
    assert instance.operations.completions[0][1]["cost"] == 0.0025
    assert instance.provider_circuit.records == [(True, "")]


def test_pair_selector_failure_updates_provider_circuit():
    instance = _orchestrator(_Provider(_Calendar(), _Manager()))
    instance.llm = _LLM("not-json")
    instance.operations = _Ops()
    instance.provider_circuit = _Circuit()
    instance._llm_router = None
    instance._agent_name = "trader"
    instance.model_name = "test-model"
    instance.run_id = "run-1"
    assert LLMOrchestrator.select_pairs_with_llm(instance, count=1) == []
    assert instance.provider_circuit.records[0][0] is False
    assert "JSON pair list" in instance.provider_circuit.records[0][1]


def test_pair_selector_rejects_invalid_usage_telemetry_and_marks_request_failed():
    instance = _orchestrator(_Provider(_Calendar(), _Manager()))
    instance.llm = _LLM('["EURUSD"]', usage={"total_tokens": 1.5, "cost": 0.002})
    instance.operations = _Ops()
    instance.provider_circuit = _Circuit()
    instance._llm_router = None
    instance._agent_name = "trader"
    instance.model_name = "test-model"
    instance.run_id = "run-1"
    assert LLMOrchestrator.select_pairs_with_llm(instance, count=1) == []
    assert instance.operations.completions[0][1]["success"] is False
    assert instance.operations.completions[0][1]["tokens"] == 0
    assert instance.provider_circuit.records[0][0] is False
    assert "usage metadata invalid" in instance.provider_circuit.records[0][1]


def test_llm_trace_outcome_distinguishes_valid_hold_from_provider_failure():
    assert LLMOrchestrator._llm_trace_outcome({"parse_status": "SUCCESS"}) == (True, "SUCCESS", "")
    success, status, error = LLMOrchestrator._llm_trace_outcome(
        {"parse_status": "NO_RESPONSE", "attempts": 3}
    )
    assert success is False
    assert status == "NO_RESPONSE"
    assert error == "parse_status=NO_RESPONSE attempts=3"


def test_llm_trace_outcome_fails_closed_for_missing_or_malformed_trace():
    success, status, error = LLMOrchestrator._llm_trace_outcome(None)
    assert success is False
    assert status == "UNKNOWN"
    assert error == "parse_status=UNKNOWN attempts=0"


def test_get_decision_marks_unexpected_provider_error_in_trace():
    class _BrokenMemory:
        def set_system_prompt(self, _prompt):
            raise RuntimeError("memory/provider boundary failed")

    instance = _bare_orchestrator()
    instance.perf = None
    instance.memory = _BrokenMemory()
    instance.model_name = "test-model"
    instance.run_id = "run-1"
    instance.operations = _Ops()
    toon = {
        "account": {}, "multi_timeframe": {}, "indicators": {},
        "feature_snapshot": {}, "volume_analysis": {}, "microstructure": {},
        "dom": {}, "news": [], "event_state": {}, "api_news": [],
        "hot_topics": [], "macro_data": {}, "macro_causal_context": [],
        "open_positions": [], "playbook": {}, "similar_past_regimes": [],
        "scenario_memory": [], "core_memory": [], "working_memory": {},
        "learned_patterns": [], "journal_context": {},
    }
    decision = instance.get_decision(toon)
    assert decision["action"] == "HOLD"
    assert instance._last_llm_trace["parse_status"] == "ERROR"
