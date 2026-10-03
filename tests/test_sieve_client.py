"""Sieve scrape integration tests.

Recorded responses live only at the HTTP boundary (a fake ``requests.Session``);
everything under test — request building, error mapping, status classification,
the follow-up turn gate, the never-retry-POST rule, and the durable store — is
the real production code.
"""
from __future__ import annotations

import json

import pytest

from core import sieve_client


# --------------------------------------------------------------------------- #
# HTTP boundary fakes
# --------------------------------------------------------------------------- #

class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, chunks=(b"data",)):
        self.status_code = status_code
        self._payload = payload
        self.headers = dict(headers or {})
        self._chunks = chunks

    def json(self):
        if self._payload is None:
            raise ValueError("response has no json")
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload) if self._payload is not None else ""

    def iter_content(self, chunk_size=65536):
        for chunk in self._chunks:
            yield chunk


class FakeSession:
    """Returns queued responses/exceptions and records every call."""

    def __init__(self, *items):
        self._queue = list(items)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(*items, key="dc_sk_test"):
    session = FakeSession(*items)
    return sieve_client.SieveClient(api_key=key, session=session), session


def done_payload(turns=1, schema=None):
    payload = {"status": "done", "session_id": "s1", "turns": turns, "summary": "ok",
               "files": [{"name": "out.csv", "size": 12, "ext": "csv",
                          "url": "/files/out.csv"}],
               "result": [{"text": "x", "author": "y"}]}
    if schema is not None:
        payload["schema_conformance"] = {"status": schema, "violations": []}
    return payload


# --------------------------------------------------------------------------- #
# Key resolution
# --------------------------------------------------------------------------- #

def test_env_key_wins_and_is_configured(monkeypatch):
    monkeypatch.setenv("SIEVE_API_KEY", "dc_sk_env")
    assert sieve_client.is_configured() is True
    assert sieve_client.SieveClient().api_key == "dc_sk_env"


def test_not_configured_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("SIEVE_API_KEY", raising=False)
    monkeypatch.setattr(sieve_client, "retrieve_secret", lambda _key: "")
    session = FakeSession(FakeResponse(202, {"status": "queued"}))
    client = sieve_client.SieveClient(api_key=None, session=session)
    with pytest.raises(sieve_client.SieveNotConfigured):
        client.start_run("do it")
    assert session.calls == []


def test_secure_store_fallback(monkeypatch):
    monkeypatch.delenv("SIEVE_API_KEY", raising=False)
    monkeypatch.setattr(sieve_client, "retrieve_secret",
                        lambda key: "dc_sk_store" if key == sieve_client.SECRET_STORE_KEY else "")
    assert sieve_client.get_api_key() == "dc_sk_store"


def test_store_api_key_uses_secure_store(monkeypatch):
    captured = {}
    monkeypatch.setattr(sieve_client, "store_secret",
                        lambda key, value: captured.update(key=key, value=value) or key)
    sieve_client.store_api_key("dc_sk_new")
    assert captured == {"key": sieve_client.SECRET_STORE_KEY, "value": "dc_sk_new"}


# --------------------------------------------------------------------------- #
# Request building
# --------------------------------------------------------------------------- #

def test_start_run_builds_json_body_and_bearer_header():
    client, session = make_client(FakeResponse(202, {"status": "queued", "session_id": "s1"}))
    client.start_run("Extract quotes", target_urls=["https://example.com"],
                     fields=["text"], table_shape="long",
                     output_schema={"type": "array"}, compliance_mode="conservative")
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://scrape.usesieve.com/api/scrapes"
    assert call["headers"]["Authorization"] == "Bearer dc_sk_test"
    assert call["json"] == {
        "instruction": "Extract quotes", "compliance_mode": "conservative",
        "target_urls": ["https://example.com"], "fields": ["text"],
        "table_shape": "long", "output_schema": {"type": "array"},
    }


