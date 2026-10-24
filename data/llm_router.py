#!/usr/bin/env python3
"""
Multi-LLM Router - Unified interface for multiple LLM providers and agents
Supports: OpenRouter, Cerebras, NVIDIA NIM, TokenRouter, RoutesMe, OmniRouter
Features: Load balancing, failover, per-agent routing, rate limiting
"""

import os
import time
import logging
import random
import json
import re
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta, timezone
from collections import deque

logger = logging.getLogger("LLMRouter")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class LLMResponse(str):
    """String subclass that preserves reasoning traces, provider metadata, and usage tokens.

    Acts as a standard string for all consumers, comparisons, JSON parsing, and serialization,
    while exposing .reasoning, .provider, .model, and .usage attributes.
    """
    reasoning: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    def __new__(cls, content: str, reasoning: Optional[str] = None, provider: Optional[str] = None, model: Optional[str] = None, usage: Optional[Dict[str, Any]] = None):
        instance = super().__new__(cls, content)
        instance.reasoning = reasoning
        instance.provider = provider
        instance.model = model
        instance.usage = usage or {}
        return instance

    def get_reasoning(self) -> str:
        return self.reasoning or ""


class RateLimiter:
    """Simple per-provider rate limiter with an optional blocking cap.

    ``max_wait_seconds`` bounds how long a caller may be blocked. When the
    required wait exceeds the cap the limiter refuses immediately so a
    saturated provider cannot freeze the market pulse thread.
    """
    def __init__(self, rpm: int = 60, max_wait_seconds: Optional[float] = None):
        self.rpm = rpm
        self.max_wait_seconds = max_wait_seconds
        self.timestamps = deque()

    def wait_if_needed(self) -> bool:
        """Return True when the caller may proceed; False when the cap is hit."""
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t < 60]
        if len(self.timestamps) >= self.rpm:
            sleep_time = 60 - (now - self.timestamps[0]) + 0.5
            if sleep_time > 0:
                if self.max_wait_seconds is not None and sleep_time > self.max_wait_seconds:
                    logger.warning(
                        "Rate limit reached (%s RPM); required wait %.1fs exceeds cap %.1fs; failing fast",
                        self.rpm, sleep_time, self.max_wait_seconds,
                    )
                    return False
                logger.warning(f"Rate limit reached ({self.rpm} RPM). Sleeping {sleep_time:.1f}s...")
                time.sleep(sleep_time)
            self.timestamps = [t for t in self.timestamps if time.time() - t < 60]
        self.timestamps.append(time.time())
        return True


