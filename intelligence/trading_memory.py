"""Durable short- and long-term trading memory.

Short-term context is a small current-session playbook.  Long-term memory is
structured SQLite journal data plus compact narratives retrieved by regime
tags.  It intentionally avoids an online embedding dependency; a token-overlap
retriever is deterministic and can later be replaced with an approved vector
backend without changing the calling API.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List
from research.setup_labeling import classify_setup_outcome, ensure_setup_label_table, persist_setup_label


def _add_column(con: sqlite3.Connection, statement: str) -> None:
    """Run an ``ALTER TABLE … ADD COLUMN`` that another process may have won.

    The PRAGMA check before each ALTER is check-then-act: two processes (or
    parallel test workers) opening a fresh database both see the column
    missing and both ALTER; the loser used to fail with "duplicate column".
    """
    try:
        con.execute(statement)
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


class TradingMemory:
    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path
        self._init()

    def _con(self):
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        # Fail-safe under concurrent access (dual-velocity executor,
        # reflection lane, TUI): wait for locks instead of raising
        # "database is locked" mid-cycle.
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        con.execute("PRAGMA cache_size=-16000;")
        con.execute("PRAGMA temp_store=MEMORY;")
        return con

    def _init(self):
        with self._con() as con:
            # WAL allows readers (retrieval layers) to proceed while a writer
            # commits; it is a persistent database property so setting it here
            # also hardens databases created before this change.
            try:
                con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            con.executescript("""
            CREATE TABLE IF NOT EXISTS macro_events (
              id INTEGER PRIMARY KEY, event_time TEXT, currency TEXT, title TEXT,
              importance INTEGER, payload TEXT NOT NULL, captured_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS playbooks (
              session_date TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS narratives (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, tags TEXT NOT NULL, content TEXT NOT NULL,
              source_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'CANDIDATE',
              reviewed_by TEXT, reviewed_at TEXT);
            CREATE TABLE IF NOT EXISTS trade_labels (
              ticket INTEGER PRIMARY KEY, label TEXT NOT NULL, rationale TEXT NOT NULL, r_multiple REAL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS setup_labels (
              subject_type TEXT NOT NULL, subject_key TEXT PRIMARY KEY, label TEXT NOT NULL,
              rationale TEXT NOT NULL, label_rule TEXT NOT NULL, created_at TEXT NOT NULL,
              review_status TEXT NOT NULL DEFAULT 'AUTO'
            );
            CREATE INDEX IF NOT EXISTS idx_setup_labels_label ON setup_labels(label, created_at);
            CREATE TABLE IF NOT EXISTS verified_memories (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, source_id TEXT NOT NULL,
              kind TEXT NOT NULL, tags TEXT NOT NULL, summary TEXT NOT NULL,
              lesson TEXT NOT NULL, evidence TEXT NOT NULL, confidence REAL NOT NULL,
              status TEXT NOT NULL, supersedes INTEGER,
              reviewed_by TEXT, reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_verified_memories_lookup ON verified_memories(kind, created_at);
            CREATE TABLE IF NOT EXISTS atomic_facts (
              id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, source_id TEXT NOT NULL,
              fact_key TEXT NOT NULL, fact_value TEXT NOT NULL, fact_status TEXT NOT NULL,
              source TEXT NOT NULL, confidence REAL NOT NULL, valid_from TEXT,
              valid_until TEXT, contradicts INTEGER, superseded_by INTEGER,
              FOREIGN KEY(contradicts) REFERENCES atomic_facts(id),
              FOREIGN KEY(superseded_by) REFERENCES atomic_facts(id)
            );
            CREATE INDEX IF NOT EXISTS idx_atomic_facts_lookup ON atomic_facts(fact_key, source, observed_at);
            CREATE TABLE IF NOT EXISTS scenario_documents (
              scenario_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              tags TEXT NOT NULL, sample_count INTEGER NOT NULL, win_count INTEGER NOT NULL,
              loss_count INTEGER NOT NULL, rejected_count INTEGER NOT NULL,
              unresolved_count INTEGER NOT NULL, content TEXT NOT NULL, evidence TEXT NOT NULL,
              status TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scenario_documents_updated ON scenario_documents(updated_at);
            CREATE TABLE IF NOT EXISTS core_memories (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              namespace TEXT NOT NULL, memory_key TEXT NOT NULL, content TEXT NOT NULL,
              source_ids TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL,
              reviewed_by TEXT, reviewed_at TEXT, supersedes INTEGER,
              UNIQUE(namespace, memory_key, created_at),
              FOREIGN KEY(supersedes) REFERENCES core_memories(id)
            );
            CREATE INDEX IF NOT EXISTS idx_core_memories_lookup ON core_memories(namespace, status, updated_at);
            CREATE TABLE IF NOT EXISTS working_context_items (
              id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, created_at TEXT NOT NULL,
              kind TEXT NOT NULL, importance TEXT NOT NULL, payload TEXT NOT NULL,
              source_ids TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
              compacted_into INTEGER, FOREIGN KEY(compacted_into) REFERENCES working_context_items(id)
            );
            CREATE INDEX IF NOT EXISTS idx_working_context_active
              ON working_context_items(session_key, status, created_at);
            CREATE TABLE IF NOT EXISTS memory_retrievals (
              retrieval_id INTEGER PRIMARY KEY, retrieved_at TEXT NOT NULL, layer TEXT NOT NULL,
              query_tags TEXT NOT NULL, source_ids TEXT NOT NULL, result_count INTEGER NOT NULL,
              latency_ms REAL NOT NULL, prompt_char_estimate INTEGER NOT NULL, as_of TEXT,
              outcome_label TEXT, outcome_ticket INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_memory_retrievals_layer
               ON memory_retrievals(layer, retrieved_at);
            CREATE TABLE IF NOT EXISTS case_study_memories (
              id INTEGER PRIMARY KEY,
              created_at TEXT NOT NULL,
              case_id TEXT NOT NULL,
              label TEXT NOT NULL,
              primary_domain TEXT,
              entities TEXT NOT NULL,
              chokepoints TEXT,
              transmission_channels TEXT,
              tripwires TEXT,
              historical_outcomes TEXT,
              lead_time_days INTEGER,
              duration_days INTEGER,
              tau_substitution_horizon TEXT,
              reference_url TEXT,
              status TEXT NOT NULL DEFAULT 'CANDIDATE',
              reviewed_by TEXT,
              reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_case_study_memories_lookup
              ON case_study_memories(case_id, status);
            """)
            self._init_fts(con)
            label_columns = {row["name"] for row in con.execute("PRAGMA table_info(trade_labels)").fetchall()}
            if "label_rule" not in label_columns:
                _add_column(con, "ALTER TABLE trade_labels ADD COLUMN label_rule TEXT")
            if "review_status" not in label_columns:
                _add_column(con, "ALTER TABLE trade_labels ADD COLUMN review_status TEXT NOT NULL DEFAULT 'AUTO'")
            narrative_columns = {row["name"] for row in con.execute("PRAGMA table_info(narratives)").fetchall()}
            if "source_ids" not in narrative_columns:
                _add_column(con, "ALTER TABLE narratives ADD COLUMN source_ids TEXT NOT NULL DEFAULT '[]'")
            if "status" not in narrative_columns:
                _add_column(con, "ALTER TABLE narratives ADD COLUMN status TEXT NOT NULL DEFAULT 'CANDIDATE'")
            if "reviewed_by" not in narrative_columns:
                _add_column(con, "ALTER TABLE narratives ADD COLUMN reviewed_by TEXT")
            if "reviewed_at" not in narrative_columns:
                _add_column(con, "ALTER TABLE narratives ADD COLUMN reviewed_at TEXT")
            verified_columns = {row["name"] for row in con.execute("PRAGMA table_info(verified_memories)").fetchall()}
            if "reviewed_by" not in verified_columns:
                _add_column(con, "ALTER TABLE verified_memories ADD COLUMN reviewed_by TEXT")
            if "reviewed_at" not in verified_columns:
                _add_column(con, "ALTER TABLE verified_memories ADD COLUMN reviewed_at TEXT")
            scenario_columns = {row["name"] for row in con.execute("PRAGMA table_info(scenario_documents)").fetchall()}
            if "reviewed_by" not in scenario_columns:
                _add_column(con, "ALTER TABLE scenario_documents ADD COLUMN reviewed_by TEXT")
            if "reviewed_at" not in scenario_columns:
                _add_column(con, "ALTER TABLE scenario_documents ADD COLUMN reviewed_at TEXT")
            retrieval_columns = {row["name"] for row in con.execute("PRAGMA table_info(memory_retrievals)").fetchall()}
            if "as_of" not in retrieval_columns:
                _add_column(con, "ALTER TABLE memory_retrievals ADD COLUMN as_of TEXT")
            ensure_setup_label_table(con)

    def _init_fts(self, con: sqlite3.Connection) -> None:
        """Create FTS5 full-text indexes with sync triggers (best-effort).

        External-content tables index all rows; status/as_of filtering stays
        in the join query so CANDIDATE rows never leak into retrieval.
        Absence of FTS5 (minimal SQLite builds) is non-fatal: retrieval
        falls back to the legacy bounded scan.
        """
        try:
            con.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS narratives_fts USING fts5(
              tags, content, content='narratives', content_rowid='id', tokenize='porter');
            CREATE VIRTUAL TABLE IF NOT EXISTS verified_memories_fts USING fts5(
              tags, summary, lesson, content='verified_memories', content_rowid='id', tokenize='porter');
            CREATE VIRTUAL TABLE IF NOT EXISTS scenario_documents_fts USING fts5(
              scenario_key, tags, content, evidence, content='scenario_documents', content_rowid='rowid', tokenize='porter');
            CREATE VIRTUAL TABLE IF NOT EXISTS core_memories_fts USING fts5(
              namespace, memory_key, content, content='core_memories', content_rowid='id', tokenize='porter');
            CREATE TRIGGER IF NOT EXISTS narratives_fts_ai AFTER INSERT ON narratives BEGIN
              INSERT INTO narratives_fts(rowid, tags, content) VALUES (new.id, new.tags, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS narratives_fts_ad AFTER DELETE ON narratives BEGIN
              INSERT INTO narratives_fts(narratives_fts, rowid, tags, content) VALUES('delete', old.id, old.tags, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS narratives_fts_au AFTER UPDATE ON narratives BEGIN
              INSERT INTO narratives_fts(narratives_fts, rowid, tags, content) VALUES('delete', old.id, old.tags, old.content);
              INSERT INTO narratives_fts(rowid, tags, content) VALUES (new.id, new.tags, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS verified_fts_ai AFTER INSERT ON verified_memories BEGIN
              INSERT INTO verified_memories_fts(rowid, tags, summary, lesson) VALUES (new.id, new.tags, new.summary, new.lesson);
            END;
            CREATE TRIGGER IF NOT EXISTS verified_fts_ad AFTER DELETE ON verified_memories BEGIN
              INSERT INTO verified_memories_fts(verified_memories_fts, rowid, tags, summary, lesson) VALUES('delete', old.id, old.tags, old.summary, old.lesson);
            END;
            CREATE TRIGGER IF NOT EXISTS verified_fts_au AFTER UPDATE ON verified_memories BEGIN
              INSERT INTO verified_memories_fts(verified_memories_fts, rowid, tags, summary, lesson) VALUES('delete', old.id, old.tags, old.summary, old.lesson);
              INSERT INTO verified_memories_fts(rowid, tags, summary, lesson) VALUES (new.id, new.tags, new.summary, new.lesson);
            END;
            CREATE TRIGGER IF NOT EXISTS scenario_fts_ai AFTER INSERT ON scenario_documents BEGIN
              INSERT INTO scenario_documents_fts(rowid, scenario_key, tags, content, evidence) VALUES (new.rowid, new.scenario_key, new.tags, new.content, new.evidence);
            END;
            CREATE TRIGGER IF NOT EXISTS scenario_fts_ad AFTER DELETE ON scenario_documents BEGIN
              INSERT INTO scenario_documents_fts(scenario_documents_fts, rowid, scenario_key, tags, content, evidence) VALUES('delete', old.rowid, old.scenario_key, old.tags, old.content, old.evidence);
            END;
            CREATE TRIGGER IF NOT EXISTS scenario_fts_au AFTER UPDATE ON scenario_documents BEGIN
              INSERT INTO scenario_documents_fts(scenario_documents_fts, rowid, scenario_key, tags, content, evidence) VALUES('delete', old.rowid, old.scenario_key, old.tags, old.content, old.evidence);
              INSERT INTO scenario_documents_fts(rowid, scenario_key, tags, content, evidence) VALUES (new.rowid, new.scenario_key, new.tags, new.content, new.evidence);
            END;
            CREATE TRIGGER IF NOT EXISTS core_fts_ai AFTER INSERT ON core_memories BEGIN
              INSERT INTO core_memories_fts(rowid, namespace, memory_key, content) VALUES (new.id, new.namespace, new.memory_key, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS core_fts_ad AFTER DELETE ON core_memories BEGIN
              INSERT INTO core_memories_fts(core_memories_fts, rowid, namespace, memory_key, content) VALUES('delete', old.id, old.namespace, old.memory_key, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS core_fts_au AFTER UPDATE ON core_memories BEGIN
              INSERT INTO core_memories_fts(core_memories_fts, rowid, namespace, memory_key, content) VALUES('delete', old.id, old.namespace, old.memory_key, old.content);
              INSERT INTO core_memories_fts(rowid, namespace, memory_key, content) VALUES (new.id, new.namespace, new.memory_key, new.content);
            END;
            """)
            # One-time backfill for databases created before FTS existed:
            # rebuild only when the base table has rows but the index is empty.
            for fts_table, base_table in (
                ("narratives_fts", "narratives"),
                ("verified_memories_fts", "verified_memories"),
                ("scenario_documents_fts", "scenario_documents"),
                ("core_memories_fts", "core_memories"),
            ):
                try:
                    base_count = con.execute(f"SELECT COUNT(*) FROM {base_table}").fetchone()[0]
                    fts_count = con.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
                    if base_count and not fts_count:
                        con.execute(f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')")
                except sqlite3.DatabaseError:
                    continue
        except sqlite3.DatabaseError as exc:
            logger.debug("FTS5 unavailable, using legacy scan: %s", exc)

    def rebuild_fts(self) -> Dict[str, Any]:
        """Rebuild all FTS5 indexes from base tables (operator maintenance)."""
        rebuilt: List[str] = []
        with self._con() as con:
            for fts_table in ("narratives_fts", "verified_memories_fts",
                              "scenario_documents_fts", "core_memories_fts"):
                try:
                    con.execute(f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')")
                    rebuilt.append(fts_table)
                except sqlite3.DatabaseError as exc:
                    logger.debug("FTS rebuild skipped for %s: %s", fts_table, exc)
        return {"rebuilt": rebuilt, "execution_authority": False}

    @staticmethod
    def _fts_match_query(tags: Dict[str, Any], *, max_terms: int = 10) -> str | None:
        """Build a deterministic FTS5 OR query from tag tokens.

        Returns None when there is nothing indexable (caller uses legacy scan).
        Tokens are [a-z0-9_]+ len>2, quoted to survive FTS5 syntax.
        """
        try:
            raw = json.dumps(tags or {}, sort_keys=True, default=str).lower()
        except (TypeError, ValueError):
            return None
        terms = sorted({token for token in re.findall(r"[a-z0-9_]+", raw) if len(token) > 2})[:max_terms]
        if not terms:
            return None
        return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)

    def fts_status(self) -> Dict[str, Any]:
        """Report FTS5 index availability and row counts (evidence only)."""
        with self._con() as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            status: Dict[str, Any] = {"fts5_available": True, "tables": {}}
            for fts_table, base_table in (
                ("narratives_fts", "narratives"),
                ("verified_memories_fts", "verified_memories"),
                ("scenario_documents_fts", "scenario_documents"),
                ("core_memories_fts", "core_memories"),
            ):
                if fts_table not in tables:
                    status["fts5_available"] = False
                    status["tables"][fts_table] = {"indexed": None, "base_rows": None}
                    continue
                try:
                    indexed = con.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
                    base_rows = con.execute(f"SELECT COUNT(*) FROM {base_table}").fetchone()[0]
                    status["tables"][fts_table] = {"indexed": int(indexed), "base_rows": int(base_rows)}
                except sqlite3.DatabaseError:
                    status["fts5_available"] = False
                    status["tables"][fts_table] = {"indexed": None, "base_rows": None}
            status["execution_authority"] = False
            return status

    @staticmethod
    def _normalize_as_of(value: Any = None) -> str:
        """Normalize a point-in-time cutoff, rejecting malformed timestamps."""
        if value is None or value == "":
            return datetime.now(timezone.utc).isoformat()
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            raise ValueError("memory retrieval as_of must be a valid timestamp") from None

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("memory confidence must be finite and within [0, 1]") from None
        if not math.isfinite(parsed) or not 0 <= parsed <= 1:
            raise ValueError("memory confidence must be finite and within [0, 1]")
        return parsed

    def record_fact(self, source_id: str, fact_key: str, fact_value: Any, *,
                    fact_status: str = "OBSERVED", source: str = "local",
                    confidence: float = 1.0, valid_from: str = "", valid_until: str = "",
                    contradicts: int | None = None) -> int:
        """Append an L1 fact; later evidence supersedes rather than mutates it."""
        source_id = str(source_id or "").strip()
        fact_key = str(fact_key or "").strip()
        if not source_id:
            raise ValueError("atomic fact requires a non-empty source_id")
        if not fact_key:
            raise ValueError("atomic fact requires a non-empty fact_key")
        if fact_status not in {"OBSERVED", "INFERRED", "PROPOSED"}:
            raise ValueError("fact_status must be OBSERVED, INFERRED, or PROPOSED")
        confidence_value = self._confidence(confidence)
        with self._con() as con:
            cursor = con.execute(
                "INSERT INTO atomic_facts(observed_at,source_id,fact_key,fact_value,fact_status,source,confidence,valid_from,valid_until,contradicts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), source_id, fact_key,
                 json.dumps(fact_value, default=str), fact_status, source,
                 confidence_value, valid_from or None,
                 valid_until or None, contradicts),
            )
        return int(cursor.lastrowid)

    def supersede_fact(self, old_fact_id: int, replacement_fact_id: int) -> None:
        with self._con() as con:
            con.execute("UPDATE atomic_facts SET superseded_by=? WHERE id=?", (replacement_fact_id, old_fact_id))

    def retrieve_facts(self, fact_keys: Iterable[str], limit: int = 20) -> List[Dict[str, Any]]:
        """Return current observed/inferred facts only.

        LLM-proposed facts, superseded facts, expired facts, and future-dated
        facts are retained for audit but cannot enter a proposal context.
        """
        keys = [str(key) for key in fact_keys if key]
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            query = f"SELECT * FROM atomic_facts WHERE fact_key IN ({placeholders}) " \
                    "AND fact_status IN ('OBSERVED','INFERRED') AND superseded_by IS NULL " \
                    "AND (valid_from IS NULL OR valid_from='' OR valid_from<=?) " \
                    "AND (valid_until IS NULL OR valid_until='' OR valid_until>=?) " \
                    "ORDER BY observed_at DESC LIMIT ?"
            rows = con.execute(query, (*keys, now, now, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["fact_value"] = json.loads(item["fact_value"])
            result.append(item)
        return result

    def record_events(self, events: Iterable[Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        event_list = list(events or [])
        rows = []
        normalized_events = []
        for event in event_list:
            if not isinstance(event, dict):
                continue
            payload = dict(event)
            importance = self._event_importance(payload.get("importance", 0))
            if importance is None:
                importance = 0
                payload["_memory_quality"] = "MALFORMED_IMPORTANCE"
            normalized_events.append(payload)
            rows.append((str(payload.get("time", "")), str(payload.get("currency", "")),
                         str(payload.get("event", payload.get("title", ""))), importance,
                         json.dumps(payload, default=str), now))
        if rows:
            with self._con() as con:
                # Same (event_time, currency, title) composite the fact_key uses
                # below. The atomic_facts gate deduplicated the *fact*, but this
                # table was an unconditional INSERT, so every recurring cycle
                # re-stored the same release and grew without bound. NOT EXISTS
                # keeps it idempotent in a single statement (no read-then-write
                # race) and still admits a first sighting.
                con.executemany(
                    """INSERT INTO macro_events(event_time,currency,title,importance,payload,captured_at)
                       SELECT ?,?,?,?,?,? WHERE NOT EXISTS(
                         SELECT 1 FROM macro_events
                         WHERE event_time IS ? AND currency IS ? AND title IS ?)""",
                    [(*row, row[0], row[1], row[2]) for row in rows],
                )
            for event in normalized_events:
                event_time = str(event.get("time", ""))
                currency = str(event.get("currency", ""))
                title = str(event.get("event", event.get("title", "")))
                # record_fact takes (source_id, fact_key) - provenance first,
                # dedup key second. The composite MUST land in fact_key so
                # retrieve_facts gates repeat recordings of the same release.
                fact_key = f"macro_event:{event_time}:{currency}:{title}"
                self.record_fact(
                    f"calendar:{fact_key}", fact_key, event,
                    source="calendar", confidence=1.0, valid_from=event_time,
                )

    @staticmethod
    def _event_importance(value: Any) -> int | None:
        try:
            parsed = float(value or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(parsed) or parsed < 0 or parsed > 3 or not parsed.is_integer():
            return None
        return int(parsed)

    def current_playbook(self, symbol: str, events: Iterable[Dict[str, Any]], technical: Dict[str, Any]) -> Dict[str, Any]:
        """Create/reuse one compact, deterministic daily playbook."""
        day = datetime.now(timezone.utc).date().isoformat()
        with self._con() as con:
            row = con.execute("SELECT content FROM playbooks WHERE session_date=?", (day,)).fetchone()
        if row:
            return json.loads(row["content"])
        high = [e for e in (events or []) if isinstance(e, dict)
                and self._event_importance(e.get("importance", 0)) is not None
                and self._event_importance(e.get("importance", 0)) >= 2]
        technical = technical if isinstance(technical, dict) else {}
        playbook = {
            "date": day, "symbol_focus": symbol,
            "regime": technical.get("primary_trend", "UNKNOWN"),
            "key_levels": technical.get("key_levels", [])[:6],
            "no_trade_events": [{"time": e.get("time"), "event": e.get("event", e.get("title")), "currency": e.get("currency")} for e in high[:8]],
            "rule": "Only trade completed-candle proposals that pass the deterministic validator.",
        }
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO playbooks(session_date,content,created_at) VALUES(?,?,?)", (day, json.dumps(playbook, default=str), datetime.now(timezone.utc).isoformat()))
        return playbook

    def latest_playbook(self) -> Dict[str, Any]:
        with self._con() as con:
            row = con.execute("SELECT content FROM playbooks ORDER BY created_at DESC LIMIT 1").fetchone()
        return json.loads(row["content"]) if row else {}

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_]+", value.lower()))

    def retrieve_narratives(self, tags: Dict[str, Any], limit: int = 3, *, as_of: Any = None) -> List[str]:
        return [item["content"] for item in self.retrieve_narrative_records(tags, limit, as_of=as_of)]

    def retrieve_narrative_records(self, tags: Dict[str, Any], limit: int = 3, *, as_of: Any = None) -> List[Dict[str, Any]]:
        """Retrieve only approved narratives with their evidence metadata.

        FTS5 BM25 pre-filters the full approved corpus (no 200-row recency
        window); Jaccard re-ranking keeps deterministic ordering. Falls back
        to the legacy bounded scan when FTS5 is unavailable or matches nothing.
        """
        cutoff = self._normalize_as_of(as_of)
        query = self._tokens(json.dumps(tags, sort_keys=True, default=str))
        rows = self._fts_narrative_candidates(tags, cutoff)
        if rows is None:
            with self._con() as con:
                rows = con.execute("""SELECT * FROM narratives
                    WHERE status='APPROVED' AND created_at<=?
                      AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY id DESC LIMIT 200""", (cutoff, cutoff)).fetchall()
        ranked = []
        for row in rows:
            theirs = self._tokens(row["tags"])
            score = len(query & theirs) / max(len(query | theirs), 1)
            if score:
                item = dict(row)
                item["tags"] = json.loads(item["tags"])
                item["source_ids"] = json.loads(item["source_ids"] or "[]")
                ranked.append((score, item))
        return [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]]

    def _fts_narrative_candidates(self, tags: Dict[str, Any], cutoff: str, candidate_limit: int = 300):
        """Return BM25-ordered approved narrative rows, or None to use legacy."""
        match = self._fts_match_query(tags)
        if not match:
            return None
        try:
            with self._con() as con:
                rows = con.execute("""SELECT n.*, bm25(narratives_fts) AS fts_rank
                    FROM narratives_fts JOIN narratives n ON n.id = narratives_fts.rowid
                    WHERE narratives_fts MATCH ?
                      AND n.status='APPROVED' AND n.created_at<=?
                      AND (n.reviewed_at IS NULL OR n.reviewed_at='' OR n.reviewed_at<=?)
                    ORDER BY fts_rank LIMIT ?""", (match, cutoff, cutoff, candidate_limit)).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.debug("narrative FTS fallback: %s", exc)
            return None
        return rows if rows else None

    def save_narrative(self, tags: Dict[str, Any], content: str, *, source_ids: Iterable[str] = (),
                       status: str = "CANDIDATE") -> int:
        if status not in {"CANDIDATE", "APPROVED"}:
            raise ValueError("narrative status must be CANDIDATE or APPROVED")
        with self._con() as con:
            cursor = con.execute("INSERT INTO narratives(created_at,tags,content,source_ids,status) VALUES(?,?,?,?,?)",
                                 (datetime.now(timezone.utc).isoformat(), json.dumps(tags, default=str), content[:2000],
                                  json.dumps(list(source_ids)), status))
        narrative_id = int(cursor.lastrowid)
        # Optional advisory remote mirror mirror; local SQLite row is already committed.
        if status == "APPROVED":
            try:
                from intelligence.remote_memory_mirror import maybe_mirror_semantic_record
                maybe_mirror_semantic_record(
                    record_id=f"narrative:{narrative_id}",
                    layer="narrative",
                    tags=tags,
                    content=content[:2000],
                    source_ids=list(source_ids),
                )
            except Exception as exc:
                logger.debug("memory operation failed: %s", exc)
        return narrative_id

    def review_narrative(self, narrative_id: int, *, approve: bool, reviewer: str) -> None:
        with self._con() as con:
            changed = con.execute("UPDATE narratives SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='CANDIDATE'",
                                  ("APPROVED" if approve else "RETIRED", reviewer[:200], datetime.now(timezone.utc).isoformat(), int(narrative_id))).rowcount
        if not changed:
            raise ValueError("only an existing candidate narrative may be reviewed once")

    def list_narratives(self, status: str = "") -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM narratives WHERE status=? ORDER BY id DESC" if status else "SELECT * FROM narratives ORDER BY id DESC",
                               (status,) if status else ()).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["tags"] = json.loads(item["tags"]); item["source_ids"] = json.loads(item["source_ids"] or "[]")
            result.append(item)
        return result

    def save_verified_memory(self, source_id: str, kind: str, tags: Dict[str, Any], summary: str,
                             lesson: str, evidence: Dict[str, Any], confidence: float = 1.0,
                             status: str = "CANDIDATE") -> int:
        """Store source-linked memory. Candidates require evidence before promotion."""
        confidence_value = self._confidence(confidence)
        with self._con() as con:
            cursor = con.execute("""INSERT INTO verified_memories
                (created_at,source_id,kind,tags,summary,lesson,evidence,confidence,status)
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                datetime.now(timezone.utc).isoformat(), source_id, kind,
                json.dumps(tags, default=str), summary[:1000], lesson[:1000],
                json.dumps(evidence, default=str), confidence_value, status))
        memory_id = int(cursor.lastrowid)
        if status == "APPROVED":
            try:
                from intelligence.remote_memory_mirror import maybe_mirror_semantic_record
                maybe_mirror_semantic_record(
                    record_id=f"verified:{memory_id}",
                    layer="verified",
                    tags=tags,
                    content={"summary": summary[:1000], "lesson": lesson[:1000], "kind": kind},
                    source_ids=[source_id],
                )
            except Exception as exc:
                logger.debug("memory operation failed: %s", exc)
        return memory_id

    def review_verified_memory(self, memory_id: int, *, approve: bool, reviewer: str) -> None:
        """A distilled candidate cannot enter prompt context without review."""
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            changed = con.execute("UPDATE verified_memories SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='CANDIDATE'",
                                  ("APPROVED" if approve else "RETIRED", reviewer[:200], reviewed_at, int(memory_id))).rowcount
        if not changed:
            raise ValueError("only an existing candidate verified memory may be reviewed once")

    def list_verified_memories(self, status: str = "") -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM verified_memories WHERE status=? ORDER BY id DESC" if status else "SELECT * FROM verified_memories ORDER BY id DESC",
                               (status,) if status else ()).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["tags"] = json.loads(item["tags"]); item["evidence"] = json.loads(item["evidence"])
            result.append(item)
        return result

    def generate_daily_narrative(self, day: str = "") -> Dict[str, Any]:
        """Create a deterministic, source-linked approved daily journal summary."""
        if day:
            try:
                day = datetime.fromisoformat(str(day)).date().isoformat()
            except (TypeError, ValueError, OverflowError):
                raise ValueError("day must be an ISO date") from None
        else:
            day = datetime.now(timezone.utc).date().isoformat()
        with self._con() as con:
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            rows = con.execute("""SELECT t.ticket,t.pnl,l.label
                                FROM trades t LEFT JOIN trade_labels l ON l.ticket=t.ticket
                                WHERE t.closed_at >= ? AND t.closed_at < ? ORDER BY t.closed_at""",
                               (f"{day}T00:00:00+00:00", f"{day}T23:59:59.999999+00:00")).fetchall() if "trades" in tables else []
        tickets = [int(row["ticket"]) for row in rows]
        labels: Dict[str, int] = {}
        for row in rows:
            label = row["label"] or "UNLABELLED"
            labels[label] = labels.get(label, 0) + 1
        content = {"kind": "deterministic_daily_journal_summary", "date": day, "closed_trades": len(rows),
                   "labels": labels, "net_pnl": sum(float(row["pnl"] or 0) for row in rows),
                   "rule": "Descriptive source-linked summary; it cannot change risk, permissions or execution."}
        narrative_id = self.save_narrative({"date": day, "kind": "daily_journal"}, json.dumps(content),
                                           source_ids=[f"trade:{ticket}" for ticket in tickets], status="APPROVED")
        return {"narrative_id": narrative_id, "closed_trades": len(rows), "source_tickets": tickets}

    def generate_weekly_narrative(self, week_start: str = "") -> Dict[str, Any]:
        """Create a deterministic weekly summary from closed journal rows.

        The summary is source-linked and descriptive. It is safe to expose to
        the configured LLM, but it never changes a risk rule or execution
        permission. A week is an explicit UTC Monday-to-Monday interval so a
        restart cannot silently move observations between summaries.
        """
        if week_start:
            try:
                start_date = datetime.fromisoformat(str(week_start)).date()
            except (TypeError, ValueError, OverflowError):
                raise ValueError("week_start must be an ISO date") from None
            if start_date.weekday() != 0:
                raise ValueError("week_start must be a Monday")
        else:
            today = datetime.now(timezone.utc).date()
            start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
        start, end = start_date.isoformat(), end_date.isoformat()
        with self._con() as con:
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            rows = con.execute("""SELECT t.ticket,t.pnl,l.label
                                FROM trades t LEFT JOIN trade_labels l ON l.ticket=t.ticket
                                WHERE t.closed_at >= ? AND t.closed_at < ? ORDER BY t.closed_at""",
                               (f"{start}T00:00:00+00:00", f"{end}T00:00:00+00:00")).fetchall() if "trades" in tables else []
        labels: Dict[str, int] = {}
        for row in rows:
            label = row["label"] or "UNLABELLED"
            labels[label] = labels.get(label, 0) + 1
        content = {
            "kind": "deterministic_weekly_journal_summary", "week_start": start,
            "week_end": end, "closed_trades": len(rows), "labels": labels,
            "net_pnl": sum(float(row["pnl"] or 0) for row in rows),
            "rule": "Descriptive source-linked summary; it cannot change risk, permissions or execution.",
        }
        narrative_id = self.save_narrative(
            {"week_start": start, "week_end": end, "kind": "weekly_journal"},
            json.dumps(content), source_ids=[f"trade:{int(row['ticket'])}" for row in rows], status="APPROVED",
        )
        return {"narrative_id": narrative_id, "week_start": start, "week_end": end,
                "closed_trades": len(rows), "source_tickets": [int(row["ticket"]) for row in rows]}

    def consolidate_weekly_core(self, week_start: str = "") -> Dict[str, Any]:
        """Create a reviewable L3 candidate from the weekly summary.

        Consolidation never auto-approves a core memory. The candidate must be
        reviewed through the existing memory-core workflow before it can be
        retrieved into a proposal context.
        """
        summary = self.generate_weekly_narrative(week_start)
        if summary["closed_trades"] <= 0:
            return {"candidate_id": None, "narrative_id": summary["narrative_id"],
                    "status": "NO_DATA", "execution_authority": False}
        key = f"weekly:{summary['week_start']}"
        content = json.dumps({"summary": summary, "rule": "Candidate only; operator review required."}, sort_keys=True)
        with self._con() as con:
            existing = con.execute("""SELECT id FROM core_memories
                WHERE namespace='STABLE_LESSON' AND memory_key=? AND status='CANDIDATE'
                ORDER BY id DESC LIMIT 1""", (key,)).fetchone()
        if existing:
            return {"candidate_id": int(existing["id"]), "narrative_id": summary["narrative_id"],
                    "status": "EXISTING_CANDIDATE", "execution_authority": False}
        candidate_id = self.propose_core_memory(
            "STABLE_LESSON", key, content,
            [f"narrative:{summary['narrative_id']}", *summary["source_tickets"]],
            confidence=0.5,
        )
        return {"candidate_id": candidate_id, "narrative_id": summary["narrative_id"],
                "status": "CANDIDATE", "execution_authority": False}

    def monthly_memory_maintenance(self, older_than_days: int = 30) -> Dict[str, Any]:
        """Retire stale candidates and report approved memories needing review."""
        archived = self.archive_stale_candidates(older_than_days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(older_than_days)))).isoformat()
        with self._con() as con:
            approved_stale = con.execute("""SELECT COUNT(*) FROM verified_memories
                WHERE status='APPROVED' AND created_at < ?""", (cutoff,)).fetchone()[0]
            approved_narratives = con.execute("""SELECT COUNT(*) FROM narratives
                WHERE status='APPROVED' AND created_at < ?""", (cutoff,)).fetchone()[0]
            contradicted = con.execute("""SELECT COUNT(*) FROM atomic_facts
                WHERE contradicts IS NOT NULL AND superseded_by IS NULL""").fetchone()[0]
        pruned_retrievals = self.prune_retrievals(keep_days=max(90, int(older_than_days)))
        return {
            "archived_candidates": archived,
            "approved_memories_review_required": int(approved_stale + approved_narratives),
            "active_contradicted_facts_review_required": int(contradicted),
            "pruned_unlinked_retrieval_rows": pruned_retrievals,
            "policy": "Approved or contradicted memories are never auto-retired; operator review is required.",
            "execution_authority": False,
        }

    def archive_stale_candidates(self, older_than_days: int = 30) -> Dict[str, int]:
        """Retire only unreviewed candidates; approved items require human review."""
        now = datetime.now(timezone.utc)
        # Use timedelta instead of fromtimestamp arithmetic: Windows rejects
        # pre-1970 timestamps even though an operator may request a long audit
        # horizon for a maintenance dry run.
        cutoff = (now - timedelta(days=max(1, int(older_than_days)))).isoformat()
        with self._con() as con:
            narratives = con.execute("UPDATE narratives SET status='RETIRED',reviewed_by='maintenance',reviewed_at=? WHERE status='CANDIDATE' AND created_at < ?",
                                     (now.isoformat(), cutoff)).rowcount
            verified = con.execute("UPDATE verified_memories SET status='RETIRED',reviewed_by='maintenance',reviewed_at=? WHERE status='CANDIDATE' AND created_at < ?", (now.isoformat(), cutoff)).rowcount
            core = con.execute("UPDATE core_memories SET status='RETIRED',updated_at=?,reviewed_by='maintenance',reviewed_at=? WHERE status='CANDIDATE' AND created_at < ?",
                               (now.isoformat(), now.isoformat(), cutoff)).rowcount
        return {"narratives_retired": narratives, "verified_memories_retired": verified, "core_memories_retired": core}

    def retrieve_verified(self, tags: Dict[str, Any], limit: int = 5, *, as_of: Any = None) -> List[Dict[str, Any]]:
        """Return compact, source-linked memory; retrieval never implies truth.

        FTS5 searches tags+summary+lesson across the full corpus (not just the
        newest 300); Jaccard re-ranking preserves deterministic ordering.
        """
        cutoff = self._normalize_as_of(as_of)
        query = self._tokens(json.dumps(tags, sort_keys=True, default=str))
        rows = self._fts_verified_candidates(tags, cutoff)
        if rows is None:
            with self._con() as con:
                rows = con.execute("""SELECT * FROM verified_memories
                    WHERE status='APPROVED' AND created_at<=?
                      AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY id DESC LIMIT 300""", (cutoff, cutoff)).fetchall()
        ranked = []
        for row in rows:
            theirs = self._tokens(row["tags"])
            score = len(query & theirs) / max(len(query | theirs), 1)
            if score:
                item = dict(row)
                item["tags"] = json.loads(item["tags"])
                item["evidence"] = json.loads(item["evidence"])
                ranked.append((score, item))
        return [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]]

    def _fts_verified_candidates(self, tags: Dict[str, Any], cutoff: str, candidate_limit: int = 300):
        match = self._fts_match_query(tags)
        if not match:
            return None
        try:
            with self._con() as con:
                rows = con.execute("""SELECT v.*, bm25(verified_memories_fts) AS fts_rank
                    FROM verified_memories_fts JOIN verified_memories v ON v.id = verified_memories_fts.rowid
                    WHERE verified_memories_fts MATCH ?
                      AND v.status='APPROVED' AND v.created_at<=?
                      AND (v.reviewed_at IS NULL OR v.reviewed_at='' OR v.reviewed_at<=?)
                    ORDER BY fts_rank LIMIT ?""", (match, cutoff, cutoff, candidate_limit)).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.debug("verified FTS fallback: %s", exc)
            return None
        return rows if rows else None

    def rebuild_scenarios(self) -> int:
        """Create L2 scenarios from outcomes *and* rejected/unresolved setups.

        Closed labels provide wins/losses; setup and order-intent state records
        provide rejected/missed/unresolved examples. All are evidence only and
        remain candidate documents until a reviewer explicitly approves them.
        """
        grouped: Dict[str, Dict[str, Any]] = {}
        now = datetime.now(timezone.utc).isoformat()

        def decode(value: Any) -> Dict[str, Any]:
            try:
                parsed = json.loads(value or "{}")
                return parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}

        def group_for(strategy: Any, symbol: Any, side: Any, setup_id: Any,
                      regime: Any) -> Dict[str, Any]:
            tags = {"strategy": str(strategy or "UNKNOWN"), "symbol": str(symbol or "UNKNOWN"),
                    "side": str(side or "UNKNOWN"), "setup_id": str(setup_id or "unclassified_v1"),
                    "regime": str(regime or "UNKNOWN")}
            key = "|".join(str(tags[name]) for name in ("strategy", "symbol", "side", "setup_id", "regime"))
            return grouped.setdefault(key, {"tags": tags, "wins": [], "losses": [], "other": [],
                                             "rejected": [], "unresolved": []})

        with self._con() as con:
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {"trades", "trade_labels"}.issubset(tables):
                rows = con.execute("""
                  SELECT t.ticket,t.strategy,t.symbol,t.side,t.setup_id,t.payload,l.label,l.r_multiple
                  FROM trades t JOIN trade_labels l ON l.ticket=t.ticket
                  WHERE t.closed_at IS NOT NULL ORDER BY t.closed_at DESC
                """).fetchall()
                for row in rows:
                    payload = decode(row["payload"])
                    features = payload.get("decision", {}).get("features", payload.get("features", {}))
                    group = group_for(row["strategy"], row["symbol"], row["side"],
                                      row["setup_id"], features.get("regime", "UNKNOWN"))
                    item = {"ticket": row["ticket"], "label": row["label"], "r_multiple": row["r_multiple"]}
                    if str(row["label"]).startswith("WIN"):
                        group["wins"].append(item)
                    elif str(row["label"]).startswith("LOSS"):
                        group["losses"].append(item)
                    else:
                        group["other"].append(item)
            if "setup_instances" in tables:
                setup_rows = con.execute("""
                  SELECT id,strategy,symbol,side,setup_id,state,ticket,reason,payload
                  FROM setup_instances WHERE state IN ('REJECTED','MISSED','INVALIDATED','UNRESOLVED')
                """).fetchall()
                for row in setup_rows:
                    payload = decode(row["payload"])
                    features = payload.get("features", payload.get("decision", {}).get("features", {}))
                    group = group_for(row["strategy"], row["symbol"], row["side"],
                                      row["setup_id"], features.get("regime", "UNKNOWN"))
                    item = {"instance_id": row["id"], "ticket": row["ticket"],
                            "state": row["state"], "reason": row["reason"]}
                    outcome = self._setup_outcome_label(row["state"], row["reason"])
                    con.execute("""INSERT OR IGNORE INTO setup_labels(
                        subject_type,subject_key,label,rationale,label_rule,created_at,review_status
                    ) VALUES(?,?,?,?,?,?,?)""", (
                        "setup_instance", f"setup_instance:{int(row['id'])}",
                        outcome["label"], outcome["rationale"], outcome["label_rule"], now, "AUTO",
                    ))
                    (group["unresolved"] if row["state"] == "UNRESOLVED" else group["rejected"]).append(item)
            if "order_intents" in tables:
                intent_rows = con.execute("""
                  SELECT intent_key,status,mt5_ticket,payload,error
                  FROM order_intents WHERE status IN ('UNRESOLVED','ERROR','REJECTED','CANCELLED','EXPIRED')
                """).fetchall()
                for row in intent_rows:
                    payload = decode(row["payload"])
                    group = group_for(payload.get("strategy"), payload.get("symbol"), payload.get("side"),
                                      payload.get("setup_id"), payload.get("regime", "UNKNOWN"))
                    item = {"intent_key": row["intent_key"], "ticket": row["mt5_ticket"],
                            "status": row["status"], "error": row["error"]}
                    (group["unresolved"] if row["status"] in {"UNRESOLVED", "ERROR"} else group["rejected"]).append(item)
                    outcome = self._setup_outcome_label(row["status"], row["error"])
                    con.execute("""INSERT OR IGNORE INTO setup_labels(
                        subject_type,subject_key,label,rationale,label_rule,created_at,review_status
                    ) VALUES(?,?,?,?,?,?,?)""", (
                        "order_intent", f"order_intent:{row['intent_key']}",
                        outcome["label"], outcome["rationale"], outcome["label_rule"], now, "AUTO",
                    ))
        with self._con() as con:
            for key, group in grouped.items():
                evidence = {name: group[name][:3] for name in ("wins", "losses", "other", "rejected", "unresolved")}
                total = sum(len(group[name]) for name in ("wins", "losses", "other", "rejected", "unresolved"))
                content = {
                    "rule": "Scenario evidence is descriptive; it cannot override deterministic permission.",
                    "sample_count": total, "win_count": len(group["wins"]), "loss_count": len(group["losses"]),
                    "rejected_count": len(group["rejected"]), "unresolved_count": len(group["unresolved"]),
                    "balanced": bool(group["wins"] and group["losses"]),
                }
                con.execute("""INSERT INTO scenario_documents
                  (scenario_key,created_at,updated_at,tags,sample_count,win_count,loss_count,rejected_count,unresolved_count,content,evidence,status)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(scenario_key) DO UPDATE SET updated_at=excluded.updated_at,tags=excluded.tags,
                  sample_count=excluded.sample_count,win_count=excluded.win_count,loss_count=excluded.loss_count,
                  rejected_count=excluded.rejected_count,unresolved_count=excluded.unresolved_count,
                  content=excluded.content,evidence=excluded.evidence,
                  status=CASE WHEN scenario_documents.status='APPROVED' THEN 'APPROVED' ELSE excluded.status END""", (
                    key, now, now, json.dumps(group["tags"]), total, len(group["wins"]), len(group["losses"]),
                    len(group["rejected"]), len(group["unresolved"]),
                    json.dumps(content), json.dumps(evidence), "CANDIDATE",
                ))
        return len(grouped)

    def review_scenario(self, scenario_key: str, *, approve: bool, reviewer: str) -> None:
        """Require review before an L2 scenario can enter proposal context."""
        if not str(scenario_key).strip() or not str(reviewer).strip():
            raise ValueError("scenario_key and reviewer are required")
        status = "APPROVED" if approve else "RETIRED"
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            changed = con.execute(
                "UPDATE scenario_documents SET status=?,reviewed_by=?,reviewed_at=? "
                "WHERE scenario_key=? AND status='CANDIDATE'",
                (status, str(reviewer)[:200], now, str(scenario_key)),
            ).rowcount
        if not changed:
            raise ValueError("only an existing candidate scenario may be reviewed once")

    def list_scenarios(self, status: str = "") -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM scenario_documents WHERE status=? ORDER BY updated_at DESC" if status
                else "SELECT * FROM scenario_documents ORDER BY updated_at DESC",
                (status,) if status else (),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item["tags"])
            item["content"] = json.loads(item["content"])
            item["evidence"] = json.loads(item["evidence"])
            result.append(item)
        return result

    def retrieve_scenarios(self, tags: Dict[str, Any], limit: int = 3, *, as_of: Any = None) -> List[Dict[str, Any]]:
        """Retrieve matched L2 scenarios, preserving win/loss balance metadata.

        FTS5 BM25 scans scenario_key+tags+content+evidence across the full
        corpus; Jaccard + balance preference keep deterministic semantics.
        """
        cutoff = self._normalize_as_of(as_of)
        query = self._tokens(json.dumps(tags, sort_keys=True, default=str))
        rows = self._fts_scenario_candidates(tags, cutoff)
        if rows is None:
            with self._con() as con:
                rows = con.execute("""SELECT * FROM scenario_documents
                    WHERE status='APPROVED' AND created_at<=? AND updated_at<=?
                      AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY updated_at DESC LIMIT 300""", (cutoff, cutoff, cutoff)).fetchall()
        ranked = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item["tags"])
            item["content"] = json.loads(item["content"])
            item["evidence"] = json.loads(item["evidence"])
            score = len(query & self._tokens(json.dumps(item["tags"], sort_keys=True))) / max(len(query | self._tokens(json.dumps(item["tags"], sort_keys=True))), 1)
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        # Prefer scenarios that contain both verified wins and losses. This
        # prevents a high lexical match made only of winning examples from
        # becoming an unbalanced prompt lesson. If no balanced scenario is
        # available, return the best evidence but mark the warning explicitly
        # so the caller cannot mistake it for a complete comparison.
        balanced = [item for _, item in ranked
                    if bool(item["content"].get("balanced"))
                    and int(item.get("win_count", 0) or 0) > 0
                    and int(item.get("loss_count", 0) or 0) > 0]
        selected = balanced[:max(0, int(limit))]
        if len(selected) < limit:
            selected_keys = {item.get("scenario_key") for item in selected}
            remainder = [item for _, item in ranked if item.get("scenario_key") not in selected_keys]
            selected.extend(remainder[:max(0, int(limit) - len(selected))])
        for item in selected:
            item["retrieval_balance"] = "BALANCED" if item in balanced else "UNBALANCED"
            if item["retrieval_balance"] == "UNBALANCED":
                item["retrieval_warning"] = "No verified win/loss pair in this scenario; treat as incomplete evidence."
        return selected

    def _fts_scenario_candidates(self, tags: Dict[str, Any], cutoff: str, candidate_limit: int = 300):
        match = self._fts_match_query(tags)
        if not match:
            return None
        try:
            with self._con() as con:
                rows = con.execute("""SELECT s.*, bm25(scenario_documents_fts) AS fts_rank
                    FROM scenario_documents_fts JOIN scenario_documents s ON s.rowid = scenario_documents_fts.rowid
                    WHERE scenario_documents_fts MATCH ?
                      AND s.status='APPROVED' AND s.created_at<=? AND s.updated_at<=?
                      AND (s.reviewed_at IS NULL OR s.reviewed_at='' OR s.reviewed_at<=?)
                    ORDER BY fts_rank LIMIT ?""", (match, cutoff, cutoff, cutoff, candidate_limit)).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.debug("scenario FTS fallback: %s", exc)
            return None
        return rows if rows else None

    def propose_core_memory(self, namespace: str, memory_key: str, content: str,
                            source_ids: Iterable[str], confidence: float = 1.0,
                            supersedes: int | None = None) -> int:
        """Create an L3 candidate. It is not returned to an agent until reviewed."""
        allowed = {"TRADING_PERSONALITY", "RISK_POLICY", "STRATEGY_RULE", "STABLE_LESSON", "SKILL", "WIKI", "ACCESS_POLICY"}
        if namespace not in allowed:
            raise ValueError(f"namespace must be one of {sorted(allowed)}")
        confidence_value = self._confidence(confidence)
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            cursor = con.execute(
                "INSERT INTO core_memories(created_at,updated_at,namespace,memory_key,content,source_ids,confidence,status,supersedes) VALUES(?,?,?,?,?,?,?,?,?)",
                (now, now, namespace, memory_key, content[:4000], json.dumps(list(source_ids)),
                 confidence_value, "CANDIDATE", supersedes),
            )
        return int(cursor.lastrowid)

    def review_core_memory(self, memory_id: int, *, approve: bool, reviewer: str) -> None:
        """Human/operator review is mandatory for L3 policy/personality memory."""
        status = "APPROVED" if approve else "RETIRED"
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            updated = con.execute(
                "UPDATE core_memories SET status=?,updated_at=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='CANDIDATE'",
                (status, now, reviewer[:200], now, int(memory_id)),
            ).rowcount
        if not updated:
            raise ValueError("only an existing candidate core memory may be reviewed once")

    def retire_core_memory(self, memory_id: int, *, reviewer: str, reason: str = "") -> Dict[str, Any]:
        """Decommission an active or candidate L3 core memory safely.

        Unlike review_core_memory which only transitions CANDIDATE -> APPROVED/RETIRED,
        retire_core_memory permits retiring an already APPROVED core memory when
        superseded, decayed, or conflicting, with full audit trail.
        """
        reviewer = str(reviewer or "").strip()
        if not reviewer:
            raise ValueError("reviewer is required")
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            row = con.execute("SELECT id, status, namespace, memory_key FROM core_memories WHERE id=?", (int(memory_id),)).fetchone()
            if not row:
                raise ValueError(f"core memory {memory_id} not found")
            if row["status"] == "RETIRED":
                return {"id": int(memory_id), "status": "RETIRED", "previous_status": "RETIRED",
                        "note": "already retired", "execution_authority": False}
            old_status = row["status"]
            con.execute(
                "UPDATE core_memories SET status='RETIRED',updated_at=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                (now, reviewer[:200], now, int(memory_id)),
            )
        return {
            "id": int(memory_id),
            "status": "RETIRED",
            "previous_status": old_status,
            "reviewer": reviewer,
            "reason": str(reason or ""),
            "retired_at": now,
            "execution_authority": False,
        }

    def retrieve_core_memories(self, namespaces: Iterable[str], limit: int = 12, *, as_of: Any = None) -> List[Dict[str, Any]]:
        selected = [str(item) for item in namespaces if item]
        if not selected:
            return []
        cutoff = self._normalize_as_of(as_of)
        placeholders = ",".join("?" for _ in selected)
        with self._con() as con:
            rows = con.execute(
                f"""SELECT * FROM core_memories WHERE status='APPROVED'
                    AND namespace IN ({placeholders}) AND created_at<=?
                    AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY reviewed_at DESC LIMIT ?""",
                (*selected, cutoff, cutoff, limit),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["source_ids"] = json.loads(item["source_ids"])
            results.append(item)
        return results

    def _record_retrieval(self, layer: str, tags: Dict[str, Any], items: Iterable[Any], latency_ms: float,
                          *, as_of: Any = None) -> int:
        materialized = list(items)
        cutoff = self._normalize_as_of(as_of)
        source_ids: List[str] = []
        for item in materialized:
            if isinstance(item, dict):
                item_sources: List[str] = []
                if item.get("source_id"):
                    item_sources.append(str(item["source_id"]))
                item_sources.extend(str(value) for value in item.get("source_ids", []) if value)
                if item.get("scenario_key"):
                    item_sources.append(f"scenario:{item['scenario_key']}")
                if item.get("id") is not None and not item_sources:
                    item_sources.append(f"{layer}:{item['id']}")
                source_ids.extend(item_sources)
            elif isinstance(item, str):
                source_ids.append(f"narrative:{hashlib.sha256(item.encode('utf-8')).hexdigest()[:16]}")
        encoded = json.dumps(materialized, default=str)
        with self._con() as con:
            cursor = con.execute("INSERT INTO memory_retrievals(retrieved_at,layer,query_tags,source_ids,result_count,latency_ms,prompt_char_estimate,as_of) VALUES(?,?,?,?,?,?,?,?)",
                                 (datetime.now(timezone.utc).isoformat(), layer, json.dumps(tags, default=str),
                                  json.dumps(sorted(set(source_ids))), len(materialized), float(latency_ms), len(encoded), cutoff))
        return int(cursor.lastrowid)

    def retrieve_for_proposal(self, tags: Dict[str, Any], *, core_namespaces: Iterable[str],
                             narrative_limit: int = 3, verified_limit: int = 5,
                             scenario_limit: int = 3, core_limit: int = 12,
                             semantic_limit: int = 3, meta_loop_limit: int = 5,
                             case_study_limit: int = 5,
                             as_of: Any = None) -> Dict[str, Any]:
        """Retrieve bounded advisory context and record every layer's telemetry.

        All seven read-only layers run concurrently on worker threads; each
        getter opens its own SQLite connection so this is safe under WAL.
        A layer failure degrades to an empty list plus an ``errors`` entry —
        one broken layer can no longer stall or kill proposal assembly.
        """
        cutoff = self._normalize_as_of(as_of)
        result: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        retrieval_ids: Dict[str, int] = {}
        calls = (
            ("narrative", lambda: self.retrieve_narrative_records(tags, narrative_limit, as_of=cutoff), "similar_past_regimes"),
            ("verified", lambda: self.retrieve_verified(tags, verified_limit, as_of=cutoff), "verified_memories"),
            ("scenario", lambda: self.retrieve_scenarios(tags, scenario_limit, as_of=cutoff), "scenario_memory"),
            ("core", lambda: self.retrieve_core_memories(core_namespaces, core_limit, as_of=cutoff), "core_memory"),
        )

        def _semantic() -> List[Any]:
            # Structured filters/statuses are applied inside the local semantic
            # retriever before cosine ranking; source-linked approved records only.
            from intelligence.semantic_memory import search as semantic_search
            return semantic_search(self.db_path, tags, semantic_limit, as_of=cutoff)

        def _meta_loop() -> List[Any]:
            try:
                from core.meta_loop import get_meta_loop
                meta = get_meta_loop()
                patterns = meta.corpus.get_patterns(limit=meta_loop_limit)
                return [
                    {"type": getattr(p, "pattern_type", lambda: None)().value if hasattr(getattr(p, "pattern_type", None), "value") else str(getattr(p, "pattern_type", "")),
                     "description": getattr(p, "description", ""),
                     "confidence": getattr(p, "confidence", 0.0),
                     "recommendation": getattr(p, "recommendation", "")}
                    for p in patterns
                ]
            except Exception:
                return []

        def _case_studies() -> List[Any]:
            try:
                return self.retrieve_case_study_memories(tags, case_study_limit, as_of=cutoff)
            except Exception:
                return []

        jobs = [(layer, getter, key) for layer, getter, key in calls]
        jobs.append(("semantic", _semantic, "semantic_memory"))
        jobs.append(("meta_loop", _meta_loop, "meta_loop_patterns"))
        jobs.append(("case_study", _case_studies, "case_study_memory"))
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {pool.submit(getter): (layer, key) for layer, getter, key in jobs}
            for future, (layer, key) in futures.items():
                started = time.perf_counter()
                try:
                    items = future.result(timeout=5.0)
                except Exception as exc:  # degrade, never stall the cycle
                    items = []
                    errors[layer] = str(exc)[:300]
                latency_ms = (time.perf_counter() - started) * 1000
                result[key] = items
                try:
                    retrieval_ids[layer] = self._record_retrieval(layer, tags, items, latency_ms, as_of=cutoff)
                except Exception as exc:
                    logger.debug("executor failed: %s", exc)
        if errors:
            result["retrieval_errors"] = errors
        result["retrieval_ids"] = retrieval_ids
        result["retrieval_as_of"] = cutoff
        return result

    def prune_retrievals(self, keep_days: int = 90, *, keep_linked: bool = True) -> int:
        """Delete old unlinked retrieval telemetry so the table cannot grow unbounded.

        Rows linked to a labelled trade are retained by default because they
        back the outcome-association metrics.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(keep_days)))).isoformat()
        linked_clause = "AND outcome_ticket IS NULL" if keep_linked else ""
        with self._con() as con:
            return con.execute(
                f"DELETE FROM memory_retrievals WHERE retrieved_at < ? {linked_clause}",
                (cutoff,),
            ).rowcount

    def propose_case_study_memory(
        self,
        case_id: str,
        label: str,
        primary_domain: str = "",
        entities: List[str] | None = None,
        chokepoints: List[str] | None = None,
        transmission_channels: List[str] | None = None,
        tripwires: List[str] | None = None,
        historical_outcomes: Dict[str, Any] | None = None,
        lead_time_days: int = 0,
        duration_days: int = 30,
        tau_substitution_horizon: str = "",
        reference_url: str = "",
    ) -> int:
        """Store a case study as a CANDIDATE memory in the journal."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            cursor = con.execute("""INSERT INTO case_study_memories
                (created_at,case_id,label,primary_domain,entities,
                 chokepoints,transmission_channels,tripwires,historical_outcomes,
                 lead_time_days,duration_days,tau_substitution_horizon,reference_url,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                now_iso, case_id, label, primary_domain,
                json.dumps(list(entities or []), default=str),
                json.dumps(list(chokepoints or []), default=str),
                json.dumps(list(transmission_channels or []), default=str),
                json.dumps(list(tripwires or []), default=str),
                json.dumps(historical_outcomes or {}, default=str),
                int(lead_time_days), int(duration_days),
                tau_substitution_horizon, reference_url, "CANDIDATE",
            ))
        return int(cursor.lastrowid)

    def retrieve_case_study_memories(
        self, tags: Dict[str, Any], limit: int = 5, as_of: Any = None
    ) -> List[Dict[str, Any]]:
        """Retrieve case study memories matching tags (domain, entities, symbols)."""
        domain = tags.get("disruption_domain", "").upper()
        symbol = tags.get("symbol", "").upper()
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM case_study_memories WHERE status IN ('CANDIDATE','APPROVED') "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            entities_raw = json.loads(row["entities"] or "[]")
            if domain and row["primary_domain"] and domain not in str(row["primary_domain"]).upper():
                continue
            if symbol and symbol not in [str(e).upper() for e in entities_raw]:
                continue
            results.append({
                "id": row["id"],
                "case_id": row["case_id"],
                "label": row["label"],
                "primary_domain": row["primary_domain"],
                "entities": entities_raw,
                "chokepoints": json.loads(row["chokepoints"] or "[]"),
                "transmission_channels": json.loads(row["transmission_channels"] or "[]"),
                "tripwires": json.loads(row["tripwires"] or "[]"),
                "historical_outcomes": json.loads(row["historical_outcomes"] or "{}"),
                "lead_time_days": row["lead_time_days"],
                "duration_days": row["duration_days"],
                "tau_substitution_horizon": row["tau_substitution_horizon"],
                "status": row["status"],
            })
        return results

    def memory_quality_metrics(self) -> Dict[str, Any]:
        """Measure memory quality without inventing a causal trade hit rate."""
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            rows = con.execute("""SELECT retrieval_id,retrieved_at,layer,query_tags,
                source_ids,result_count,latency_ms,prompt_char_estimate,as_of,
                outcome_label,outcome_ticket FROM memory_retrievals""").fetchall()
            stale = con.execute("SELECT COUNT(*) FROM atomic_facts WHERE valid_until IS NOT NULL AND valid_until < ? AND superseded_by IS NULL", (now,)).fetchone()[0]
            contradictory = con.execute("SELECT COUNT(*) FROM atomic_facts WHERE contradicts IS NOT NULL AND superseded_by IS NULL").fetchone()[0]
            scenarios = con.execute("SELECT sample_count,win_count,loss_count FROM scenario_documents").fetchall()
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            outbox = {}
            if "memory_outbox" in tables:
                outbox = {str(row["status"]).lower(): int(row["n"]) for row in con.execute(
                    "SELECT status,COUNT(*) n FROM memory_outbox GROUP BY status").fetchall()}
        def percentile(values: List[float], fraction: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
            return ordered[index]

        malformed_cutoffs = 0
        future_cutoffs = 0
        malformed_telemetry = 0
        total_prompt_chars = 0
        total_results = 0
        zero_result_rows = 0
        by_layer: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            try:
                result_count = int(row["result_count"])
                prompt_chars = int(row["prompt_char_estimate"])
                latency = float(row["latency_ms"])
                if result_count < 0 or prompt_chars < 0 or not math.isfinite(latency) or latency < 0:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                malformed_telemetry += 1
                # Keep the row visible to the audit, but do not let corrupt
                # telemetry abort the operator report or create a fake result.
                result_count, prompt_chars, latency = 0, 0, 0.0
            item = by_layer.setdefault(row["layer"], {"retrievals": 0, "results": 0,
                                                        "latencies": [], "prompt_chars": [],
                                                        "outcomes": [], "zero_result": 0,
                                                        "linked": 0})
            item["retrievals"] += 1; item["results"] += result_count
            item["latencies"].append(latency); item["prompt_chars"].append(prompt_chars)
            total_results += result_count
            total_prompt_chars += prompt_chars
            if result_count == 0:
                zero_result_rows += 1
                item["zero_result"] += 1
            if row["outcome_label"] is not None:
                item["outcomes"].append(str(row["outcome_label"]))
            if row["outcome_ticket"] is not None:
                item["linked"] += 1
            # ``as_of`` is part of the research provenance. Missing or
            # malformed cutoffs are telemetry defects, not permission to use
            # the retrieval in a proposal.
            cutoff = row["as_of"]
            if cutoff:
                try:
                    cutoff_dt = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
                    retrieved_dt = datetime.fromisoformat(str(row["retrieved_at"]).replace("Z", "+00:00"))
                    if cutoff_dt.tzinfo is None:
                        cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)
                    if retrieved_dt.tzinfo is None:
                        retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
                    if cutoff_dt > retrieved_dt.astimezone(timezone.utc):
                        future_cutoffs += 1
                except (TypeError, ValueError, OverflowError):
                    malformed_cutoffs += 1
            else:
                malformed_cutoffs += 1
        layer_metrics = {
            layer: {"retrievals": values["retrievals"], "mean_results": values["results"] / values["retrievals"],
                    "zero_result_rate": values["zero_result"] / values["retrievals"],
                    "mean_latency_ms": sum(values["latencies"]) / len(values["latencies"]),
                    "latency_ms_p50": percentile(values["latencies"], 0.50),
                    "latency_ms_p95": percentile(values["latencies"], 0.95),
                    "mean_prompt_char_estimate": sum(values["prompt_chars"]) / len(values["prompt_chars"]),
                    "prompt_token_estimate": round(sum(values["prompt_chars"]) / 4.0),
                    "outcome_linked": len(values["outcomes"]),
                    "outcome_association_rate": values["linked"] / values["retrievals"],
                    "outcome_counts": {"WIN": sum(label.startswith("WIN") for label in values["outcomes"]),
                                       "LOSS": sum(label.startswith("LOSS") for label in values["outcomes"]),
                                       "OTHER": sum(not (label.startswith("WIN") or label.startswith("LOSS")) for label in values["outcomes"])},
                    "outcome_associated_win_rate": (sum(label.startswith("WIN") for label in values["outcomes"]) / len(values["outcomes"]) if values["outcomes"] else None),
                    # Deliberately not called predictive hit rate: retrieval is
                    # context association, not a randomized causal experiment.
                    "hit_rate": None}
            for layer, values in by_layer.items()
        }
        balanced = sum(1 for row in scenarios if row["win_count"] and row["loss_count"])
        return {"layers": layer_metrics, "stale_active_atomic_facts": stale,
                "active_contradicted_atomic_facts": contradictory,
                "scenario_documents": len(scenarios), "balanced_scenarios": balanced,
                "scenario_balance_rate": balanced / len(scenarios) if scenarios else None,
                "retrieval_count": len(rows), "total_results": total_results,
                "zero_result_rate": (zero_result_rows / len(rows) if rows else None),
                "total_prompt_char_estimate": total_prompt_chars,
                "total_prompt_token_estimate": round(total_prompt_chars / 4.0),
                "point_in_time_cutoff_errors": {"malformed_or_missing": malformed_cutoffs,
                                                 "future_cutoff": future_cutoffs},
                "malformed_telemetry_rows": malformed_telemetry,
                "memory_outbox": outbox,
                "limitation": "Hit rate remains unavailable until retrieval IDs are linked to reconciled labelled trades."}

    def link_retrievals_to_trade(self, retrieval_ids: Dict[str, Any], ticket: int) -> int:
        """Associate proposal-time retrieval evidence with one broker position."""
        ids = [int(value) for value in (retrieval_ids or {}).values() if str(value).isdigit()]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._con() as con:
            return con.execute(f"UPDATE memory_retrievals SET outcome_ticket=? WHERE retrieval_id IN ({placeholders}) AND outcome_ticket IS NULL", (int(ticket), *ids)).rowcount

    def resolve_retrieval_outcome(self, ticket: int, label: str) -> int:
        """Attach only a reconciled deterministic trade label to linked retrievals."""
        with self._con() as con:
            return con.execute("UPDATE memory_retrievals SET outcome_label=? WHERE outcome_ticket=? AND outcome_label IS NULL", (label[:100], int(ticket))).rowcount

    def list_core_memories(self, status: str = "") -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM core_memories WHERE status=? ORDER BY updated_at DESC" if status else "SELECT * FROM core_memories ORDER BY updated_at DESC", (status,) if status else ()).fetchall()
        return [dict(row) for row in rows]

    def append_working_context(self, session_key: str, kind: str, payload: Dict[str, Any], *,
                               source_ids: Iterable[str] = (), importance: str = "NORMAL",
                               max_active: int = 24) -> int:
        """Append bounded short-term context without losing risk/error evidence.

        Normal observations are compacted into a deterministic evidence-indexed
        summary when the per-session window fills.  Validation, risk and error
        records remain individually active; their underlying L0/L1 evidence is
        never deleted by this routine.
        """
        allowed = {"LOW", "NORMAL", "HIGH", "CRITICAL"}
        importance = importance.upper()
        if importance not in allowed:
            raise ValueError(f"importance must be one of {sorted(allowed)}")
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            cursor = con.execute(
                "INSERT INTO working_context_items(session_key,created_at,kind,importance,payload,source_ids) VALUES(?,?,?,?,?,?)",
                (session_key[:200], now, kind[:100], importance, json.dumps(payload, default=str), json.dumps(list(source_ids))),
            )
        self.compact_working_context(session_key, max_active=max_active)
        return int(cursor.lastrowid)

    def compact_working_context(self, session_key: str, *, max_active: int = 24) -> int | None:
        """Replace old low-value observations with an auditable local summary."""
        protected = {"risk_decision", "validation_decision", "error", "kill_switch", "execution"}
        with self._con() as con:
            active = con.execute(
                "SELECT * FROM working_context_items WHERE session_key=? AND status='ACTIVE' ORDER BY id",
                (session_key,),
            ).fetchall()
            if len(active) <= max_active:
                return None
            overflow = len(active) - max_active + 1
            candidates = [row for row in active if row["importance"] in {"LOW", "NORMAL"} and row["kind"] not in protected]
            selected = candidates[:overflow]
            if not selected:
                return None
            summary = {
                "compaction": "deterministic_working_context_v1",
                "count": len(selected),
                "items": [
                    {"id": row["id"], "created_at": row["created_at"], "kind": row["kind"],
                     "payload": json.loads(row["payload"]), "source_ids": json.loads(row["source_ids"])}
                    for row in selected
                ],
                "rule": "Compaction cannot replace individual risk, validation, error, kill-switch or execution records.",
            }
            source_ids = [source for row in selected for source in json.loads(row["source_ids"])]
            cursor = con.execute(
                "INSERT INTO working_context_items(session_key,created_at,kind,importance,payload,source_ids) VALUES(?,?,?,?,?,?)",
                (session_key, datetime.now(timezone.utc).isoformat(), "working_summary", "HIGH",
                 json.dumps(summary, default=str), json.dumps(source_ids)),
            )
            summary_id = int(cursor.lastrowid)
            con.executemany("UPDATE working_context_items SET status='COMPACTED',compacted_into=? WHERE id=?",
                            [(summary_id, row["id"]) for row in selected])
        return summary_id

    def retrieve_working_context(self, session_key: str, limit: int = 16) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM working_context_items WHERE session_key=? AND status='ACTIVE' ORDER BY id DESC LIMIT ?",
                (session_key, max(1, limit)),
            ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            item["source_ids"] = json.loads(item["source_ids"])
            result.append(item)
        return result

    def prune_stale_working_contexts(
        self,
        max_age_hours: int = 48,
        keep_per_session: int = 12,
    ) -> Dict[str, Any]:
        """Prune and compact stale working context across all completed/dormant sessions.

        Scans active working context items in ``working_context_items``.
        Identifies items created more than ``max_age_hours`` ago.
        Strict invariant: protected records (risk_decision, validation_decision, error,
        kill_switch, execution) are NEVER compacted, archived, or deleted.

        For non-protected stale items exceeding ``keep_per_session`` in old sessions,
        sets status='ARCHIVED' to reclaim token budget and database footprint.

        Returns a dictionary summarizing scanned, archived, and preserved record counts.
        """
        protected = {"risk_decision", "validation_decision", "error", "kill_switch", "execution"}
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        cutoff_str = cutoff_dt.isoformat()

        archived_count = 0
        preserved_protected_count = 0
        scanned_sessions = set()

        with self._con() as con:
            rows = con.execute(
                "SELECT DISTINCT session_key FROM working_context_items WHERE status='ACTIVE' AND created_at < ?",
                (cutoff_str,),
            ).fetchall()
            sessions = [r["session_key"] for r in rows]

            for session_key in sessions:
                scanned_sessions.add(session_key)
                active_items = con.execute(
                    "SELECT * FROM working_context_items WHERE session_key=? AND status='ACTIVE' ORDER BY id ASC",
                    (session_key,),
                ).fetchall()

                stale_candidates = []
                for item in active_items:
                    if item["kind"] in protected or item["importance"] == "CRITICAL":
                        preserved_protected_count += 1
                    elif item["created_at"] < cutoff_str:
                        stale_candidates.append(item)

                if len(stale_candidates) > keep_per_session:
                    to_archive = stale_candidates[:-keep_per_session]
                else:
                    to_archive = []

                if to_archive:
                    ids = [row["id"] for row in to_archive]
                    placeholders = ",".join("?" for _ in ids)
                    con.execute(
                        f"UPDATE working_context_items SET status='ARCHIVED' WHERE id IN ({placeholders})",
                        ids,
                    )
                    archived_count += len(ids)

        return {
            "scanned_sessions_count": len(scanned_sessions),
            "archived_count": archived_count,
            "preserved_protected_count": preserved_protected_count,
            "max_age_hours": max_age_hours,
            "keep_per_session": keep_per_session,
            "execution_authority": False,
        }

    def label_closed_trade(self, ticket: int, pnl: float, risk_amount: float, exit_reason: str) -> Dict[str, Any]:
        r = pnl / risk_amount if risk_amount > 0 else 0.0
        reason = (exit_reason or "").lower()
        if "target" in reason or "take profit" in reason or " tp" in reason:
            label, rationale, rule = "WIN_TARGET", "broker/manager recorded target exit", "exit_reason_target_v1"
        elif "stop" in reason or "sl" in reason:
            label, rationale, rule = "LOSS_STOP", "closed at/through protective stop", "exit_reason_stop_v1"
        elif "breakeven" in reason or "break even" in reason:
            label, rationale, rule = "BREAKEVEN_EXIT", "recorded breakeven exit", "exit_reason_breakeven_v1"
        elif "time" in reason:
            label, rationale, rule = "TIME_EXIT", "recorded time-based exit", "exit_reason_time_v1"
        elif "event" in reason or "news" in reason:
            label, rationale, rule = "EVENT_EXIT", "recorded event-risk exit", "exit_reason_event_v1"
        elif "manual" in reason:
            label, rationale, rule = "MANUAL_EXIT", "manual/operator exit", "exit_reason_manual_v1"
        elif pnl > 0 and r >= 1:
            label, rationale, rule = "WIN_VALIDATED", "positive result of at least +1R", "pnl_r_v1"
        elif pnl > 0:
            label, rationale, rule = "WIN_SMALL", "positive result below +1R", "pnl_r_v1"
        elif pnl < 0:
            label, rationale, rule = "LOSS_OTHER", "negative result outside explicit stop reason", "pnl_r_v1"
        else:
            label, rationale, rule = "BREAKEVEN_EXIT", "zero-P&L exit without a more specific broker reason", "pnl_r_v1"
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status) VALUES(?,?,?,?,?,?,?)", (ticket, label, rationale, r, datetime.now(timezone.utc).isoformat(), rule, "AUTO"))
        return {"label": label, "r_multiple": r, "rationale": rationale, "label_rule": rule, "review_status": "AUTO"}

    @staticmethod
    def _setup_outcome_label(state: str, reason: str = "") -> Dict[str, str]:
        return classify_setup_outcome(state, reason)

    def label_setup_instance(self, instance_id: int, state: str, reason: str = "") -> Dict[str, Any]:
        """Persist an immutable deterministic label for a non-trade setup."""
        outcome = self._setup_outcome_label(state, reason)
        subject_key = f"setup_instance:{int(instance_id)}"
        with self._con() as con:
            persist_setup_label(con, "setup_instance", subject_key, state, reason)
        return {**outcome, "subject_type": "setup_instance", "subject_key": subject_key, "review_status": "AUTO"}

    def label_order_intent(self, intent_key: str, status: str, error: str = "") -> Dict[str, Any]:
        """Persist an immutable deterministic label for an unresolved intent."""
        outcome = self._setup_outcome_label(status, error)
        subject_key = f"order_intent:{str(intent_key)}"
        with self._con() as con:
            persist_setup_label(con, "order_intent", subject_key, status, error)
        return {**outcome, "subject_type": "order_intent", "subject_key": subject_key, "review_status": "AUTO"}

    def list_setup_labels(self, label: str = "") -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM setup_labels WHERE label=? ORDER BY created_at DESC" if label
                else "SELECT * FROM setup_labels ORDER BY created_at DESC",
                (label,) if label else (),
            ).fetchall()
        return [dict(row) for row in rows]

    def review_setup_label(self, subject_key: str, *, approve: bool, reviewer: str) -> None:
        """Record operator review without changing the deterministic label."""
        if not str(subject_key).strip() or not str(reviewer).strip():
            raise ValueError("subject_key and reviewer are required")
        status = "APPROVED" if approve else "RETIRED"
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            ensure_setup_label_table(con)
            changed = con.execute("""UPDATE setup_labels SET review_status=?,reviewed_by=?,reviewed_at=?
                WHERE subject_key=? AND review_status='AUTO'""",
                                 (status, str(reviewer)[:200], now, str(subject_key))).rowcount
        if not changed:
            raise ValueError("only an existing AUTO setup label may be reviewed once")

    def is_verified_memory_source(self, kind: str, source_id: str, payload: Dict[str, Any] | None = None) -> bool:
        """Return whether an outbox item is backed by a durable, point-in-time fact.

        Distillation is deliberately allow-listed.  Recalled narratives,
        arbitrary operator text, and unknown source kinds cannot be promoted
        into new memory merely because an LLM produced plausible JSON.
        """
        kind = str(kind or "")
        source_id = str(source_id or "")
        payload = payload or {}
        with self._con() as con:
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if kind == "closed_trade":
                try:
                    ticket = int(source_id)
                except (TypeError, ValueError):
                    return False
                if not {"trades", "trade_labels"}.issubset(tables):
                    return False
                row = con.execute("""SELECT t.closed_at,l.label
                    FROM trades t JOIN trade_labels l ON l.ticket=t.ticket
                    WHERE t.ticket=? AND t.closed_at IS NOT NULL""", (ticket,)).fetchone()
                if not row:
                    return False
                claimed = str(payload.get("label", ""))
                return not claimed or claimed == str(row["label"])
            if kind in {"macro_event", "event_reaction"}:
                if kind == "event_reaction" and "event_reactions" in tables:
                    try:
                        return bool(con.execute("SELECT 1 FROM event_reactions WHERE id=?", (int(source_id),)).fetchone())
                    except (TypeError, ValueError):
                        return False
                if kind == "macro_event" and "macro_events" in tables:
                    try:
                        return bool(con.execute("SELECT 1 FROM macro_events WHERE id=?", (int(source_id),)).fetchone())
                    except (TypeError, ValueError):
                        return False
        return False

    def pattern_summary(self, limit: int = 8) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("""
              SELECT t.strategy,t.symbol,t.side,l.label,COUNT(*) n,AVG(l.r_multiple) avg_r
              FROM trades t JOIN trade_labels l ON t.ticket=l.ticket
              GROUP BY t.strategy,t.symbol,t.side,l.label HAVING n >= 3
              ORDER BY avg_r DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def candidate_review_queue(self, limit: int = 20) -> Dict[str, Any]:
        """Aggregate CANDIDATE memory rows for a single operator review surface.

        Does not approve anything and never grants execution authority.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        limit = min(int(limit), 100)
        narratives_all = self.list_narratives("CANDIDATE")
        verified_all = self.list_verified_memories("CANDIDATE")
        scenarios_all = self.list_scenarios("CANDIDATE")
        core_all = self.list_core_memories("CANDIDATE")
        narratives = narratives_all[:limit]
        verified = verified_all[:limit]
        scenarios = scenarios_all[:limit]
        core = core_all[:limit]

        def _brief_narrative(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "kind": "narrative",
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "summary": str(row.get("content") or row.get("summary") or "")[:240],
                "status": row.get("status"),
            }

        def _brief_verified(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "kind": "verified",
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "memory_kind": row.get("kind"),
                "summary": str(row.get("summary") or "")[:240],
                "status": row.get("status"),
            }

        def _brief_scenario(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "kind": "scenario",
                "scenario_key": row.get("scenario_key"),
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "sample_count": row.get("sample_count"),
                "win_count": row.get("win_count"),
                "loss_count": row.get("loss_count"),
                "status": row.get("status"),
            }

        def _brief_core(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "kind": "core",
                "id": row.get("id"),
                "namespace": row.get("namespace"),
                "memory_key": row.get("memory_key"),
                "updated_at": row.get("updated_at"),
                "status": row.get("status"),
            }

        counts = {
            "narrative": len(narratives_all),
            "verified": len(verified_all),
            "scenario": len(scenarios_all),
            "core": len(core_all),
        }
        items = (
            [_brief_narrative(r) for r in narratives]
            + [_brief_verified(r) for r in verified]
            + [_brief_scenario(r) for r in scenarios]
            + [_brief_core(r) for r in core]
        )
        return {
            "status": "REVIEW_REQUIRED" if sum(counts.values()) else "CLEAR",
            "counts": counts,
            "total_candidates": int(sum(counts.values())),
            "items": items[: max(limit * 4, limit)],
            "next_actions": [
                "python mt5_trader.py memory-review narrative approve --id ID",
                "python mt5_trader.py memory-review verified approve --id ID",
                "python mt5_trader.py memory-review scenario approve --scenario-key KEY",
                "python mt5_trader.py core-memory approve --id ID",
            ],
            "execution_authority": False,
            "limitation": "Queue is operator evidence only; approval does not grant order permission.",
        }

    def format_approved_lessons(self, *, limit: int = 6) -> str:
        """Build auditable ICL lesson text from APPROVED journal memory only."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        lines: List[str] = []
        verified = self.list_verified_memories("APPROVED")[:limit]
        for row in verified:
            src = row.get("id")
            summary = str(row.get("summary") or row.get("lesson") or "").strip()
            if summary:
                lines.append(f"- [verified:{src}] {summary[:300]}")
        scenarios = self.list_scenarios("APPROVED")[: max(1, limit // 2)]
        for row in scenarios:
            key = row.get("scenario_key")
            wins = row.get("win_count")
            losses = row.get("loss_count")
            lines.append(
                f"- [scenario:{key}] samples wins={wins} losses={losses}; treat as research evidence only."
            )
        # Recent labelled losses from the authoritative journal (not perf JSON).
        with self._con() as con:
            tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {"trades", "trade_labels"}.issubset(tables):
                rows = con.execute("""
                    SELECT t.ticket,t.symbol,t.side,t.pnl,l.label,l.r_multiple,l.rationale
                    FROM trades t JOIN trade_labels l ON l.ticket=t.ticket
                    WHERE t.closed_at IS NOT NULL AND (l.r_multiple < 0 OR t.pnl < 0)
                    ORDER BY t.closed_at DESC LIMIT ?
                """, (min(3, limit),)).fetchall()
                for row in rows:
                    rationale = str(row["rationale"] or "")[:160]
                    lines.append(
                        f"- [trade:{row['ticket']}] LOSS {row['symbol']} {row['side']} "
                        f"label={row['label']} R={float(row['r_multiple'] or 0):.2f}. {rationale}"
                    )
        if not lines:
            return "No approved journal lessons yet."
        return "\n".join(lines[:limit])