def test_start_run_defaults_to_regular_compliance():
    client, session = make_client(FakeResponse(202, {"status": "queued", "session_id": "s1"}))
    client.start_run("x")
    assert session.calls[0]["json"]["compliance_mode"] == "regular"


def test_start_run_rejects_bad_input_without_calling_http():
    client, session = make_client()
    with pytest.raises(ValueError):
        client.start_run("")
    with pytest.raises(ValueError):
        client.start_run("x", compliance_mode="ludicrous")
    assert session.calls == []


def test_start_run_requires_queued_session_id():
    client, _ = make_client(FakeResponse(200, {"status": "done"}))
    with pytest.raises(sieve_client.SieveProtocolError):
        client.start_run("x")


# --------------------------------------------------------------------------- #
# Never-retry-POST-on-timeout
# --------------------------------------------------------------------------- #

def test_start_run_does_not_retry_on_transport_timeout():
    import requests

    client, session = make_client(requests.exceptions.Timeout("read timed out"))
    with pytest.raises(sieve_client.SieveTransportError) as excinfo:
        client.start_run("x")
    # Crucially only ONE attempt: the first call may already have spent credits.
    assert len(session.calls) == 1
    assert "not retried" in str(excinfo.value)


def test_start_run_retries_safe_status_only():
    client, session = make_client(
        FakeResponse(429, {"error": "slow down"}, headers={"Retry-After": "1"}),
        FakeResponse(503, {"error": "unavailable"}),
        FakeResponse(202, {"status": "queued", "session_id": "s1"}),
    )
    slept = []
    client.start_run("x", sleep=slept.append)
    assert len(session.calls) == 3
    assert len(slept) == 2


def test_start_run_gives_up_after_safe_retries():
    client, session = make_client(*[FakeResponse(500, {"error": "boom"}) for _ in range(5)])
    with pytest.raises(sieve_client.SieveServerError):
        client.start_run("x", max_attempts=2, sleep=lambda _s: None)
    assert len(session.calls) == 2


def test_send_message_409_raises_turn_in_flight():
    client, _ = make_client(FakeResponse(409, {"error": "turn in flight"}))
    with pytest.raises(sieve_client.SieveTurnInFlight):
        client.send_message("s1", "follow up")


def test_send_message_does_not_retry_on_timeout():
    import requests

    client, session = make_client(requests.exceptions.ConnectionError("reset"))
    with pytest.raises(sieve_client.SieveTransportError):
        client.send_message("s1", "follow up")
    assert len(session.calls) == 1


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status,expected", [
    (400, sieve_client.SieveBadRequest),
    (401, sieve_client.SieveAuthError),
    (402, sieve_client.SievePaymentRequired),
    (404, sieve_client.SieveNotFound),
    (429, sieve_client.SieveRateLimited),
    (500, sieve_client.SieveServerError),
    (418, sieve_client.SieveError),
])
def test_map_error_types(status, expected):
    assert isinstance(sieve_client.map_error(status, {"error": "x"}), expected)


def test_error_retryable_flags():
    assert sieve_client.map_error(400, {}).retryable is False
    assert sieve_client.map_error(401, {}).retryable is False
    assert sieve_client.map_error(402, {}).retryable is False
    assert sieve_client.map_error(404, {}).retryable is False
    assert sieve_client.map_error(429, {}).retryable is True
    assert sieve_client.map_error(500, {}).retryable is True


def test_rate_limit_reads_retry_after():
    error = sieve_client.map_error(429, {"retry_after": 12})
    assert isinstance(error, sieve_client.SieveRateLimited)
    assert error.retry_after == 12.0


def test_poll_401_surfaces_auth_error():
    client, _ = make_client(FakeResponse(401, {"error": "revoked"}))
    with pytest.raises(sieve_client.SieveAuthError):
        client.poll("s1")


# --------------------------------------------------------------------------- #
# Status handling
# --------------------------------------------------------------------------- #

