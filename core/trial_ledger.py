"""S.Ξ Trial Ledger — Append-Only Validation History.

Implements the UAQP Patch 2 specification:

    S.Ξ = (n_trials, trial_sharpe_distribution)

Key invariants:
    1. n_trials is MONOTONIC — never decremented, never reset
    2. trial_sharpe_distribution is APPEND-ONLY — no entries removed
    3. No agent (including Meta-Loop) may decrement or reset n_trials
    4. DSR (Deflated Sharpe Ratio) is computed using the true trial count,
       not the caller-supplied count — prevents gaming

Storage: SQLite with hash-chained rows (tamper-evident).
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.hypothesis_class import (
    UNCLASSIFIED,
    classify,
    group_by_effective_class,
    resolve_class,
)
from core.metrics import calculate_deflated_sharpe_ratio, expected_max_sharpe


# ---------------------------------------------------------------------------
# Hard gate thresholds (UAQP Patch 2)
# ---------------------------------------------------------------------------
DSR_GATE_THRESHOLD: float = 0.95  # τ_dsr from spec — hard reject below this
MIN_OBSERVATIONS: int = 30        # Minimum sample for valid DSR


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TrialRecord:
    """Single trial entry — immutable once written."""
    trial_id: int
    strategy_id: str
    sharpe: float
    n_observations: int
    skewness: float = 0.0
    kurtosis: float = 3.0
    timestamp: float = 0.0
    hash_prev: str = ""
    hash_current: str = ""
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "strategy_id": self.strategy_id,
            "sharpe": self.sharpe,
            "n_observations": self.n_observations,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
            "timestamp": self.timestamp,
            "hash_current": self.hash_current,
            "metadata": self.metadata or {},
        }


@dataclass
class DSRResult:
    """Deflated Sharpe Ratio result from the trial ledger."""
    dsr: float                    # DSR confidence Phi(z) = 1 - p (higher = more significant)
    is_significant: bool          # True if DSR >= threshold
    n_trials: int                 # True trial count from ledger
    estimated_sharpe: float       # The Sharpe being tested
    trial_sharpe_mean: float      # Mean of historical trial Sharpes
    trial_sharpe_std: float       # Std of historical trial Sharpes
    max_expected_sharpe: float    # E[max Sharpe] under null
    verdict: str                  # "PASS" or "FAIL_NOT_SIGNIFICANT"
    gate_threshold: float         # τ_dsr threshold used
    pbo_proxy: float = 0.0       # Empirical PBO (fraction of trials with Sharpe <= 0)
    strategy_class: str = "global"  # Hypothesis class the deflation was scoped to ("global" = the whole ledger)
    effective_trials: int = 0    # Effective deduplicated independent trial count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dsr": round(self.dsr, 6),
            "is_significant": self.is_significant,
            "n_trials": self.n_trials,
            "estimated_sharpe": round(self.estimated_sharpe, 4),
            "trial_sharpe_mean": round(self.trial_sharpe_mean, 4),
            "trial_sharpe_std": round(self.trial_sharpe_std, 4),
            "max_expected_sharpe": round(self.max_expected_sharpe, 4),
            "verdict": self.verdict,
            "gate_threshold": self.gate_threshold,
            "pbo_proxy": round(self.pbo_proxy, 4),
            "strategy_class": self.strategy_class,
        }


# ---------------------------------------------------------------------------
# Hash chain utilities
# ---------------------------------------------------------------------------
def _hash_record(record_json: str, prev_hash: str) -> str:
    """SHA-256 hash chain: H(record || prev_hash)."""
    payload = f"{record_json}||{prev_hash}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# S.Ξ Trial Ledger
# ---------------------------------------------------------------------------
class TrialLedger:
    """Append-only trial history with hash chain integrity.

    This is the S.Ξ state tensor field. Key properties:
    - n_trials can only increase (monotonic)
    - Trial Sharpes are append-only
    - Every row is hash-chained for tamper evidence
    - DSR is computed from the TRUE trial count, not caller input
    """

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.Lock()
        # (row_count, {class_id: [sharpe, ...]}) — invalidated by count, the
        # ledger being append-only.
        self._class_cache: Optional[Tuple[int, Dict[str, List[float]]]] = None
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "artifacts", "trial_ledger.db"
            )
        self._db_path = str(Path(db_path).resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Serialize schema init across processes: PRAGMA journal_mode=WAL
        # needs brief exclusive access; concurrent constructors race.
        with _cross_process_lock(self._db_path):
            self._init_db()

    def _init_db(self) -> None:
        with self._conn() as con:
            try:
                con.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass  # concurrent init switched/holds it; best-effort WAL
            con.execute("PRAGMA busy_timeout=5000;")
            con.execute("""
                CREATE TABLE IF NOT EXISTS trials (
                    trial_id    INTEGER PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    sharpe      REAL NOT NULL,
                    n_observations INTEGER NOT NULL,
                    skewness    REAL NOT NULL DEFAULT 0.0,
                    kurtosis    REAL NOT NULL DEFAULT 3.0,
                    timestamp   REAL NOT NULL,
                    hash_prev   TEXT NOT NULL DEFAULT '',
                    hash_current TEXT NOT NULL,
                    metadata    TEXT DEFAULT NULL,
                    strategy_class TEXT NOT NULL DEFAULT 'general'
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS gate_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   REAL NOT NULL,
                    strategy_id TEXT,
                    estimated_sharpe REAL,
                    n_trials    INTEGER,
                    dsr         REAL,
                    verdict     TEXT,
                    details     TEXT DEFAULT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_trials_strategy
                ON trials(strategy_id)
            """)
            self._migrate_strategy_class(con)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_trials_class
                ON trials(strategy_class)
            """)

    def _migrate_strategy_class(self, con: sqlite3.Connection) -> None:
        """Idempotent migration: add strategy_class column to legacy DBs.

        Existing rows keep hash integrity (strategy_class is deliberately
        NOT part of the hash payload) and default to 'general' — the
        untagged pool whose deflation bar is the strictest.
        """
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(trials)").fetchall()}
            if "strategy_class" not in cols:
                con.execute(
                    "ALTER TABLE trials ADD COLUMN strategy_class TEXT "
                    "NOT NULL DEFAULT 'general'")
        except sqlite3.OperationalError:
            pass  # concurrent migration — column already present

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self._db_path, timeout=20.0)
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA synchronous=NORMAL;")
            con.execute("PRAGMA busy_timeout=5000;")
        except Exception:
            pass
        try:
            with con:
                yield con
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Core invariants
    # ------------------------------------------------------------------
    @property
    def n_trials(self) -> int:
        """Total number of trials recorded (monotonic, never decreases)."""
        with self._lock:
            with self._conn() as con:
                row = con.execute("SELECT COUNT(*) FROM trials").fetchone()
                return int(row[0]) if row else 0

    @property
    def n_effective_trials(self) -> int:
        """Effective independent trial count (distinct strategy hypotheses)."""
        with self._lock:
            with self._conn() as con:
                row = con.execute("SELECT COUNT(DISTINCT strategy_id) FROM trials").fetchone()
                return int(row[0]) if row else 0

    def n_effective_trials_for_class(self, strategy_class: Optional[str] = None) -> int:
        """Effective independent trial count scoped to hypothesis class.
        
        Decouples true strategy hypotheses from cron heartbeat execution counts.
        """
        if strategy_class is None:
            return self.n_effective_trials
        with self._lock:
            with self._conn() as con:
                rows = con.execute("SELECT strategy_id, strategy_class FROM trials").fetchall()
                distinct_strats = set()
                target_cls = str(strategy_class)
                for sid, scls in rows:
                    eff_cls = scls if scls and scls != "general" else classify(sid)
                    if eff_cls == target_cls:
                        distinct_strats.add(sid)
                return max(1, len(distinct_strats))

    def trial_sharpes(self) -> List[float]:
        """All recorded trial Sharpes (append-only list)."""
        with self._lock:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT sharpe FROM trials ORDER BY trial_id ASC"
                ).fetchall()
                return [float(r[0]) for r in rows]

    @property
    def trial_sharpe_distribution(self) -> np.ndarray:
        """Current trial Sharpe distribution as numpy array."""
        return np.array(self.trial_sharpes(), dtype=float)

    def _class_snapshot(self) -> Dict[str, List[float]]:
        """Trial Sharpes grouped by EFFECTIVE hypothesis class.

        A stored tag that names a family is authoritative.  A stored
        ``general`` tag means "no class claim", so the class is re-derived
        from the strategy id (see ``core.hypothesis_class``) — that is what
        pulls the rows recorded before the taxonomy existed into the family
        they belong to, instead of leaving every class pool a fraction of
        its true size (a class bar computed against 30 trials when the
        family holds 355 is a bar that invents significance).

        The ledger is append-only, so a snapshot keyed by the row count
        cannot go stale: rows are never removed or re-tagged.
        """
        with self._lock:
            with self._conn() as con:
                total = int(con.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
                if self._class_cache is not None and self._class_cache[0] == total:
                    return self._class_cache[1]
                rows = con.execute(
                    "SELECT strategy_id, strategy_class, sharpe, metadata "
                    "FROM trials ORDER BY trial_id ASC"
                ).fetchall()
        groups = group_by_effective_class(rows)
        self._class_cache = (total, groups)
        return groups

    def n_trials_for_class(self, strategy_class: Optional[str] = None) -> int:
        """Trial count scoped to a hypothesis class (S.Ξ multiple-testing fix).

        Every sweep cell, harness run, and CPCV path lands in one shared
        ledger; deflating a brand-new strategy against ALL of them pushes
        the bar to E[max|null] of the entire research history. With class
        tags, the multiple-testing bar is computed WITHIN the hypothesis
        class actually being tested.

        The count is the EFFECTIVE class count (see ``_class_snapshot``):
        it includes the rows that carry no tag of their own but whose
        strategy id places them in this family.

        Args:
            strategy_class: Class tag ("persistence_tsmom", "alpha101_short",
                "news_gate", ...). None falls back to the GLOBAL count —
                the strictest bar, used when the caller cannot honestly
                classify its hypothesis (fail-closed).
        """
        if strategy_class is None:
            return self.n_trials
        return len(self._class_snapshot().get(str(strategy_class), []))

    def trial_sharpes_for_class(
        self, strategy_class: Optional[str] = None
    ) -> List[float]:
        """Trial Sharpes scoped to a hypothesis class (None → global list)."""
        if strategy_class is None:
            return self.trial_sharpes()
        return list(self._class_snapshot().get(str(strategy_class), []))

    def list_strategy_classes(self) -> Dict[str, int]:
        """Trial counts per effective hypothesis class (for observability)."""
        groups = self._class_snapshot()
        return {cls: len(sharpes) for cls, sharpes in
                sorted(groups.items(), key=lambda kv: -len(kv[1]))}

    # ------------------------------------------------------------------
    # Append trial (never delete, never update)
    # ------------------------------------------------------------------
    def record_trial(
        self,
        strategy_id: str,
        sharpe: float,
        n_observations: int,
        skewness: float = 0.0,
        kurtosis: float = 3.0,
        metadata: Optional[Dict[str, Any]] = None,
        strategy_class: Optional[str] = None,
    ) -> TrialRecord:
        """Record a new trial — append-only, monotonic.

        strategy_class: hypothesis-class tag used for per-class DSR
        deflation (e.g. "persistence_tsmom"). NOT part of the hash
        payload, so legacy rows and verify_chain() stay valid.  When it is
        NOT given, the class is DERIVED from the strategy id by the
        canonical taxonomy (``core.hypothesis_class``) so a caller cannot
        leave a trial stranded in the global family by forgetting a tag.
        An identity that cannot be attributed to a family is stored as
        ``general`` and keeps facing the global bar (fail-closed).

        This is the ONLY write operation. There is no delete, no update.

        Args:
            strategy_id: Identifier for the strategy/hypothesis tested
            sharpe: Backtested Sharpe ratio
            n_observations: Number of observations in the backtest
            skewness: Return distribution skewness
            kurtosis: Return distribution kurtosis (excess + 3)
            metadata: Optional extra data (params, universe, etc.)

        Returns:
            TrialRecord with hash chain info
        """
        stored_class = str(strategy_class) if strategy_class else classify(
            strategy_id, metadata)
        with self._lock:
            for attempt in range(10):
                try:
                    # The cross-process lockfile makes the read-compute-insert
                    # cycle atomic across processes (threading.Lock alone
                    # caused the Sep-14 link-order break under pytest-xdist).
                    with _cross_process_lock(self._db_path):
                        with self._conn() as con:
                            con.execute("BEGIN IMMEDIATE")
                            # Get last hash for chain
                            last = con.execute(
                                "SELECT hash_current FROM trials ORDER BY trial_id DESC LIMIT 1"
                            ).fetchone()
                            prev_hash = last[0] if last else ""

                            # Get next trial_id (monotonic)
                            row = con.execute(
                                "SELECT COALESCE(MAX(trial_id), 0) + 1 FROM trials"
                            ).fetchone()
                            trial_id = int(row[0])

                            ts = time.time()
                            record = TrialRecord(
                                trial_id=trial_id,
                                strategy_id=strategy_id,
                                sharpe=float(sharpe),
                                n_observations=int(n_observations),
                                skewness=float(skewness),
                                kurtosis=float(kurtosis),
                                timestamp=ts,
                                hash_prev=prev_hash,
                                metadata=metadata,
                            )

                            # Compute hash
                            record_json = json.dumps({
                                "trial_id": record.trial_id,
                                "strategy_id": record.strategy_id,
                                "sharpe": record.sharpe,
                                "n_observations": record.n_observations,
                                "skewness": record.skewness,
                                "kurtosis": record.kurtosis,
                                "timestamp": record.timestamp,
                            }, sort_keys=True)
                            record.hash_current = _hash_record(record_json, prev_hash)

                            con.execute(
                                """INSERT INTO trials
                                (trial_id, strategy_id, sharpe, n_observations,
                                 skewness, kurtosis, timestamp, hash_prev, hash_current,
                                 metadata, strategy_class)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    record.trial_id, record.strategy_id, record.sharpe,
                                    record.n_observations, record.skewness, record.kurtosis,
                                    record.timestamp, record.hash_prev, record.hash_current,
                                    json.dumps(metadata) if metadata else None,
                                    stored_class,
                                ),
                            )
                        # Commit happened at the connection-context exit. Still
                        # holding the lockfile: verify the new row is linked
                        # correctly — fail LOUDLY rather than leave a silently
                        # broken chain (defense in depth against any exotic
                        # interleaving the lock cannot exclude).
                        self._verify_row_link(record.trial_id)
                    return record
                except sqlite3.IntegrityError:
                    time.sleep(0.01 * (attempt + 1))
                    continue
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        time.sleep(0.02 * (attempt + 1))
                        continue
                    raise
            raise RuntimeError(f"Failed to record trial for {strategy_id} after 10 concurrent attempts")

    def _verify_row_link(self, trial_id: int) -> None:
        """Verify one freshly-written row is correctly hash-linked.

        Row payload must recompute against its stored hash_prev, and
        hash_prev must equal the previous row's hash_current (or '' for
        the first row). Raises RuntimeError on any mismatch — the chain
        must never break silently.
        """
        con = sqlite3.connect(
            f"file:{self._db_path}?mode=ro", uri=True, timeout=10.0
        )
        try:
            row = con.execute(
                "SELECT trial_id, strategy_id, sharpe, n_observations, "
                "skewness, kurtosis, timestamp, hash_prev, hash_current "
                "FROM trials WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
            prior = con.execute(
                "SELECT hash_current FROM trials WHERE trial_id < ? "
                "ORDER BY trial_id DESC LIMIT 1",
                (trial_id,),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise RuntimeError(
                f"trial {trial_id} vanished immediately after insert — "
                "ledger integrity violated"
            )
        rec_json = json.dumps({
            "trial_id": row[0], "strategy_id": row[1], "sharpe": row[2],
            "n_observations": row[3], "skewness": row[4], "kurtosis": row[5],
            "timestamp": row[6],
        }, sort_keys=True)
        if _hash_record(rec_json, row[7]) != row[8]:
            raise RuntimeError(
                f"trial {trial_id} payload does not recompute after insert — "
                "ledger integrity violated"
            )
        expected_prev = prior[0] if prior else ""
        if row[7] != expected_prev:
            raise RuntimeError(
                f"trial {trial_id} linked against a stale predecessor "
                "(concurrent-writer race detected) — refusing to leave a "
                "broken chain; remediation required"
            )

    # ------------------------------------------------------------------
    # Hash chain verification
    # ------------------------------------------------------------------
    def verify_chain(self) -> Dict[str, Any]:
        """Verify hash chain integrity — returns verification report."""
        with self._lock:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT trial_id, strategy_id, sharpe, n_observations, "
                    "skewness, kurtosis, timestamp, hash_prev, hash_current "
                    "FROM trials ORDER BY trial_id ASC"
                ).fetchall()

        if not rows:
            return {"valid": True, "n_records": 0, "broken_at": None}

        prev_hash = ""
        broken_at = None
        for row in rows:
            trial_id, strat, sharpe, n_obs, skew, kurt, ts, hp, hc = row
            rec_json = json.dumps({
                "trial_id": trial_id, "strategy_id": strat,
                "sharpe": sharpe, "n_observations": n_obs,
                "skewness": skew, "kurtosis": kurt, "timestamp": ts,
            }, sort_keys=True)
            expected = _hash_record(rec_json, prev_hash)
            if expected != hc:
                broken_at = trial_id
                break
            prev_hash = hc

        return {
            "valid": broken_at is None,
            "n_records": len(rows),
            "broken_at": broken_at,
        }

    # ------------------------------------------------------------------
    # DSR Hard Gate (G_β from UAQP Patch 2)
    # ------------------------------------------------------------------
    def evaluate_dsr(
        self,
        estimated_sharpe: float,
        n_observations: int,
        skewness: float = 0.0,
        kurtosis: float = 3.0,
        strategy_id: str = "unknown",
        gate_threshold: float = DSR_GATE_THRESHOLD,
        metadata: Optional[Dict[str, Any]] = None,
        strategy_class: Optional[str] = None,
        record: bool = True,
        annualized: bool = True,
        periods_per_year: float = 252.0,
    ) -> DSRResult:
        """Compute DSR using TRUE trial count from this ledger.

        This is the hard gate G_β from UAQP Patch 2:
            DSR < τ_dsr → FAIL_NOT_SIGNIFICANT → route to α
            DSR >= τ_dsr → PASS (continue to other gates)

        Critical: uses self.n_trials (the true count), NOT a caller-supplied
        count. This prevents any agent from gaming the multiple-testing
        correction by underreporting trials.

        Args:
            estimated_sharpe: The Sharpe ratio being evaluated. ANNUALIZED by
                default; pass ``annualized=False`` when it is the raw
                per-period estimator (mean/std) -- see the DSR units contract
                in core/metrics.py. ``overfit_gate`` passes the raw estimator
                and must therefore set ``annualized=False``.
            n_observations: Number of observations in the sample, at the same
                frequency that produced ``estimated_sharpe``
            skewness: Return skewness
            kurtosis: Return kurtosis (excess + 3)
            strategy_id: Strategy identifier for logging
            gate_threshold: τ_dsr threshold (default 0.95)
            metadata: Optional extra context
            strategy_class: Hypothesis-class tag for per-class deflation.
                When set, n_trials and the Sharpe distribution are scoped
                to that class (the right multiple-testing bar for a new
                hypothesis in a class with prior research). None → the
                GLOBAL count (strictest, fail-closed).
                Resolve it ONCE at the entry point with
                ``core.hypothesis_class.resolve_class()``: an explicit tag
                wins, otherwise the class is derived from the strategy id,
                and an identity that cannot be attributed to a family stays
                None and keeps the global bar. The scope must not be
                re-derived deeper in the call chain — two gates inside one
                call must not disagree about the family they deflate
                within.
            annualized: Whether ``estimated_sharpe`` is annualized.
            periods_per_year: Annualization constant that produced it.

        Returns:
            DSRResult with verdict
        """
        # The deflation scope is resolved ONCE, here: an explicit class tag
        # selects the family to deflate within; an untagged identity is
        # attributed by the canonical taxonomy; "general" or an
        # unattributable identity keeps the GLOBAL family (strictest,
        # fail-closed) — never the small unattributed bucket.
        scope = resolve_class(strategy_class, strategy_id, metadata)

        # Record this trial FIRST (UAQP Patch 2: n_trials ← n_trials + 1).
        # record=False re-gates an already-recorded hypothesis WITHOUT
        # appending a phantom trial (e.g. OverfitGate.check_strategy).
        if record:
            self.record_trial(
                strategy_id=strategy_id,
                sharpe=estimated_sharpe,
                n_observations=n_observations,
                skewness=skewness,
                kurtosis=kurtosis,
                metadata=metadata,
                strategy_class=scope,
            )

        # Now read the UPDATED trial count and distribution — scoped to the
        # hypothesis class when tagged, global when untagged (fail-closed).
        true_n_trials = self.n_trials_for_class(scope)
        effective_n_trials = self.n_effective_trials_for_class(scope)
        sharpes = self.trial_sharpes_for_class(scope)

        # Compute trial distribution stats
        if sharpes:
            trial_mean = float(np.mean(sharpes))
            trial_std = float(np.std(sharpes)) if len(sharpes) > 1 else 0.0
            pbo_proxy = float(np.mean(np.array(sharpes) <= 0.0))
        else:
            trial_mean = 0.0
            trial_std = 1.0
            pbo_proxy = 0.0

        # DSR p-value using EFFECTIVE trial count (deduplicates repeated cron runs).
        # Units are declared by the caller (see the DSR contract): ignoring
        # them here -- the state before 2026-10-20 -- de-annualized a raw
        # mean/std estimator and failed every honest strategy.
        dsr_pvalue = calculate_deflated_sharpe_ratio(
            estimated_sharpe=estimated_sharpe,
            num_trials=max(1, effective_n_trials),
            sample_size=max(10, n_observations),
            skewness=skewness,
            kurtosis=kurtosis,
            annualized=annualized,
            periods_per_year=periods_per_year,
        )

        # Expected max Sharpe under null, in the caller's units.
        max_expected = expected_max_sharpe(
            num_trials=max(1, effective_n_trials),
            sample_size=max(10, n_observations),
            annualized=annualized,
            periods_per_year=periods_per_year,
        )

        # Bailey's DSR is the CONFIDENCE Phi(z) = 1 - p_value that the true
        # Sharpe exceeds the multiple-testing-adjusted benchmark. The gate is
        # DSR >= tau (UAQP Patch 2). calculate_deflated_sharpe_ratio returns
        # the complementary p-value, so invert it here. Gating on the raw
        # p-value (p >= tau) would PASS catastrophic Sharpes and FAIL good
        # ones — the exact inversion this comment exists to prevent.
        dsr_confidence = 1.0 - dsr_pvalue

        # Gate decision: DSR (confidence) must be >= threshold
        is_significant = dsr_confidence >= gate_threshold

        verdict = "PASS" if is_significant else "FAIL_NOT_SIGNIFICANT"

        result = DSRResult(
            dsr=dsr_confidence,
            is_significant=is_significant,
            n_trials=true_n_trials,
            estimated_sharpe=estimated_sharpe,
            trial_sharpe_mean=trial_mean,
            trial_sharpe_std=trial_std,
            max_expected_sharpe=max_expected,
            verdict=verdict,
            gate_threshold=gate_threshold,
            pbo_proxy=pbo_proxy,
            strategy_class=scope or "global",
            effective_trials=effective_n_trials,
        )

        # Log the gate evaluation
        self._log_gate(strategy_id, estimated_sharpe, true_n_trials, dsr_pvalue, verdict, metadata)

        return result

    def _log_gate(
        self,
        strategy_id: str,
        estimated_sharpe: float,
        n_trials: int,
        dsr: float,
        verdict: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log gate evaluation to audit trail."""
        with self._lock:
            with self._conn() as con:
                con.execute(
                    """INSERT INTO gate_log
                    (timestamp, strategy_id, estimated_sharpe, n_trials, dsr, verdict, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        time.time(), strategy_id, estimated_sharpe,
                        n_trials, dsr, verdict,
                        json.dumps(metadata) if metadata else None,
                    ),
                )

    # ------------------------------------------------------------------
    # Query / reporting
    # ------------------------------------------------------------------
    def get_gate_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Recent gate evaluations."""
        with self._lock:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT timestamp, strategy_id, estimated_sharpe, n_trials, "
                    "dsr, verdict, details FROM gate_log "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "timestamp": r[0], "strategy_id": r[1],
                "estimated_sharpe": r[2], "n_trials": r[3],
                "dsr": r[4], "verdict": r[5],
                "details": json.loads(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    def get_strategy_stats(self, strategy_id: str) -> Dict[str, Any]:
        """Statistics for a specific strategy across all trials."""
        with self._lock:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT sharpe FROM trials WHERE strategy_id = ? ORDER BY trial_id ASC",
                    (strategy_id,),
                ).fetchall()
        sharpes = [float(r[0]) for r in rows]
        if not sharpes:
            return {"strategy_id": strategy_id, "n_trials": 0}
        arr = np.array(sharpes)
        return {
            "strategy_id": strategy_id,
            "n_trials": len(sharpes),
            "sharpe_mean": float(np.mean(arr)),
            "sharpe_std": float(np.std(arr)),
            "sharpe_min": float(np.min(arr)),
            "sharpe_max": float(np.max(arr)),
            "positive_fraction": float(np.mean(arr > 0)),
        }

    def summary(self) -> Dict[str, Any]:
        """High-level summary of the trial ledger state."""
        sharpes = self.trial_sharpes()
        chain = self.verify_chain()
        n = len(sharpes)
        arr = np.array(sharpes) if sharpes else np.array([0.0])
        return {
            "n_trials": n,
            "trial_sharpe_mean": float(np.mean(arr)),
            "trial_sharpe_std": float(np.std(arr)) if n > 1 else 0.0,
            "positive_fraction": float(np.mean(arr > 0)) if n > 0 else 0.0,
            "pbo_proxy": float(np.mean(arr <= 0)) if n > 0 else 0.0,
            "chain_valid": chain["valid"],
            "chain_records": chain["n_records"],
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_default_ledger: Optional[TrialLedger] = None
_ledger_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Cross-process write serialization
# ---------------------------------------------------------------------------
@contextmanager
def _cross_process_lock(db_path: str):
    """OS-level lockfile serializing ledger appends ACROSS processes.

    threading.Lock only covers threads. Multi-process writers (pytest-xdist
    workers, harness + live loop) caused the Sep-14 chain break: writer B
    read prev-hash before writer A committed, then linked after — both rows
    individually valid, the LINK broken. BEGIN IMMEDIATE + busy_timeout
    narrows but cannot guarantee the read-compute-insert cycle is atomic
    across every platform/interleaving; the lockfile does.
    """
    import sys as _sys

    from core.file_lock import FileLock
    lock_path = str(db_path) + ".lock"
    with FileLock(lock_path):
        yield


def get_trial_ledger(db_path: Optional[str] = None) -> TrialLedger:
    """Get or create the global trial ledger singleton."""
    global _default_ledger
    with _ledger_lock:
        if _default_ledger is None:
            _default_ledger = TrialLedger(db_path=db_path)
        return _default_ledger


# ---------------------------------------------------------------------------
# Read-only chain verification (diagnostics / readiness dashboards)
# ---------------------------------------------------------------------------
def _default_db_path(filename: str) -> str:
    return str(
        Path(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "artifacts", filename,
            )
        ).resolve()
    )


def verify_chain_readonly(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Verify the hash chain WITHOUT opening the DB read-write.

    Instantiating TrialLedger runs schema migration + WAL journaling —
    a write. Diagnostic surfaces (readiness dashboard, monitoring) must
    verify tamper-evidence without mutating the evidence they audit.

    Statuses:
        OK      — chain present and intact
        BROKEN  — chain present, hash mismatch at broken_at (tamper alarm)
        ABSENT  — no ledger file yet (valid=True: nothing to tamper with)
        ERROR   — database unreadable (fail-closed: treated as invalid)
    """
    path = db_path or _default_db_path("trial_ledger.db")
    if not os.path.exists(path):
        return {"status": "ABSENT", "valid": True, "n_records": 0,
                "broken_at": None, "db_path": path}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
        try:
            rows = con.execute(
                "SELECT trial_id, strategy_id, sharpe, n_observations, "
                "skewness, kurtosis, timestamp, hash_prev, hash_current "
                "FROM trials ORDER BY trial_id ASC"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"status": "ERROR", "valid": False, "n_records": 0,
                "broken_at": None, "db_path": path,
                "error": type(exc).__name__}

    prev_hash = ""
    broken_at = None
    for row in rows:
        trial_id, strat, sharpe, n_obs, skew, kurt, ts, hp, hc = row
        rec_json = json.dumps({
            "trial_id": trial_id, "strategy_id": strat,
            "sharpe": sharpe, "n_observations": n_obs,
            "skewness": skew, "kurtosis": kurt, "timestamp": ts,
        }, sort_keys=True)
        expected = _hash_record(rec_json, prev_hash)
        if expected != hc:
            broken_at = trial_id
            break
        prev_hash = hc

    return {
        "status": "BROKEN" if broken_at is not None else "OK",
        "valid": broken_at is None,
        "n_records": len(rows),
        "broken_at": broken_at,
        "db_path": path,
    }
