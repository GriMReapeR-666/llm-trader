"""Sieve scrape API integration (server-side only).

Sieve (https://scrape.usesieve.com) turns a plain-language ``instruction`` into
delivered files / validated JSON.  This module owns the whole contract:

* request building + ``Authorization: Bearer`` (key never logged, never echoed),
* error mapping (400/401/402/404/429/5xx) to typed exceptions,
* safe retry policy: GET/429/5xx are retried, but a POST that starts a run or a
  turn is **never** auto-retried after a timeout or network error, because the
  first call may already have succeeded and spent credits,
* durable session persistence written *before* any polling, so a crash resumes
  the existing run instead of starting a duplicate,
* run-status classification (running / done / refused / unknown-is-an-error),
* follow-up turn acknowledgement (done AND ``turns`` advanced),
* device-login primitives used by ``scripts/sieve_login.py``.

It reuses the repo's existing building blocks instead of adding new ones:
``core.http_retry.request_with_retry`` for safe GETs, ``requests.Session`` for
the connection, ``core.secure_store`` for the key, and a small sqlite table in
the same style as ``operations.alert_outbox`` for durable run records.

Behavior when SIEVE_API_KEY is absent: :func:`is_configured` is False and the
scheduled handler returns SKIPPED without touching the network, so the trading
loop is byte-for-byte unchanged.

ponytail: single sqlite file, poll cadence owned by the existing scheduler; if
run volume grows, swap the store for the journal state KV behind the same API.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

logger = logging.getLogger("SieveClient")

try:  # flat import under the repo's sys.path setup
    from http_retry import request_with_retry
except ImportError:  # pragma: no cover - package import path
    from core.http_retry import request_with_retry

try:
    from secure_store import retrieve_secret, store_secret
except ImportError:  # pragma: no cover - package import path
    from core.secure_store import retrieve_secret, store_secret


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://scrape.usesieve.com"
API_KEY_ENV = "SIEVE_API_KEY"
SECRET_STORE_KEY = "sieve:api_key"
DEFAULT_TIMEOUT = 30.0
STREAM_TIMEOUT = 120.0

#: Safe statuses to retry for the non-idempotent POSTs.  A 429/5xx means the
#: run was NOT created, so retrying cannot double-spend credits.
SAFE_POST_RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_POST_ATTEMPTS = 3

#: Poll cadence: start at 5s, back off to ~30s.  Runs take minutes.
POLL_INITIAL_S = 5.0
POLL_MAX_S = 30.0

#: Terminal-vs-transient status names from the API.
RUNNING = "running"
DONE = "done"
REFUSED = "refused"

COMPLIANCE_MODES = ("conservative", "regular", "yolo")

DEFAULT_DB = "sieve_runs.db"

#: Schema-conformance statuses that must never be presented as clean data.
SCHEMA_PASS = "pass"
SCHEMA_PARTIAL = "partial"
SCHEMA_FAIL = "fail"
SCHEMA_NOT_CHECKABLE = "not_checkable"
SCHEMA_NO_ARTIFACT = "no_artifact"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class SieveError(Exception):
    """Base error; ``retryable`` says whether a bounded retry is safe."""

    status: Optional[int] = None
    retryable = False

    def __init__(self, message: str, *, status: Optional[int] = None,
                 retryable: bool = False, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.detail = detail


class SieveNotConfigured(SieveError):
    """No API key available (env or secure store)."""


class SieveBadRequest(SieveError):
    """HTTP 400 — the request is wrong; do not retry, fix the payload."""


class SieveAuthError(SieveError):
    """HTTP 401 — key missing or revoked."""


class SievePaymentRequired(SieveError):
    """HTTP 402 — out of credits."""


class SieveNotFound(SieveError):
    """HTTP 404 — unknown session, or not owned by this key."""


class SieveRateLimited(SieveError):
    """HTTP 429 — retryable after ``retry_after`` seconds."""

    retryable = True

    def __init__(self, message: str, *, status: int = 429, retry_after: float = 0.0,
                 detail: Any = None) -> None:
        super().__init__(message, status=status, retryable=True, detail=detail)
        self.retry_after = max(0.0, float(retry_after or 0.0))


class SieveServerError(SieveError):
    """HTTP 5xx — safe to retry on GET, and safe to retry POST (no run created)."""

    retryable = True


class SieveTransportError(SieveError):
    """Network/timeout on a POST whose outcome is unknown — never auto-retry."""

    retryable = False


class SieveProtocolError(SieveError):
    """The API returned an unexpected status value or malformed payload."""


class SieveTurnInFlight(SieveError):
    """HTTP 409 on a follow-up — a turn is already running; wait then resend."""

    def __init__(self, message: str = "a turn is already in flight",
                 detail: Any = None) -> None:
        super().__init__(message, status=409, retryable=False, detail=detail)


def map_error(status: int, payload: Any = None, *, retry_after: float = 0.0) -> SieveError:
    """Map an HTTP status to a typed error.  400/401/402/404 are terminal."""
    detail = _error_detail(payload)
    return _map_error(status, detail, retry_after=retry_after)


def _map_error(status: int, detail: Any, *, retry_after: float = 0.0) -> SieveError:
    text = _error_message(detail)
    if status == 400:
        return SieveBadRequest(f"sieve rejected the request: {text}", status=400,
                               detail=detail)
    if status == 401:
        return SieveAuthError("Sieve API key missing or revoked", status=401, detail=detail)
    if status == 402:
        return SievePaymentRequired("Sieve account is out of credits", status=402,
                                    detail=detail)
    if status == 404:
        return SieveNotFound("Sieve resource not found (or not owned by this key)",
                             status=404, detail=detail)
    if status == 409:
        return SieveTurnInFlight(detail=detail)
    if status == 429:
        return SieveRateLimited("Sieve rate limited the request", status=429,
                                retry_after=retry_after or _retry_after(detail),
                                detail=detail)
    if 500 <= status <= 599:
        return SieveServerError(f"Sieve server error ({status}): {text}", status=status,
                                retryable=True, detail=detail)
    return SieveError(f"Sieve returned unexpected status {status}: {text}", status=status,
                      detail=detail)


def _error_detail(payload: Any) -> Any:
    if payload is None:
        return {}
    if isinstance(payload, (dict, list)):
        return payload
    return {"error": str(payload)[:500]}


def _error_message(detail: Any) -> str:
    if isinstance(detail, dict):
        for key in ("error", "detail", "message"):
            value = detail.get(key)
            if value:
                return str(value)[:500]
    return json.dumps(detail, default=str)[:500] if detail else "no detail"


def _retry_after(detail: Any) -> float:
    if isinstance(detail, dict):
        for key in ("retry_after", "retry_after_seconds"):
            try:
                return max(0.0, float(detail[key]))
            except (KeyError, TypeError, ValueError):
                continue
    return 0.0


# --------------------------------------------------------------------------- #
# API key resolution (never log the value)
# --------------------------------------------------------------------------- #

def get_api_key() -> str:
    """Resolve the key: ``SIEVE_API_KEY`` env (dotenv) first, then secure store."""
    key = (os.getenv(API_KEY_ENV) or "").strip()
    if key:
        return key
    try:
        return (retrieve_secret(SECRET_STORE_KEY) or "").strip()
    except Exception as exc:  # pragma: no cover - store backends are best-effort
        logger.debug("secure store read failed: %s", exc)
        return ""


def is_configured() -> bool:
    """True when a key is available; the only gate that touches the network."""
    return bool(get_api_key())


def store_api_key(api_key: str) -> str:
    """Persist a freshly minted key in the secure store.  Never logs it."""
    api_key = str(api_key or "").strip()
    if not api_key:
        raise ValueError("api_key is required")
    return store_secret(SECRET_STORE_KEY, api_key)


# --------------------------------------------------------------------------- #
# Status classification
# --------------------------------------------------------------------------- #

def classify_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a GET /api/scrapes/<id> payload.

    Only ``running``/``done``/``refused`` are valid; anything else raises
    :class:`SieveProtocolError` rather than being silently treated as success.
    """
    if not isinstance(payload, dict):
        raise SieveProtocolError(f"run payload is not an object: {type(payload).__name__}")
    status = str(payload.get("status") or "").strip().lower()
    turns = _int_or_zero(payload.get("turns"))
    if status == RUNNING:
        return {"status": RUNNING, "state": RUNNING, "terminal": False,
                "session_id": payload.get("session_id"), "turns": turns,
                "execution_authority": False}
    if status == REFUSED:
        refusal = payload.get("refusal") or {}
        code = str((refusal or {}).get("code") or "").strip() or "refused"
        return {"status": REFUSED, "state": REFUSED, "terminal": True,
                "session_id": payload.get("session_id"), "turns": turns,
                "refusal": _redact(refusal), "refusal_code": code,
                "summary": str(payload.get("summary") or "")[:1000],
                "execution_authority": False}
    if status == DONE:
        result = summarize_result(payload)
        result.update({"status": DONE, "state": DONE, "terminal": True,
                       "session_id": payload.get("session_id"), "turns": turns})
        return result
    raise SieveProtocolError(f"unknown run status: {status!r}")