def test_classify_running():
    info = sieve_client.classify_run({"status": "running", "session_id": "s1", "turns": 2})
    assert info["state"] == "running"
    assert info["terminal"] is False


def test_classify_done_with_schema_pass_is_presentable():
    info = sieve_client.classify_run(done_payload(schema="pass"))
    assert info["state"] == "done" and info["terminal"] is True
    assert info["present_as_clean"] is True
    assert info["files"][0]["url"] == "/files/out.csv"
    assert info["result"] == [{"text": "x", "author": "y"}]


def test_classify_done_schema_fail_is_never_clean():
    info = sieve_client.classify_run(done_payload(schema="fail"))
    assert info["schema_status"] == "fail"
    assert info["present_as_clean"] is False


def test_classify_done_schema_partial_flags_missing_columns():
    info = sieve_client.classify_run(done_payload(schema="partial"))
    assert info["schema_status"] == "partial"
    assert info["present_as_clean"] is True  # no violations, but status is surfaced


def test_classify_refused_terminal_with_code():
    info = sieve_client.classify_run({
        "status": "refused", "session_id": "s1",
        "refusal": {"code": "refusal.quota", "message": "out of credits"}})
    assert info["state"] == "refused" and info["terminal"] is True
    assert info["refusal_code"] == "refusal.quota"


def test_classify_unknown_status_is_an_error():
    with pytest.raises(sieve_client.SieveProtocolError):
        sieve_client.classify_run({"status": "weird", "session_id": "s1"})
    with pytest.raises(sieve_client.SieveProtocolError):
        sieve_client.classify_run({"session_id": "s1"})


def test_next_poll_delay_backs_off_and_caps():
    assert sieve_client.next_poll_delay(5.0) == 7.5
    assert sieve_client.next_poll_delay(1.0) == sieve_client.POLL_INITIAL_S
    assert sieve_client.next_poll_delay(1000.0) == sieve_client.POLL_MAX_S


def test_wait_for_completion_stops_at_terminal():
    client, _ = make_client(FakeResponse(200, {"status": "running", "turns": 0}),
                            FakeResponse(200, done_payload()))
    info = client.wait_for_completion("s1", sleep=lambda _s: None)
    assert info["state"] == "done"


# --------------------------------------------------------------------------- #
# Follow-up turn gate
# --------------------------------------------------------------------------- #

def test_wait_for_turn_requires_counter_to_advance():
    client, _ = make_client()
    seen = [
        {"status": "done", "session_id": "s1", "turns": 1},  # previous answer
        {"status": "done", "session_id": "s1", "turns": 2},  # the new turn
    ]
    calls = []

    def fake_poll(session_id, *, sleep=None):
        calls.append(session_id)
        return seen.pop(0)

    client.poll = fake_poll  # type: ignore[assignment]
    info = client.wait_for_turn("s1", prior_turns=1, sleep=lambda _s: None)
    assert info["turns"] == 2
    assert len(calls) == 2  # did NOT return the stale first answer


def test_wait_for_turn_returns_on_refusal():
    client, _ = make_client()
    client.poll = lambda session_id, *, sleep=None: {
        "status": "refused", "session_id": "s1", "turns": 1,
        "refusal": {"code": "refusal.quota"}}  # type: ignore[assignment]
    info = client.wait_for_turn("s1", prior_turns=1, sleep=lambda _s: None)
    assert info["state"] == "refused"


def test_wait_for_turn_times_out():
    client, _ = make_client()
    client.poll = lambda session_id, *, sleep=None: {
        "status": "running", "session_id": "s1", "turns": 1}  # type: ignore[assignment]
    ticks = iter([0.0, 0.0, 999.0])
    with pytest.raises(sieve_client.SieveError):
        client.wait_for_turn("s1", prior_turns=1, max_wait_s=10,
                             sleep=lambda _s: None, clock=lambda: next(ticks))


