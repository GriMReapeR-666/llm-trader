"""ContextBuffer — Conversational context continuity for the LLM orchestrator.

Per TRADING_MEMORY_SPEC.md §1 "Short-term working memory", the buffer holds
the last N messages so the LLM receives continuity across cycles without
accumulating unbounded history.  When the buffer grows beyond ``max_messages``,
older entries are compressed into a structured summary while the raw trace
remains in L0 (SQLite append-only).

**Compact context mode (R1, payload-flow audit 2026-09-29).**  Legacy mode
replays every prior cycle's full ~30K market-data user payload plus its
assistant turn, so real prompts average ~2.4× the 60K-char sanitizer budget
and mostly contain stale fallback HOLDs.  When ``LLM_TRADER_COMPACT_CONTEXT``
is truthy (``1``/``true``/``yes``), the buffer keeps only the last
``LLM_TRADER_CONTEXT_HISTORY_WINDOW`` decision cycles (default 1 → 2 messages:
one user+assistant pair) and folds evicted cycles into ≤300-char summaries.
Both env vars are read at instantiation; default off = unchanged behavior.

Design principles (from spec):
- Capital preservation is the primary objective.
- Memory can explain or adjust an LLM hypothesis, but it can never place
  an order or override a hard rule.
- Every memory item must include a source and timestamp.
- Never silently drop a risk decision or unresolved error.

Orchestrator API (intelligence/llm_orchestrator.py lines 1173-1252):

    self.memory.set_system_prompt(system_prompt)
    self.memory.add_message("user", user_prompt)
    messages = self.memory.get_messages()
    self.memory.add_message("assistant", response_content)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes"}


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(0, value)


def compact_context_enabled() -> bool:
    """R1 opt-in flag (payload-flow audit): rolling-window + summaries mode."""
    return _env_enabled("LLM_TRADER_COMPACT_CONTEXT")


@dataclass
class ContextMessage:
    """A single message in the conversational context window."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Risk decisions and errors are never silently dropped
    pinned: bool = False  # pinned messages survive compression

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ContextMessage":
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", 0.0),
            metadata=d.get("metadata", {}),
            pinned=d.get("pinned", False),
        )


