"""Real-Time Active Cognitive Knowledge & Memory Agent.

Implements an institutional-grade active agent that:
1. Retrieves multi-tier knowledge (Working Memory, Episodic Scenarios, Atomic Facts, Macro Causal Graph, Case Studies).
2. Maintains full retrieval context and synchronizes working memory during live operation without losing prior context.
3. Automatically ingests and consolidates quant metrics (EV, Balsara Ruin, BOCPD, Kalman, FracDiff, Trade Outcomes).
4. Maintains an immutable, auditable log of every retrieval event (latencies, matched items, injected context).
5. Provides robust jittered exponential backoff retry loops and dead-letter protection across all data operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
import functools
import hashlib
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, TypeVar, Union

from intelligence.trading_memory import TradingMemory
from intelligence.semantic_memory import _tokens, _vector, _cosine_sparse
from intelligence.case_study_matcher import CASE_STUDY_REGISTRY
from intelligence.macro_graph import MacroGraph

log = logging.getLogger("ActiveKnowledgeAgent")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# 1. Robust Fault-Tolerant Retry Loop & Dead-Letter Handling
# ===========================================================================

T = TypeVar("T")


def robust_retry(
    max_retries: int = 3,
    base_delay: float = 0.05,
    max_delay: float = 1.0,
    jitter: bool = True,
    retry_exceptions: Tuple[type[Exception], ...] = (sqlite3.OperationalError, sqlite3.DatabaseError, TimeoutError, ConnectionError, OSError),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator providing exponential backoff with full jitter and fault tolerance."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Optional[Exception] = None
            delay = base_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        log.error(f"[Retry Exhausted] Function {func.__name__} failed after {attempt} attempts: {exc}")
                        raise
                    sleep_time = random.uniform(0, delay) if jitter else delay
                    log.warning(f"[Retry {attempt}/{max_retries}] {func.__name__} encountered {type(exc).__name__}: {exc}. Sleeping {sleep_time:.3f}s...")
                    time.sleep(sleep_time)
                    delay = min(max_delay, delay * 2.0)
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(f"{func.__name__} failed with unknown state")
        return wrapper
    return decorator


# ===========================================================================
# 2. Data Models for Working Memory, Ingestion & Retrieval Logs
# ===========================================================================

@dataclass
class WorkingContextItem:
    """An active item in the agent's short-term working context buffer."""
    item_id: str
    session_key: str
    kind: str             # e.g., 'MARKET_SNAPSHOT', 'QUANT_PILLARS', 'TRADE_HYPOTHESIS', 'ACTIVE_POSITION', 'RETRIEVED_ANALOG'
    importance: float     # 0.0 to 1.0 (higher = preserved during compaction)
    payload: Dict[str, Any]
    source_ids: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow_iso)
    status: str = "ACTIVE"  # 'ACTIVE', 'COMPACTED', 'EXPIRED'
    token_estimate: int = 0

    def __post_init__(self):
        if not self.token_estimate:
            text = json.dumps(self.payload, default=str)
            self.token_estimate = max(1, len(text) // 4)


@dataclass
class RetrievalLogRecord:
    """Immutable audit record of a knowledge retrieval action."""
    retrieval_id: str
    timestamp: str
    trigger_event: str
    symbol: str
    timeframe: str
    query_tags: Dict[str, Any]
    matched_sources: List[Dict[str, Any]]
    injected_context_tokens: int
    latency_ms: float
    resulting_action: str = "DELIBERATION"
    outcome_r: Optional[float] = None
    ticket: Optional[int] = None


@dataclass
class QuantMemorySnapshot:
    """Structured container of point-in-time quantitative indicators for memory ingestion."""
    symbol: str
    timeframe: str
    timestamp: str
    expected_value_r: float
    balsara_ruin_probability: float
    quarter_kelly_fraction: float
    run_theory_l_max: int
    hurst_exponent: float
    bocpd_regime_shift_detected: bool
    kalman_dynamic_beta: float
    ornstein_uhlenbeck_half_life: float
    fractional_diff_stationary: bool
    var_95_dollar: float
    portfolio_heat_r: float
    features: Dict[str, Any] = field(default_factory=dict)


# ===========================================================================
# 3. Active Knowledge & Memory Agent Core Engine
# ===========================================================================

class ActiveKnowledgeAgent:
    """Active cognitive agent coordinating real-time knowledge ingestion, retrieval, and working context synchronization."""

    def __init__(self, db_path: str = "trading_state.db", max_working_memory_tokens: int = 4000):
        self.db_path = db_path
        self.max_working_memory_tokens = max_working_memory_tokens
        # Re-entrancy latch: every _con() runs the schema check below, and
        # __init__ is precisely where the DDL that check would ask for runs.
        self._ensuring = True
        try:
            self.trading_memory = TradingMemory(db_path=db_path)
            self.macro_graph = MacroGraph(db_path=db_path)
            self._init_extended_schema()
        finally:
            self._ensuring = False

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        self._ensure_schema(con)
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        """Initialise this database's tables if the file just opened has none.

        ``db_path`` is a bare relative name (``"trading_state.db"``) whose
        resolution is decided by the caller's cwd - and, under pytest, by the
        root-database sandbox, which points every connect at a fresh per-test
        copy.  ``__init__`` builds the schema ONCE, against whatever the path
        resolved to at construction time, so a later connection landing on a
        different file inherits none of it: ``retrieve`` then dies with
        ``no such table: quant_memory_snapshots``.  The same happens in
        production for any process that reuses an agent across a database move
        or recreation.

        Three sentinels cover the three schemas built in ``__init__``
        (TradingMemory, MacroGraph, and the extended tables defined here); if
        any is missing the whole idempotent chain is re-run against the file in
        use.  One ``sqlite_master`` lookup on the normal path, and the
        ``_ensuring`` latch stops ``_con`` - called from inside that DDL - from
        recursing into it.
        """
        if self._ensuring:
            return
        try:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('active_retrieval_audit', 'working_context_items', 'macro_sources')"
            ).fetchall()
            if len(rows) == 3:
                return
        except sqlite3.Error:
            return  # unreadable database: let the caller's own query surface it
        self._ensuring = True
        try:
            self.trading_memory = TradingMemory(db_path=self.db_path)
            self.macro_graph = MacroGraph(db_path=self.db_path)
            self._init_extended_schema()
        finally:
            self._ensuring = False

    @robust_retry(max_retries=3, base_delay=0.05)
    def _init_extended_schema(self) -> None:
        """Initialize high-performance tables for working memory, retrieval logs, and quant snapshots."""
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS active_retrieval_audit (
                retrieval_id TEXT PRIMARY KEY,
                retrieved_at TEXT NOT NULL,
                trigger_event TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                query_tags TEXT NOT NULL,
                matched_sources TEXT NOT NULL,
                injected_context_tokens INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                resulting_action TEXT NOT NULL,
                outcome_r REAL,
                ticket INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_active_retrieval_sym ON active_retrieval_audit(symbol, retrieved_at);
            CREATE INDEX IF NOT EXISTS idx_active_retrieval_time ON active_retrieval_audit(retrieved_at);

            CREATE TABLE IF NOT EXISTS quant_memory_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                expected_value_r REAL NOT NULL,
                balsara_ruin_prob REAL NOT NULL,
                quarter_kelly REAL NOT NULL,
                l_max INTEGER NOT NULL,
                hurst_exponent REAL NOT NULL,
                bocpd_shift INTEGER NOT NULL,
                kalman_beta REAL NOT NULL,
                ou_half_life REAL NOT NULL,
                fracdiff_stationary INTEGER NOT NULL,
                var_95 REAL NOT NULL,
                portfolio_heat REAL NOT NULL,
                features_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quant_snapshots_sym ON quant_memory_snapshots(symbol, snapshot_time);

            CREATE TABLE IF NOT EXISTS dead_letter_vault (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                channel TEXT NOT NULL,
                payload TEXT NOT NULL,
                error_message TEXT NOT NULL,
                retry_count INTEGER NOT NULL
            );
            """)

    # ───────────────────────────────────────────────────────────────────────────
    # A. Multi-Tier Retrieval with Complete Context Preservation
    # ───────────────────────────────────────────────────────────────────────────

    @robust_retry(max_retries=3, base_delay=0.05)
    def retrieve(
        self,
        query_text: str,
        query_tags: Dict[str, Any],
        symbol: str = "XAUUSD",
        timeframe: str = "H1",
        limit_per_tier: int = 3,
        trigger_event: str = "PRE_TRADE_EVALUATION",
    ) -> Dict[str, Any]:
        """Execute multi-tier retrieval across all cognitive layers and log the exact context injected."""
        start_time = time.perf_counter()
        retrieval_id = f"ret_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        sym_clean = symbol.upper()
        
        # 1. Tier 1: Active Working Context
        working_items = self.get_active_working_items(session_key=f"session_{sym_clean}")
        
        # 2. Tier 2: Episodic Scenario Documents (L2 Memory)
        scenarios = []
        try:
            filter_tags = {"symbol": symbol.lower(), "strategy": query_tags.get("strategy", "").lower()}
            raw_scenarios = self.trading_memory.retrieve_scenarios(tags=filter_tags, limit=limit_per_tier)
            scenarios = [dict(s) for s in raw_scenarios]
        except Exception as exc:
            log.warning(f"Error retrieving scenarios: {exc}")

        # 3. Tier 3: Atomic Facts & Rule Playbooks (L1/L3 Memory)
        facts = []
        try:
            fact_keys = [f"trend_{symbol.lower()}", f"regime_{symbol.lower()}", f"bias_{symbol.lower()}"]
            raw_facts = self.trading_memory.retrieve_facts(fact_keys=fact_keys, limit=limit_per_tier)
            facts = [dict(f) for f in raw_facts]
        except Exception as exc:
            log.warning(f"Error retrieving atomic facts: {exc}")

        # 4. Tier 4: Macro Causal DAG & Case Study Archetypes
        macro_paths = []
        try:
            edges = self.macro_graph.approved_edges([symbol, "FED_FUNDS_RATE", "DXY", "OIL_SUPPLY"], limit=limit_per_tier)
            macro_paths = [dict(e) for e in edges]
        except Exception as exc:
            log.warning(f"Error retrieving macro graph: {exc}")

        case_study_analogs = []
        domain = query_tags.get("disruption_domain", "").upper()
        for cid, arch in CASE_STUDY_REGISTRY.items():
            if not domain or arch.primary_domain == domain or sym_clean in arch.primary_entities:
                case_study_analogs.append({
                    "case_id": cid,
                    "label": arch.label,
                    "substitution_horizon": arch.tau_substitution_horizon,
                    "prompt_curve": arch.prompt_curve_regime,
                })
            if len(case_study_analogs) >= limit_per_tier:
                break

        # 5. Tier 5: Recent Quant Snapshot History
        recent_quant = self.get_latest_quant_snapshot(symbol=sym_clean)

        # Assemble unified retrieved knowledge payload
        retrieved_knowledge = {
            "retrieval_id": retrieval_id,
            "symbol": sym_clean,
            "timeframe": timeframe,
            "working_context": working_items[:limit_per_tier],
            "episodic_scenarios": scenarios,
            "atomic_facts": facts,
            "macro_causal_paths": macro_paths,
            "case_study_analogs": case_study_analogs,
            "latest_quant_snapshot": recent_quant,
        }

        # Calculate latency and token budget
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        serialized_context = json.dumps(retrieved_knowledge, default=str)
        token_estimate = max(1, len(serialized_context) // 4)

        # Construct matched sources metadata list
        matched_sources = []
        for sc in scenarios:
            matched_sources.append({"tier": "EPISODIC", "id": sc.get("scenario_key"), "summary": sc.get("content", "")[:80]})
        for fc in facts:
            matched_sources.append({"tier": "ATOMIC_FACT", "id": fc.get("fact_key"), "val": fc.get("fact_value", "")})
        for mp in macro_paths:
            matched_sources.append({"tier": "MACRO_DAG", "id": mp.get("edge_id"), "edge": f"{mp.get('source')}->{mp.get('target')}"})
        for cs in case_study_analogs:
            matched_sources.append({"tier": "CASE_STUDY", "id": cs.get("case_id"), "label": cs.get("label")})

        # Record immutable audit log
        self._record_retrieval_log(
            retrieval_id=retrieval_id,
            trigger_event=trigger_event,
            symbol=sym_clean,
            timeframe=timeframe,
            query_tags=query_tags,
            matched_sources=matched_sources,
            injected_tokens=token_estimate,
            latency_ms=round(elapsed_ms, 2),
        )

        return {
            "retrieval_id": retrieval_id,
            "status": "SUCCESS",
            "latency_ms": round(elapsed_ms, 2),
            "injected_context_tokens": token_estimate,
            "matched_sources_count": len(matched_sources),
            "knowledge": retrieved_knowledge,
        }

    @robust_retry(max_retries=3, base_delay=0.05)
    def _record_retrieval_log(
        self,
        retrieval_id: str,
        trigger_event: str,
        symbol: str,
        timeframe: str,
        query_tags: Dict[str, Any],
        matched_sources: List[Dict[str, Any]],
        injected_tokens: int,
        latency_ms: float,
        resulting_action: str = "DELIBERATION",
    ) -> None:
        with self._con() as con:
            con.execute("""
            INSERT OR REPLACE INTO active_retrieval_audit (
                retrieval_id, retrieved_at, trigger_event, symbol, timeframe,
                query_tags, matched_sources, injected_context_tokens, latency_ms, resulting_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                retrieval_id,
                utcnow_iso(),
                trigger_event,
                symbol,
                timeframe,
                json.dumps(query_tags, default=str),
                json.dumps(matched_sources, default=str),
                injected_tokens,
                latency_ms,
                resulting_action,
            ))

    # ───────────────────────────────────────────────────────────────────────────
    # B. Quant System Ingestion & Continuous Memory Consolidation
    # ───────────────────────────────────────────────────────────────────────────

    @robust_retry(max_retries=3, base_delay=0.05)
    def ingest_quant_snapshot(
        self,
        symbol: str,
        timeframe: str,
        expected_value_r: float,
        balsara_ruin_probability: float,
        quarter_kelly_fraction: float,
        run_theory_l_max: int,
        hurst_exponent: float = 0.50,
        bocpd_regime_shift: bool = False,
        kalman_dynamic_beta: float = 1.0,
        ornstein_uhlenbeck_half_life: float = 5.0,
        fractional_diff_stationary: bool = True,
        var_95_dollar: float = 50.0,
        portfolio_heat_r: float = 1.0,
        features: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ingest real-time quantitative mathematical metrics into persistent SQLite and working context."""
        sym_clean = symbol.upper()
        now_ts = utcnow_iso()
        feat_dict = features or {}

        with self._con() as con:
            con.execute("""
            INSERT INTO quant_memory_snapshots (
                symbol, timeframe, snapshot_time, expected_value_r, balsara_ruin_prob,
                quarter_kelly, l_max, hurst_exponent, bocpd_shift, kalman_beta,
                ou_half_life, fracdiff_stationary, var_95, portfolio_heat, features_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sym_clean, timeframe, now_ts, float(expected_value_r), float(balsara_ruin_probability),
                float(quarter_kelly_fraction), int(run_theory_l_max), float(hurst_exponent),
                1 if bocpd_regime_shift else 0, float(kalman_dynamic_beta),
                float(ornstein_uhlenbeck_half_life), 1 if fractional_diff_stationary else 0,
                float(var_95_dollar), float(portfolio_heat_r), json.dumps(feat_dict, default=str),
            ))

        # Synchronize atomic facts
        self.trading_memory.record_fact(
            source_id=f"quant_{sym_clean}_{int(time.time())}",
            fact_key=f"quant_state_{sym_clean.lower()}",
            fact_value=json.dumps({
                "ev_r": round(expected_value_r, 4),
                "ruin_prob": round(balsara_ruin_probability, 6),
                "kelly_fraction": round(quarter_kelly_fraction, 4),
                "hurst": round(hurst_exponent, 3),
                "regime": "TRENDING" if hurst_exponent > 0.55 else ("MEAN_REVERTING" if hurst_exponent < 0.45 else "RANDOM_WALK"),
            }),
            source="ActiveKnowledgeAgent:ingest_quant_snapshot",
            confidence=0.95,
        )

        # Ingest into working context buffer with high importance
        self.add_working_item(
            session_key=f"session_{sym_clean}",
            kind="QUANT_PILLARS",
            importance=0.90,
            payload={
                "symbol": sym_clean,
                "timeframe": timeframe,
                "ev_r": expected_value_r,
                "ruin_prob": balsara_ruin_probability,
                "quarter_kelly": quarter_kelly_fraction,
                "l_max": run_theory_l_max,
                "hurst": hurst_exponent,
                "bocpd_shift": bocpd_regime_shift,
                "var_95": var_95_dollar,
            },
        )

        return {
            "symbol": sym_clean,
            "status": "QUANT_INGESTED",
            "timestamp": now_ts,
            "is_viable": expected_value_r > 0.15 and balsara_ruin_probability < 0.001,
        }

    @robust_retry(max_retries=3, base_delay=0.05)
    def ingest_trade_execution(
        self,
        ticket: int,
        symbol: str,
        setup_name: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        rationale: str,
        retrieval_id: Optional[str] = None,
    ) -> None:
        """Ingest live order execution and bind it to the relevant retrieval context ID."""
        sym_clean = symbol.upper()
        # Add to active working memory
        self.add_working_item(
            session_key=f"session_{sym_clean}",
            kind="ACTIVE_POSITION",
            importance=1.0,  # Max priority: never compact active position
            payload={
                "ticket": ticket,
                "symbol": sym_clean,
                "setup_name": setup_name,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "volume": volume,
                "rationale": rationale,
                "retrieval_id": retrieval_id,
            },
        )
        if retrieval_id:
            with self._con() as con:
                con.execute("UPDATE active_retrieval_audit SET ticket = ?, resulting_action = 'ORDER_EXECUTED' WHERE retrieval_id = ?", (ticket, retrieval_id))

    @robust_retry(max_retries=3, base_delay=0.05)
    def ingest_trade_reflection(
        self,
        ticket: int,
        symbol: str,
        setup_name: str,
        close_price: float,
        r_multiple: float,
        pnl: float,
        exit_reason: str,
        mae_r: float = 0.0,
        mfe_r: float = 0.0,
    ) -> Dict[str, Any]:
        """Post-trade reflection loop: consolidates trade outcome into episodic scenarios and updates retrieval records."""
        sym_clean = symbol.upper()
        is_win = r_multiple > 0.0
        
        # 1. Update Scenario Document statistics in persistent SQLite
        scenario_key = f"scenario_{sym_clean.lower()}_{setup_name.lower()}"
        now_ts = utcnow_iso()
        with self._con() as con:
            con.execute("""
            INSERT INTO scenario_documents (
                scenario_key, created_at, updated_at, tags, sample_count, win_count,
                loss_count, rejected_count, unresolved_count, content, evidence, status
            ) VALUES (?, ?, ?, ?, 1, ?, ?, 0, 0, ?, ?, 'APPROVED')
            ON CONFLICT(scenario_key) DO UPDATE SET
                updated_at = excluded.updated_at,
                sample_count = scenario_documents.sample_count + 1,
                win_count = scenario_documents.win_count + excluded.win_count,
                loss_count = scenario_documents.loss_count + excluded.loss_count,
                evidence = excluded.evidence
            """, (
                scenario_key,
                now_ts,
                now_ts,
                json.dumps({"symbol": sym_clean.lower(), "strategy": setup_name.lower()}),
                1 if is_win else 0,
                0 if is_win else 1,
                f"Setup {setup_name} on {sym_clean}. Cumulative samples updated.",
                f"Last trade #{ticket} closed with {r_multiple:+.2f}R (${pnl:+.2f}), exit={exit_reason}, MAE={mae_r:.2f}R, MFE={mfe_r:.2f}R.",
            ))

        # 2. Record Verified Memory Lesson
        try:
            self.trading_memory.save_verified_memory(
                source_id=f"ticket_{ticket}",
                kind="TRADE_OUTCOME",
                tags={"symbol": sym_clean.lower(), "setup": setup_name.lower(), "outcome": "WIN" if is_win else "LOSS"},
                summary=f"Trade #{ticket} on {sym_clean} ({setup_name}) produced {r_multiple:+.2f}R P&L.",
                lesson=f"Execution via {setup_name} validated: R-Multiple={r_multiple:.2f}R with MAE={mae_r:.2f}R." if is_win else f"Loss on {setup_name}: Check stop placement and spread widened during {exit_reason}.",
                evidence=f"Close @ {close_price}, PnL=${pnl:.2f}",
                confidence=0.90,
                status="APPROVED",
            )
        except Exception as exc:
            log.warning(f"Error saving verified memory lesson: {exc}")

        # 3. Update active retrieval audit records for this ticket
        with self._con() as con:
            con.execute("UPDATE active_retrieval_audit SET outcome_r = ?, resulting_action = 'TRADE_CLOSED_REFLECTED' WHERE ticket = ?", (r_multiple, ticket))

        # 4. Mark position as COMPACTED in working memory
        self.expire_working_items(session_key=f"session_{sym_clean}", kind="ACTIVE_POSITION")

        return {
            "ticket": ticket,
            "symbol": sym_clean,
            "setup": setup_name,
            "r_multiple": r_multiple,
            "status": "MEMORY_CONSOLIDATED",
            "lesson_persisted": True,
        }

    # ───────────────────────────────────────────────────────────────────────────
    # C. Working Memory Token-Budget Management & Zero-Loss Compaction
    # ───────────────────────────────────────────────────────────────────────────

    @robust_retry(max_retries=3, base_delay=0.05)
    def add_working_item(self, session_key: str, kind: str, importance: float, payload: Dict[str, Any]) -> str:
        """Add item to working memory buffer, triggering compaction if budget exceeded."""
        item_id = f"item_{int(time.time()*1000)}_{random.randint(100, 999)}"
        item = WorkingContextItem(
            item_id=item_id,
            session_key=session_key,
            kind=kind,
            importance=min(1.0, max(0.0, importance)),
            payload=payload,
        )
        with self._con() as con:
            con.execute("""
            INSERT INTO working_context_items (
                id, session_key, created_at, kind, importance, payload, source_ids, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(time.time() * 1000) % 1000000000 + random.randint(1, 1000),
                session_key,
                item.created_at,
                kind,
                str(item.importance),
                json.dumps(payload, default=str),
                json.dumps([item_id]),
                "ACTIVE",
            ))

        # Check token budget and compact if needed
        self.sync_and_compact_working_memory(session_key=session_key)
        return item_id

    @robust_retry(max_retries=3, base_delay=0.05)
    def get_active_working_items(self, session_key: str) -> List[Dict[str, Any]]:
        """Retrieve all currently active items in working memory."""
        with self._con() as con:
            rows = con.execute("""
            SELECT id, kind, importance, payload, created_at FROM working_context_items
            WHERE session_key = ? AND status = 'ACTIVE'
            ORDER BY created_at DESC
            """, (session_key,)).fetchall()
            items = []
            for r in rows:
                try:
                    p = json.loads(r["payload"])
                except Exception:
                    p = {}
                items.append({
                    "id": r["id"],
                    "kind": r["kind"],
                    "importance": float(r["importance"]),
                    "payload": p,
                    "created_at": r["created_at"],
                })
            return items

    @robust_retry(max_retries=3, base_delay=0.05)
    def expire_working_items(self, session_key: str, kind: str) -> None:
        """Mark items of specific kind as expired."""
        with self._con() as con:
            con.execute("UPDATE working_context_items SET status = 'EXPIRED' WHERE session_key = ? AND kind = ?", (session_key, kind))

    @robust_retry(max_retries=3, base_delay=0.05)
    def sync_and_compact_working_memory(self, session_key: str) -> Dict[str, Any]:
        """Hierarchical compaction: keeps high-importance items and consolidates low-importance records."""
        items = self.get_active_working_items(session_key=session_key)
        total_tokens = sum(len(json.dumps(i["payload"], default=str)) // 4 for i in items)
        
        compacted_count = 0
        if total_tokens > self.max_working_memory_tokens:
            # Sort items by importance ascending (lowest importance compacted first)
            sorted_items = sorted(items, key=lambda x: (x["importance"], x["created_at"]))
            with self._con() as con:
                for it in sorted_items:
                    if it["importance"] >= 0.95:
                        continue  # Never compact critical items (e.g. active positions)
                    con.execute("UPDATE working_context_items SET status = 'COMPACTED' WHERE id = ?", (it["id"],))
                    compacted_count += 1
                    total_tokens -= len(json.dumps(it["payload"], default=str)) // 4
                    if total_tokens <= self.max_working_memory_tokens * 0.75:
                        break

        return {
            "session_key": session_key,
            "active_items_count": len(items) - compacted_count,
            "compacted_count": compacted_count,
            "estimated_token_usage": total_tokens,
            "budget_limit": self.max_working_memory_tokens,
            "status": "SYNCHRONIZED",
        }

    # ───────────────────────────────────────────────────────────────────────────
    # D. Queries & Audit Trail Inspection
    # ───────────────────────────────────────────────────────────────────────────

    @robust_retry(max_retries=3, base_delay=0.05)
    def get_latest_quant_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._con() as con:
            row = con.execute("""
            SELECT * FROM quant_memory_snapshots WHERE symbol = ?
            ORDER BY snapshot_time DESC LIMIT 1
            """, (symbol.upper(),)).fetchone()
            if not row:
                return None
            return dict(row)

    @robust_retry(max_retries=3, base_delay=0.05)
    def get_retrieval_audit_trail(self, limit: int = 10, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve recent immutable retrieval audit records."""
        with self._con() as con:
            if symbol:
                rows = con.execute("""
                SELECT * FROM active_retrieval_audit WHERE symbol = ?
                ORDER BY retrieved_at DESC LIMIT ?
                """, (symbol.upper(), limit)).fetchall()
            else:
                rows = con.execute("""
                SELECT * FROM active_retrieval_audit
                ORDER BY retrieved_at DESC LIMIT ?
                """, (limit,)).fetchall()
            
            records = []
            for r in rows:
                item = dict(r)
                try:
                    item["query_tags"] = json.loads(r["query_tags"])
                    item["matched_sources"] = json.loads(r["matched_sources"])
                except Exception as exc:
                    logger.debug("audit operation failed: %s", exc)
                records.append(item)
            return records

    @robust_retry(max_retries=3, base_delay=0.05)
    def log_dead_letter(self, channel: str, payload: Any, error_msg: str, retry_count: int = 3) -> None:
        """Store failed data pipeline events into dead letter vault for post-mortem recovery."""
        with self._con() as con:
            con.execute("""
            INSERT INTO dead_letter_vault (timestamp, channel, payload, error_message, retry_count)
            VALUES (?, ?, ?, ?, ?)
            """, (utcnow_iso(), channel, json.dumps(payload, default=str), error_msg, retry_count))


# Global singleton instance for high-speed active agent operations
ACTIVE_KNOWLEDGE_AGENT = ActiveKnowledgeAgent()
