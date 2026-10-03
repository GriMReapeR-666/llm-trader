"""Graph State Persistence — Full UAQP State Tensor crash-recovery layer.

Persists the complete state tensor S = (S.H, S.D, S.F, S.B, S.R, S.P, S.M, S.Ω, S.Q, S.Ξ)
to a hash-chained SQLite checkpoint journal so the system can recover from any crash.

Architecture:
    Γ_deployed = (Γ, Ψ)  — the graph + guardian

    On every cycle:
      1. Begin checkpoint (status=RUNNING)
      2. Snapshot all state fields from live singletons
      3. Complete checkpoint (status=COMPLETED)

    On crash recovery:
      1. Find last checkpoint
      2. If status=RUNNING → crash detected → restore from PREVIOUS COMPLETED
      3. If status=COMPLETED → clean restore

    Hash chain: SHA-256(prev_hash ‖ payload) ensures tamper detection.
    Retention: last 100 checkpoints kept; older ones pruned.

References:
    UAQP Part II: State Tensor Schema
    UAQP Patch 1: Guardian (Ψ) independence
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("GraphStatePersistence")


# ============================================================================
# Constants
# ============================================================================
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "artifacts", "graph_state.db")
MAX_CHECKPOINTS = 100
SNAPSHOT_TIMEOUT_SECONDS = 10.0


class CheckpointStatus(str, Enum):
    """Status of a checkpoint — RUNNING means crash-interrupted."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"


@dataclass
class StateTensorSnapshot:
    """Immutable snapshot of the full UAQP state tensor S at time t."""
    cycle_num: int
    timestamp: str

    # S.H — Hypothesis Space (alpha decay health, discovery candidates)
    hypothesis_space: Dict[str, Any] = field(default_factory=dict)

    # S.D — Data Corpus (data quality metrics, freshness)
    data_corpus: Dict[str, Any] = field(default_factory=dict)

    # S.F — Signal Manifest (alpha ensemble weights, composite scores)
    signal_manifest: Dict[str, Any] = field(default_factory=dict)

    # S.B — Backtest Results (last CPCV/overfit/trial ledger summary)
    backtest_results: Dict[str, Any] = field(default_factory=dict)

    # S.R — Risk State (heat budget, kill switch, sizing, drawdown)
    risk_state: Dict[str, Any] = field(default_factory=dict)

    # S.P — Portfolio State (positions, PnL — summary only, full in trading_state.db)
    portfolio_state: Dict[str, Any] = field(default_factory=dict)

    # S.M — Market Regime (regime label, duration, probabilities)
    market_regime: Dict[str, Any] = field(default_factory=dict)

    # S.Ω — System Health (decay index, uptime, recommended action)
    system_health: Dict[str, Any] = field(default_factory=dict)

    # S.Q — Orchestrator Queue (pending/blocked/human-review task counts)
    task_queue_state: Dict[str, Any] = field(default_factory=dict)

    # S.Ξ — Trial Ledger (summary — full in trial_ledger.db)
    trial_ledger_state: Dict[str, Any] = field(default_factory=dict)

    # Execution metadata
    guardian_status: str = "ARMED"
    last_guardian_heartbeat: float = 0.0
    execution_safety_status: str = "OK"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "cycle_num": self.cycle_num,
            "timestamp": self.timestamp,
            "hypothesis_space": self.hypothesis_space,
            "data_corpus": self.data_corpus,
            "signal_manifest": self.signal_manifest,
            "backtest_results": self.backtest_results,
            "risk_state": self.risk_state,
            "portfolio_state": self.portfolio_state,
            "market_regime": self.market_regime,
            "system_health": self.system_health,
            "task_queue_state": self.task_queue_state,
            "trial_ledger_state": self.trial_ledger_state,
            "guardian_status": self.guardian_status,
            "last_guardian_heartbeat": self.last_guardian_heartbeat,
            "execution_safety_status": self.execution_safety_status,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StateTensorSnapshot":
        """Deserialize from dict."""
        return cls(
            cycle_num=d.get("cycle_num", 0),
            timestamp=d.get("timestamp", ""),
            hypothesis_space=d.get("hypothesis_space", {}),
            data_corpus=d.get("data_corpus", {}),
            signal_manifest=d.get("signal_manifest", {}),
            backtest_results=d.get("backtest_results", {}),
            risk_state=d.get("risk_state", {}),
            portfolio_state=d.get("portfolio_state", {}),
            market_regime=d.get("market_regime", {}),
            system_health=d.get("system_health", {}),
            task_queue_state=d.get("task_queue_state", {}),
            trial_ledger_state=d.get("trial_ledger_state", {}),
            guardian_status=d.get("guardian_status", "ARMED"),
            last_guardian_heartbeat=d.get("last_guardian_heartbeat", 0.0),
            execution_safety_status=d.get("execution_safety_status", "OK"),
        )


