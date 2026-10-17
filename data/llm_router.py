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
        return choices[0].message

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
                    "api_keys": self._get_env_keys("OMNIROUTER_KEY"),
                    "rpm": int(os.getenv("OMNIROUTER_RPM", "60")),
                    "enabled": bool(os.getenv("OMNIROUTER_KEY") or os.getenv("OMNIROUTER_API_KEY")),
                },
            },
            "agents": {
                "trader": {
                    "provider": os.getenv("TRADER_LLM_PROVIDER", "routesme"),
                    "model": os.getenv("TRADER_LLM_MODEL", "meta/llama-3.2-11b-vision-instruct"),
                    "fallback_providers": os.getenv("TRADER_LLM_FALLBACK", "openrouter,tokenrouter,cerebras,nvidia_nim").split(","),
                    "allow_failover": os.getenv("LLM_TRADER_ALLOW_PROVIDER_FAILOVER", "false").lower() in {"1", "true", "yes"},
                },
                "analyst": {
                    "provider": os.getenv("ANALYST_LLM_PROVIDER", "routesme"),
                    "model": os.getenv("ANALYST_LLM_MODEL", "meta/llama-3.2-11b-vision-instruct"),
                    "fallback_providers": os.getenv("ANALYST_LLM_FALLBACK", "cerebras,openrouter,tokenrouter,nvidia_nim").split(","),
                    "allow_failover": os.getenv("LLM_TRADER_ALLOW_PROVIDER_FAILOVER", "false").lower() in {"1", "true", "yes"},
                },
                "risk": {
                    "provider": os.getenv("RISK_LLM_PROVIDER", "routesme"),
                    "model": os.getenv("RISK_LLM_MODEL", "meta/llama-3.2-11b-vision-instruct"),
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

    def get_available_models(self, provider: Optional[str] = None) -> List[Dict[str, str]]:
        models = []
        providers = [provider] if provider else list(self.providers.keys())
        for p in providers:
            if p == "openrouter":
                models.extend([
                    {"id": "mistralai/mistral-nemo", "name": "Mistral Nemo (Free)", "provider": "OpenRouter"},
                    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B (Free)", "provider": "OpenRouter"},
                    {"id": "qwen/qwen3-8b", "name": "Qwen3 8B (Free)", "provider": "OpenRouter"},
                    {"id": "openai/gpt-4-turbo", "name": "GPT-4 Turbo (Paid)", "provider": "OpenRouter"},
                ])
            elif p == "routesme":
                models.extend([
                    {"id": "mistralai/mistral-nemo", "name": "Mistral Nemo (Free)", "provider": "RoutesMe"},
                ])
            elif p == "cerebras":
                models.extend([
                    {"id": "gpt-oss-120b", "name": "GPT-OSS 120B (billing-blocked)", "provider": "Cerebras"},
                ])
            elif p == "nvidia_nim":
                models.extend([
                    {"id": "meta/llama-3.2-11b-vision-instruct", "name": "Llama 3.2 11B Vision Instruct", "provider": "NVIDIA NIM"},
                    {"id": "openai/gpt-oss-20b", "name": "GPT-OSS 20B", "provider": "NVIDIA NIM"},
                ])
            elif p == "omnirouter":
                models.extend([
                    {"id": "auto", "name": "Auto (gateway failover across built-in bridges)", "provider": "OmniRouter"},
                    {"id": "qwen/qwen3.8-max", "name": "Qwen3.8 Max (built-in bridge)", "provider": "OmniRouter"},
                    {"id": "glm/glm-5.3-flash", "name": "GLM 5.3 Flash (built-in bridge)", "provider": "OmniRouter"},
                    {"id": "gemini/gemini-3.6-flash", "name": "Gemini 3.6 Flash (built-in bridge)", "provider": "OmniRouter"},
                ])
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
            model_for_provider = provider_models.get(prov, model or agent_cfg.get("model", ""))
            try:
                result = provider.generate(messages, model_for_provider, **kwargs)
                # Handle both message objects (new) and strings (legacy)
                if hasattr(result, 'content'):
                    # Message object from OpenAI SDK
                    text = result.content or ""
                    if text:
                        return text
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
                        return result
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