# --------------------------------------------------------------------------- #
# File download
# --------------------------------------------------------------------------- #

def test_download_file_prefixes_base_url_and_sends_bearer(tmp_path):
    client, session = make_client(FakeResponse(200, None, chunks=(b"a", b"b")))
    dest = client.download_file({"name": "out.csv", "url": "/files/out.csv"},
                                str(tmp_path))
    call = session.calls[0]
    assert call["url"] == "https://scrape.usesieve.com/files/out.csv"
    assert call["headers"]["Authorization"] == "Bearer dc_sk_test"
    assert open(dest, "rb").read() == b"ab"


# --------------------------------------------------------------------------- #
# Durable store + runner
# --------------------------------------------------------------------------- #

class StubClient:
    def __init__(self, start_payload=None, polls=None):
        self.start_payload = start_payload or {}
        self._polls = list(polls or [])
        self.started = []

    def start_run(self, instruction, **kwargs):
        self.started.append((instruction, kwargs))
        return self.start_payload

    def poll(self, session_id, *, sleep=None):
        item = self._polls.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def send_message(self, session_id, instruction, **kwargs):
        return {"status": "queued", "session_id": session_id}


def test_runner_persists_session_id_before_polling(tmp_path):
    store = sieve_client.SieveSessionStore(str(tmp_path / "runs.db"))
    client = StubClient({"status": "queued", "session_id": "sess-1"})
    runner = sieve_client.SieveRunner(client=client, store=store)
    result = runner.start("extract")
    assert result["session_id"] == "sess-1"
    row = store.get("sess-1")
    assert row is not None and row["status"] == "queued"


def test_runner_poll_once_marks_done(tmp_path):
    store = sieve_client.SieveSessionStore(str(tmp_path / "runs.db"))
    store.record_started("sess-1", "extract")
    client = StubClient(polls=[done_payload(turns=1, schema="pass")])
    runner = sieve_client.SieveRunner(client=client, store=store)
    info = runner.poll_once("sess-1")
    assert info["state"] == "done"
    row = store.get("sess-1")
    assert row["status"] == "done"
    assert row["files_json"] and json.loads(row["files_json"])[0]["name"] == "out.csv"


def test_runner_poll_once_schedules_backoff_while_running(tmp_path):
    store = sieve_client.SieveSessionStore(str(tmp_path / "runs.db"))
    store.record_started("sess-1", "extract")
    client = StubClient(polls=[{"status": "running", "session_id": "sess-1", "turns": 0}])
    runner = sieve_client.SieveRunner(client=client, store=store)
    runner.poll_once("sess-1")
    row = store.get("sess-1")
    assert row["status"] == "running"
    assert row["poll_delay_s"] > sieve_client.POLL_INITIAL_S
    assert row["next_poll_at"] is not None


def test_runner_followup_waits_for_turn_advance(tmp_path):
    store = sieve_client.SieveSessionStore(str(tmp_path / "runs.db"))
    store.record_started("sess-1", "extract")
    store.update("sess-1", turns=1, status="done")  # first answer already read
    client = StubClient(polls=[{"status": "running", "session_id": "sess-1", "turns": 1}])
    runner = sieve_client.SieveRunner(client=client, store=store)
    runner.send_followup("sess-1", "now do more")  # prior turn = 1, expects turn 2
    # A done-but-not-advanced turn must NOT be marked complete.
    client._polls = [{"status": "done", "session_id": "sess-1", "turns": 1}]
    info = runner.poll_once("sess-1")
    assert info["state"] == "done"
    assert store.get("sess-1")["status"] == "awaiting_turn"
    # Once the counter advances, the run settles.
    client._polls = [{"status": "done", "session_id": "sess-1", "turns": 2}]
    runner.poll_once("sess-1")
    assert store.get("sess-1")["status"] == "done"