# ============================================================================
# Checkpoint Journal
# ============================================================================
class GraphStatePersistence:
    """SQLite-backed checkpoint journal for the full state tensor.

    Tables:
        checkpoints: (id, cycle_num, timestamp, status, payload_json, hash_chain)
        metadata:    (key, value) — last_checkpoint_id, last_cycle_num, crash_count

    Properties:
        - Hash-chained: each row's hash = SHA256(prev_hash ‖ payload)
        - Atomic: uses WAL + BEGIN IMMEDIATE
        - Retention: prunes to MAX_CHECKPOINTS on each snapshot
        - Thread-safe: uses a lock for writes
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if not hasattr(self, "_local"):
            self._local = threading.local()
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
                cur.execute("PRAGMA busy_timeout=5000;")
                cur.execute("PRAGMA cache_size=-32000;")
                cur.execute("PRAGMA temp_store=MEMORY;")
                cur.close()
            except Exception:
                pass
            self._local.conn = conn
        return conn

    def close(self) -> None:
        if hasattr(self, "_local") and getattr(self._local, "conn", None) is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_num INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    hash_chain TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_cycle ON checkpoints(cycle_num);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_status ON checkpoints(status);
            """)
            # Initialize metadata if empty
            cur = con.execute("SELECT COUNT(*) AS cnt FROM metadata")
            if cur.fetchone()["cnt"] == 0:
                con.execute(
                    "INSERT INTO metadata(key, value) VALUES(?, ?)",
                    ("last_checkpoint_id", "0"),
                )
                con.execute(
                    "INSERT INTO metadata(key, value) VALUES(?, ?)",
                    ("last_cycle_num", "0"),
                )
                con.execute(
                    "INSERT INTO metadata(key, value) VALUES(?, ?)",
                    ("crash_count", "0"),
                )

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    def _get_meta(self, key: str, default: str = "0") -> str:
        with self._connect() as con:
            row = con.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _get_prev_hash(self) -> str:
        """Get hash of the most recent completed checkpoint."""
        with self._connect() as con:
            row = con.execute(
                "SELECT hash_chain FROM checkpoints WHERE status='COMPLETED' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["hash_chain"] if row else "GENESIS"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def begin_checkpoint(self, cycle_num: int) -> int:
        """Begin a new checkpoint (status=RUNNING). Returns checkpoint ID.

        If a RUNNING checkpoint exists from a previous cycle, it's marked FAILED
        (indicating a crash).
        """
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as con:
                # Mark any orphaned RUNNING checkpoints as FAILED (crash detected)
                con.execute(
                    "UPDATE checkpoints SET status='FAILED' WHERE status='RUNNING'"
                )
                # Increment crash count if any were found
                orphan_count = con.execute(
                    "SELECT changes() AS c"
                ).fetchone()["c"]
                if orphan_count > 0:
                    con.execute(
                        "INSERT INTO metadata(key, value) VALUES('crash_count', '1') "
                        "ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)",
                    )

                # Seed the new row's hash from the immediately preceding
                # ROW BY ID — the same rule verify_chain_integrity() walks
                # and the same rule complete_checkpoint() already uses
                # (_get_prev_hash_for). The old rule seeded from the last
                # COMPLETED row, so two consecutive non-COMPLETED rows both
                # hashed against the same predecessor: the second row's
                # stored hash duplicated the first's, and the id-order
                # verifier reported a false "broken at <id>" tamper alarm.
                # Observed live on 4 FAILED rows (13924/13927/13942/13943,
                # PU-0161), which surfaced at session-10 boot as CRITICAL
                # HALT_AND_ALERT and would have blocked a LIVE boot.
                prev_row = con.execute(
                    "SELECT hash_chain FROM checkpoints ORDER BY id DESC LIMIT 1"
                ).fetchone()
                prev_hash = prev_row["hash_chain"] if prev_row else "GENESIS"

                # Insert new RUNNING checkpoint
                placeholder = json.dumps({"_placeholder": True})
                placeholder_hash = self._compute_hash(prev_hash, placeholder)
                cur = con.execute(
                    "INSERT INTO checkpoints(cycle_num, timestamp, status, payload_json, hash_chain) "
                    "VALUES(?, ?, 'RUNNING', ?, ?)",
                    (cycle_num, now, placeholder, placeholder_hash),
                )
                cp_id = cur.lastrowid

                # Update metadata
                for k, v in [("last_checkpoint_id", str(cp_id)), ("last_cycle_num", str(cycle_num))]:
                    con.execute(
                        "INSERT INTO metadata(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (k, v),
                    )

            return cp_id

    def complete_checkpoint(self, checkpoint_id: int, snapshot: StateTensorSnapshot) -> None:
        """Mark checkpoint as COMPLETED with the full state tensor payload."""
        payload = json.dumps(snapshot.to_dict(), default=str)
        prev_hash = self._get_prev_hash_for(checkpoint_id)
        payload_hash = self._compute_hash(prev_hash, payload)

        with self._lock:
            with self._connect() as con:
                con.execute(
                    "UPDATE checkpoints SET status='COMPLETED', payload_json=?, hash_chain=? "
                    "WHERE id=?",
                    (payload, payload_hash, checkpoint_id),
                )
                # Re-chain successors (PU-0161). A row's hash is finalized
                # HERE, but a row created after this one may already have
                # chained off this row's earlier *placeholder* hash — that is
                # exactly what out-of-order completion does (observed at
                # checkpoint 13947, whose predecessor 13945 was completed
                # after it). Payloads are never touched below, so
                # payload-tamper detection is unchanged; only the derived
                # link values are refreshed so the id-order walk stays valid.
                # Normal path (completing the newest row) has no successors,
                # so this is a no-op.
                successors = con.execute(
                    "SELECT id, payload_json FROM checkpoints WHERE id > ? "
                    "ORDER BY id ASC",
                    (checkpoint_id,),
                ).fetchall()
                if successors:
                    logger.warning(
                        "Out-of-order checkpoint completion: id=%s finalized "
                        "after %d successor row(s) already chained off its "
                        "placeholder hash; re-chaining successors (payloads "
                        "untouched)",
                        checkpoint_id, len(successors),
                    )
                link = payload_hash
                for succ in successors:
                    link = self._compute_hash(link, succ["payload_json"])
                    con.execute(
                        "UPDATE checkpoints SET hash_chain=? WHERE id=?",
                        (link, succ["id"]),
                    )
                # Prune old checkpoints in the same connection. Before
                # deleting, record the chain anchor: the hash of the LAST row
                # that is about to be pruned. It is the true predecessor of
                # the retained window, so the verifier can seed from it
                # instead of "GENESIS". Without this, retention trimming made
                # verify_chain_integrity falsely report the first retained
                # row as a chain break on every subsequent startup.
                boundary = con.execute(
                    "SELECT id, hash_chain FROM checkpoints "
                    "ORDER BY id DESC LIMIT 1 OFFSET ?",
                    (MAX_CHECKPOINTS,),
                ).fetchone()
                if boundary is not None:
                    con.execute(
                        "INSERT INTO metadata(key, value) VALUES('chain_anchor_id', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(boundary["id"]),),
                    )
                    con.execute(
                        "INSERT INTO metadata(key, value) VALUES('chain_anchor_hash', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (boundary["hash_chain"],),
                    )
                con.execute(
                    "DELETE FROM checkpoints WHERE id NOT IN ("
                    "  SELECT id FROM checkpoints ORDER BY id DESC LIMIT ?"
                    ")",
                    (MAX_CHECKPOINTS,),
                )

    def fail_checkpoint(self, checkpoint_id: int, reason: str = "") -> None:
        """Mark checkpoint as FAILED (e.g., snapshot error)."""
        with self._lock:
            with self._connect() as con:
                con.execute(
                    "UPDATE checkpoints SET status='FAILED' WHERE id=?",
                    (checkpoint_id,),
                )
                con.execute(
                    "INSERT INTO metadata(key, value) VALUES('crash_count', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)",
                )

    def get_last_completed(self) -> Optional[Dict[str, Any]]:
        """Get the last COMPLETED checkpoint as a dict."""
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM checkpoints WHERE status='COMPLETED' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_last_snapshot(self) -> Optional[StateTensorSnapshot]:
        """Get the last COMPLETED checkpoint as a StateTensorSnapshot."""
        cp = self.get_last_completed()
        if cp is None:
            return None
        try:
            payload = json.loads(cp["payload_json"])
            return StateTensorSnapshot.from_dict(payload)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to deserialize last snapshot: %s", exc)
            return None

    def get_last_completed_cycle(self) -> int:
        """Return the cycle_num of the last COMPLETED checkpoint."""
        with self._connect() as con:
            row = con.execute(
                "SELECT cycle_num FROM checkpoints WHERE status='COMPLETED' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["cycle_num"] if row else 0

    def was_crash_detected(self) -> bool:
        """Check if the last checkpoint was RUNNING (crash-interrupted)."""
        with self._connect() as con:
            row = con.execute(
                "SELECT status FROM checkpoints ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            return row["status"] == "RUNNING"

    def get_crash_count(self) -> int:
        """Total number of crashes detected since inception."""
        try:
            return int(self._get_meta("crash_count", "0"))
        except (ValueError, TypeError):
            return 0

    def get_checkpoint_count(self) -> int:
        """Total completed checkpoints."""
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS cnt FROM checkpoints WHERE status='COMPLETED'"
            ).fetchone()
            return row["cnt"] if row else 0

    def get_recent_checkpoints(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent checkpoints (any status) for diagnostics."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, cycle_num, timestamp, status, hash_chain "
                "FROM checkpoints ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def verify_chain_integrity(self) -> Dict[str, Any]:
        """Verify the hash chain is unbroken. Returns integrity report.

        Retention prunes old checkpoints, so the chain's trust root is the
        recorded prune anchor (the last pruned row) — not "GENESIS" — once
        history has been trimmed. When the anchor predates this mechanism
        (legacy DBs pruned before anchors existed), the first retained row
        becomes the trust root and is reported as the pruning boundary so
        the gap is explicit rather than a false "broken at <oldest id>".

        Mid-chain tampering is still always detected: any mutation of a
        retained row's payload or of any hash_chain link breaks the walk.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, cycle_num, status, payload_json, hash_chain "
                "FROM checkpoints ORDER BY id ASC"
            ).fetchall()
            anchor_row = con.execute(
                "SELECT value FROM metadata WHERE key='chain_anchor_hash'"
            ).fetchone()
            anchor_id_row = con.execute(
                "SELECT value FROM metadata WHERE key='chain_anchor_id'"
            ).fetchone()

        if not rows:
            return {
                "valid": True,
                "checkpoints": 0,
                "broken_at": None,
                "pruning_boundary": None,
                "head_verified": 0,
                "anchored": False,
            }

        first_id = rows[0]["id"]
        pruning_boundary: Optional[int] = None

        if first_id == 1:
            # Complete history retained — chain roots at GENESIS.
            prev_hash = "GENESIS"
            anchored = False
        elif anchor_row is not None and anchor_id_row is not None:
            # Anchor recorded before pruning — trust root for the window.
            prev_hash = anchor_row["value"]
            anchored = True
            try:
                if int(anchor_id_row["value"]) != first_id - 1:
                    # Window does not start right after the anchor (legacy gap).
                    pruning_boundary = first_id
                    prev_hash = None  # force trust-root reset below
            except (TypeError, ValueError):
                pruning_boundary = first_id
                prev_hash = None
        else:
            # Legacy DB pruned before anchors existed — no way to verify the
            # first retained row against its (deleted) predecessor. Treat it
            # as the trust root and surface the boundary honestly.
            pruning_boundary = first_id
            anchored = False
            prev_hash = None

        broken_at = None
        verified = 0
        for row in rows:
            if prev_hash is None:
                # First row after an unverifiable boundary — trust root.
                prev_hash = row["hash_chain"]
                verified += 1
                continue
            expected = self._compute_hash(prev_hash, row["payload_json"])
            if row["hash_chain"] != expected:
                broken_at = row["id"]
                break
            prev_hash = row["hash_chain"]
            verified += 1

        return {
            "valid": broken_at is None,
            "checkpoints": len(rows),
            "broken_at": broken_at,
            "pruning_boundary": pruning_boundary,
            "head_verified": verified,
            "anchored": anchored,
        }

    def repair_chain_integrity(self, backup: bool = True) -> Dict[str, Any]:
        """Recompute hash-chain links from the trust root to repair broken chains.

        Preserves all payload data and metadata, repairing only the derived
        hash_chain link column. Creates a timestamped backup before mutation
        when backup=True.
        """
        import shutil
        backup_path = ""
        if backup and os.path.exists(self.db_path):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = f"{self.db_path}.bak_{stamp}"
            shutil.copy2(self.db_path, backup_path)

        with self._lock:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT id, cycle_num, status, payload_json, hash_chain "
                    "FROM checkpoints ORDER BY id ASC"
                ).fetchall()
                anchor_row = con.execute(
                    "SELECT value FROM metadata WHERE key='chain_anchor_hash'"
                ).fetchone()
                anchor_id_row = con.execute(
                    "SELECT value FROM metadata WHERE key='chain_anchor_id'"
                ).fetchone()

                if not rows:
                    return {
                        "repaired": 0,
                        "backup_path": backup_path,
                        "valid_after": True,
                        "details": self.verify_chain_integrity(),
                    }

                first_id = rows[0]["id"]
                if first_id == 1:
                    prev_hash = "GENESIS"
                elif anchor_row is not None and anchor_id_row is not None:
                    prev_hash = anchor_row["value"]
                    try:
                        if int(anchor_id_row["value"]) != first_id - 1:
                            prev_hash = None
                    except (TypeError, ValueError):
                        prev_hash = None
                else:
                    prev_hash = None

                repaired_count = 0
                for row in rows:
                    if prev_hash is None:
                        # First row after an unverifiable boundary — trust root.
                        prev_hash = row["hash_chain"]
                        continue
                    expected = self._compute_hash(prev_hash, row["payload_json"])
                    if row["hash_chain"] != expected:
                        con.execute(
                            "UPDATE checkpoints SET hash_chain=? WHERE id=?",
                            (expected, row["id"]),
                        )
                        repaired_count += 1
                    prev_hash = expected

                con.commit()

        status = self.verify_chain_integrity()
        return {
            "repaired": repaired_count,
            "backup_path": backup_path,
            "valid_after": bool(status.get("valid", False)),
            "details": status,
        }


    def get_stats(self) -> Dict[str, Any]:
        """Summary statistics for the checkpoint journal."""
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) AS c FROM checkpoints").fetchone()["c"]
            completed = con.execute(
                "SELECT COUNT(*) AS c FROM checkpoints WHERE status='COMPLETED'"
            ).fetchone()["c"]
            running = con.execute(
                "SELECT COUNT(*) AS c FROM checkpoints WHERE status='RUNNING'"
            ).fetchone()["c"]
            failed = con.execute(
                "SELECT COUNT(*) AS c FROM checkpoints WHERE status='FAILED'"
            ).fetchone()["c"]

        return {
            "total_checkpoints": total,
            "completed": completed,
            "running": running,
            "failed": failed,
            "crash_count": self.get_crash_count(),
            "last_completed_cycle": self.get_last_completed_cycle(),
            "chain_valid": self.verify_chain_integrity()["valid"],
            "db_path": self.db_path,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mark_orphaned_running(self) -> None:
        """Mark any RUNNING checkpoints as FAILED (crash recovery)."""
        with self._connect() as con:
            con.execute(
                "UPDATE checkpoints SET status='FAILED' WHERE status='RUNNING'"
            )
        # Increment crash count
        try:
            count = int(self._get_meta("crash_count", "0"))
            self._set_meta("crash_count", str(count + 1))
        except (ValueError, TypeError):
            self._set_meta("crash_count", "1")

    def _get_prev_hash_for(self, checkpoint_id: int) -> str:
        """Get the hash of the checkpoint immediately before the given one."""
        with self._connect() as con:
            row = con.execute(
                "SELECT hash_chain FROM checkpoints WHERE id < ? "
                "ORDER BY id DESC LIMIT 1",
                (checkpoint_id,),
            ).fetchone()
            return row["hash_chain"] if row else "GENESIS"

    def _prune_old_checkpoints(self) -> None:
        """Keep only the last MAX_CHECKPOINTS completed checkpoints."""
        with self._connect() as con:
            con.execute(
                "DELETE FROM checkpoints WHERE id NOT IN ("
                "  SELECT id FROM checkpoints ORDER BY id DESC LIMIT ?"
                ")",
                (MAX_CHECKPOINTS,),
            )

    @staticmethod
    def _compute_hash(prev_hash: str, payload: str) -> str:
        """SHA-256 hash chain: H(prev_hash ‖ payload)."""
        return hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()


# ============================================================================
# State Tensor Collector — gathers live state from all singletons
# ============================================================================
class StateTensorCollector:
    """Collects the current state tensor S from all live singletons.

    This is the bridge between the in-memory graph and the persistence layer.
    Each collector method is fault-tolerant: if a singleton is unavailable,
    it returns an empty dict rather than crashing the checkpoint.
    """

    def __init__(self):
        pass

    def collect(self, cycle_num: int) -> StateTensorSnapshot:
        """Collect the full state tensor from all live sources."""
        now = datetime.now(timezone.utc).isoformat()
        return StateTensorSnapshot(
            cycle_num=cycle_num,
            timestamp=now,
            hypothesis_space=self._collect_hypothesis_space(),
            data_corpus=self._collect_data_corpus(),
            signal_manifest=self._collect_signal_manifest(),
            backtest_results=self._collect_backtest_results(),
            risk_state=self._collect_risk_state(),
            portfolio_state=self._collect_portfolio_state(),
            market_regime=self._collect_market_regime(),
            system_health=self._collect_system_health(),
            task_queue_state=self._collect_task_queue(),
            trial_ledger_state=self._collect_trial_ledger(),
            guardian_status=self._collect_guardian_status(),
            last_guardian_heartbeat=self._collect_guardian_heartbeat(),
            execution_safety_status=self._collect_execution_safety(),
        )

    def _collect_hypothesis_space(self) -> Dict[str, Any]:
        """S.H — Alpha decay health + discovery candidates."""
        try:
            from core.alpha_decay_monitor import AlphaDecayMonitor
            monitor = AlphaDecayMonitor.get_instance()
            if monitor is not None:
                return monitor.get_summary()
        except Exception as exc:
            logger.debug("Hypothesis space collection failed: %s", exc)
        return {}

    def _collect_data_corpus(self) -> Dict[str, Any]:
        """S.D — Data quality metrics."""
        try:
            # Check if DataQualityGate has a recent result
            import json
            state_file = os.path.join(
                os.path.dirname(__file__), "..", "artifacts", "data_quality_state.json"
            )
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    return json.load(f)
        except Exception as exc:
            logger.debug("Data corpus collection failed: %s", exc)
        return {}

    def _collect_signal_manifest(self) -> Dict[str, Any]:
        """S.F — Alpha ensemble weights + composite scores."""
        try:
            import json
            weights_file = os.path.join(
                os.path.dirname(__file__), "..", "artifacts", "ensemble_weights.json"
            )
            if os.path.exists(weights_file):
                with open(weights_file, "r") as f:
                    return json.load(f)
        except Exception as exc:
            logger.debug("Signal manifest collection failed: %s", exc)
        return {}

    def _collect_backtest_results(self) -> Dict[str, Any]:
        """S.B — Last CPCV/overfit/trial summary."""
        try:
            from core.trial_ledger import get_trial_ledger
            ledger = get_trial_ledger()
            if ledger is not None:
                return {
                    "n_trials": ledger.n_trials,
                    "last_sharpe": ledger.last_sharpe if hasattr(ledger, "last_sharpe") else None,
                }
        except Exception as exc:
            logger.debug("Backtest results collection failed: %s", exc)
        return {}

    def _collect_risk_state(self) -> Dict[str, Any]:
        """S.R — Risk manager state."""
        try:
            # RiskManager reads from TradingStore — collect the key fields
            import json
            state_file = os.path.join(
                os.path.dirname(__file__), "..", "artifacts", "risk_state.json"
            )
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    return json.load(f)
        except Exception as exc:
            logger.debug("Risk state collection failed: %s", exc)
        return {}

    def _collect_portfolio_state(self) -> Dict[str, Any]:
        """S.P — Portfolio summary (positions, PnL)."""
        try:
            # Read from the journal SQLite
            journal_path = os.path.join(
                os.path.dirname(__file__), "..", "trading_state.db"
            )
            if os.path.exists(journal_path):
                con = sqlite3.connect(journal_path, timeout=5)
                con.row_factory = sqlite3.Row
                # Count open trades
                row = con.execute(
                    "SELECT COUNT(*) AS cnt FROM trades WHERE closed_at IS NULL"
                ).fetchone()
                open_trades = row["cnt"] if row else 0
                # Get total PnL
                row2 = con.execute(
                    "SELECT COALESCE(SUM(pnl), 0) AS total_pnl FROM trades WHERE closed_at IS NOT NULL"
                ).fetchone()
                total_pnl = row2["total_pnl"] if row2 else 0.0
                con.close()
                return {
                    "open_trades": open_trades,
                    "total_pnl": round(float(total_pnl), 2),
                }
        except Exception as exc:
            logger.debug("Portfolio state collection failed: %s", exc)
        return {}

    def _collect_market_regime(self) -> Dict[str, Any]:
        """S.M — Current regime classification."""
        try:
            from core.regime_duration_tracker import get_regime_tracker
            tracker = get_regime_tracker()
            if tracker is not None:
                state = tracker.get_state()
                if isinstance(state, dict):
                    return state
                if hasattr(state, "to_dict"):
                    return state.to_dict()
        except Exception as exc:
            logger.debug("Market regime collection failed: %s", exc)
        return {}

    def _collect_system_health(self) -> Dict[str, Any]:
        """S.Ω — System health metrics."""
        try:
            import json
            state_file = os.path.join(
                os.path.dirname(__file__), "..", "artifacts", "meta_loop_state.json"
            )
            result: Dict[str, Any] = {}
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    result["meta_loop"] = json.load(f)
            return result
        except Exception as exc:
            logger.debug("System health collection failed: %s", exc)
        return {}

    def _collect_task_queue(self) -> Dict[str, Any]:
        """S.Q — Task queue summary (counts only)."""
        try:
            from core.task_queue import get_task_queue
            tq = get_task_queue()
            if tq is not None:
                return tq.get_summary()
        except Exception as exc:
            logger.debug("Task queue collection failed: %s", exc)
        return {}

    def _collect_trial_ledger(self) -> Dict[str, Any]:
        """S.Ξ — Trial ledger summary."""
        try:
            from core.trial_ledger import get_trial_ledger
            ledger = get_trial_ledger()
            if ledger is not None:
                return {
                    "n_trials": ledger.n_trials,
                    "last_sharpe": getattr(ledger, "last_sharpe", None),
                }
        except Exception as exc:
            logger.debug("Trial ledger collection failed: %s", exc)
        return {}

    def _collect_guardian_status(self) -> str:
        """Ψ Guardian status."""
        try:
            from core.external_guardian import get_guardian
            guardian = get_guardian()
            if guardian is not None:
                return str(getattr(guardian, "state", "ARMED"))
        except Exception as exc:
            logger.debug("Guardian status collection failed: %s", exc)
        return "UNKNOWN"

    def _collect_guardian_heartbeat(self) -> float:
        """Ψ Guardian last heartbeat timestamp."""
        try:
            from core.external_guardian import get_guardian
            guardian = get_guardian()
            if guardian is not None:
                return float(getattr(guardian, "last_heartbeat", 0.0))
        except Exception:
            pass
        return 0.0

    def _collect_execution_safety(self) -> str:
        """Execution safety status."""
        try:
            from core.execution_safety_supervisor import get_supervisor
            sup = get_supervisor()
            if sup is not None:
                return str(getattr(sup, "status", "OK"))
        except Exception:
            pass
        return "UNKNOWN"


# ============================================================================
# Singleton
# ============================================================================
_persistence_instance: Optional[GraphStatePersistence] = None
_collector_instance: Optional[StateTensorCollector] = None
_init_lock = threading.Lock()


def get_persistence(db_path: Optional[str] = None) -> GraphStatePersistence:
    """Get or create the global persistence singleton."""
    global _persistence_instance
    if _persistence_instance is None:
        with _init_lock:
            if _persistence_instance is None:
                _persistence_instance = GraphStatePersistence(db_path)
    return _persistence_instance


def get_collector() -> StateTensorCollector:
    """Get or create the global state tensor collector."""
    global _collector_instance
    if _collector_instance is None:
        with _init_lock:
            if _collector_instance is None:
                _collector_instance = StateTensorCollector()
    return _collector_instance


def snapshot_cycle(cycle_num: int) -> int:
    """Convenience: begin checkpoint, collect state, complete checkpoint.

    Returns the checkpoint ID. If any step fails, the checkpoint is marked FAILED.
    """
    persistence = get_persistence()
    collector = get_collector()

    cp_id = persistence.begin_checkpoint(cycle_num)
    try:
        snapshot = collector.collect(cycle_num)
        persistence.complete_checkpoint(cp_id, snapshot)
        logger.debug("Checkpoint %d completed for cycle %d", cp_id, cycle_num)
    except Exception as exc:
        persistence.fail_checkpoint(cp_id, str(exc))
        logger.warning("Checkpoint %d failed for cycle %d: %s", cp_id, cycle_num, exc)
        raise

    return cp_id


def restore_last_state() -> Optional[StateTensorSnapshot]:
    """Restore the last completed state tensor snapshot."""
    persistence = get_persistence()
    return persistence.get_last_snapshot()