def parse_rate_limit_reset(exc: Any) -> Optional[float]:
    """Best-effort extraction of a quota-reset timestamp from a 429 error.

    Returns an absolute epoch timestamp (float seconds) or ``None`` when no
    reset information can be recovered. Understood signals, in order of
    trust:
      * ``Retry-After`` header (relative seconds)
      * ``X-RateLimit-Reset`` header as epoch ms, epoch s, or relative s
      * ISO-8601 timestamp embedded in the stringified error body
    Header source: an exception-attached response (the OpenAI SDK exposes
    ``exc.response.headers``), else the error text as a last resort so
    aggregated error strings still yield a usable reset.
    """
    headers = {}
    resp = getattr(exc, "response", None)
    raw_headers = getattr(resp, "headers", None)
    if raw_headers is not None:
        try:
            headers = {str(k).lower(): str(v) for k, v in dict(raw_headers).items()}
        except Exception:
            headers = {}
    text = str(exc or "")

    def _lookup(name: str) -> Optional[str]:
        value = headers.get(name)
        if value is None:
            match = re.search(
                name + r'["\':=\\s]+([0-9]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
            value = match.group(1) if match else None
        return value

    retry_after = _lookup("retry-after")
    if retry_after is not None:
        try:
            return time.time() + float(retry_after)
        except (TypeError, ValueError):
            pass

    reset = _lookup("x-ratelimit-reset")
    if reset is not None:
        try:
            value = float(reset)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            if value > 1e11:      # epoch milliseconds
                return value / 1000.0
            if value > 1e9:       # epoch seconds
                return value
            return time.time() + value  # relative seconds

    iso = re.search(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
        text,
    )
    if iso:
        try:
            parsed = datetime.fromisoformat(iso.group(0).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return None


class BaseLLMProvider:
    """Base class for LLM providers"""
    provider_name: str = "base"
    supports_temperature: bool = True
    supports_seed: bool = True

    def __init__(self, api_keys: List[str], **kwargs):
        self.api_keys = api_keys or []
        self.current_key_index = 0
        # Env-tunable defaults keep a slow or saturated provider from
        # freezing the market pulse thread (previous worst case ~3 min/call).
        self.rate_limiter = RateLimiter(
            kwargs.get("rpm", 60),
            max_wait_seconds=kwargs.get(
                "max_wait_seconds", float(os.getenv("LLM_ROUTER_MAX_RATE_WAIT", "15"))
            ),
        )
        self.timeout = kwargs.get("timeout", float(os.getenv("LLM_ROUTER_TIMEOUT", "30")))
        self.max_retries = kwargs.get("max_retries", 3)
        self.base_delay = kwargs.get("base_delay", 2)
        self.consecutive_failures = 0
        self.max_consecutive_failures = kwargs.get("max_consecutive_failures", 3)
        self.circuit_cooldown_seconds = kwargs.get("circuit_cooldown_seconds", 300.0)
        self.circuit_open_until = 0.0
        # Quota-pause: on a 429 carrying a reset timestamp, park the provider
        # until the reset instead of burning sleep-retries every decision.
        self.quota_paused_until = 0.0
        self.quota_pause_margin_seconds = kwargs.get(
            "quota_pause_margin_seconds",
            float(os.getenv("LLM_ROUTER_QUOTA_PAUSE_MARGIN", "30")),
        )
        self.quota_pause_max_seconds = kwargs.get(
            "quota_pause_max_seconds",
            float(os.getenv("LLM_ROUTER_QUOTA_PAUSE_MAX", "86400")),
        )
        self.quota_pause_min_seconds = kwargs.get(
            "quota_pause_min_seconds",
            float(os.getenv("LLM_ROUTER_QUOTA_PAUSE_MIN", "60")),
        )

    def is_circuit_open(self) -> bool:
        return time.time() < self.circuit_open_until

    def _record_success(self):
        self.consecutive_failures = 0
        self.quota_paused_until = 0.0

    def _record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.circuit_open_until = time.time() + self.circuit_cooldown_seconds
            logger.warning(f"⚡ Circuit breaker TRIPPED for provider '{self.provider_name}' until {self.circuit_open_until}")

    def _is_quota_paused(self) -> bool:
        return time.time() < self.quota_paused_until

    def _pause_until_reset(self, reset_ts: float) -> None:
        """Park the provider until a 429 quota reset (+ safety margin).

        Duration is clamped: a stale (past) reset pauses for zero seconds,
        and the total pause never exceeds ``quota_pause_max_seconds`` so a
        bogus header cannot take a provider out for days.
        """
        now = time.time()
        duration = min(
            max(0.0, reset_ts + self.quota_pause_margin_seconds - now),
            self.quota_pause_max_seconds,
        )
        self.quota_paused_until = now + duration
        if duration > 0:
            logger.warning(
                "Provider '%s' quota exhausted; pausing until quota reset "
                "(%.0fs, until %s)",
                self.provider_name,
                duration,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.quota_paused_until)),
            )

    def _get_next_key(self) -> Optional[str]:
        if not self.api_keys:
            return None
        key = self.api_keys[self.current_key_index % len(self.api_keys)]
        self.current_key_index += 1
        return key

    def generate(self, messages: list, model: str, **kwargs) -> str:
        raise NotImplementedError

    @staticmethod
    def _extract_message(completion: Any, provider_name: str):
        """Unwrap an OpenAI-SDK completion, treating a null/empty choices
        list as a provider failure instead of a crash.

        OpenRouter and similar aggregators return HTTP 200 with
        ``choices=null`` when the upstream model fails. Indexing
        ``completion.choices[0]`` there raised ``'NoneType' object is not
        subscriptable``, which masked the real upstream error and burned a
        provider slot. Raising here routes the failure through
        ``_retry_with_backoff`` -> circuit breaker -> router failover.
        """
        choices = getattr(completion, "choices", None)
        if not choices:
            upstream = getattr(completion, "error", None)
            detail = ""
            if isinstance(upstream, dict):
                detail = str(upstream.get("message") or upstream)
            elif upstream is not None:
                detail = str(upstream)
            raise ValueError(
                f"{provider_name}: upstream returned HTTP 200 with no choices"
                + (f": {detail[:200]}" if detail else " (upstream model failure)"))
        msg = choices[0].message
        usage = getattr(completion, "usage", None)
        if usage is not None:
            try:
                setattr(msg, "_usage", usage)
            except Exception:
                pass
        return msg

    def fetch_models(self) -> List[Dict[str, Any]]:
        """Query provider's live model catalogue via OpenAI SDK client.

        For aggregators (OpenRouter/TokenRouter) the live catalogue contains
        thousands of paid models; we filter to free-tier entries so the
        catalogue stays useful for 'best free model' selection.  On rate-limit
        or quota errors we return [] and let the caller fall back to the
        static list (no noisy 429 spam).
        """
        clients = getattr(self, "clients", None) or []
        if not clients:
            return []
        try:
            res = clients[0].models.list()
        except Exception as e:
            logger.debug("Live model fetch failed for %s: %s", self.provider_name, e)
            return []
        out = []
        for m in getattr(res, "data", []) or []:
            m_id = getattr(m, "id", None)
            if not m_id:
                continue
            if self.provider_name in ("openrouter", "tokenrouter"):
                if not (str(m_id).endswith(":free") or "free" in str(getattr(m, "name", "")).lower()):
                    continue
            out.append({
                "id": m_id,
                "name": getattr(m, "name", m_id),
                "provider": self.provider_name,
            })
        return out

    def _retry_with_backoff(self, request_fn: Callable, **kwargs) -> str:
        if self.is_circuit_open():
            return f"Error: Provider {self.provider_name} circuit open (cooling down)"
        if self._is_quota_paused():
            remaining = int(self.quota_paused_until - time.time())
            return (f"Error: Provider {self.provider_name} paused until quota reset "
                    f"(~{remaining}s remaining)")
        last_error = None
        for attempt in range(self.max_retries):
            try:
                if not self.rate_limiter.wait_if_needed():
                    self._record_failure()
                    return f"Error: {self.provider_name} rate-limit wait exceeded configured cap"
                result = request_fn(**kwargs)
                self._record_success()
                return result
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "rate limit" in error_str
                is_server_error = "500" in error_str or "502" in error_str or "503" in error_str

                if is_rate_limit:
                    reset_ts = parse_rate_limit_reset(e)
                    if (reset_ts is not None
                            and reset_ts - time.time() > self.quota_pause_min_seconds):
                        self._pause_until_reset(reset_ts)
                        self._record_failure()
                        remaining = int(self.quota_paused_until - time.time())
                        return (f"Error: {self.provider_name} quota exhausted (429); "
                                f"paused until quota reset (~{remaining}s remaining)")

                if (is_rate_limit or is_server_error) and attempt < self.max_retries - 1:
                    delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"Provider {self.provider_name} error ({'429' if is_rate_limit else '5xx'}). Retrying in {delay:.1f}s... (Attempt {attempt+1}/{self.max_retries})")
                    time.sleep(delay)
                    continue
                self._record_failure()
                return f"Error: {self.provider_name} failed: {e}"
        self._record_failure()
        return f"Error: Max retries exceeded for {self.provider_name}. Last error: {last_error}"


class OpenRouterProvider(BaseLLMProvider):
    provider_name = "openrouter"

    def __init__(self, api_keys: List[str], **kwargs):
        super().__init__(api_keys, **kwargs)
        self.clients = []
        if OPENAI_AVAILABLE:
            for key in self.api_keys:
                self.clients.append(OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=key,
                    timeout=self.timeout,
                    max_retries=0,
                ))

    def fetch_models(self) -> List[Dict[str, Any]]:
        models = super().fetch_models()
        if models:
            return models
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"User-Agent": "LLMRouter/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    payload = _json.loads(resp.read().decode())
                    out = []
                    for m in payload.get("data", []):
                        m_id = m.get("id")
                        if m_id and (m_id.endswith(":free") or "free" in str(m.get("name", "")).lower()):
                            out.append({"id": m_id, "name": m.get("name", m_id), "provider": self.provider_name})
                    if out:
                        return out
        except Exception as e:
            logger.debug("Public OpenRouter model fetch fallback failed: %s", e)
        return []

    def generate(self, messages: list, model: str, **kwargs) -> str:
        if not self.clients:
            return "Error: OpenRouter client not initialized. Check API keys."

        client = self.clients[self.current_key_index % len(self.clients)]
        self.current_key_index += 1

        def request():
            call_kwargs = dict(
                model=model,
                messages=messages,
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", 2500),
                seed=kwargs.get("seed", 42),
            )
            if kwargs.get("tools"):
                call_kwargs["tools"] = kwargs["tools"]
                if kwargs.get("tool_choice"):
                    call_kwargs["tool_choice"] = kwargs["tool_choice"]
            completion = client.chat.completions.create(**call_kwargs)
            # Return raw completion object for tool-call support
            return self._extract_message(completion, self.provider_name)

        return self._retry_with_backoff(request)