class ContextBuffer:
    """Bounded conversational context buffer with compression.

    Holds the last ``max_messages`` messages for the LLM orchestrator.
    When the buffer exceeds the limit, non-pinned older messages are
    compressed into a summary entry.  Pinned messages (risk decisions,
    unresolved errors) are never dropped.

    The system prompt is stored separately and always included as the
    first message in ``get_messages()``.

    Usage::

        buf = ContextBuffer(max_messages=10)
        buf.set_system_prompt("You are a trading assistant.")
        buf.add_message("user", "Analyze EURUSD H1")
        buf.add_message("assistant", "EURUSD shows bearish structure...")

        # Get messages for LLM prompt
        messages = buf.get_messages()
        # → [{"role": "system", "content": "..."}, {"role": "user", ...}, ...]

        # Compact mode (LLM_TRADER_COMPACT_CONTEXT=1): same API, but only the
        # last decision cycles are replayed and evicted cycles become short
        # "Previous context:" summaries.

        # Persist to disk
        buf.save("brain/context_buffer.json")

        # Restore from disk
        buf = ContextBuffer.load("brain/context_buffer.json")
    """

    def __init__(self, max_messages: int = 10, summary_threshold: int = 15):
        self.max_messages = max_messages
        self.summary_threshold = summary_threshold
        self._system_prompt: str = ""
        self._messages: deque[ContextMessage] = deque(maxlen=max_messages)
        self._summaries: List[str] = []
        self._total_tokens_estimate: int = 0
        # R1 compact mode (opt-in via LLM_TRADER_COMPACT_CONTEXT): history
        # window is expressed in whole decision cycles (one user+assistant
        # pair per cycle), so 2 * window messages survive.
        self.compact = compact_context_enabled()
        self.history_window = max(1, _env_int("LLM_TRADER_CONTEXT_HISTORY_WINDOW", 1))
        self.summary_char_limit = max(80, _env_int("LLM_TRADER_CONTEXT_SUMMARY_CHARS", 300))
        self.summary_retention = max(2, _env_int("LLM_TRADER_CONTEXT_SUMMARY_RETENTION", 4))

    # ── Orchestrator API ────────────────────────────────────────────

    def set_system_prompt(self, system_prompt: str) -> None:
        """Set the system prompt.  Always included as first message."""
        self._system_prompt = system_prompt

    def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        pinned: bool = False,
    ) -> None:
        """Add a message to the buffer.  Orchestrator-facing alias for ``add()``."""
        self.add(role, content, metadata=metadata, pinned=pinned)

    def get_messages(self, include_summaries: bool = True,
                     trade_memory: Any = None, lesson_limit: int = 6) -> List[Dict[str, Any]]:
        """Return messages suitable for LLM prompt construction.

        Returns a list of dicts with ``role`` and ``content`` keys, matching
        the OpenAI/Anthropic chat format expected by the LLM router.

        The system prompt is always first.  Compressed summaries are
        prepended as an additional system message when available.
        If ``trade_memory`` (a TradingMemory instance) is provided, approved
        journal lessons are injected as a system message after the prompt.
        """
        result: List[Dict[str, Any]] = []

        # 1. System prompt (always first)
        if self._system_prompt:
            result.append({
                "role": "system",
                "content": self._system_prompt,
            })

        # 2. Approved journal lessons from TradingMemory
        if trade_memory is not None:
            try:
                lessons_text = trade_memory.format_approved_lessons(limit=lesson_limit)
                if lessons_text and lessons_text != "No approved journal lessons yet.":
                    result.append({
                        "role": "system",
                        "content": f"Approved journal lessons:\n{lessons_text}",
                    })
            except Exception:
                pass

        # 3. Compressed historical context
        if include_summaries and self._summaries:
            summary_text = "Previous context: " + " | ".join(self._summaries[-3:])
            result.append({
                "role": "system",
                "content": summary_text,
            })

        # 3. Current messages
        for msg in self._messages:
            result.append(msg.to_dict())

        return result

    def discard_failed_cycle(self) -> None:
        """R3 (payload-flow audit 2026-09-29): replace the just-added failed
        decision cycle with a short summary note.

        The orchestrator calls this right after appending a deterministic
        fallback (or otherwise failed) assistant reply, so the full
        market-data user payload is never replayed in later prompts — its
        information content after a failed call is a HOLD note, not a
        decision.  The note is retained as a ``[cycle-failed]`` summary, so
        nothing is silently dropped (spec: unresolved errors are never
        silently discarded).  Works in both legacy and compact modes; in
        compact mode it also prevents the failed pair from consuming the
        rolling window."""
        user_idx = next(
            (i for i in range(len(self._messages) - 1, -1, -1)
             if self._messages[i].role == "user"),
            None,
        )
        if user_idx is None:
            return
        evicted = [user_idx]
        if (
            user_idx + 1 < len(self._messages)
            and self._messages[user_idx + 1].role == "assistant"
        ):
            evicted.append(user_idx + 1)
        assistant_msg = (
            self._messages[user_idx + 1] if len(evicted) == 2 else None
        )
        fallback = bool(
            assistant_msg and assistant_msg.metadata.get("deterministic_fallback")
        )
        summary = self._cycle_summary(
            self._messages[user_idx].content,
            assistant_msg.content if assistant_msg else "",
        )
        if fallback:
            summary = f"[cycle-failed] {summary}"
        for idx in sorted(evicted, reverse=True):
            del self._messages[idx]
        self._summaries.append(summary)
        if len(self._summaries) > self.summary_retention:
            self._summaries[:] = self._summaries[-self.summary_retention:]
        # Legacy overflow compressor still guards pathological growth.
        if len(self._messages) >= self.max_messages:
            self._compress()

    # ── R1 compact-mode helpers ─────────────────────────────────────

    def _cycle_summary(self, user_content: str, assistant_content: str) -> str:
        """Fold one decision cycle (user payload + assistant reply) into a
        short summary line.  Best-effort: extracts the assistant action and
        keeps a head snippet of the user payload."""
        head = " ".join(user_content.split())
        if not head:
            head = "(empty market-data payload)"
        head = head[:120]
        action = ""
        if assistant_content:
            try:
                parsed = json.loads(assistant_content)
                if isinstance(parsed, dict):
                    action = str(parsed.get("action") or parsed.get("decision") or "").strip()
            except (json.JSONDecodeError, ValueError):
                action = ""
        if not action:
            action = " ".join(assistant_content.split())[:40]
        return f"[cycle] {head}... action={action or 'n/a'}"[: self.summary_char_limit]

    def _enforce_compact_window(self) -> None:
        """Compact mode: keep only the last ``history_window`` decision cycles
        in the live window and fold evicted cycles into short summaries.

        A cycle is the (user, assistant) pair appended by the orchestrator
        each decision.  Cycles whose assistant reply carries
        ``deterministic_fallback`` metadata are recorded as failures in the
        summary — they carry no decision information and must not consume
        the rolling window.  Pinned messages (risk decisions, unresolved
        errors) are never evicted, per the TRADING_MEMORY_SPEC guarantee.
        Legacy (compact=False) buffers are untouched."""
        if not self.compact:
            return
        max_messages = self.history_window * 2
        while len(self._messages) > max_messages:
            user_msg = next(
                (m for m in self._messages if m.role == "user" and not m.pinned),
                None,
            )
            if user_msg is None:
                # Only pinned users remain — nothing may be evicted.
                break
            idx = self._messages.index(user_msg)
            assistant_msg = (
                self._messages[idx + 1]
                if idx + 1 < len(self._messages) and self._messages[idx + 1].role == "assistant"
                else None
            )
            fallback = bool(assistant_msg and assistant_msg.metadata.get("deterministic_fallback"))
            summary = self._cycle_summary(
                user_msg.content,
                assistant_msg.content if assistant_msg else "",
            )
            if fallback:
                summary = f"[cycle-failed] {summary}"
            self._summaries.append(summary)
            for msg in (user_msg, assistant_msg):
                if msg is not None and msg in self._messages:
                    self._messages.remove(msg)
            if len(self._messages) <= max_messages:
                break

        # Never let summaries grow unbounded either.
        if len(self._summaries) > self.summary_retention:
            self._summaries[:] = self._summaries[-self.summary_retention:]

    # ── Internal API ────────────────────────────────────────────────

    def add(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        pinned: bool = False,
    ) -> None:
        """Append a message to the context buffer.

        If the buffer is full, the oldest non-pinned messages are
        compressed into a summary.
        """
        msg = ContextMessage(
            role=role,
            content=content,
            metadata=metadata or {},
            pinned=pinned,
        )

        if self.compact:
            # R1: trim to the rolling decision-cycle window first, then let
            # the legacy overflow compressor handle pathological cases (e.g.
            # assistant turns without a user partner).
            self._enforce_compact_window()
            if len(self._messages) >= self.max_messages:
                self._compress()
        elif len(self._messages) >= self.max_messages:
            # If at capacity, compress oldest non-pinned messages
            self._compress()

        self._messages.append(msg)
        self._total_tokens_estimate += len(content.split())

    def _compress(self) -> None:
        """Compress oldest non-pinned messages into a summary.

        Preserves pinned messages (risk decisions, errors).  The summary
        is kept separately and prepended as context when needed.
        """
        if not self._messages:
            return

        # Find oldest non-pinned messages to compress
        to_compress: List[ContextMessage] = []
        for msg in self._messages:
            if not msg.pinned:
                to_compress.append(msg)
            if len(to_compress) >= 3:  # compress in batches of 3
                break

        if not to_compress:
            # All messages are pinned — cannot compress further
            return

        # Build summary
        summary_parts = []
        for msg in to_compress:
            role_label = msg.role.upper()
            snippet = msg.content[:120].replace("\n", " ")
            summary_parts.append(f"[{role_label}] {snippet}")

        summary = " | ".join(summary_parts)
        self._summaries.append(summary)

        # Remove compressed messages from the deque
        compressed_ids = {id(m) for m in to_compress}
        remaining = [m for m in self._messages if id(m) not in compressed_ids]
        self._messages.clear()
        for m in remaining:
            self._messages.append(m)

        logger.debug("Context compressed: %d messages -> summary", len(compressed_ids))

    def get_pinned(self) -> List[ContextMessage]:
        """Return all pinned messages (risk decisions, errors)."""
        return [m for m in self._messages if m.pinned]

    def clear(self) -> None:
        """Clear all messages and summaries."""
        self._messages.clear()
        self._summaries.clear()
        self._total_tokens_estimate = 0

    @property
    def size(self) -> int:
        """Current number of messages in the buffer (excluding system prompt)."""
        return len(self._messages)

    @property
    def token_estimate(self) -> int:
        """Rough token count estimate (words * 1.3)."""
        sys_tokens = len(self._system_prompt.split()) if self._system_prompt else 0
        return int((self._total_tokens_estimate + sys_tokens) * 1.3)

    @property
    def summary_count(self) -> int:
        """Number of compressed summaries retained."""
        return len(self._summaries)

    # ── Persistence ─────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Persist buffer state to disk as JSON."""
        data = {
            "max_messages": self.max_messages,
            "summary_threshold": self.summary_threshold,
            "system_prompt": self._system_prompt,
            "messages": [m.to_dict() for m in self._messages],
            "summaries": self._summaries,
            "total_tokens_estimate": self._total_tokens_estimate,
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("ContextBuffer saved to %s (%d messages)", path, len(self._messages))

    @classmethod
    def load(cls, path: str) -> "ContextBuffer":
        """Restore buffer state from a JSON file.

        Returns a fresh buffer if the file does not exist or is corrupted.
        """
        p = Path(path)
        if not p.exists():
            logger.info("No context buffer at %s, starting fresh", path)
            return cls()

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            buf = cls(
                max_messages=data.get("max_messages", 10),
                summary_threshold=data.get("summary_threshold", 15),
            )
            buf._system_prompt = data.get("system_prompt", "")
            for msg_dict in data.get("messages", []):
                buf._messages.append(ContextMessage.from_dict(msg_dict))
            buf._summaries = data.get("summaries", [])
            buf._total_tokens_estimate = data.get("total_tokens_estimate", 0)
            if buf.compact:
                # A legacy buffer saved with the full replay window can exceed
                # the compact window; trim immediately (evicted cycles are
                # folded into summaries) and cap restored summaries.
                buf._enforce_compact_window()
                if len(buf._summaries) > buf.summary_retention:
                    buf._summaries[:] = buf._summaries[-buf.summary_retention:]
            logger.info("ContextBuffer restored from %s (%d messages)", path, len(buf._messages))
            return buf
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Corrupted context buffer at %s: %s — starting fresh", path, exc)
            return cls()