def test_resume_only_polls_due_runs_and_survives_errors(tmp_path):
    store = sieve_client.SieveSessionStore(str(tmp_path / "runs.db"))
    store.record_started("sess-1", "extract")
    client = StubClient(polls=[sieve_client.SieveAuthError("revoked")])
    runner = sieve_client.SieveRunner(client=client, store=store)
    results = runner.resume(limit=5)
    assert results == []
    assert "revoked" in store.get("sess-1")["last_error"]


# --------------------------------------------------------------------------- #
# Device login
# --------------------------------------------------------------------------- #

def test_device_code_parsing_and_validation():
    code = sieve_client.device_code_from_payload({
        "device_code": "d", "user_code": "WDJB-MJHT",
        "verification_uri": "https://scrape.usesieve.com/device",
        "verification_uri_complete": "https://scrape.usesieve.com/device?code=WDJB-MJHT",
        "expires_in": 600, "interval": 5})
    assert code.user_code == "WDJB-MJHT"
    assert code.is_complete
    with pytest.raises(sieve_client.SieveProtocolError):
        sieve_client.device_code_from_payload({"user_code": "X"})


@pytest.mark.parametrize("error,state", [
    ("authorization_pending", "pending"),
    ("slow_down", "slow_down"),
    ("access_denied", "denied"),
    ("expired_token", "expired"),
])
def test_device_token_state_mapping(error, state):
    client, _ = make_client(FakeResponse(400, {"error": error}))
    assert client.poll_device_token("d")["state"] == state


def test_device_token_approved_returns_key():
    client, _ = make_client(FakeResponse(200, {"api_key": "dc_sk_new",
                                               "token_type": "Bearer", "key_name": "cli"}))
    result = client.poll_device_token("d")
    assert result["state"] == "approved"
    assert result["api_key"] == "dc_sk_new"


def test_device_token_unknown_error_raises():
    client, _ = make_client(FakeResponse(400, {"error": "???"}))
    with pytest.raises(sieve_client.SieveProtocolError):
        client.poll_device_token("d")


def test_device_code_request_has_no_auth_header():
    client, session = make_client(FakeResponse(200, {
        "device_code": "d", "user_code": "WDJB-MJHT",
        "verification_uri": "https://x", "verification_uri_complete": "https://x?c=1"}))
    client.request_device_code("llm-trader")
    assert "Authorization" not in session.calls[0]["headers"]


# --------------------------------------------------------------------------- #
# Background handler + wiring
# --------------------------------------------------------------------------- #

def test_handler_skips_when_unconfigured(monkeypatch):
    from operations.agents import sieve_agent

    monkeypatch.setattr(sieve_client, "is_configured", lambda: False)
    result = sieve_agent.handle_sieve_poll({}, None)
    assert result["status"] == "SKIPPED"
    assert result["execution_authority"] is False


def test_handler_polls_when_configured(monkeypatch):
    from operations.agents import sieve_agent

    class FakeRunner:
        def __init__(self):
            self.seen = None
            self.store = self

        def resume(self, limit=5, deadline_s=None):
            self.seen = (limit, deadline_s)
            return [{"state": "done"}, {"state": "refused"}]

        def status_counts(self):
            return {"done": 1, "refused": 1}

    monkeypatch.setattr(sieve_client, "is_configured", lambda: True)
    monkeypatch.setattr(sieve_client, "SieveRunner", FakeRunner)
    result = sieve_agent.handle_sieve_poll({"limit": 3}, None)
    assert result["polled"] == 2 and result["completed"] == 1 and result["refused"] == 1


def test_sieve_poll_is_wired_into_scheduler_and_executors():
    from operations import agent_scheduler, executors

    names = [item["name"] for item in agent_scheduler.SCHEDULES]
    assert "sieve_poll" in names
    spec = executors.REGISTRY["sieve_poll"]
    assert spec.principal == "ResearchAgent"
    handler = executors.resolve_handler(spec)
    assert handler is not None
    assert callable(handler)