class TokenRouterProvider(BaseLLMProvider):
    provider_name = "tokenrouter"

    def __init__(self, api_keys: List[str], **kwargs):
        super().__init__(api_keys, **kwargs)
        self.clients = []
        base_url = os.getenv("TOKENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        if OPENAI_AVAILABLE:
            for key in self.api_keys:
                self.clients.append(OpenAI(
                    base_url=base_url,
                    api_key=key,
                    timeout=self.timeout,
                    max_retries=0,
                ))

    def generate(self, messages: list, model: str, **kwargs) -> str:
        if not self.clients:
            return "Error: TokenRouter client not initialized. Check TOKENROUTER_KEY."

        client = self.clients[self.current_key_index % len(self.clients)]
        self.current_key_index += 1

        def request():
            call_kwargs = dict(
                model=model,
                messages=messages,
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", 2500),
                seed=kwargs.get("seed", 42),
            )
            if kwargs.get("tools"):
                call_kwargs["tools"] = kwargs["tools"]
                if kwargs.get("tool_choice"):
                    call_kwargs["tool_choice"] = kwargs["tool_choice"]
            completion = client.chat.completions.create(**call_kwargs)
            return self._extract_message(completion, self.provider_name)

        return self._retry_with_backoff(request)


class RoutesMeProvider(BaseLLMProvider):
    provider_name = "routesme"

    def __init__(self, api_keys: List[str], **kwargs):
        rpm = kwargs.pop("rpm", 20)
        super().__init__(api_keys, rpm=rpm, **kwargs)
        self.clients = []
        base_url = os.getenv("ROUTESME_BASE_URL", "https://routesme.online/v1")
        if OPENAI_AVAILABLE:
            for key in self.api_keys:
                self.clients.append(OpenAI(
                    base_url=base_url,
                    api_key=key,
                    timeout=self.timeout,
                    max_retries=0,
                ))

    def generate(self, messages: list, model: str, **kwargs) -> str:
        if not self.clients:
            return "Error: RoutesMe client not initialized. Check ROUTESME_KEY."

        client = self.clients[self.current_key_index % len(self.clients)]
        self.current_key_index += 1
        target_model = model or "mistralai/mistral-nemo"

        def request():
            call_kwargs = dict(
                model=target_model,
                messages=messages,
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", 2500),
            )
            if kwargs.get("tools"):
                call_kwargs["tools"] = kwargs["tools"]
                if kwargs.get("tool_choice"):
                    call_kwargs["tool_choice"] = kwargs["tool_choice"]
            completion = client.chat.completions.create(**call_kwargs)
            return self._extract_message(completion, self.provider_name)

        return self._retry_with_backoff(request)


class OmniRouterProvider(BaseLLMProvider):
    """Self-hosted OmniRouter gateway (github.com/Godde3s/omnirouter).

    One OpenAI-compatible endpoint in front of the gateway's built-in bridges
    (Qwen / GLM / DeepSeek / Gemini / OpenCode Zen). ``model="auto"`` hands
    routing and failover to the gateway itself, which is what makes this a
    good fit for cheap, high-volume work (subagents, summaries, extraction)
    rather than the high-reasoning trading chain.

    Deliberately kept out of the trader/analyst/risk fallback lists: the
    gateway may be offline (it is a local process) and those agents must never
    silently drop onto it. Point an agent or subagent at it explicitly.
    """
    provider_name = "omnirouter"
    supports_seed = False  # free web bridges behind "auto" ignore seed

    def __init__(self, api_keys: List[str], **kwargs):
        rpm = kwargs.pop("rpm", 60)
        super().__init__(api_keys, rpm=rpm, **kwargs)
        self.clients = []
        base_url = os.getenv("OMNIROUTER_BASE_URL", "http://localhost:8080/v1")
        if OPENAI_AVAILABLE:
            for key in self.api_keys:
                self.clients.append(OpenAI(
                    base_url=base_url,
                    api_key=key,
                    timeout=self.timeout,
                    max_retries=0,
                ))

    def generate(self, messages: list, model: str, **kwargs) -> str:
        if not self.clients:
            return "Error: OmniRouter client not initialized. Check OMNIROUTER_KEY."

        client = self.clients[self.current_key_index % len(self.clients)]
        self.current_key_index += 1
        # An empty model means "let the gateway choose" — its own failover
        # chain across the built-in bridges.
        target_model = model or "auto"

        def request():
            call_kwargs = dict(
                model=target_model,
                messages=messages,
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", 2500),
            )
            if kwargs.get("tools"):
                call_kwargs["tools"] = kwargs["tools"]
                if kwargs.get("tool_choice"):
                    call_kwargs["tool_choice"] = kwargs["tool_choice"]
            completion = client.chat.completions.create(**call_kwargs)
            return self._extract_message(completion, self.provider_name)

        return self._retry_with_backoff(request)


class CerebrasProvider(BaseLLMProvider):
    provider_name = "cerebras"

    def __init__(self, api_keys: List[str], **kwargs):
        rpm = min(kwargs.pop("rpm", 30), 30)
        super().__init__(api_keys, rpm=rpm, **kwargs)
        self.clients = []
        if OPENAI_AVAILABLE:
            for key in self.api_keys:
                self.clients.append(OpenAI(
                    base_url="https://api.cerebras.ai/v1",
                    api_key=key,
                    timeout=self.timeout,
                    max_retries=0,
                ))

    def generate(self, messages: list, model: str, **kwargs) -> str:
        if not self.clients:
            return "Error: Cerebras client not initialized. Check CEREBRAS_API_KEY."

        client = self.clients[self.current_key_index % len(self.clients)]
        self.current_key_index += 1

        def request():
            call_kwargs = dict(
                model=model,
                messages=messages,
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", 2500),
                seed=kwargs.get("seed", 42),
            )
            if kwargs.get("tools"):
                call_kwargs["tools"] = kwargs["tools"]
                if kwargs.get("tool_choice"):
                    call_kwargs["tool_choice"] = kwargs["tool_choice"]
            completion = client.chat.completions.create(**call_kwargs)
            return self._extract_message(completion, self.provider_name)

        return self._retry_with_backoff(request)


class NvidiaNIMProvider(BaseLLMProvider):
    provider_name = "nvidia_nim"

    def __init__(self, api_keys: List[str], **kwargs):
        rpm = kwargs.pop("rpm", 60)
        super().__init__(api_keys, rpm=rpm, **kwargs)
        self.clients = []
        if OPENAI_AVAILABLE:
            for key in self.api_keys:
                self.clients.append(OpenAI(
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key=key,
                    timeout=self.timeout,
                    max_retries=0,
                ))

    def generate(self, messages: list, model: str, **kwargs) -> str:
        if not self.clients:
            return "Error: NVIDIA NIM client not initialized. Check NVIDIA_API_KEY."

        client = self.clients[self.current_key_index % len(self.clients)]
        self.current_key_index += 1

        nvidia_model = model if "/" in model else f"nvidia/{model}"
        if nvidia_model in {"meta/llama-3.2-11b-vision-instruct", "nvidia/meta/llama-3.2-11b-vision-instruct", "llama-3.2-11b-vision-instruct", "nvidia/nemotron-3-super-120b-a12b"}:
            nvidia_model = os.getenv("LLM_ROUTER_MODEL_NVIDIA_NIM", "meta/llama-3.3-70b-instruct")

        def request():
            call_kwargs = dict(
                model=nvidia_model,
                messages=messages,
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", 2500),
                seed=kwargs.get("seed", 42),
            )
            if kwargs.get("tools"):
                call_kwargs["tools"] = kwargs["tools"]
                if kwargs.get("tool_choice"):
                    call_kwargs["tool_choice"] = kwargs["tool_choice"]
            completion = client.chat.completions.create(**call_kwargs)
            return self._extract_message(completion, self.provider_name)

        return self._retry_with_backoff(request)


class LLMRouter:
    """
    Multi-LLM Router with agent-based routing, load balancing, and failover
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._load_config_from_env()
        self.providers: Dict[str, BaseLLMProvider] = {}
        self.agents: Dict[str, Dict[str, Any]] = {}
        self._init_providers()
        self._init_agents()

    def _load_config_from_env(self) -> Dict[str, Any]:
        config = {
            "providers": {
                "openrouter": {
                    "api_keys": self._get_env_keys("OPENROUTER_API_KEY"),
                    "rpm": int(os.getenv("OPENROUTER_RPM", "60")),
                    "enabled": bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("TOKENROUTER_KEY") or os.getenv("TOKENROUTER_API_KEY")),
                },
                "tokenrouter": {
                    "api_keys": self._get_env_keys("TOKENROUTER_KEY"),
                    "rpm": int(os.getenv("TOKENROUTER_RPM", "60")),
                    "enabled": bool(os.getenv("TOKENROUTER_KEY") or os.getenv("TOKENROUTER_API_KEY")),
                },
                "routesme": {
                    "api_keys": self._get_env_keys("ROUTESME_KEY"),
                    "rpm": int(os.getenv("ROUTESME_RPM", "20")),
                    "enabled": bool(os.getenv("ROUTESME_KEY") or os.getenv("ROUTESME_API_KEY")),
                },
                "cerebras": {
                    "api_keys": self._get_env_keys("CEREBRAS_API_KEY"),
                    "rpm": int(os.getenv("CEREBRAS_RPM", "30")),
                    "enabled": bool(os.getenv("CEREBRAS_API_KEY")),
                },
                "nvidia_nim": {
                    "api_keys": self._get_env_keys("NVIDIA_KEY"),
                    "rpm": int(os.getenv("NVIDIA_RPM", "60")),
                    "enabled": bool(os.getenv("NVIDIA_KEY_1") or os.getenv("NVIDIA_API_KEY")),
                },
                # Self-hosted gateway for simple/small-subagent work only.
                # Opt-in per agent; never a silent fallback for the reasoning chain.
                "omnirouter": {
                    "api_keys": self._get_env_keys("OMNIROUTER_KEY") or (
                        ["sk-omnirouter-local"] if (
                            os.getenv("OMNIROUTER_ENABLED", "").lower() in {"1", "true", "yes"}
                            or os.getenv("OMNIROUTER_AUTONOMOUS", "").lower() in {"1", "true", "yes"}
                        ) else []
                    ),
                    "rpm": int(os.getenv("OMNIROUTER_RPM", "60")),
                    "enabled": bool(
                        os.getenv("OMNIROUTER_KEY")
                        or os.getenv("OMNIROUTER_API_KEY")
                        or (os.getenv("OMNIROUTER_ENABLED", "").lower() in {"1", "true", "yes"}
                            or os.getenv("OMNIROUTER_AUTONOMOUS", "").lower() in {"1", "true", "yes"})
                    ),
                },
            },
            "agents": {
                "trader": {
                    "provider": os.getenv("TRADER_LLM_PROVIDER", "routesme"),
                    "model": os.getenv("TRADER_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                    "fallback_providers": os.getenv("TRADER_LLM_FALLBACK", "openrouter,tokenrouter,cerebras,nvidia_nim").split(","),
                    "allow_failover": os.getenv("LLM_TRADER_ALLOW_PROVIDER_FAILOVER", "false").lower() in {"1", "true", "yes"},
                },
                "analyst": {
                    "provider": os.getenv("ANALYST_LLM_PROVIDER", "routesme"),
                    "model": os.getenv("ANALYST_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                    "fallback_providers": os.getenv("ANALYST_LLM_FALLBACK", "cerebras,openrouter,tokenrouter,nvidia_nim").split(","),
                    "allow_failover": os.getenv("LLM_TRADER_ALLOW_PROVIDER_FAILOVER", "false").lower() in {"1", "true", "yes"},
                },
                "risk": {
                    "provider": os.getenv("RISK_LLM_PROVIDER", "routesme"),
                    "model": os.getenv("RISK_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                    "fallback_providers": os.getenv("RISK_LLM_FALLBACK", "nvidia_nim,openrouter,tokenrouter,cerebras").split(","),
                    "allow_failover": os.getenv("LLM_TRADER_ALLOW_PROVIDER_FAILOVER", "false").lower() in {"1", "true", "yes"},
                },
            },
            "global_fallback_order": [
                p.strip()
                for p in os.getenv(
                    "LLM_ROUTER_FALLBACK_ORDER",
                    "nvidia_nim,cerebras,openrouter,tokenrouter,routesme",
                ).split(",")
                if p.strip()
            ],
        }
        return config

    def _get_env_keys(self, prefix: str) -> List[str]:
        keys = []
        i = 1
        while True:
            key = os.getenv(f"{prefix}_{i}") or os.getenv(prefix) if i == 1 else os.getenv(f"{prefix}_{i}")
            if not key:
                break
            keys.append(key)
            i += 1
        if not keys and prefix == "NVIDIA_KEY":
            key = os.getenv("NVIDIA_API_KEY")
            if key:
                keys.append(key)
        if not keys and prefix in {"ROUTESME_KEY", "ROUTESME_API_KEY"}:
            for alt_env in ("ROUTESME_KEY", "ROUTESME_API_KEY"):
                val = os.getenv(alt_env)
                if val:
                    keys.append(val)
                    break
        if not keys and prefix in {"OMNIROUTER_KEY", "OMNIROUTER_API_KEY"}:
            for alt_env in ("OMNIROUTER_KEY", "OMNIROUTER_API_KEY"):
                val = os.getenv(alt_env)
                if val:
                    keys.append(val)
                    break
        if not keys and prefix in {"OPENROUTER_API_KEY", "TOKENROUTER_KEY", "TOKENROUTER_API_KEY"}:
            for alt_env in ("TOKENROUTER_KEY", "TOKENROUTER_API_KEY", "OPENROUTER_API_KEY"):
                val = os.getenv(alt_env)
                if val:
                    keys.append(val)
                    break
        return keys

    def _init_providers(self):
        provider_classes = {
            "openrouter": OpenRouterProvider,
            "tokenrouter": TokenRouterProvider,
            "routesme": RoutesMeProvider,
            "cerebras": CerebrasProvider,
            "nvidia_nim": NvidiaNIMProvider,
            "omnirouter": OmniRouterProvider,
        }
        for name, cfg in self.config.get("providers", {}).items():
            if cfg.get("enabled") and cfg.get("api_keys"):
                try:
                    provider_cls = provider_classes.get(name)
                    if provider_cls:
                        self.providers[name] = provider_cls(
                            api_keys=cfg["api_keys"],
                            rpm=cfg.get("rpm", 60),
                            timeout=cfg.get("timeout", 60),
                        )
                        logger.info(f"Initialized LLM provider: {name} ({len(cfg['api_keys'])} keys)")
                except Exception as e:
                    logger.warning(f"Failed to initialize provider {name}: {e}")

    def _init_agents(self):
        self.agents = self.config.get("agents", {})
        for agent_name, agent_cfg in self.agents.items():
            provider_name = agent_cfg.get("provider")
            if provider_name not in self.providers:
                logger.warning("Agent '%s' configured provider '%s' is unavailable; refusing silent provider substitution.",
                               agent_name, provider_name)
                agent_cfg["provider_unavailable"] = True

    def register_agent(self, name: str, provider: str, model: str, fallback_providers: Optional[List[str]] = None,
                       allow_failover: bool = False):
        self.agents[name] = {
            "provider": provider,
            "model": model,
            "fallback_providers": fallback_providers or [],
            "allow_failover": bool(allow_failover),
        }
        logger.info(f"Registered LLM agent '{name}' -> provider={provider}, model={model}")

    def get_available_providers(self) -> List[str]:
        return list(self.providers.keys())

    def _get_static_models(self, p: str) -> List[Dict[str, str]]:
        if p == "openrouter":
            return [
                {"id": "deepseek/deepseek-r1:free", "name": "DeepSeek R1 (Free)", "provider": "OpenRouter"},
                {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B (Free)", "provider": "OpenRouter"},
                {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash Exp (Free)", "provider": "OpenRouter"},
                {"id": "qwen/qwen-2.5-coder-32b-instruct:free", "name": "Qwen 2.5 Coder 32B (Free)", "provider": "OpenRouter"},
                {"id": "mistralai/mistral-small-24b-instruct-2501:free", "name": "Mistral Small 24B (Free)", "provider": "OpenRouter"},
                {"id": "meta-llama/llama-3.1-8b-instruct:free", "name": "Llama 3.1 8B (Free)", "provider": "OpenRouter"},
            ]
        elif p == "tokenrouter":
            return [
                {"id": "deepseek/deepseek-r1:free", "name": "DeepSeek R1 (Free)", "provider": "TokenRouter"},
                {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B (Free)", "provider": "TokenRouter"},
                {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash Exp (Free)", "provider": "TokenRouter"},
                {"id": "qwen/qwen-2.5-coder-32b-instruct:free", "name": "Qwen 2.5 Coder 32B (Free)", "provider": "TokenRouter"},
                {"id": "mistralai/mistral-small-24b-instruct-2501:free", "name": "Mistral Small 24B (Free)", "provider": "TokenRouter"},
            ]
        elif p == "routesme":
            return [
                {"id": "mistralai/mistral-nemo", "name": "Mistral Nemo (Free)", "provider": "RoutesMe"},
                {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B", "provider": "RoutesMe"},
            ]
        elif p == "cerebras":
            return [
                {"id": "llama-3.3-70b", "name": "Llama 3.3 70B", "provider": "Cerebras"},
                {"id": "deepseek-r1-distill-llama-70b", "name": "DeepSeek R1 Distill Llama 70B", "provider": "Cerebras"},
                {"id": "llama3.1-70b", "name": "Llama 3.1 70B", "provider": "Cerebras"},
                {"id": "llama3.1-8b", "name": "Llama 3.1 8B", "provider": "Cerebras"},
            ]
        elif p == "nvidia_nim":
            return [
                {"id": "meta/llama-3.3-70b-instruct", "name": "Llama 3.3 70B Instruct", "provider": "NVIDIA NIM"},
                {"id": "nvidia/llama-3.1-nemotron-70b-instruct", "name": "Llama 3.1 Nemotron 70B Instruct", "provider": "NVIDIA NIM"},
                {"id": "meta/llama-3.1-70b-instruct", "name": "Llama 3.1 70B Instruct", "provider": "NVIDIA NIM"},
                {"id": "meta/llama-3.1-8b-instruct", "name": "Llama 3.1 8B Instruct", "provider": "NVIDIA NIM"},
                {"id": "deepseek-ai/deepseek-r1", "name": "DeepSeek R1", "provider": "NVIDIA NIM"},
            ]
        elif p == "omnirouter":
            return [
                {"id": "auto", "name": "Auto (gateway failover)", "provider": "OmniRouter"},
            ]
        return []

    # Quality ranking of free models per provider (best first). Used by
    # select_best_free_model() to pick a sensible default when no explicit
    # model is configured, and to choose a provider-appropriate model id
    # during failover instead of reusing the primary's (possibly foreign) id.
    FREE_MODEL_RANKING = {
        "openrouter": [
            "deepseek/deepseek-r1:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "mistralai/mistral-small-24b-instruct-2501:free",
            "meta-llama/llama-3.1-8b-instruct:free",
        ],
        "tokenrouter": [
            "deepseek/deepseek-r1:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "mistralai/mistral-small-24b-instruct-2501:free",
        ],
        "cerebras": [
            "llama-3.3-70b",
            "deepseek-r1-distill-llama-70b",
            "llama3.1-70b",
            "llama3.1-8b",
        ],
        "nvidia_nim": [
            "meta/llama-3.3-70b-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            "deepseek-ai/deepseek-r1",
        ],
        "routesme": [
            "mistralai/mistral-nemo",
            "meta-llama/llama-3.3-70b-instruct",
        ],
        "omnirouter": ["auto"],
    }

    # Catalogue entries that are free but not general-purpose chat models.
    # They should never be chosen to drive a trading decision.
    _NON_CHAT_MARKERS = (
        "embed", "parse", "guard", "safety", "reward", "detector", "nvclip",
        "deplot", "riva-", "cosmos", "video", "diffusion", "calibration",
        "starcoder", "codellama", "codegemma", "coder", "code-",
    )

    @staticmethod
    def _model_size_b(model_id: str) -> float:
        """Approximate parameter count in billions parsed from the id.

        Deterministic quality proxy for catalogue entries that are not in the
        curated ranking. Unknown sizes score 0.
        """
        import re as _re

        matches = _re.findall(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", str(model_id or ""))
        best = 0.0
        for raw in matches:
            try:
                best = max(best, float(raw))
            except (TypeError, ValueError):
                continue
        return best

    def _best_cached_free_model(self, provider: str, cached: List[Dict[str, Any]]) -> Optional[str]:
        """Pick a sensible general-purpose free model from a live catalogue.

        Used only when no curated ranking entry is available. Prefers
        general-purpose chat models and, among them, the largest parameter
        count advertised in the id (tie-break: id) so selection is
        deterministic instead of depending on catalogue order.
        """
        candidates = []
        for m in cached or []:
            mid = str(m.get("id") or "")
            if not mid or not self._is_free_model(mid):
                continue
            lower = mid.lower()
            if any(marker in lower for marker in self._NON_CHAT_MARKERS):
                continue
            # A ':free' suffix is an explicit price declaration; other ids
            # are accepted only for providers whose whole catalogue is free.
            free_suffix = 1 if lower.endswith(":free") else 0
            candidates.append((self._model_size_b(mid), free_suffix, mid))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return candidates[0][2]

    def select_best_free_model(self, provider: str) -> Optional[str]:
        """Return the best available free model id for *provider*.

        Preference order: curated quality ranking that is present in the live
        catalogue -> largest general-purpose free catalogue entry ->
        curated ranking default (provider may not be reachable). Returns None
        when the provider has no known free model.
        """
        ranking = self.FREE_MODEL_RANKING.get(provider, [])
        cached = getattr(self, "_catalogue_cache", {}).get(provider)
        available_ids = {m.get("id") for m in (cached or []) if m.get("id")}
        if available_ids:
            for mid in ranking:
                if mid in available_ids:
                    return mid
            best_cached = self._best_cached_free_model(provider, cached)
            if best_cached:
                return best_cached
        if ranking:
            return ranking[0]
        return None

    def resolve_model_for_provider(self, provider: str, requested_model: Optional[str] = None) -> str:
        """Ensure the model id is valid and supported by the target provider.

        Substitutes obsolete, incompatible, or nonexistent model identifiers
        with the provider's best available active model.
        """
        best_default = self.select_best_free_model(provider) or "auto"
        if not requested_model:
            return best_default

        req = requested_model.strip()

        # Check provider-specific syntax and known valid catalogue
        static_models = [m["id"] for m in self._get_static_models(provider)]
        cached = [m.get("id") for m in getattr(self, "_catalogue_cache", {}).get(provider, []) if m.get("id")]
        valid_ids = set(static_models + cached)

        if req in valid_ids:
            return req

        # Provider compatibility transformations
        if provider == "cerebras":
            if "llama-3.3-70b" in req:
                return "llama-3.3-70b"
            if "llama-3.1-70b" in req or "llama3.1-70b" in req:
                return "llama3.1-70b"
            if "llama-3.1-8b" in req or "llama3.1-8b" in req:
                return "llama3.1-8b"
            if "deepseek" in req or "r1" in req:
                return "deepseek-r1-distill-llama-70b"
            return best_default

        elif provider == "nvidia_nim":
            clean_id = req.replace(":free", "")
            if not clean_id.startswith(("meta/", "nvidia/", "deepseek-ai/")):
                if clean_id.startswith("llama"):
                    clean_id = f"meta/{clean_id}"
                else:
                    clean_id = f"nvidia/{clean_id}"
            if clean_id in valid_ids:
                return clean_id
            if "llama-3.3-70b" in req:
                return "meta/llama-3.3-70b-instruct"
            if "nemotron" in req:
                return "nvidia/llama-3.1-nemotron-70b-instruct"
            if "deepseek" in req or "r1" in req:
                return "deepseek-ai/deepseek-r1"
            return best_default

        elif provider in ("openrouter", "tokenrouter"):
            stale_models = {
                "gpt-oss-120b", "qwen-3.8-27b", "openai/gpt-oss-20b",
                "nvidia/nemotron-3-super-120b-a12b:free", "qwen/qwen3-8b:free",
                "meta/llama-3.2-11b-vision-instruct", "meta-llama/llama-3.2-11b-vision-instruct:free",
            }
            if req in stale_models:
                return best_default
            if req.endswith(":free"):
                return req
            if f"{req}:free" in valid_ids:
                return f"{req}:free"
            return req if "/" in req else best_default

        elif provider == "routesme":
            if req in {"mistralai/mistral-nemo", "meta-llama/llama-3.3-70b-instruct"}:
                return req
            return best_default

        elif provider == "omnirouter":
            return req or "auto"

        return req or best_default

    @staticmethod
    def _is_free_model(model_id: str) -> bool:
        """Heuristic: a model id is 'free' if it carries a ':free' suffix
        or is a known free-tier id (nvidia_nim / cerebras / routesme)."""
        if not model_id:
            return False
        if model_id.endswith(":free"):
            return True
        free_prefixes = (
            "nvidia/", "meta/llama", "meta-llama/llama", "deepseek",
            "llama-3.3", "llama3.1", "mistralai/mistral-nemo", "google/gemini", "qwen/",
        )
        return model_id.startswith(free_prefixes)

    def fetch_model_catalogue(self, provider: Optional[str] = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch live model catalogue from provider(s) via OpenAI client with caching."""
        if not hasattr(self, "_catalogue_cache"):
            self._catalogue_cache = {}
            self._catalogue_cache_ts = {}

        now = time.time()
        providers = [provider] if provider else list(self.providers.keys())
        results = []

        for p in providers:
            if not force_refresh and p in self._catalogue_cache and (now - self._catalogue_cache_ts.get(p, 0)) < 300:
                results.extend(self._catalogue_cache[p])
                continue

            inst = self.providers.get(p)
            fetched = []
            if inst and hasattr(inst, "fetch_models"):
                fetched = inst.fetch_models()

            if fetched:
                self._catalogue_cache[p] = fetched
                self._catalogue_cache_ts[p] = now
                results.extend(fetched)
            else:
                fallback = self._get_static_models(p)
                results.extend(fallback)

        return results

    def get_available_models(self, provider: Optional[str] = None, dynamic: bool = False) -> List[Dict[str, str]]:
        if dynamic:
            return self.fetch_model_catalogue(provider=provider)
        models = []
        providers = [provider] if provider else list(self.providers.keys())
        for p in providers:
            if hasattr(self, "_catalogue_cache") and p in self._catalogue_cache:
                models.extend(self._catalogue_cache[p])
            else:
                models.extend(self._get_static_models(p))
        return models

    def configure(self, active_providers: Optional[List[str]] = None, max_keys_per_provider: int = 0, provider_models: Optional[Dict[str, str]] = None):
        if active_providers:
            filtered = {}
            for name, prov in self.providers.items():
                if name in active_providers:
                    if max_keys_per_provider > 0:
                        prov.api_keys = prov.api_keys[:max_keys_per_provider]
                    filtered[name] = prov
            self.providers = filtered
            logger.info(f"LLM Router configured providers: {list(self.providers.keys())}")

        if max_keys_per_provider > 0:
            for prov in self.providers.values():
                if len(prov.api_keys) > max_keys_per_provider:
                    prov.api_keys = prov.api_keys[:max_keys_per_provider]

        if provider_models:
            for agent_name, agent_cfg in self.agents.items():
                agent_cfg["provider_models"] = provider_models
                if agent_cfg.get("provider") in provider_models:
                    agent_cfg["model"] = provider_models[agent_cfg["provider"]]
                if "fallback_providers" in agent_cfg:
                    updated_fallbacks = []
                    for fb in agent_cfg["fallback_providers"]:
                        if fb in provider_models:
                            updated_fallbacks.append(fb)
                    agent_cfg["fallback_providers"] = updated_fallbacks
            logger.info(f"LLM Router configured models: {provider_models}")

    def generate(self, agent: str, messages: list, model: Optional[str] = None, **kwargs) -> str:
        if agent not in self.agents:
            return f"Error: Unknown agent '{agent}'. Available: {list(self.agents.keys())}"

        agent_cfg = self.agents[agent]
        # Per-provider model map: explicit config wins, then env defaults
        # (LLM_ROUTER_MODEL_<PROVIDER>) so failover sends a model id the
        # target provider actually serves instead of reusing the primary's.
        provider_models = dict(agent_cfg.get("provider_models", {}))
        for _prov in self.providers:
            _env_model = os.getenv("LLM_ROUTER_MODEL_" + _prov.upper())
            if _env_model and _prov not in provider_models:
                provider_models[_prov] = _env_model
        provider_name = agent_cfg.get("provider")
        fallbacks = agent_cfg.get("fallback_providers", [])

        errors = []

        # Agent-level config is authoritative when explicitly set; the env
        # var is only a fallback default for agents that don't specify.
        if "allow_failover" in agent_cfg:
            allow_failover = bool(agent_cfg["allow_failover"])
        else:
            allow_failover = os.getenv(
                "LLM_TRADER_ALLOW_PROVIDER_FAILOVER", "false").lower() in {"1", "true", "yes"}
        providers_to_try = [provider_name]
        if allow_failover:
            providers_to_try += [f for f in fallbacks if f in self.providers]
        elif fallbacks:
            errors.append("provider failover disabled; selected provider is authoritative")
        for prov in providers_to_try:
            if prov not in self.providers:
                errors.append(f"Provider '{prov}' not initialized")
                continue

            provider = self.providers[prov]
            if getattr(provider, "_is_quota_paused", None) and provider._is_quota_paused():
                remaining = int(getattr(provider, "quota_paused_until", 0) - time.time())
                errors.append(
                    f"{prov}: paused until quota reset (~{remaining}s remaining); skipped")
                continue
            model_for_provider = provider_models.get(prov)
            if not model_for_provider:
                raw_candidate = model or agent_cfg.get("model", "")
                model_for_provider = self.resolve_model_for_provider(prov, raw_candidate)
            else:
                model_for_provider = self.resolve_model_for_provider(prov, model_for_provider)
            try:
                result = provider.generate(messages, model_for_provider, **kwargs)
                # Handle both message objects (new) and strings (legacy)
                if hasattr(result, 'content'):
                    # Message object from OpenAI SDK
                    text = result.content or ""
                    reasoning = (
                        getattr(result, "reasoning_content", None)
                        or getattr(result, "reasoning", None)
                        or (getattr(result, "model_extra", {}).get("reasoning") if isinstance(getattr(result, "model_extra", None), dict) else None)
                        or (getattr(result, "model_extra", {}).get("reasoning_content") if isinstance(getattr(result, "model_extra", None), dict) else None)
                    )
                    usage = getattr(result, "_usage", None)
                    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else (dict(usage) if usage else {})

                    # If text is empty/None but reasoning was generated (reasoning models)
                    if not text and reasoning:
                        logger.info("Provider '%s' returned reasoning only (%d chars); surfacing as content", prov, len(reasoning))
                        text = str(reasoning)

                    if text:
                        if reasoning:
                            logger.info("LLM reasoning trace captured (%d chars) from %s", len(reasoning), prov)
                        return LLMResponse(
                            text,
                            reasoning=reasoning,
                            provider=prov,
                            model=model_for_provider,
                            usage=usage_dict,
                        )
                    # Tool calls present but no text — return tool call info
                    if hasattr(result, 'tool_calls') and result.tool_calls:
                        import json as _json
                        return _json.dumps({
                            "tool_calls": [
                                {"name": tc.function.name, "arguments": tc.function.arguments}
                                for tc in result.tool_calls
                            ]
                        })
                    errors.append(f"{prov}: empty response")
                elif isinstance(result, str):
                    if result and not result.startswith("Error:"):
                        reasoning = None
                        think_match = re.search(r"<think>(.*?)</think>", result, re.DOTALL)
                        if think_match:
                            reasoning = think_match.group(1).strip()
                        return LLMResponse(
                            result,
                            reasoning=reasoning,
                            provider=prov,
                            model=model_for_provider,
                        )
                    errors.append(f"{prov}: {result}")
                else:
                    errors.append(f"{prov}: unexpected response type")
            except Exception as e:
                errors.append(f"{prov}: {e}")

        # GAP-12: never leave callers with an unstructured error string that
        # might be misinterpreted as a trade instruction. Emit a machine-readable
        # HOLD so execution stays fail-closed.
        return self.deterministic_hold_fallback(agent, errors)

    @staticmethod
    def deterministic_hold_fallback(agent: str, errors: Optional[List[str]] = None) -> str:
        """Hardcoded HOLD payload when every LLM provider is unavailable."""
        payload = {
            "action": "HOLD",
            "decision": "HOLD",
            "confidence": 0.0,
            "deterministic_fallback": True,
            "reasoning": (
                f"Deterministic HOLD fallback: all LLM providers failed for agent '{agent}'. "
                "No new risk may be taken until a provider recovers."
            ),
            "self_reflection": "Provider total failure; safety HOLD engaged.",
            "confluence_explanation": "N/A — deterministic fallback",
            "entry": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "provider_errors": list(errors or []),
        }
        logger.error(
            "All LLM providers failed for agent '%s'; returning deterministic HOLD. Errors: %s",
            agent, "; ".join(errors or []),
        )
        return json.dumps(payload)

    def generate_with_tools(
        self, agent: str, messages: list, tools: List[Dict[str, Any]],
        tool_executor: Callable, model: Optional[str] = None,
        max_tool_rounds: int = 5, **kwargs,
    ) -> str:
        """Multi-turn tool-calling loop.

        Sends messages + tool schemas to the LLM. If the LLM responds with
        tool calls, executes them via ``tool_executor``, appends results,
        and re-queries.  Repeats until the LLM returns plain text or
        ``max_tool_rounds`` is exhausted.

        Args:
            agent: LLM agent name (e.g. 'trader').
            messages: Conversation history.
            tools: OpenAI-format tool schemas (list of dicts).
            tool_executor: ``fn(name: str, args: dict) -> str`` that runs a tool.
            model: Optional model override.
            max_tool_rounds: Max iterations before returning last response.

        Returns:
            Final text response from the LLM.
        """
        import json as _json
        current_messages = list(messages)
        for _round in range(max_tool_rounds):
            raw = self.generate(
                agent, current_messages, model=model,
                tools=tools, tool_choice="auto", **kwargs,
            )
            # If raw is an error string or deterministic fallback, return as-is
            if isinstance(raw, str) and (raw.startswith("Error:") or raw.startswith("{\"action\": \"HOLD\"")):
                return raw
            # If the response is a message object with tool calls
            if hasattr(raw, 'tool_calls') and raw.tool_calls:
                # Append the assistant message with tool calls
                current_messages.append({
                    "role": "assistant",
                    "content": raw.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in raw.tool_calls
                    ],
                })
                # Execute each tool call and append results
                for tc in raw.tool_calls:
                    try:
                        args = _json.loads(tc.function.arguments)
                    except Exception:
                        args = {}
                    try:
                        result_str = tool_executor(tc.function.name, args)
                    except Exception as e:
                        result_str = _json.dumps({"error": str(e)[:300]})
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })
                logger.info(
                    "Tool calling round %d/%d: executed %d tools",
                    _round + 1, max_tool_rounds, len(raw.tool_calls),
                )
                continue
            # Plain text response — extract and return
            if isinstance(raw, str):
                return raw
            if hasattr(raw, 'content') and raw.content:
                return raw.content
            # Empty response
            return raw if isinstance(raw, str) else str(raw)
        # Exhausted all rounds — return last response
        logger.warning("Tool calling exhausted %d rounds", max_tool_rounds)
        return raw if isinstance(raw, str) else (raw.content if hasattr(raw, 'content') else str(raw))

    def get_status(self) -> Dict[str, Any]:
        return {
            "providers": {
                name: {
                    "status": (
                        "PAUSED_QUOTA"
                        if getattr(p, "_is_quota_paused", None) and p._is_quota_paused()
                        else "ONLINE"
                    ),
                    "keys": len(p.api_keys),
                    "rpm": p.rate_limiter.rpm,
                    "quota_paused_until": getattr(p, "quota_paused_until", 0.0),
                }
                for name, p in self.providers.items()
            },
            "agents": {
                name: {
                    "provider": cfg.get("provider"),
                    "model": cfg.get("model"),
                    "fallbacks": cfg.get("fallback_providers", []),
                    "allow_failover": bool(cfg.get("allow_failover", False)),
                    "provider_unavailable": bool(cfg.get("provider_unavailable", False)),
                }
                for name, cfg in self.agents.items()
            },
        }


