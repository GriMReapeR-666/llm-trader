"""Bounded dual-velocity workers for continuous trading maintenance.

The fast lane is intended for market/position attention and the slow lane for
memory distillation, macro reviews, and research.  This module never talks to
MT5, never creates order intents, and never grants execution authority.  The
caller remains responsible for durable task claims in ``AgentOperations``;
these workers only prevent slow work from blocking the pulse thread.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any, Callable, Dict, Optional


@dataclass
class _Job:
    lane: str
    key: str
    future: Future
    submitted_at: float


class DualVelocityExecutor:
    """Run bounded fast/slow jobs with idempotent keys and safe result polling."""

    def __init__(self, *, fast_workers: int = 1, slow_workers: int = 1,
                 max_pending_fast: int = 32, max_pending_slow: int = 8):
        for value, name in ((fast_workers, "fast_workers"), (slow_workers, "slow_workers"),
                            (max_pending_fast, "max_pending_fast"), (max_pending_slow, "max_pending_slow")):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._limits = {"fast": max_pending_fast, "slow": max_pending_slow}
        self._executors = {
            "fast": ThreadPoolExecutor(max_workers=fast_workers, thread_name_prefix="trading-fast"),
            "slow": ThreadPoolExecutor(max_workers=slow_workers, thread_name_prefix="trading-slow"),
        }
        self._jobs: Dict[str, _Job] = {}
        self._lock = Lock()
        self._closed = False

    def _submit(self, lane: str, key: str, function: Callable[..., Any], *args: Any,
                **kwargs: Any) -> Dict[str, Any]:
        if lane not in self._executors:
            raise ValueError("lane must be fast or slow")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("job key is required")
        if not callable(function):
            raise ValueError("job function must be callable")
        key = key.strip()
        with self._lock:
            if self._closed:
                return {"status": "CLOSED", "lane": lane, "key": key,
                        "execution_authority": False}
            existing = self._jobs.get(key)
            if existing and not existing.future.done():
                return {"status": "DEDUPLICATED", "lane": existing.lane, "key": key,
                        "execution_authority": False}
            if existing:
                self._jobs.pop(key, None)
            pending = sum(1 for item in self._jobs.values()
                          if item.lane == lane and not item.future.done())
            if pending >= self._limits[lane]:
                return {"status": "BACKPRESSURE", "lane": lane, "key": key,
                        "pending": pending, "limit": self._limits[lane],
                        "execution_authority": False}
            started = monotonic()
            future = self._executors[lane].submit(function, *args, **kwargs)
            self._jobs[key] = _Job(lane, key, future, started)
        return {"status": "SUBMITTED", "lane": lane, "key": key,
                "execution_authority": False}

    def submit_fast(self, key: str, function: Callable[..., Any], *args: Any,
                    **kwargs: Any) -> Dict[str, Any]:
        """Submit non-blocking market/position attention work."""
        return self._submit("fast", key, function, *args, **kwargs)

    def submit_slow(self, key: str, function: Callable[..., Any], *args: Any,
                    **kwargs: Any) -> Dict[str, Any]:
        """Submit non-blocking memory/research work."""
        return self._submit("slow", key, function, *args, **kwargs)

    def poll(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        """Collect completed results without raising worker exceptions."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("poll limit must be a positive integer")
        completed: list[Dict[str, Any]] = []
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if len(completed) >= limit or not job.future.done():
                continue
            try:
                result = job.future.result()
                status, error = "COMPLETED", ""
            except Exception as exc:  # worker errors are telemetry, not crashes
                result, status, error = None, "FAILED", str(exc)[:1000]
            item = {"lane": job.lane, "key": job.key, "status": status,
                    "result": result, "error": error,
                    "latency_seconds": max(0.0, monotonic() - job.submitted_at),
                    "execution_authority": False}
            completed.append(item)
            with self._lock:
                current = self._jobs.get(job.key)
                if current is job:
                    self._jobs.pop(job.key, None)
        return completed

    def status(self) -> Dict[str, Any]:
        with self._lock:
            pending = {lane: sum(1 for job in self._jobs.values()
                                 if job.lane == lane and not job.future.done())
                       for lane in self._executors}
            return {"pending": pending, "limits": dict(self._limits),
                    "closed": self._closed, "execution_authority": False}

    def shutdown(self, *, wait: bool = False) -> None:
        """Stop accepting jobs; optionally wait for already-submitted work."""
        with self._lock:
            self._closed = True
        for executor in self._executors.values():
            executor.shutdown(wait=wait, cancel_futures=True)