def summarize_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a finished run: summary, files, schema conformance, inline result."""
    files = []
    for item in (payload.get("files") or []):
        if not isinstance(item, dict):
            continue
        files.append({"name": str(item.get("name") or ""),
                      "size": _int_or_zero(item.get("size")),
                      "ext": str(item.get("ext") or ""),
                      "url": str(item.get("url") or "")})
    conformance = payload.get("schema_conformance")
    conf_status = SCHEMA_NOT_CHECKABLE
    if isinstance(conformance, dict):
        conf_status = str(conformance.get("status") or SCHEMA_NOT_CHECKABLE).strip().lower()
        conformance = _redact(conformance)
    elif conformance is None:
        conformance = {}
    else:
        conf_status = SCHEMA_NOT_CHECKABLE
    # A "fail" run is still non-conforming after repair: never present as clean.
    presentable = conf_status not in (SCHEMA_FAIL,)
    return {
        "summary": str(payload.get("summary") or "")[:2000],
        "files": files,
        "schema_conformance": conformance,
        "schema_status": conf_status,
        "present_as_clean": presentable,
        "result": payload.get("result"),
        "execution_authority": False,
    }


def _redact(value: Any) -> Any:
    """Bound/redact payloads before they reach logs or the run store."""
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, dict):
        return {str(k)[:80]: _redact(v) for k, v in list(value.items())[:40]
                if not _secret_like(str(k))}
    if isinstance(value, list):
        return [_redact(v) for v in value[:40]]
    return value


def _secret_like(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("api_key", "apikey", "secret", "token", "password"))


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def next_poll_delay(previous: float) -> float:
    """5s -> ~30s exponential-ish backoff, capped."""
    return min(POLL_MAX_S, max(POLL_INITIAL_S, float(previous) * 1.5))


# --------------------------------------------------------------------------- #
# Durable run store
# --------------------------------------------------------------------------- #

_UPDATE_FIELDS = ("status", "turns", "expected_turns", "next_poll_at", "poll_delay_s",
                  "last_error",
                  "result_json", "files_json", "schema_conformance", "refusal_code",
                  "payload_json")


class SieveSessionStore:
    """Restart-safe run records (sqlite, same style as the alert outbox).

    The session id is committed *before* polling starts, so a crash resumes
    polling instead of starting a duplicate paid run.
    """

    def __init__(self, path: str = DEFAULT_DB) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("""CREATE TABLE IF NOT EXISTS sieve_sessions(
            session_id TEXT PRIMARY KEY,
            instruction TEXT NOT NULL,
            status TEXT NOT NULL,
            turns INTEGER NOT NULL DEFAULT 0,
            expected_turns INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            next_poll_at TEXT,
            poll_delay_s REAL NOT NULL DEFAULT 5.0,
            last_error TEXT,
            refusal_code TEXT,
            result_json TEXT,
            files_json TEXT,
            schema_conformance TEXT,
            payload_json TEXT
        )""")
        con.commit()
        return con

    def record_started(self, session_id: str, instruction: str,
                       response: Optional[Dict[str, Any]] = None) -> None:
        """Commit the run durably; callers do nothing else until this returns."""
        if not session_id:
            raise ValueError("session_id is required")
        now = _now()
        con = self._connect()
        try:
            con.execute("""INSERT OR REPLACE INTO sieve_sessions
                (session_id,instruction,status,turns,expected_turns,created_at,
                 updated_at,next_poll_at,poll_delay_s,payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (str(session_id), str(instruction)[:2000], "queued", 0, 0,
                         now, now, now, POLL_INITIAL_S,
                         json.dumps(_redact(response or {}), default=str)))
            con.commit()
        finally:
            con.close()

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM sieve_sessions WHERE session_id=?",
                              (str(session_id),)).fetchone()
        finally:
            con.close()
        return dict(row) if row is not None else None

    def active(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Non-terminal runs whose next poll is due."""
        now = _now()
        con = self._connect()
        try:
            rows = con.execute("""SELECT * FROM sieve_sessions
                WHERE status IN ('queued','running','awaiting_turn')
                  AND COALESCE(next_poll_at, created_at) <= ?
                ORDER BY COALESCE(next_poll_at, created_at), created_at
                LIMIT ?""", (now, int(limit))).fetchall()
        finally:
            con.close()
        return [dict(row) for row in rows]

    def update(self, session_id: str, **fields: Any) -> None:
        clean = {k: v for k, v in fields.items() if k in _UPDATE_FIELDS}
        if not clean:
            return
        clean["updated_at"] = _now()
        assignments = ",".join(f"{name}=?" for name in clean)
        values = list(clean.values()) + [str(session_id)]
        con = self._connect()
        try:
            con.execute(f"UPDATE sieve_sessions SET {assignments} WHERE session_id=?",
                        values)
            con.commit()
        finally:
            con.close()

    def status_counts(self) -> Dict[str, int]:
        con = self._connect()
        try:
            rows = con.execute("SELECT status,COUNT(*) AS n FROM sieve_sessions "
                               "GROUP BY status").fetchall()
        finally:
            con.close()
        return {str(row["status"]): int(row["n"]) for row in rows}


# --------------------------------------------------------------------------- #
# Device login primitives
# --------------------------------------------------------------------------- #

@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int

    @property
    def is_complete(self) -> bool:
        return bool(self.device_code and self.user_code and self.verification_uri_complete)


def device_code_from_payload(payload: Dict[str, Any]) -> DeviceCode:
    if not isinstance(payload, dict):
        raise SieveProtocolError("device/code response is not an object")
    code = DeviceCode(
        device_code=str(payload.get("device_code") or ""),
        user_code=str(payload.get("user_code") or ""),
        verification_uri=str(payload.get("verification_uri") or ""),
        verification_uri_complete=str(payload.get("verification_uri_complete") or ""),
        expires_in=_int_or_zero(payload.get("expires_in")) or 600,
        interval=_int_or_zero(payload.get("interval")) or 5,
    )
    if not code.is_complete:
        raise SieveProtocolError("device/code response missing required fields")
    return code


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class SieveClient:
    """Thin HTTP client.  Injects a ``requests.Session`` for connection reuse."""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 session: Any = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self._api_key = api_key
        self.base_url = (base_url or os.getenv("SIEVE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._session = session
        self.timeout = float(timeout)

    # -- plumbing ---------------------------------------------------------

    @property
    def api_key(self) -> str:
        return (self._api_key if self._api_key is not None else get_api_key()).strip()

    @property
    def session(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", str(path).lstrip("/"))

    def _headers(self) -> Dict[str, str]:
        key = self.api_key
        if not key:
            raise SieveNotConfigured("SIEVE_API_KEY is not configured")
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def _request(self, method: str, path: str, *, auth: bool = True,
                 **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", None) or {}
        if auth:
            headers = {**self._headers(), **headers}
        kwargs.setdefault("timeout", self.timeout)
        return self.session.request(method, self._url(path), headers=headers, **kwargs)

    @staticmethod
    def _json_or_text(response: Any) -> Any:
        try:
            return response.json()
        except Exception:
            return getattr(response, "text", "")

    def _handle_response(self, response: Any, *, allow_statuses: tuple = (200, 202)) -> Dict[str, Any]:
        status = int(response.status_code)
        payload = self._json_or_text(response)
        if status in allow_statuses:
            return payload if isinstance(payload, dict) else {"raw": payload}
        raise map_error(status, payload, retry_after=_header_float(response, "Retry-After"))

    def _post_with_safe_retries(self, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                                files: Any = None, auth: bool = True,
                                allow_statuses: tuple = (200, 202),
                                max_attempts: int = MAX_POST_ATTEMPTS,
                                sleep: Any = time.sleep) -> Dict[str, Any]:
        """POST with retries ONLY for 429/5xx; transport errors never auto-retry."""
        kwargs: Dict[str, Any] = {}
        if files is not None:
            kwargs["files"] = files
            if json_body:
                kwargs["data"] = {k: _form_value(v) for k, v in json_body.items()}
        else:
            kwargs["json"] = json_body or {}
        last_error: Optional[SieveError] = None
        for attempt in range(max(1, int(max_attempts))):
            try:
                response = self._request("POST", path, auth=auth, **kwargs)
            except SieveError:
                # Configuration/auth errors are not transport failures.
                raise
            except Exception as exc:
                # Unknown outcome: the first call may have succeeded.  Stop.
                raise SieveTransportError(
                    f"POST {path} failed before a response ({exc}); "
                    "not retried because the run may already exist"
                ) from exc
            status = int(response.status_code)
            if status in SAFE_POST_RETRY_STATUSES and attempt < max_attempts - 1:
                delay = _backoff_delay(attempt)
                retry_after = _header_float(response, "Retry-After")
                wait = max(delay, retry_after)
                logger.warning("sieve POST %s got %s; no run created — retry in %.1fs",
                               path, status, wait)
                sleep(wait)
                last_error = map_error(status, self._json_or_text(response))
                continue
            return self._handle_response(response, allow_statuses=allow_statuses)
        assert last_error is not None
        raise last_error

    # -- runs -------------------------------------------------------------

    def start_run(self, instruction: str, *, target_urls: Optional[List[str]] = None,
                  fields: Optional[List[str]] = None, schema: Optional[Any] = None,
                  output_schema: Optional[Any] = None,
                  table_shape: Optional[str] = None,
                  compliance_mode: str = "regular",
                  document_path: Optional[str] = None,
                  max_attempts: int = MAX_POST_ATTEMPTS,
                  sleep: Any = time.sleep) -> Dict[str, Any]:
        """POST /api/scrapes.  Returns the 202 payload including ``session_id``."""
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("instruction is required")
        mode = str(compliance_mode or "regular").strip().lower()
        if mode not in COMPLIANCE_MODES:
            raise ValueError(f"compliance_mode must be one of {COMPLIANCE_MODES}")
        body: Dict[str, Any] = {"instruction": instruction, "compliance_mode": mode}
        if target_urls:
            body["target_urls"] = list(target_urls)
        if fields:
            body["fields"] = list(fields)
        if schema is not None:
            body["schema"] = schema
        if output_schema is not None:
            body["output_schema"] = output_schema
        if table_shape:
            body["table_shape"] = str(table_shape)
        files = None
        if document_path:
            files = {"file": _open_binary(document_path)}
        try:
            payload = self._post_with_safe_retries("/api/scrapes", json_body=body,
                                                   files=files, max_attempts=max_attempts,
                                                   sleep=sleep)
        finally:
            if files is not None:
                _close_quietly(files["file"])
        session_id = str(payload.get("session_id") or "").strip()
        if str(payload.get("status") or "").lower() != "queued" or not session_id:
            raise SieveProtocolError("start_run succeeded without status=queued/session_id")
        return payload

    def poll(self, session_id: str, *, sleep: Any = time.sleep) -> Dict[str, Any]:
        """GET /api/scrapes/<id>.  Transient GET failures are retried safely."""
        if not session_id:
            raise ValueError("session_id is required")
        response = request_with_retry(
            self.session, "GET", self._url(f"/api/scrapes/{session_id}"),
            headers=self._headers(), timeout=self.timeout,
        )
        return self._handle_response(response, allow_statuses=(200,))

    def wait_for_completion(self, session_id: str, *, max_wait_s: Optional[float] = None,
                            sleep: Any = time.sleep, clock: Any = time.monotonic,
                            on_state: Any = None) -> Dict[str, Any]:
        """Poll until terminal.  Runs take minutes; never a short timeout."""
        delay = POLL_INITIAL_S
        started = clock()
        while True:
            info = classify_run(self.poll(session_id, sleep=sleep))
            if on_state is not None:
                on_state(info)
            if info["terminal"]:
                return info
            if max_wait_s is not None and (clock() - started) >= float(max_wait_s):
                raise SieveError(f"run {session_id} still running after {max_wait_s}s",
                                 retryable=True)
            sleep(delay)
            delay = next_poll_delay(delay)

    def send_message(self, session_id: str, instruction: str, **kwargs: Any) -> Dict[str, Any]:
        """POST a follow-up turn.  A 409 is surfaced, not retried blindly."""
        if not session_id:
            raise ValueError("session_id is required")
        body: Dict[str, Any] = {"instruction": str(instruction or "").strip()}
        for key in ("target_urls", "fields", "schema", "output_schema",
                    "table_shape", "compliance_mode"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        return self._post_with_safe_retries(
            f"/api/scrapes/{session_id}/messages", json_body=body,
            allow_statuses=(200, 202),
        )

    def wait_for_turn(self, session_id: str, prior_turns: int, *,
                      max_wait_s: Optional[float] = None, sleep: Any = time.sleep,
                      clock: Any = time.monotonic) -> Dict[str, Any]:
        """Poll until ``done`` AND ``turns`` advanced past ``prior_turns``.

        Reading before the turn counter advances yields the *previous* answer,
        so both conditions are required.
        """
        delay = POLL_INITIAL_S
        started = clock()
        while True:
            info = classify_run(self.poll(session_id, sleep=sleep))
            # A refusal is terminal for the turn too (the run never started).
            if info["state"] == REFUSED or (info["terminal"]
                                            and info["turns"] > int(prior_turns)):
                return info
            if max_wait_s is not None and (clock() - started) >= float(max_wait_s):
                raise SieveError(
                    f"turn for {session_id} not acknowledged after {max_wait_s}s "
                    f"(turns={info.get('turns')}, prior={prior_turns})", retryable=True)
            sleep(delay)
            delay = next_poll_delay(delay)

    def get_credits(self) -> Dict[str, Any]:
        """GET /api/me/credits — plan, limit, used, remaining."""
        response = request_with_retry(
            self.session, "GET", self._url("/api/me/credits"),
            headers=self._headers(), timeout=self.timeout,
        )
        return self._handle_response(response, allow_statuses=(200,))

    def download_file(self, file_info: Dict[str, Any], dest_dir: str) -> str:
        """Download one ``files[]`` entry (relative url + Bearer) to ``dest_dir``."""
        url = str((file_info or {}).get("url") or "").strip()
        if not url:
            raise ValueError("file url is required")
        absolute = url if url.startswith(("http://", "https://")) else self._url(url)
        name = str((file_info or {}).get("name") or "").strip() or Path(url).name or "sieve_file"
        target = Path(dest_dir)
        target.mkdir(parents=True, exist_ok=True)
        dest = target / Path(name).name
        response = self.session.request("GET", absolute, headers=self._headers(),
                                        timeout=STREAM_TIMEOUT, stream=True)
        status = int(response.status_code)
        if status != 200:
            raise map_error(status, self._json_or_text(response))
        with open(dest, "wb") as handle:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    handle.write(chunk)
        return str(dest)

    # -- device login -----------------------------------------------------

    def request_device_code(self, client_name: str = "llm-trader") -> DeviceCode:
        response = self._request("POST", "/api/auth/device/code", auth=False,
                                 json={"client_name": str(client_name or "llm-trader")})
        return device_code_from_payload(self._handle_response(response, allow_statuses=(200, 201)))

    def poll_device_token(self, device_code: str) -> Dict[str, Any]:
        """One poll.  Returns ``{state: pending|slow_down|denied|expired|approved,...}``.

        Never returns or logs the key except on ``approved`` (the caller writes
        it straight to the secure store).
        """
        response = self._request("POST", "/api/auth/device/token", auth=False,
                                 json={"device_code": str(device_code or "")})
        status = int(response.status_code)
        payload = self._json_or_text(response)
        if status == 200 and isinstance(payload, dict):
            api_key = str(payload.get("api_key") or "").strip()
            if not api_key:
                raise SieveProtocolError("device/token approved without an api_key")
            return {"state": "approved", "api_key": api_key,
                    "token_type": str(payload.get("token_type") or "Bearer"),
                    "key_name": str(payload.get("key_name") or "")}
        if status != 400:
            raise map_error(status, payload)
        error = str((payload or {}).get("error") if isinstance(payload, dict)
                    else payload).strip().lower()
        if error == "authorization_pending":
            return {"state": "pending"}
        if error == "slow_down":
            return {"state": "slow_down"}
        if error == "access_denied":
            return {"state": "denied"}
        if error == "expired_token":
            return {"state": "expired"}
        raise SieveProtocolError(f"unknown device/token error: {error!r}")


# --------------------------------------------------------------------------- #
# Runner: durable start + resume
# --------------------------------------------------------------------------- #

class SieveRunner:
    """Ties the client to the durable store so a crash resumes, not restarts."""

    def __init__(self, client: Optional[SieveClient] = None,
                 store: Optional[SieveSessionStore] = None) -> None:
        self.client = client or SieveClient()
        self.store = store or SieveSessionStore(os.getenv("SIEVE_DB") or DEFAULT_DB)

    def start(self, instruction: str, **kwargs: Any) -> Dict[str, Any]:
        """Start a run and persist its id BEFORE anything else can fail."""
        response = self.client.start_run(instruction, **kwargs)
        session_id = str(response["session_id"])
        self.store.record_started(session_id, instruction, response)
        logger.info("sieve run queued session=%s", session_id)
        return {"session_id": session_id, "status": "queued",
                "poll": response.get("poll") or f"/api/scrapes/{session_id}",
                "execution_authority": False}

    def poll_once(self, session_id: str, *, sleep: Any = time.sleep) -> Dict[str, Any]:
        """Poll one session, persist the observed state, and return it."""
        current = self.store.get(session_id) or {}
        info = classify_run(self.client.poll(session_id, sleep=sleep))
        turns = int(info.get("turns") or 0)
        expected = int(current.get("expected_turns") or 0)
        # A follow-up is only readable once ``turns`` has advanced past the
        # pre-turn value, even if the run already reports done.
        turn_settled = turns >= expected
        if info["terminal"] and turn_settled:
            self.store.update(session_id, status=info["state"], turns=turns,
                              next_poll_at=None,
                              last_error=None,
                              refusal_code=info.get("refusal_code"),
                              poll_delay_s=POLL_INITIAL_S,
                              schema_conformance=json.dumps(info.get("schema_conformance") or {},
                                                            default=str),
                              files_json=json.dumps(info.get("files") or [], default=str),
                              result_json=json.dumps(info.get("result"), default=str),
                              payload_json=json.dumps(_redact(info), default=str))
        else:
            delay = next_poll_delay(_float_or(current.get("poll_delay_s"), POLL_INITIAL_S))
            status = "awaiting_turn" if int(current.get("expected_turns") or 0) > turns else "running"
            self.store.update(session_id, status=status,
                              turns=max(turns, int(current.get("turns") or 0)),
                              next_poll_at=(datetime.now(timezone.utc)
                                            + timedelta(seconds=delay)).isoformat(),
                              poll_delay_s=delay,
                              last_error=None,
                              payload_json=json.dumps(_redact(info), default=str))
        return info

    def send_followup(self, session_id: str, instruction: str, **kwargs: Any) -> Dict[str, Any]:
        """Record the turn, send it, and leave the run awaiting the new answer."""
        current = self.store.get(session_id) or {}
        prior = int(current.get("turns") or 0)
        self.store.update(session_id, status="awaiting_turn",
                          expected_turns=prior + 1, last_error=None,
                          next_poll_at=_now())
        response = self.client.send_message(session_id, instruction, **kwargs)
        logger.info("sieve follow-up sent session=%s prior_turns=%s", session_id, prior)
        return {"session_id": session_id, "prior_turns": prior,
                "response": _redact(response), "execution_authority": False}

    def resume(self, limit: int = 5,
               deadline_s: Optional[float] = None) -> List[Dict[str, Any]]:
        """Poll due non-terminal runs.  Never lets one failure stop the batch.

        ``deadline_s`` bounds total wall-clock so the handler stays inside the
        scheduler's per-handler budget; un-polled runs remain due for the next
        cadence (the run's own ``next_poll_at`` is untouched).
        """
        results: List[Dict[str, Any]] = []
        started = time.monotonic()
        for row in self.store.active(limit=limit):
            if deadline_s is not None and (time.monotonic() - started) >= float(deadline_s):
                logger.info("sieve poll deadline reached; deferring remaining runs")
                break
            session_id = str(row.get("session_id"))
            try:
                results.append(self.poll_once(session_id))
            except SieveError as exc:
                self.store.update(session_id, last_error=str(exc)[:500],
                                  next_poll_at=(datetime.now(timezone.utc)
                                                + timedelta(seconds=POLL_MAX_S)).isoformat())
                logger.warning("sieve poll failed session=%s: %s", session_id, exc)
            except Exception as exc:  # noqa: BLE001 - must never break the pulse
                self.store.update(session_id, last_error=str(exc)[:500],
                                  next_poll_at=(datetime.now(timezone.utc)
                                                + timedelta(seconds=POLL_MAX_S)).isoformat())
                logger.error("sieve poll unexpected error session=%s: %s", session_id, exc)
        return results


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _backoff_delay(attempt: int) -> float:
    import random

    return 1.0 * (2 ** attempt) + random.uniform(0, 0.35)


def _header_float(response: Any, name: str) -> float:
    try:
        value = response.headers.get(name)
    except Exception:
        value = None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _form_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _open_binary(path: str) -> Any:
    return open(path, "rb")


def _close_quietly(handle: Any) -> None:
    try:
        handle.close()
    except Exception:
        pass