# Singleton instance
_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def reset_router():
    global _router
    _router = None


# ---------------------------------------------------------------------------
# Token Budget Enforcement
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English prose)."""
    return max(1, len(text) // 4)


def enforce_token_budget(
    messages: list,
    max_total_tokens: int = 24000,
    model_name: str = "",
) -> list:
    """Truncate the system message to fit within a token budget.

    Models with small context windows (e.g. LLaMA-3.3-70B @ 32k) can fail
    when the assembled prompt exceeds their limit. This function keeps the
    user message intact (it carries the latest live data) and progressively
    trims the system message from the bottom (less critical sections last).

    Args:
        messages: The ``[system, user]`` message list.
        max_total_tokens: Hard cap on total prompt tokens.
        model_name: For logging.

    Returns:
        Possibly-truncated message list.
    """
    if not messages:
        return messages
    total = sum(estimate_tokens(m.get("content", "")) for m in messages)
    if total <= max_total_tokens:
        return messages

    # Only truncate system message — user message is live market data
    system_idx = None
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            system_idx = i
            break
    if system_idx is None:
        return messages

    system_text = messages[system_idx]["content"]
    excess = total - max_total_tokens
    target_len = max(1000, len(system_text) - excess * 4)  # ~4 chars/token

    if len(system_text) > target_len:
        # Keep header (first 2000 chars) + trim tail
        header = system_text[:2000]
        remaining = system_text[2000:]
        trimmed = remaining[:max(0, target_len - 2000)]
        messages[system_idx] = {
            "role": "system",
            "content": header + trimmed + "\n\n[TRUNCATED: prompt exceeded token budget]",
        }
        new_total = sum(estimate_tokens(m.get("content", "")) for m in messages)
        logger.warning(
            "Token budget enforced for %s: %d -> %d tokens (cap %d)",
            model_name or "unknown", total, new_total, max_total_tokens,
        )
    return messages
