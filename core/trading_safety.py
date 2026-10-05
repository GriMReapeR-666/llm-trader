"""Deterministic, restart-safe safety primitives for the MT5 trader.

This module deliberately has no LLM dependency.  It can be unit tested and
replayed from journal data before a proposal is allowed to reach MT5.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple


log = logging.getLogger(__name__)

try:
    from operations.journal_scope import ensure_scope
except ImportError:
    from operations.journal_scope import ensure_scope

try:
    from research.setup_labeling import ensure_setup_label_table, persist_setup_label
except ImportError:
    from research.setup_labeling import ensure_setup_label_table, persist_setup_label

# ── Live Health Monitor (Blind Spot audit) ───────────────────────────────────
try:
    from core.live_health_monitor import LiveHealthMonitor as _LiveHealthMonitor
except ImportError:
    _LiveHealthMonitor = None


SWING_MAGIC = 460001
SCALP_MAGIC = 460002
NEOCORTEX_MAGIC = 460003

#: Every magic number the engine itself issues. Operator flatten, kill-switch
#: cancellation, position management, risk limits and reconciliation must all
#: use this set: anything outside it is manual trading and is left alone by
#: owned-only actions (the guardian's RED trip still flattens everything).
BOT_MAGICS = frozenset({SWING_MAGIC, SCALP_MAGIC, NEOCORTEX_MAGIC})


@dataclass(frozen=True)
class TradeProposal:
    strategy: str
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    features: Dict[str, Any]
    model: str = ""
    prompt_hash: str = ""

    @property
    def magic(self) -> int:
        if self.strategy in ("macro", "neocortex", "causal"):
            return NEOCORTEX_MAGIC
        return SWING_MAGIC if self.strategy == "swing" else SCALP_MAGIC

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        return abs(self.take_profit - self.entry) / risk if risk > 0 else 0.0

    def setup_key(self) -> str:
        raw = f"{self.strategy}|{self.features.get('setup_id', '')}|{self.symbol}|{self.side}|{round(self.entry, 5)}|{self.features.get('regime', '')}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class MarketEvidence:
    """Frozen typed market evidence required by DeterministicValidator (P1-H4).

    Every gate input is a required field with no default — except
    `breakout_event` (an optional trigger; absent means False) and
    `setup_cooldown_seconds` (configuration with a 900 s default), which
    are not evidence (M6). Both still fail safe when absent.
    """
    market_open: bool
    tick_age_ms: float
    max_tick_age_ms: float
    symbol_tradable: bool
    spread: float
    max_spread: float
    clock_ok: bool
    session_allowed: bool
    liquidity_ok: bool
    microstructure_ok: bool
    required_data_ok: bool
    completed_candle: bool
    breakout_event: bool = False
    stop_distance: float = 0.0
    min_stop_distance: float = 0.0
    high_impact_news: bool = False
    setup_cooldown_seconds: float = 900.0
    raw: Dict[str, Any] = field(default_factory=dict)

    REQUIRED_FIELDS = (
        "market_open",
        "tick_age_ms",
        "max_tick_age_ms",
        "symbol_tradable",
        "spread",
        "max_spread",
        "clock_ok",
        "session_allowed",
        "liquidity_ok",
        "microstructure_ok",
        "required_data_ok",
        "completed_candle",
        "stop_distance",
        "min_stop_distance",
        "high_impact_news",
    )

    # Optional non-evidence inputs: validated when supplied, defaulted when absent.
    OPTIONAL_FIELDS = ("breakout_event", "setup_cooldown_seconds")

    @classmethod
    def parse(cls, market: Any) -> Tuple[Optional["MarketEvidence"], List[str]]:
        problems: List[str] = []
        if not isinstance(market, dict):
            return None, ["missing evidence: market evidence must be a dict"]

        for field_name in cls.REQUIRED_FIELDS:
            if field_name not in market or market[field_name] is None:
                problems.append(f"missing evidence: {field_name}")
            else:
                val = market[field_name]
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if not math.isfinite(val):
                        problems.append(f"missing evidence: {field_name}")
                elif not isinstance(val, (bool, int, float)):
                    problems.append(f"missing evidence: {field_name}")

        # Check non-finite optional numeric inputs when supplied
        for opt in ("changepoint_probability", "lag_confidence", "friction_r", "min_net_ev_r", "entry_tolerance", "execution_price"):
            if opt in market and market[opt] is not None:
                try:
                    v = float(market[opt])
                    if not math.isfinite(v):
                        problems.append(f"missing evidence: {opt}")
                except (TypeError, ValueError):
                    problems.append(f"missing evidence: {opt}")

        if problems:
            return None, problems

        def _optional_bool(name: str, default: bool = False) -> bool:
            val = market.get(name, default)
            return bool(val) if isinstance(val, (bool, int, float)) else default

        def _optional_float(name: str, default: float) -> float:
            val = market.get(name, default)
            try:
                num = float(val) if val is not None else default
                return num if math.isfinite(num) else default
            except (TypeError, ValueError):
                return default

        return cls(
            market_open=bool(market["market_open"]),
            tick_age_ms=float(market["tick_age_ms"]),
            max_tick_age_ms=float(market["max_tick_age_ms"]),
            symbol_tradable=bool(market["symbol_tradable"]),
            spread=float(market["spread"]),
            max_spread=float(market["max_spread"]),
            clock_ok=bool(market["clock_ok"]),
            session_allowed=bool(market["session_allowed"]),
            liquidity_ok=bool(market["liquidity_ok"]),
            microstructure_ok=bool(market["microstructure_ok"]),
            required_data_ok=bool(market["required_data_ok"]),
            completed_candle=bool(market["completed_candle"]),
            breakout_event=_optional_bool("breakout_event", False),
            stop_distance=float(market["stop_distance"]),
            min_stop_distance=float(market["min_stop_distance"]),
            high_impact_news=bool(market["high_impact_news"]),
            setup_cooldown_seconds=_optional_float("setup_cooldown_seconds", 900.0),
            raw=dict(market),
        ), []


@dataclass
class ValidationResult:
    approved: bool
    reasons: List[str]
    confluence_score: int
    rule_trace: Dict[str, Any] = field(default_factory=dict)
    # Evidence binding for OrderGateway (review R6): which setup was approved
    # and when. The gateway refuses LIVE/DEMO entries whose evidence is for
    # another setup or older than its max_evidence_age_s.
    setup_key: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Order-intent states that hold risk budget (CC-F1). A ticketed FILLED/ERROR
# intent reserves only until a trades row exists for its ticket; once the
# journal has the trade (open or closed) the trade row is the record, so a
# broker-side SL/TP close releases the budget (review R1).
RESERVING_INTENT_STATUSES = ("CREATED", "PENDING", "PARTIAL", "UNRESOLVED")
TICKETED_INTENT_STATUSES = ("FILLED", "ERROR")


def reserving_intent_payloads(con: sqlite3.Connection, exclude_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Payloads of order intents that still reserve risk, deduped against trades."""
    def table(name: str) -> bool:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    if not table("order_intents"):
        return []
    traded = {r[0] for r in con.execute("SELECT ticket FROM trades").fetchall()} if table("trades") else set()
    statuses = RESERVING_INTENT_STATUSES + TICKETED_INTENT_STATUSES
    marks = ",".join("?" for _ in statuses)
    out: List[Dict[str, Any]] = []
    for key, status, ticket, payload in con.execute(
            f"SELECT intent_key, status, mt5_ticket, payload FROM order_intents WHERE status IN ({marks})", statuses):
        if key == exclude_key or (ticket is not None and ticket in traded):
            continue
        if status in TICKETED_INTENT_STATUSES and ticket is None:
            continue
        try:
            out.append(json.loads(payload) if isinstance(payload, str) else dict(payload or {}))
        except Exception:
            continue
    return out


class TradingStore:
    """Small SQLite store for limits, proposals, orders, and durable journal."""

    def __init__(self, path: str = "trading_state.db", mode: Optional[str] = None, account_fingerprint: str = ""):
        if path == "trading_state.db":
            env_path = os.getenv("LLM_TRADER_JOURNAL_PATH")
            if env_path:
                path = env_path
            elif not os.path.exists("trading_state.db") and os.path.exists(os.path.join("data", "trading_state.db")):
                path = os.path.join("data", "trading_state.db")
        self.path = path
        self._local = threading.local()
        requested_mode = mode or os.getenv("TRADING_JOURNAL_MODE", "").strip()
        # Validate the execution scope before creating any operational tables.
        # An invalid DEMO/LIVE identity must not leave behind a database that
        # looks ready for use after startup fails closed.
        self.scope = ensure_scope(path, requested_mode, account_fingerprint or os.getenv("MT5_LOGIN", "")) if requested_mode else None
        self._init_db()
        # Research evidence is part of the authoritative journal schema from
        # first creation, not a side table that appears only after a research
        # CLI run. The local import avoids an import-time dependency cycle.
        try:
            from research.research_runs import ensure as ensure_research_validation_runs
        except ImportError:
            try:
                from research.research_runs import ensure as ensure_research_validation_runs
            except ImportError:
                ensure_research_validation_runs = lambda _p: None
        ensure_research_validation_runs(self.path)

    def _connect(self) -> sqlite3.Connection:
        if not hasattr(self, "_local"):
            self._local = threading.local()
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
                cur.execute("PRAGMA busy_timeout=5000;")
                cur.execute("PRAGMA cache_size=-64000;")
                cur.execute("PRAGMA temp_store=MEMORY;")
                cur.close()
            except Exception:
                pass
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close thread-local database connection if active."""
        if hasattr(self, "_local") and getattr(self._local, "conn", None) is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, setup_key TEXT NOT NULL,
              strategy TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
              payload TEXT NOT NULL, approved INTEGER NOT NULL, reasons TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trades (
              ticket INTEGER PRIMARY KEY, magic INTEGER NOT NULL, strategy TEXT NOT NULL,
              symbol TEXT NOT NULL, side TEXT NOT NULL, volume REAL NOT NULL,
              entry REAL NOT NULL, stop_loss REAL, take_profit REAL, risk_amount REAL NOT NULL,
              opened_at TEXT NOT NULL, closed_at TEXT, exit_price REAL, pnl REAL,
              exit_reason TEXT, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(closed_at, magic);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol_closed ON trades(symbol, closed_at);
            CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at DESC);
            CREATE INDEX IF NOT EXISTS idx_proposals_setup ON proposals(setup_key, created_at);
            CREATE INDEX IF NOT EXISTS idx_proposals_symbol_created ON proposals(symbol, created_at DESC);
            -- The net-EV expectancy gate joins this table (see
            -- DeterministicValidator._rolling_net_ev). It was missing from this
            -- DDL, so the query raised "no such table: trade_labels" on every
            -- call and the gate was silently bypassed via the call-site
            -- `except`. Schema mirrors intelligence/trading_memory.py so the two
            -- writers agree.
            CREATE TABLE IF NOT EXISTS trade_labels (
              ticket INTEGER PRIMARY KEY, label TEXT NOT NULL, rationale TEXT NOT NULL,
              r_multiple REAL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trade_labels_created ON trade_labels(created_at);
            CREATE TABLE IF NOT EXISTS setup_instances (
              id INTEGER PRIMARY KEY, setup_key TEXT NOT NULL, setup_id TEXT NOT NULL,
              strategy TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
              state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              ticket INTEGER, reason TEXT, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_setup_instances_key ON setup_instances(setup_key, id);
            CREATE INDEX IF NOT EXISTS idx_setup_instances_state ON setup_instances(setup_id, state, created_at);
            CREATE INDEX IF NOT EXISTS idx_setup_instances_lookup ON setup_instances(setup_key, state, created_at);
            CREATE TABLE IF NOT EXISTS setup_labels (
              subject_type TEXT NOT NULL, subject_key TEXT PRIMARY KEY, label TEXT NOT NULL,
              rationale TEXT NOT NULL, label_rule TEXT NOT NULL, created_at TEXT NOT NULL,
              review_status TEXT NOT NULL DEFAULT 'AUTO'
            );
            CREATE INDEX IF NOT EXISTS idx_setup_labels_label ON setup_labels(label, created_at);
            CREATE TABLE IF NOT EXISTS manual_activity (
              id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, ticket INTEGER,
              symbol TEXT, side TEXT, magic INTEGER, action TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_manual_activity_ticket ON manual_activity(ticket, observed_at);
            CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, ticket INTEGER,
              setup_key TEXT, event_type TEXT NOT NULL, actor TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trade_lifecycle_ticket ON trade_lifecycle_events(ticket, created_at);
            CREATE TRIGGER IF NOT EXISTS trade_lifecycle_events_immutable_update
              BEFORE UPDATE ON trade_lifecycle_events BEGIN
                SELECT RAISE(ABORT, 'trade lifecycle events are append-only');
              END;
            CREATE TRIGGER IF NOT EXISTS trade_lifecycle_events_immutable_delete
              BEFORE DELETE ON trade_lifecycle_events BEGIN
                SELECT RAISE(ABORT, 'trade lifecycle events are append-only');
              END;
            CREATE TABLE IF NOT EXISTS position_observations (
              id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, ticket INTEGER NOT NULL,
              price REAL NOT NULL, unrealized_pnl REAL, r_multiple REAL,
              source TEXT NOT NULL DEFAULT 'unknown', quote_time_msc INTEGER,
              tick_age_ms REAL, spread REAL, mark_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
              FOREIGN KEY(ticket) REFERENCES trades(ticket)
            );
            CREATE INDEX IF NOT EXISTS idx_position_observations_ticket ON position_observations(ticket, observed_at);
            CREATE TRIGGER IF NOT EXISTS position_observations_immutable_update
              BEFORE UPDATE ON position_observations BEGIN
                SELECT RAISE(ABORT, 'position observations are append-only');
              END;
            CREATE TRIGGER IF NOT EXISTS position_observations_immutable_delete
              BEFORE DELETE ON position_observations BEGIN
                SELECT RAISE(ABORT, 'position observations are append-only');
              END;
            CREATE TABLE IF NOT EXISTS market_ticks (
              id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, symbol TEXT NOT NULL,
              source TEXT NOT NULL, quote_time_msc INTEGER, bid REAL, ask REAL,
              mid REAL, spread REAL, tick_age_ms REAL, quality TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_market_ticks_lookup
              ON market_ticks(symbol, observed_at);
            CREATE TRIGGER IF NOT EXISTS market_ticks_immutable_update
              BEFORE UPDATE ON market_ticks BEGIN
                SELECT RAISE(ABORT, 'market ticks are append-only');
              END;
            CREATE TRIGGER IF NOT EXISTS market_ticks_immutable_delete
              BEFORE DELETE ON market_ticks BEGIN
                SELECT RAISE(ABORT, 'market ticks are append-only');
              END;
            CREATE TABLE IF NOT EXISTS broker_execution_reports (
              id INTEGER PRIMARY KEY, reported_at TEXT NOT NULL, intent_key TEXT,
              order_ticket INTEGER, deal_ticket INTEGER, position_ticket INTEGER,
              event_type TEXT NOT NULL, retcode INTEGER,
              requested_price REAL, accepted_price REAL, filled_price REAL,
              requested_volume REAL, accepted_volume REAL, filled_volume REAL,
              commission REAL, swap REAL, execution_latency_ms REAL,
              spread_at_submit REAL, spread_at_fill REAL,
              implementation_shortfall REAL, requote_count INTEGER,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_broker_execution_intent
              ON broker_execution_reports(intent_key, reported_at);
            CREATE INDEX IF NOT EXISTS idx_broker_execution_position
              ON broker_execution_reports(position_ticket, reported_at);
            CREATE TRIGGER IF NOT EXISTS broker_execution_reports_immutable_update
              BEFORE UPDATE ON broker_execution_reports BEGIN
                SELECT RAISE(ABORT, 'broker execution reports are append-only');
              END;
            CREATE TRIGGER IF NOT EXISTS broker_execution_reports_immutable_delete
              BEFORE DELETE ON broker_execution_reports BEGIN
                SELECT RAISE(ABORT, 'broker execution reports are append-only');
              END;
            CREATE TABLE IF NOT EXISTS daily_reconciliations (
              period_start TEXT PRIMARY KEY, period_end TEXT NOT NULL,
              recorded_at TEXT NOT NULL, status TEXT NOT NULL,
              report TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_daily_reconciliations_status
              ON daily_reconciliations(status, period_start);
            CREATE TABLE IF NOT EXISTS model_registry (
              id INTEGER PRIMARY KEY, model_key TEXT NOT NULL, version TEXT NOT NULL,
              kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'CANDIDATE',
              feature_schema_version TEXT, training_data_hash TEXT,
              metrics TEXT NOT NULL, artifact_uri TEXT, created_at TEXT NOT NULL,
              reviewed_at TEXT, reviewed_by TEXT, review_note TEXT,
              UNIQUE(model_key, version)
            );
            CREATE INDEX IF NOT EXISTS idx_model_registry_status
              ON model_registry(model_key, status, created_at);
             CREATE TABLE IF NOT EXISTS execution_reviews (
              intent_key TEXT PRIMARY KEY, first_detected_at TEXT NOT NULL,
              last_detected_at TEXT NOT NULL, status TEXT NOT NULL,
              severity TEXT NOT NULL, detections INTEGER NOT NULL DEFAULT 1,
              reason TEXT NOT NULL, operator TEXT, resolution_note TEXT,
              resolved_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT, last_retry_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_execution_reviews_status
              ON execution_reviews(status, last_detected_at);
            CREATE TABLE IF NOT EXISTS operator_controls (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              action TEXT NOT NULL,
              actor TEXT NOT NULL,
              source TEXT NOT NULL,
              issued_at TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('REQUESTED', 'ACKNOWLEDGED', 'APPLIED', 'FAILED', 'EXPIRED')),
              acked_at TEXT,
              applied_at TEXT,
              result_json TEXT,
              idempotency_key TEXT UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_operator_controls_status
              ON operator_controls(status, issued_at);
            """)
            columns = {row["name"] for row in con.execute("PRAGMA table_info(trades)").fetchall()}
            if "setup_id" not in columns:
                con.execute("ALTER TABLE trades ADD COLUMN setup_id TEXT")
            for name, definition in {
                "gross_pnl": "REAL", "commission": "REAL", "swap": "REAL", "financing": "REAL",
            }.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE trades ADD COLUMN {name} {definition}")
            mark_columns = {row["name"] for row in con.execute("PRAGMA table_info(position_observations)").fetchall()}
            for name, definition in {
                "source": "TEXT NOT NULL DEFAULT 'unknown'", "quote_time_msc": "INTEGER",
                "tick_age_ms": "REAL", "spread": "REAL", "mark_quality": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            }.items():
                if name not in mark_columns:
                    con.execute(f"ALTER TABLE position_observations ADD COLUMN {name} {definition}")
            execution_columns = {row["name"] for row in con.execute("PRAGMA table_info(broker_execution_reports)").fetchall()}
            for name, definition in {
                "execution_latency_ms": "REAL", "spread_at_submit": "REAL", "spread_at_fill": "REAL",
                "implementation_shortfall": "REAL", "requote_count": "INTEGER",
            }.items():
                if name not in execution_columns:
                    con.execute(f"ALTER TABLE broker_execution_reports ADD COLUMN {name} {definition}")
            # trading_memory.py adds these in its own init after the same CREATE;
            # without them the research gate's `l.label_rule` join crashes instead
            # of grading the row, so fresh journals diverge from migrated ones.
            trade_label_columns = {row["name"] for row in con.execute("PRAGMA table_info(trade_labels)").fetchall()}
            for name, definition in {
                "label_rule": "TEXT", "review_status": "TEXT NOT NULL DEFAULT 'AUTO'",
            }.items():
                if name not in trade_label_columns:
                    con.execute(f"ALTER TABLE trade_labels ADD COLUMN {name} {definition}")
            con.execute("CREATE INDEX IF NOT EXISTS idx_trades_setup ON trades(setup_id, closed_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_position_observations_quality ON position_observations(ticket, mark_quality, observed_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_market_ticks_quality ON market_ticks(symbol, quality, observed_at)")

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._connect() as con:
            row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("INSERT INTO state(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, json.dumps(value), now))

    def update_state(self, key: str, mutator: Any, default: Any = None) -> Any:
        """Atomically read-modify-write durable state under BEGIN IMMEDIATE.

        Prevents lost updates when the trading loop and TUI concurrent threads
        both mutate the same key.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
                current = json.loads(row["value"]) if row else default
                updated = mutator(current)
                con.execute(
                    "INSERT INTO state(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    (key, json.dumps(updated, default=str), now),
                )
                con.commit()
                return updated
            except Exception:
                con.rollback()
                raise

    def kill_switch_active(self) -> bool:
        return bool(self.get_state("kill_switch", False))

    def set_kill_switch(self, active: bool, reason: str = "") -> None:
        self.set_state("kill_switch", {"active": active, "reason": reason, "at": datetime.now(timezone.utc).isoformat()})

    def is_kill_switch_active(self) -> bool:
        value = self.get_state("kill_switch", {"active": False})
        return bool(value.get("active", False)) if isinstance(value, dict) else bool(value)

    def create_operator_control(
        self,
        action: str,
        actor: str = "operator",
        source: str = "command-deck",
        idempotency_key: Optional[str] = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key:
                    existing = con.execute(
                        "SELECT id FROM operator_controls WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing:
                        con.commit()
                        return int(existing["id"])
                cur = con.execute(
                    """INSERT INTO operator_controls
                       (action, actor, source, issued_at, status, idempotency_key)
                       VALUES (?, ?, ?, ?, 'REQUESTED', ?)""",
                    (action, actor, source, now, idempotency_key),
                )
                cid = int(cur.lastrowid)
                con.commit()
                return cid
            except Exception:
                con.rollback()
                raise

    #: A control request older than this with a stale/missing engine
    #: heartbeat means the engine is down: surface EXPIRED (H4).
    CONTROL_EXPIRY_S = 60.0
    #: Engine heartbeat older than this counts as offline (H4).
    ENGINE_HEARTBEAT_STALE_S = 90.0

    def engine_heartbeat_age_s(self) -> Optional[float]:
        """Age of the freshest agent_runs heartbeat, or None when unknown."""
        try:
            with self._connect() as con:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "agent_runs" not in tables:
                    return None
                row = con.execute("SELECT MAX(heartbeat_at) AS hb FROM agent_runs").fetchone()
                if not row or not row["hb"]:
                    return None
                try:
                    hb_dt = datetime.fromisoformat(str(row["hb"]))
                except (TypeError, ValueError):
                    return None
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                return max(0.0, (datetime.now(timezone.utc) - hb_dt).total_seconds())
        except Exception:
            return None

    def _engine_offline_for_control(self, issued_at: Optional[str]) -> bool:
        """H4: expiry keys off engine liveness, never request age alone.

        A control expires only when it is older than CONTROL_EXPIRY_S *and*
        the engine heartbeat is stale or absent (engine down). While the
        engine lives, even an old request is still claimed — dropping an
        operator flatten is worse than running it late.
        """
        try:
            if not issued_at:
                return True
            born = datetime.fromisoformat(str(issued_at))
            if born.tzinfo is None:
                born = born.replace(tzinfo=timezone.utc)
            age_s = (datetime.now(timezone.utc) - born).total_seconds()
            if age_s < self.CONTROL_EXPIRY_S:
                return False
            hb_age = self.engine_heartbeat_age_s()
            return hb_age is None or hb_age > self.ENGINE_HEARTBEAT_STALE_S
        except Exception:
            return True

    def recover_stale_controls(self) -> int:
        """H4: at engine boot, fail rows a dead engine left ACKNOWLEDGED.

        An ACKNOWLEDGED row predating this boot means the previous engine
        claimed the control and died before applying it. Re-running it
        blindly could double-apply; mark FAILED so the operator re-issues
        deliberately. Returns the recovered count.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                try:
                    cur = con.execute(
                        """UPDATE operator_controls
                           SET status='FAILED', applied_at=?,
                               result_json='{"error": "engine restarted before applying (stale ACKNOWLEDGED recovered)"}'
                           WHERE status='ACKNOWLEDGED'""",
                        (now_iso,),
                    )
                    con.commit()
                    return int(cur.rowcount or 0)
                except Exception:
                    con.rollback()
                    raise
        except Exception:
            return 0

    def get_operator_control(self, control_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM operator_controls WHERE id = ?",
                (int(control_id),),
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("result_json"):
                try:
                    res["result"] = json.loads(res["result_json"])
                except Exception:
                    res["result"] = res["result_json"]
            else:
                res["result"] = {}
            if res.get("action") == "killswitch" and res.get("status") == "REQUESTED":
                if self.is_kill_switch_active():
                    res["status"] = "ACKNOWLEDGED"
            # H4 read-side expiry: with the engine down nobody runs the
            # claim sweep, so surface EXPIRED here — otherwise the deck polls
            # a REQUESTED row forever and never sees a terminal state.
            if res.get("status") == "REQUESTED" and self._engine_offline_for_control(res.get("issued_at")):
                try:
                    con.execute("BEGIN IMMEDIATE")
                    try:
                        cur = con.execute(
                            """UPDATE operator_controls
                               SET status='EXPIRED',
                                   result_json='{"error": "engine offline: not acknowledged within expiry window"}'
                               WHERE id=? AND status='REQUESTED'""",
                            (int(control_id),),
                        )
                        con.commit()
                        if cur.rowcount == 1:
                            res["status"] = "EXPIRED"
                            res["result"] = {"error": "engine offline: not acknowledged within expiry window"}
                    except Exception:
                        con.rollback()
                except Exception:
                    pass
            return res

    def get_open_operator_controls(self) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM operator_controls WHERE status IN ('REQUESTED', 'CLAIMED') ORDER BY id DESC"
            ).fetchall()
            controls = []
            for r in rows:
                c = dict(r)
                if c.get("result_json"):
                    try:
                        c["result"] = json.loads(c["result_json"])
                    except Exception:
                        c["result"] = c["result_json"]
                else:
                    c["result"] = {}
                controls.append(c)
            return controls

    def claim_pending_operator_controls(
        self,
        expiry_seconds: float = 60.0,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        claimed: List[Dict[str, Any]] = []
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                # 1. Expire stale requested controls — but only when the
                # engine is actually offline (H4). This sweep runs inside
                # the live engine, whose heartbeat is normally fresh, so in
                # practice nothing expires here: even an old request is
                # still claimed below. Dropping an operator flatten because
                # a cycle ran long is worse than running it late.
                hb_age = self.engine_heartbeat_age_s()
                if hb_age is None or hb_age > self.ENGINE_HEARTBEAT_STALE_S:
                    cutoff = (now_dt - timedelta(seconds=expiry_seconds)).isoformat()
                    con.execute(
                        """UPDATE operator_controls
                           SET status = 'EXPIRED',
                               result_json = '{"error": "engine offline: not acknowledged within expiry window"}'
                           WHERE status = 'REQUESTED' AND issued_at < ?""",
                        (cutoff,),
                    )
                # 2. Select pending requested controls
                query = """SELECT id, action, actor, source, issued_at, idempotency_key
                           FROM operator_controls
                           WHERE status = 'REQUESTED'
                           ORDER BY id ASC"""
                params: tuple = ()
                if limit is not None:
                    query += " LIMIT ?"
                    params = (int(limit),)
                rows = con.execute(query, params).fetchall()
                # 3. Compare-and-set claim each
                for row in rows:
                    cid = int(row["id"])
                    cur = con.execute(
                        """UPDATE operator_controls
                           SET status = 'ACKNOWLEDGED', acked_at = ?
                           WHERE id = ? AND status = 'REQUESTED'""",
                        (now_iso, cid),
                    )
                    if cur.rowcount == 1:
                        ctrl = dict(row)
                        ctrl["status"] = "ACKNOWLEDGED"
                        ctrl["acked_at"] = now_iso
                        claimed.append(ctrl)
                con.commit()
                return claimed
            except Exception:
                con.rollback()
                raise

    def complete_operator_control(
        self,
        control_id: int,
        status: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a control terminal (APPLIED/FAILED/EXPIRED), exactly once (M5).

        Compare-and-set against non-terminal states: a late APPLIED must not
        overwrite an EXPIRED or FAILED row, and a second drainer cannot
        re-complete a finished control. Terminal rows are immutable.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        res_json = json.dumps(result or {})
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute(
                    """UPDATE operator_controls
                       SET status = ?, applied_at = ?, result_json = ?
                       WHERE id = ? AND status IN ('REQUESTED', 'ACKNOWLEDGED')""",
                    (status, now_iso, res_json, int(control_id)),
                )
                con.commit()
            except Exception:
                con.rollback()
                raise

    def set_autonomy_paused(self, paused: bool, actor: str = "operator", reason: str = "") -> None:
        self.update_state(
            "autonomy_state",
            lambda curr: {
                **(curr or {"level": "L1_PAPER", "halted": False, "halt_reason": "", "history": []}),
                "paused": bool(paused),
                "pause_reason": reason or (f"operator:{actor}" if paused else ""),
            },
            default={"level": "L1_PAPER", "halted": False, "halt_reason": "", "paused": bool(paused), "pause_reason": reason or (f"operator:{actor}" if paused else ""), "history": []},
        )

    def is_autonomy_paused(self) -> bool:
        st = self.get_state("autonomy_state", {})
        return bool(st.get("paused", False)) if isinstance(st, dict) else False

    def log_proposal(self, proposal: TradeProposal, result: ValidationResult) -> None:
        # Preserve the model hypothesis before recording the deterministic
        # decision.  This makes rejected and accepted proposals equally
        # measurable in research rather than letting validation erase evidence.
        self.log_lifecycle_event(None, proposal.setup_key(), "PROPOSED", "market_model", {
            "symbol": proposal.symbol, "side": proposal.side, "strategy": proposal.strategy,
            "entry": proposal.entry, "stop_loss": proposal.stop_loss,
            "take_profit": proposal.take_profit, "model": proposal.model,
        })
        proposal_payload = asdict(proposal)
        proposal_payload["validation_trace"] = result.rule_trace
        with self._connect() as con:
            con.execute("INSERT INTO proposals(created_at,setup_key,strategy,symbol,side,payload,approved,reasons) VALUES(?,?,?,?,?,?,?,?)", (
                datetime.now(timezone.utc).isoformat(), proposal.setup_key(), proposal.strategy, proposal.symbol,
                proposal.side, json.dumps(proposal_payload, default=str), int(result.approved), json.dumps(result.reasons)))
        instance_id = self.record_setup_instance(proposal, payload={
            "validation_reasons": result.reasons,
            "validation_trace": result.rule_trace,
        })
        self.transition_setup_instance(instance_id, "VALIDATED" if result.approved else "REJECTED",
                                       reason="deterministic validation approved" if result.approved else "; ".join(result.reasons))
        self.log_lifecycle_event(None, proposal.setup_key(), "VALIDATED" if result.approved else "REJECTED", "validator", {
            "symbol": proposal.symbol, "side": proposal.side, "strategy": proposal.strategy,
            "confluence_score": result.confluence_score, "reasons": result.reasons,
            "rule_trace": result.rule_trace,
        })

    def record_setup_instance(self, proposal: TradeProposal, *, payload: Optional[Dict[str, Any]] = None) -> int:
        """Create a candidate setup instance before its permission decision."""
        now = datetime.now(timezone.utc).isoformat()
        setup_id = str(proposal.features.get("setup_id", "unclassified_v1"))
        with self._connect() as con:
            cursor = con.execute("""INSERT INTO setup_instances(
                setup_key,setup_id,strategy,symbol,side,state,created_at,updated_at,reason,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                proposal.setup_key(), setup_id, proposal.strategy, proposal.symbol,
                proposal.side, "CANDIDATE", now, now, "candidate detected", json.dumps(payload or {}, default=str),
            ))
            return int(cursor.lastrowid)

    def transition_setup_instance(self, instance_id: int, state: str, *, ticket: Optional[int] = None,
                                  reason: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
        """Apply a constrained, auditable setup state transition."""
        terminal = {"REJECTED", "MISSED", "INVALIDATED", "UNRESOLVED", "CLOSED"}
        allowed = {
            "CANDIDATE": {"VALIDATED", "REJECTED", "INVALIDATED", "UNRESOLVED"},
            "VALIDATED": {"ACCEPTED", "MISSED", "INVALIDATED", "REJECTED", "UNRESOLVED"},
            "ACCEPTED": {"FILLED", "MISSED", "INVALIDATED", "UNRESOLVED"},
            "FILLED": {"PARTIAL", "MANAGED", "CLOSED", "INVALIDATED", "UNRESOLVED"},
            "PARTIAL": {"PARTIAL", "MANAGED", "CLOSED", "INVALIDATED", "UNRESOLVED"},
            "MANAGED": {"PARTIAL", "MANAGED", "CLOSED", "INVALIDATED", "UNRESOLVED"},
            "REJECTED": set(), "MISSED": set(), "INVALIDATED": set(), "UNRESOLVED": set(), "CLOSED": set(),
        }
        state = str(state).upper()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT state,payload FROM setup_instances WHERE id=?", (int(instance_id),)).fetchone()
                if not row:
                    raise KeyError(f"unknown setup instance {instance_id}")
                current = str(row["state"])
                if state not in allowed.get(current, set()) and state != current:
                    raise ValueError(f"invalid setup transition {current}->{state}")
                merged = json.loads(row["payload"] or "{}")
                if payload:
                    merged.update(payload)
                con.execute("UPDATE setup_instances SET state=?,updated_at=?,ticket=COALESCE(?,ticket),reason=?,payload=? WHERE id=?", (
                    state, datetime.now(timezone.utc).isoformat(), ticket, reason[:2000], json.dumps(merged, default=str), int(instance_id)))
                if state in {"REJECTED", "MISSED", "INVALIDATED", "UNRESOLVED"}:
                    persist_setup_label(con, "setup_instance", f"setup_instance:{int(instance_id)}", state, reason)
                con.commit()
            except Exception:
                con.rollback()
                raise

    def latest_setup_instance(self, setup_key: str) -> Optional[sqlite3.Row]:
        with self._connect() as con:
            return con.execute("SELECT * FROM setup_instances WHERE setup_key=? ORDER BY id DESC LIMIT 1", (setup_key,)).fetchone()

    def transition_latest_setup(self, setup_key: str, state: str, *, ticket: Optional[int] = None,
                                reason: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
        row = self.latest_setup_instance(setup_key)
        if row:
            self.transition_setup_instance(int(row["id"]), state, ticket=ticket, reason=reason, payload=payload)

    def transition_setup_for_ticket(self, ticket: int, state: str, *, reason: str = "",
                                    payload: Optional[Dict[str, Any]] = None) -> bool:
        """Transition the latest setup linked to a broker position ticket."""
        with self._connect() as con:
            row = con.execute(
                "SELECT id FROM setup_instances WHERE ticket=? ORDER BY id DESC LIMIT 1",
                (int(ticket),),
            ).fetchone()
        if not row:
            return False
        self.transition_setup_instance(int(row["id"]), state, ticket=int(ticket), reason=reason, payload=payload)
        return True

    def record_manual_activity(self, action: str, *, ticket: Optional[int] = None,
                               symbol: str = "", side: str = "", magic: Optional[int] = None,
                               payload: Optional[Dict[str, Any]] = None) -> None:
        """Record unowned/manual broker activity without granting management rights."""
        with self._connect() as con:
            con.execute("""INSERT INTO manual_activity(observed_at,ticket,symbol,side,magic,action,payload)
                VALUES(?,?,?,?,?,?,?)""", (datetime.now(timezone.utc).isoformat(), ticket, symbol, side,
                                            magic, action, json.dumps(payload or {}, default=str)))

    def log_lifecycle_event(self, ticket: Optional[int], setup_key: str, event_type: str, actor: str, payload: Dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO trade_lifecycle_events(created_at,ticket,setup_key,event_type,actor,payload) VALUES(?,?,?,?,?,?)", (
                datetime.now(timezone.utc).isoformat(), ticket, setup_key, event_type, actor, json.dumps(payload, default=str)))

    def record_execution_report(self, *, event_type: str, intent_key: str = "",
                                order_ticket: Optional[int] = None, deal_ticket: Optional[int] = None,
                                position_ticket: Optional[int] = None, retcode: Optional[int] = None,
                                requested_price: Optional[float] = None, accepted_price: Optional[float] = None,
                                filled_price: Optional[float] = None, requested_volume: Optional[float] = None,
                                accepted_volume: Optional[float] = None, filled_volume: Optional[float] = None,
                                commission: Optional[float] = None, swap: Optional[float] = None,
                                execution_latency_ms: Optional[float] = None,
                                spread_at_submit: Optional[float] = None,
                                spread_at_fill: Optional[float] = None,
                                implementation_shortfall: Optional[float] = None,
                                requote_count: Optional[int] = None,
                                payload: Optional[Dict[str, Any]] = None) -> None:
        """Append a normalized broker/execution evidence record.

        Order, deal and position identifiers are intentionally separate: MT5
        netting and hedging accounts do not share a universal one-to-one model.
        Missing broker fields remain ``NULL`` rather than being fabricated.
        """
        with self._connect() as con:
            con.execute("""INSERT INTO broker_execution_reports(
                reported_at,intent_key,order_ticket,deal_ticket,position_ticket,event_type,retcode,
                requested_price,accepted_price,filled_price,requested_volume,accepted_volume,filled_volume,
                commission,swap,execution_latency_ms,spread_at_submit,spread_at_fill,
                implementation_shortfall,requote_count,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    datetime.now(timezone.utc).isoformat(), intent_key or None, order_ticket, deal_ticket,
                    position_ticket, event_type, retcode, requested_price, accepted_price, filled_price,
                    requested_volume, accepted_volume, filled_volume, commission, swap,
                    execution_latency_ms, spread_at_submit, spread_at_fill,
                    implementation_shortfall, requote_count,
                    json.dumps(payload or {}, default=str),
                ))

    def record_execution_review(self, intent_key: str, ticket: Optional[int] = None, severity: str = "WARNING",
                                reason: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("""INSERT INTO execution_reviews(
                intent_key, first_detected_at, last_detected_at, status, severity, detections, reason
            ) VALUES(?,?,?,'OPEN',?,1,?)
            ON CONFLICT(intent_key) DO UPDATE SET
                last_detected_at=excluded.last_detected_at,
                severity=excluded.severity,
                detections=execution_reviews.detections+1,
                reason=excluded.reason""", (
                intent_key, now, now, severity, reason
            ))

    def get_unresolved_reviews(self) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM execution_reviews WHERE status IN ('OPEN','ACKNOWLEDGED','UNRESOLVED') ORDER BY last_detected_at DESC").fetchall()
        return [dict(row) for row in rows]

    def update_review_status(self, intent_key: str, status: str, resolved_by: str = "operator", note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("""UPDATE execution_reviews SET
                status=?, operator=?, resolution_note=?, resolved_at=?
                WHERE intent_key=?""", (status, resolved_by, note, now, intent_key))

    def latest_proposal(self, setup_key: str) -> Optional[Dict[str, Any]]:
        """Return the immutable hypothesis that created a planned order."""
        with self._connect() as con:
            row = con.execute("SELECT payload FROM proposals WHERE setup_key=? ORDER BY id DESC LIMIT 1", (setup_key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def setup_seen_recently(self, setup_key: str, seconds: int) -> bool:
        with self._connect() as con:
            row = con.execute("SELECT created_at FROM proposals WHERE setup_key=? ORDER BY id DESC LIMIT 1", (setup_key,)).fetchone()
        if not row:
            return False
        then = datetime.fromisoformat(row["created_at"])
        return (datetime.now(timezone.utc) - then).total_seconds() < seconds

    def record_open_trade(self, ticket: int, proposal: TradeProposal, volume: float, risk_amount: float, payload: Dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,entry,stop_loss,take_profit,risk_amount,opened_at,payload,setup_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticket) DO UPDATE SET payload=excluded.payload,setup_id=excluded.setup_id", (
                ticket, proposal.magic, proposal.strategy, proposal.symbol, proposal.side, volume, proposal.entry,
                proposal.stop_loss, proposal.take_profit, risk_amount, datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=str), proposal.features.get("setup_id", "unclassified_v1")))
        self.transition_latest_setup(proposal.setup_key(), "FILLED", ticket=int(ticket), reason="broker position recorded")
        self.log_lifecycle_event(ticket, proposal.setup_key(), "FILLED", "execution", {
            "symbol": proposal.symbol, "side": proposal.side, "volume": volume,
            "entry": proposal.entry, "stop_loss": proposal.stop_loss,
            "take_profit": proposal.take_profit, "risk_amount": risk_amount,
        })

    def record_close(self, ticket: int, exit_price: float, pnl: float, reason: str, *,
                     gross_pnl: Optional[float] = None, commission: float = 0.0,
                     swap: float = 0.0, financing: float = 0.0,
                     closed_at: Optional[Any] = None) -> None:
        # The broker-execution close is a reliable terminal mark.  Writing it
        # before calculating excursions prevents a missing final observation
        # from silently understating MFE/MAE.
        close_stamp = datetime.now(timezone.utc)
        if closed_at is not None:
            try:
                if isinstance(closed_at, datetime):
                    close_stamp = closed_at
                else:
                    close_stamp = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
                close_stamp = (close_stamp.replace(tzinfo=timezone.utc)
                               if close_stamp.tzinfo is None else close_stamp.astimezone(timezone.utc))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("closed_at must be an ISO timestamp or datetime") from None
        self.record_position_observation(
            ticket, exit_price, pnl, source="mt5_close_execution", mark_quality="BROKER_EXECUTION",
            observed_at=close_stamp,
        )
        with self._connect() as con:
            con.execute("UPDATE trades SET closed_at=?,exit_price=?,pnl=?,exit_reason=?,gross_pnl=?,commission=?,swap=?,financing=? WHERE ticket=?", (
                close_stamp.isoformat(), exit_price, pnl, reason,
                pnl if gross_pnl is None else gross_pnl, commission, swap, financing, ticket))
        self.transition_setup_for_ticket(ticket, "CLOSED", reason=reason, payload={"exit_price": exit_price, "pnl": pnl})
        self.log_lifecycle_event(ticket, "", "CLOSED", "reconciler", {
            "exit_price": exit_price, "pnl": pnl, "gross_pnl": pnl if gross_pnl is None else gross_pnl,
            "commission": commission, "swap": swap, "financing": financing, "reason": reason,
            **self.excursions(ticket),
        })

    def record_position_observation(self, ticket: int, price: float, unrealized_pnl: float = 0.0, *,
                                    source: str = "unknown", quote_time_msc: Optional[int] = None,
                                    tick_age_ms: Optional[float] = None, spread: Optional[float] = None,
                                    mark_quality: str = "UNKNOWN", observed_at: Optional[datetime] = None) -> None:
        """Append a mark with enough provenance to qualify excursion metrics.

        A stale observation is retained as an operations fact but excluded from
        MFE/MAE.  This separates "no evidence" from an apparently benign
        price path that simply was not observed frequently enough.
        """
        with self._connect() as con:
            row = con.execute("SELECT side,entry,stop_loss,risk_amount FROM trades WHERE ticket=?", (ticket,)).fetchone()
            if not row or not price:
                return
            initial_distance = abs(float(row["entry"]) - float(row["stop_loss"] or row["entry"]))
            favorable = (float(price) - float(row["entry"])) if row["side"] == "BUY" else (float(row["entry"]) - float(price))
            r_multiple = favorable / initial_distance if initial_distance > 0 else None
            stamp = observed_at or datetime.now(timezone.utc)
            con.execute("""INSERT INTO position_observations(
                observed_at,ticket,price,unrealized_pnl,r_multiple,source,quote_time_msc,tick_age_ms,spread,mark_quality
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                stamp.isoformat(), ticket, price, unrealized_pnl, r_multiple, source,
                quote_time_msc, tick_age_ms, spread, mark_quality))

    def record_market_tick(self, symbol: str, *, bid: Optional[float] = None,
                           ask: Optional[float] = None, quote_time_msc: Optional[int] = None,
                           tick_age_ms: Optional[float] = None, source: str = "unknown",
                           quality: str = "UNKNOWN", payload: Optional[Dict[str, Any]] = None,
                           observed_at: Optional[datetime] = None) -> str:
        """Persist every quote observation, including missing or stale data.

        This ledger deliberately records *bad* observations as facts instead of
        silently dropping them.  Execution and analytics may only consume
        ``FRESH`` rows; ``STALE`` and ``MISSING_OR_INVALID`` are operational
        evidence and must never be used as a tradable quote.
        """
        def finite(value: Optional[float]) -> Optional[float]:
            try:
                number = float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
            return number if number is not None and math.isfinite(number) and number > 0 else None

        bid_value = finite(bid)
        ask_value = finite(ask)
        mid = spread = None
        if bid_value is not None and ask_value is not None and ask_value >= bid_value:
            mid = (bid_value + ask_value) / 2.0
            spread = ask_value - bid_value
        quality = str(quality or "UNKNOWN").upper()
        stamp = observed_at or datetime.now(timezone.utc)
        with self._connect() as con:
            cursor = con.execute("""INSERT INTO market_ticks(
                observed_at,symbol,source,quote_time_msc,bid,ask,mid,spread,
                tick_age_ms,quality,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                stamp.isoformat(), str(symbol), str(source), quote_time_msc,
                bid_value, ask_value, mid, spread, tick_age_ms, quality,
                json.dumps(payload or {}, default=str),
            ))
            return f"market_tick:{int(cursor.lastrowid)}"

    def excursions(self, ticket: int, max_mark_gap_seconds: int = 900) -> Dict[str, Any]:
        """Return MFE/MAE only with explicit observation-quality metadata."""
        with self._connect() as con:
            rows = con.execute("""SELECT observed_at,r_multiple,mark_quality FROM position_observations
                WHERE ticket=? ORDER BY observed_at""", (ticket,)).fetchall()
        valid = [row for row in rows if row["mark_quality"] in {"BROKER_FRESH", "BROKER_EXECUTION"} and row["r_multiple"] is not None]
        gaps: List[float] = []
        for earlier, later in zip(valid, valid[1:]):
            try:
                gaps.append((datetime.fromisoformat(later["observed_at"]) - datetime.fromisoformat(earlier["observed_at"])).total_seconds())
            except (TypeError, ValueError):
                continue
        max_gap = max(gaps) if gaps else None
        complete = bool(valid) and (max_gap is None or max_gap <= max_mark_gap_seconds)
        return {
            "mfe_r": max((float(row["r_multiple"]) for row in valid), default=None),
            "mae_r": min((float(row["r_multiple"]) for row in valid), default=None),
            "mark_count": len(valid), "mark_max_gap_seconds": max_gap,
            "mark_quality": "COMPLETE" if complete else "SPARSE_OR_STALE",
        }

    def trade_risk(self, ticket: int) -> float:
        with self._connect() as con:
            row = con.execute("SELECT risk_amount FROM trades WHERE ticket=?", (ticket,)).fetchone()
        return float(row["risk_amount"]) if row else 0.0

    def trade_payload(self, ticket: int) -> Dict[str, Any]:
        """Return the durable open/closed trade payload for feedback loops."""
        with self._connect() as con:
            row = con.execute("SELECT payload FROM trades WHERE ticket=?", (int(ticket),)).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def open_trade_rows(self) -> List[sqlite3.Row]:
        """Return durable open trades for restart-safe reconciliation."""
        with self._connect() as con:
            return con.execute("SELECT * FROM trades WHERE closed_at IS NULL").fetchall()

    def closed_trade_rows_between(self, start_iso: str, end_iso: str) -> List[sqlite3.Row]:
        """Closed trade rows in a half-open UTC range for broker reconciliation."""
        with self._connect() as con:
            return con.execute("""SELECT * FROM trades
                WHERE closed_at IS NOT NULL AND closed_at >= ? AND closed_at < ?""", (start_iso, end_iso)).fetchall()

    def trade_tickets_for_position(self, position_ticket: int) -> List[int]:
        """Resolve a broker position ID to locally owned trade tickets.

        MT5 netting/hedging execution can expose an order ticket, deal ticket,
        and position ticket as three different identifiers.  Reconciliation
        may use this mapping only when the journal's execution ledger provides
        an unambiguous relationship; an empty or multi-ticket result remains a
        discrepancy instead of being guessed away.
        """
        try:
            position = int(position_ticket)
        except (TypeError, ValueError):
            return []
        if position <= 0:
            return []
        with self._connect() as con:
            rows = con.execute("""
                SELECT DISTINCT t.ticket
                FROM trades AS t
                JOIN broker_execution_reports AS e
                  ON e.position_ticket = ?
                 AND (e.order_ticket = t.ticket OR e.deal_ticket = t.ticket)
                WHERE t.magic IN (?, ?)
                ORDER BY t.ticket
            """, (position, SCALP_MAGIC, SWING_MAGIC)).fetchall()
        return [int(row["ticket"]) for row in rows]

    def record_daily_reconciliation(self, report: Dict[str, Any]) -> None:
        """Persist one immutable-ish current report per reconciliation period."""
        start, end = str(report["period_start"]), str(report["period_end"])
        with self._connect() as con:
            con.execute("""INSERT INTO daily_reconciliations(period_start,period_end,recorded_at,status,report)
                VALUES(?,?,?,?,?) ON CONFLICT(period_start) DO UPDATE SET
                period_end=excluded.period_end, recorded_at=excluded.recorded_at,
                status=excluded.status, report=excluded.report""", (
                    start, end, datetime.now(timezone.utc).isoformat(), str(report.get("status", "ATTENTION")),
                    json.dumps(report, default=str),
                ))
        self.log_lifecycle_event(None, "", "DAILY_RECONCILIATION", "reconciler", {
            "period_start": start, "period_end": end, "status": report.get("status"),
            "discrepancies": report.get("discrepancies", {}),
        })

    def latest_daily_reconciliation(self) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT report FROM daily_reconciliations ORDER BY period_start DESC LIMIT 1").fetchone()
        return json.loads(row["report"]) if row else None

    def perform_maintenance(self, backup_dir: str = "backups") -> Dict[str, Any]:
        """Perform institutional database hygiene: WAL checkpoint truncate, PRAGMA integrity_check, and online backup.

        1. Runs `PRAGMA wal_checkpoint(TRUNCATE)` to flush and truncate write-ahead logs.
        2. Runs `PRAGMA integrity_check` to detect any page corruption or indexing faults.
        3. Creates an atomic online backup snapshot using `sqlite3.Connection.backup` into `backup_dir`.
        4. Computes SHA-256 digest of the backup file for cold-storage auditability.
        Strictly read-only / administrative: execution_authority is False.
        """
        os.makedirs(backup_dir, exist_ok=True)
        now_utc = datetime.now(timezone.utc)
        ts_str = now_utc.strftime("%Y%m%d_%H%M%S")
        base_name = os.path.splitext(os.path.basename(self.path))[0]
        backup_filename = f"{base_name}_{ts_str}.db.bak"
        backup_path = os.path.join(backup_dir, backup_filename)

        with self._connect() as con:
            wal_res = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            wal_checkpoint_busy = int(wal_res[0]) if wal_res else 0
            wal_checkpoint_log = int(wal_res[1]) if wal_res else 0
            wal_checkpoint_checkpointed = int(wal_res[2]) if wal_res else 0

            integrity_rows = con.execute("PRAGMA integrity_check").fetchall()
            integrity_messages = [str(r[0]) for r in integrity_rows]
            integrity_ok = len(integrity_messages) == 1 and integrity_messages[0].lower() == "ok"

            bck_con = sqlite3.connect(backup_path)
            try:
                con.backup(bck_con)
            finally:
                bck_con.close()

        with open(backup_path, "rb") as bf:
            digest = hashlib.sha256(bf.read()).hexdigest()

        backup_size = os.path.getsize(backup_path)

        result = {
            "timestamp": now_utc.isoformat(),
            "database_path": self.path,
            "wal_checkpoint": {
                "status": "SUCCESS" if wal_checkpoint_busy == 0 else "BUSY",
                "busy": wal_checkpoint_busy,
                "log_pages": wal_checkpoint_log,
                "checkpointed_pages": wal_checkpoint_checkpointed,
            },
            "integrity_check": {
                "passed": integrity_ok,
                "messages": integrity_messages,
            },
            "backup": {
                "created": True,
                "path": os.path.abspath(backup_path),
                "filename": backup_filename,
                "size_bytes": backup_size,
                "sha256": digest,
            },
            "execution_authority": False,
        }
        self.log_lifecycle_event(None, "", "DB_MAINTENANCE", "operator", result)
        return result

    def reconcile_period(self, broker_deals: Iterable[Any], start: datetime, end: datetime,
                         require_timestamps: bool = False) -> Dict[str, Any]:
        """Execute a daily reconciliation between provided broker deals and local store trades."""
        from operations.daily_reconciliation import compare_day
        return compare_day(self, broker_deals, start, end, require_timestamps=require_timestamps)

    def open_risk(self, include_pending_intents: bool = True) -> float:
        with self._connect() as con:
            row = con.execute("SELECT COALESCE(SUM(risk_amount),0) AS total FROM trades WHERE closed_at IS NULL").fetchone()
            total = float(row["total"])
            if include_pending_intents:
                total += sum(float(pl.get("risk_amount", 0.0) or 0.0) for pl in reserving_intent_payloads(con))
        return total

    def open_bucket_risk(self, bucket: str, include_pending_intents: bool = True) -> float:
        with self._connect() as con:
            rows = con.execute("SELECT symbol,side,risk_amount FROM trades WHERE closed_at IS NULL").fetchall()
            total = 0.0
            for row in rows:
                if bucket in exposure_buckets(row["symbol"], row["side"]):
                    total += float(row["risk_amount"])
            if include_pending_intents:
                for pl in reserving_intent_payloads(con):
                    sym, side = pl.get("symbol", ""), pl.get("side", "")
                    if sym and side and bucket in exposure_buckets(sym, side):
                        total += float(pl.get("risk_amount", 0.0) or 0.0)
        return total

    def realized_loss(self, since_iso: str) -> float:
        with self._connect() as con:
            row = con.execute("SELECT COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END),0) AS total FROM trades WHERE closed_at >= ?", (since_iso,)).fetchone()
        return float(row["total"])

    def open_positions_for_netting(self) -> List[Dict[str, Any]]:
        """Return all open positions with symbol, side, and risk_amount for FX netting."""
        with self._connect() as con:
            rows = con.execute("SELECT symbol, side, risk_amount FROM trades WHERE closed_at IS NULL").fetchall()
        return [
            {
                "symbol": str(r["symbol"]),
                "side": str(r["side"]),
                "risk_amount": float(r["risk_amount"] or 0.0),
            }
            for r in rows
        ]


def check_selective_activation(
    strategy: str,
    regime: str = "",
    adx: float = 0.0,
    choppiness: float = 0.0,
    side: str = "BUY",
) -> Tuple[bool, str]:
    """Complex Adaptive Systems Selective Activation Gate (P22 Han 2026).

    Validates whether the strategy is permitted to trade in the current market regime.
    Findings from Han (2026):
    1. Trend/breakout algorithms fail in ranging/choppy markets (ADX < 20 or Choppiness > 61.8).
    2. Counter-trend/mean-reversion algorithms fail in strong trending breakouts (ADX > 32).
    3. Bear-insurance/short strategies are activated specifically during downtrends (BEAR regime).
    4. Switching off incompatible algorithms preserves capital (p = 0.036 vs always-on).
    """
    strat = str(strategy or "").lower()
    reg = str(regime or "").upper()
    adx_val = float(adx or 0.0)
    chop_val = float(choppiness or 0.0)

    # 1. Trend / Breakout / Momentum strategies
    if any(k in strat for k in ["trend", "breakout", "momentum", "swing"]):
        if "CHOPPY" in reg or "RANGE" in reg or "RANGING" in reg:
            return False, "trend_disabled_in_choppy_regime"
        if 0.0 < adx_val < 20.0:
            return False, f"trend_disabled_in_choppy_regime: adx_{adx_val:.1f}_below_20"
        if chop_val > 61.8:
            return False, f"trend_disabled_in_choppy_regime: choppiness_{chop_val:.1f}_above_61.8"

    # 2. Mean-Reversion / Range / Counter-Trend strategies
    elif any(k in strat for k in ["mean_reversion", "range", "reversion", "counter_trend", "scalp"]):
        if "STRONG_TREND" in reg or "RUNAWAY" in reg:
            return False, "mean_reversion_disabled_in_strong_trend"
        if adx_val > 32.0:
            return False, f"mean_reversion_disabled_in_strong_trend: adx_{adx_val:.1f}_above_32"

    # 3. Bear Insurance / Dedicated Hedging strategies
    elif "bear" in strat or "hedge" in strat:
        if "BULL" in reg or side.upper() == "BUY":
            return False, "bear_insurance_only_in_downtrend"

    return True, "admissible"


class DeterministicValidator:
    """Hard rule gate. All unavailable critical data is rejected, never assumed safe."""
    def __init__(self, store: TradingStore, min_rr: float = 2.0, min_confluence: int = 3,
                 lifecycle_gate=None,
                 min_net_ev_r: float = 0.0, min_ev_samples: int = 30,
                 default_friction_r: float = 0.05,
                 max_changepoint_probability: float = 0.45,
                 min_walk_forward_efficiency: float = 0.0,
                 block_on_data_drift: bool = False):
        self.store, self.min_rr, self.min_confluence = store, min_rr, min_confluence
        # Optional callable(setup_id) -> blocking dict|None. A FROZEN/RETIRED
        # lifecycle row refuses new proposals for that setup; unknown setups
        # and absent gates leave behavior unchanged (backwards compatible).
        self.lifecycle_gate = lifecycle_gate
        # Net-of-friction expectancy floor over the rolling labelled journal.
        # Cold starts (< min_ev_samples reconciled trades) pass so evidence can
        # seed; afterwards a negative net edge refuses new risk. Override per
        # proposal via market["friction_r"] / market["min_net_ev_r"].
        self.min_net_ev_r = float(min_net_ev_r)
        self.min_ev_samples = int(min_ev_samples)
        self.default_friction_r = float(default_friction_r)
        # Regime-break veto: callers may supply a BOCPD changepoint probability
        # per cycle; absent key leaves behavior unchanged (backwards compatible).
        self.max_changepoint_probability = float(max_changepoint_probability)
        # Walk-Forward Efficiency floor: WFE = mean(OOS Sharpe)/mean(IS Sharpe).
        # Default 0.0 = disabled (backwards compatible); set e.g. 0.5 to refuse
        # entries when the strategy loses more than half its in-sample edge OOS.
        # Absent/stale market["walk_forward_wfe"] leaves behavior unchanged.
        self.min_walk_forward_efficiency = float(min_walk_forward_efficiency)
        # PSI/KS data-drift veto. Default False = advisory only (backwards
        # compatible); True refuses entries while live feature distributions
        # are in major shift (market["data_drift_status"] == "DRIFT").
        self.block_on_data_drift = bool(block_on_data_drift)

    @staticmethod
    def confluence(features: Dict[str, Any], side: str) -> int:
        direction = 1 if side.upper() == "BUY" else -1
        score = 0
        if features.get("ema_alignment") == direction: score += 1
        if features.get("mtf_alignment") == direction: score += 1
        raw_adx = features.get("adx") if features.get("adx") is not None else features.get("adx_14", 0)
        try:
            if float(raw_adx or 0) >= 20: score += 1
        except (TypeError, ValueError):
            pass
        if features.get("near_key_level", False): score += 1
        confirmed = bool(features.get("confirmed_candle") if "confirmed_candle" in features else (features.get("completed_bar_time") is not None or "close" in features))
        if confirmed: score += 1
        volume = bool(features.get("volume_confirmed") if "volume_confirmed" in features else (float(features.get("tick_volume_ratio_20", 0) or 0) >= 1.1))
        if volume: score += 1
        if bool(features.get("hawkes_cascade_active", False)): score += 1
        return score

    def _rolling_net_ev(self, limit: int = 40) -> Dict[str, Any]:
        """Rolling labelled-trade expectancy from the durable journal.

        Uses only broker-reconciled closed trades carrying R-multiples. The
        trade_labels row is written by the same journal that opened the trade,
        so it is self-declared; independent order/execution evidence is
        required or a synthetic fixture grades itself as expectancy. Raises on
        a missing/corrupt journal; validate() degrades to cold-start behavior.
        """
        try:
            from research.research_validation import broker_reconciled_tickets
        except ImportError:
            from research.research_validation import broker_reconciled_tickets
        tickets = broker_reconciled_tickets(self.store.path)
        if not tickets:
            return {"sample_count": 0, "win_rate": 0.0, "avg_win_r": 0.0,
                    "avg_loss_r": 0.0, "ev_gross": 0.0}
        marks = ",".join("?" for _ in tickets)
        with self.store._connect() as con:
            rows = con.execute(
                f"""SELECT l.label AS label, l.r_multiple AS r_multiple
                   FROM trade_labels l JOIN trades t ON t.ticket = l.ticket
                   WHERE t.closed_at IS NOT NULL AND l.r_multiple IS NOT NULL
                     AND t.ticket IN ({marks})
                   ORDER BY t.closed_at DESC LIMIT ?""",
                (*tickets, int(limit)),
            ).fetchall()
        rs = [float(r["r_multiple"]) for r in rows if r["r_multiple"] is not None]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r < 0]
        n = len(rs)
        win_rate = (len(wins) / n) if n else 0.0
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (abs(sum(losses) / len(losses))) if losses else 0.0
        ev_gross = (win_rate * avg_win - (1.0 - win_rate) * avg_loss) if n else 0.0
        return {"sample_count": n, "win_rate": win_rate, "avg_win_r": avg_win,
                "avg_loss_r": avg_loss, "ev_gross": ev_gross}

    def validate(self, proposal: TradeProposal, market: Dict[str, Any]) -> ValidationResult:
        reasons: List[str] = []
        evidence, problems = MarketEvidence.parse(market)
        if problems:
            reasons.extend(problems)

        from core.kill_switch import entries_blocked
        blocked, block_reason = entries_blocked(self.store)
        kill_switch_active = blocked
        if blocked:
            reasons.append(block_reason)
        try:
            tick_age = float(market.get("tick_age_ms", 10**9))
            max_tick_age = float(market.get("max_tick_age_ms", 5_000))
            tick_fresh = math.isfinite(tick_age) and math.isfinite(max_tick_age) and 0 <= tick_age <= max_tick_age
        except (TypeError, ValueError):
            tick_age, max_tick_age, tick_fresh = None, None, False
        try:
            spread = float(market.get("spread", float("inf")))
            max_spread = float(market.get("max_spread", 0))
            spread_ok = math.isfinite(spread) and math.isfinite(max_spread) and 0 <= spread <= max_spread
        except (TypeError, ValueError):
            spread, max_spread, spread_ok = None, None, False
        if not market.get("market_open"): reasons.append("market not open")
        if not tick_fresh: reasons.append("stale tick")
        if not market.get("clock_ok"): reasons.append("broker/local clock health rejected tick")
        if not market.get("symbol_tradable"): reasons.append("symbol not tradable")
        if not spread_ok: reasons.append("spread too wide")
        if not market.get("session_allowed"): reasons.append("session/rollover no-trade filter")
        if not market.get("liquidity_ok"): reasons.append("liquidity filter rejected entry")
        if not market.get("microstructure_ok"): reasons.append("microstructure filter rejected entry")
        if not market.get("required_data_ok"):
            req_reasons = [str(r) for r in market.get("required_data_reasons", []) if r]
            if req_reasons:
                reasons.extend(req_reasons)
            else:
                reasons.append("required data incomplete")
        reasons.extend(str(reason) for reason in market.get("market_condition_reasons", []) if reason)
        reasons.extend(str(reason) for reason in market.get("microstructure_reasons", []) if reason)
        try:
            raw_exec = market.get("execution_price", proposal.entry)
            raw_tol = market.get("entry_tolerance", 0)
            exec_p = float(raw_exec) if raw_exec is not None else proposal.entry
            tol = float(raw_tol) if raw_tol is not None else 0.0
            if not (math.isfinite(exec_p) and math.isfinite(tol)):
                planned_entry_ok = False
            else:
                planned_entry_ok = bool(market.get("breakout_event")) or abs(exec_p - proposal.entry) <= tol
        except (TypeError, ValueError):
            planned_entry_ok = False
        if not planned_entry_ok:
            reasons.append("price moved beyond planned-entry tolerance")
        completed_or_breakout = bool(market.get("completed_candle")) or bool(market.get("breakout_event"))
        if not completed_or_breakout: reasons.append("no completed-candle/breakout trigger")
        try:
            raw_stop = market.get("stop_distance")
            raw_min_stop = market.get("min_stop_distance")
            if raw_stop is None or raw_min_stop is None:
                stop_distance, min_stop_distance, stop_ok = None, None, False
            else:
                stop_distance = float(raw_stop)
                min_stop_distance = float(raw_min_stop)
                stop_ok = math.isfinite(stop_distance) and math.isfinite(min_stop_distance) and stop_distance >= min_stop_distance
        except (TypeError, ValueError):
            stop_distance, min_stop_distance, stop_ok = None, None, False
        if not stop_ok: reasons.append("stop too close")
        required_rr = max(self.min_rr, float(market.get("setup_min_rr", self.min_rr) or self.min_rr))
        reward_risk_ok = proposal.reward_risk >= required_rr
        if not reward_risk_ok: reasons.append(f"reward/risk below setup minimum {required_rr:.2f}")
        hin = market.get("high_impact_news")
        event_window_ok = not (market.get("setup_event_exclusion", False) and (hin is not False))
        if not event_window_ok:
            reasons.append("setup excludes high-impact event window")
        score = self.confluence(proposal.features, proposal.side)
        confluence_ok = score >= self.min_confluence
        if not confluence_ok: reasons.append("insufficient deterministic confluence")
        news_safe = (hin is False)
        if not news_safe: reasons.append("news data missing or high-impact event")
        setup_recent = self.store.setup_seen_recently(proposal.setup_key(), market.get("setup_cooldown_seconds", 900))
        setup_cooldown_ok = not setup_recent
        if setup_recent: reasons.append("setup recently considered")
        # Strategy lifecycle gate: a FROZEN/RETIRED setup refuses new proposals.
        lifecycle_block = None
        lifecycle_ok = True
        if self.lifecycle_gate is not None:
            try:
                lifecycle_block = self.lifecycle_gate(proposal.features.get("setup_id"))
            except Exception:
                # Fail closed: an unreadable lifecycle store blocks the setup
                # rather than silently trading a possibly-frozen strategy.
                lifecycle_block = {"state": "UNKNOWN", "reason": "lifecycle gate error"}
            if lifecycle_block is not None:
                lifecycle_ok = False
                reasons.append(
                    f"setup lifecycle {lifecycle_block.get('state', 'BLOCKED')}: "
                    f"{lifecycle_block.get('reason') or 'operator review required'}")
        # Lag-delay confidence gate (wired from lag_delay_engine envelope).
        lag_confidence = None
        min_lag_confidence = 0.35
        lag_ok = True
        if "lag_confidence" in market:
            try:
                lag_confidence = float(market.get("lag_confidence"))
                min_lag_confidence = float(market.get("min_lag_confidence", 0.35))
                lag_ok = math.isfinite(lag_confidence) and lag_confidence >= min_lag_confidence
            except (TypeError, ValueError, OverflowError):
                lag_confidence, lag_ok = None, False
            if not lag_ok:
                reasons.append("lag confidence decayed below threshold")

        # Net-of-friction expectancy floor from the rolling labelled journal.
        net_ev_stats: Dict[str, Any] = {"sample_count": 0}
        net_ev_ok = True
        net_ev_value = None
        try:
            friction_r = float(market.get("friction_r", self.default_friction_r))
            stats = self._rolling_net_ev()
            net_ev_stats.update(stats)
            net_ev_stats["friction_r"] = friction_r
            if int(stats.get("sample_count", 0)) >= self.min_ev_samples:
                net_ev_value = float(stats["ev_gross"]) - friction_r
                floor = float(market.get("min_net_ev_r", self.min_net_ev_r))
                net_ev_ok = math.isfinite(net_ev_value) and net_ev_value >= floor
                if not net_ev_ok:
                    reasons.append(
                        f"rolling net expectancy {net_ev_value:.3f}R below floor {floor:.2f}R")
        except Exception as _ev_exc:
            # Missing/corrupt journal degrades to cold-start pass, never crashes.
            # Logged rather than swallowed: before trade_labels existed in this
            # DDL, EVERY call landed here, so a permanently disabled gate was
            # indistinguishable from a satisfied one. This is the same
            # module-level `log` that the live-health guard needs.
            log.warning("rolling net-EV gate degraded to cold start (%s: %s); it will not block until min_ev_samples labelled trades exist", type(_ev_exc).__name__, _ev_exc)
            net_ev_ok = True
            net_ev_stats["degraded"] = True
            net_ev_stats["degraded_reason"] = f"{type(_ev_exc).__name__}: {_ev_exc}"[:200]
        # Regime changepoint veto (BOCPD probability supplied by the caller).
        changepoint_probability = None
        changepoint_ok = True
        if "changepoint_probability" in market:
            try:
                changepoint_probability = float(market.get("changepoint_probability"))
                cp_threshold = float(market.get("max_changepoint_probability",
                                                self.max_changepoint_probability))
                changepoint_ok = (
                    math.isfinite(changepoint_probability)
                    and changepoint_probability <= cp_threshold)
            except (TypeError, ValueError):
                changepoint_probability, changepoint_ok = None, False
            if not changepoint_ok:
                reasons.append("regime changepoint probability above veto threshold")

        # --- Walk-Forward Decay Gate (UAQP P0) ---
        walk_forward_ok = True
        walk_forward_status = None
        if "walk_forward_status" in market:
            try:
                walk_forward_status = str(market.get("walk_forward_status", "UNKNOWN"))
                if walk_forward_status == "DEAD":
                    walk_forward_ok = False
                    reasons.append("walk-forward decay DEAD: alpha is harmful, all entries blocked")
                elif walk_forward_status == "DECAYING":
                    # Reduce conviction but don't block
                    pass
            except Exception as exc:
                walk_forward_ok = False
                reasons.append(f"walk-forward decay gate evaluation error: {exc}")

        # --- Walk-Forward Efficiency Gate (quantitative acceptance bar) ---
        # WFE = mean(OOS Sharpe) / mean(IS Sharpe). Only binds when the caller
        # supplies a real WFE verdict with >= 2 folds (fresh evidence); a floor
        # of 0.0 disables the gate entirely.
        wfe_value = None
        wfe_ok = True
        wfe_raw = market.get("walk_forward_wfe")
        if self.min_walk_forward_efficiency > 0.0 and isinstance(wfe_raw, dict):
            try:
                wfe_value = float(wfe_raw.get("wfe", 0.0))
                n_folds = int(wfe_raw.get("n_folds", 0))
                mean_test = float(wfe_raw.get("mean_test_sharpe", 0.0))
                if not (math.isfinite(wfe_value) and math.isfinite(mean_test)):
                    wfe_ok = False
                    reasons.append("walk-forward efficiency REJECT: non-finite WFE or mean test Sharpe")
                elif n_folds >= 2 and mean_test <= 0.0:
                    wfe_ok = False
                    reasons.append(
                        f"walk-forward efficiency REJECT: OOS unprofitable "
                        f"(mean OOS Sharpe {mean_test:.4f} <= 0)")
                elif n_folds >= 2 and wfe_value < self.min_walk_forward_efficiency:
                    wfe_ok = False
                    reasons.append(
                        f"walk-forward efficiency REJECT: WFE {wfe_value:.3f} "
                        f"below floor {self.min_walk_forward_efficiency:.2f}")
            except (TypeError, ValueError):
                wfe_value, wfe_ok = None, False
                reasons.append("walk-forward efficiency REJECT: invalid WFE structure")

        # --- Overfit Gate (UAQP P0) ---
        overfit_ok = True
        overfit_risk_level = None
        if "overfit_risk_level" in market:
            try:
                overfit_risk_level = str(market.get("overfit_risk_level", "LOW"))
                if overfit_risk_level in ("HIGH", "CRITICAL"):
                    overfit_ok = False
                    reasons.append(f"overfit gate REJECT: risk={overfit_risk_level}")
            except Exception as exc:
                overfit_ok = False
                reasons.append(f"overfit gate evaluation error: {exc}")

        # --- Data-Drift Gate (PSI/KS feature-distribution monitoring) ---
        drift_ok = True
        drift_status = None
        if "data_drift_status" in market:
            try:
                drift_status = str(market.get("data_drift_status") or "")
                if drift_status == "DRIFT" and self.block_on_data_drift:
                    drift_ok = False
                    reasons.append(
                        "data-drift gate REJECT: live feature distributions in "
                        "major shift (PSI) — retrain/re-baseline before entries")
            except Exception as exc:
                if self.block_on_data_drift:
                    drift_ok = False
                    reasons.append(f"data-drift gate evaluation error: {exc}")
                else:
                    drift_ok = True

        # --- CAS Selective Activation Gate (P22 Han 2026) ---
        features = getattr(proposal, "features", {}) or {}
        cas_regime = str(market.get("regime") if market.get("regime") is not None else features.get("regime", ""))
        cas_adx = float(market.get("adx") if market.get("adx") is not None else features.get("adx", 0.0) or 0.0)
        cas_chop = float(market.get("choppiness_index") if market.get("choppiness_index") is not None else features.get("choppiness_index", 0.0) or 0.0)
        cas_ok, cas_reason = check_selective_activation(
            strategy=proposal.strategy,
            regime=cas_regime,
            adx=cas_adx,
            choppiness=cas_chop,
            side=proposal.side,
        )
        if not cas_ok:
            reasons.append(f"selective_activation_gate: {cas_reason}")

        # --- Microstructure Safety: VPIN Toxicity Shield & Phantom Liquidity Guard ---
        vpin_val = None
        vpin_ok = True
        max_vpin = float(market.get("max_vpin_toxicity", 0.75))
        if "vpin_toxicity" in features and features["vpin_toxicity"] is not None:
            try:
                vpin_val = float(features["vpin_toxicity"])
                vpin_ok = math.isfinite(vpin_val) and vpin_val <= max_vpin
            except (TypeError, ValueError):
                vpin_ok = True
            if not vpin_ok:
                reasons.append("vpin toxicity too high")

        phantom_liquidity = bool(features.get("phantom_liquidity_flag") or market.get("phantom_liquidity_flag"))
        phantom_ok = not phantom_liquidity
        if not phantom_ok:
            reasons.append("phantom liquidity / quote stuffing detected")

        rule_trace = {
            "vpin_toxicity_acceptable": {"passed": vpin_ok, "value": vpin_val, "limit": max_vpin},
            "phantom_liquidity_clear": {"passed": phantom_ok, "flag": phantom_liquidity},
            "kill_switch_clear": {"passed": not kill_switch_active, "value": kill_switch_active},
            "market_open": {"passed": bool(market.get("market_open")), "value": market.get("market_open")},
            "tick_fresh": {"passed": tick_fresh, "value": tick_age, "limit": max_tick_age},
            "clock_healthy": {"passed": bool(market.get("clock_ok", True)), "value": market.get("clock_ok", True)},
            "symbol_tradable": {"passed": bool(market.get("symbol_tradable")), "value": market.get("symbol_tradable")},
            "spread_within_limit": {"passed": spread_ok, "value": spread, "limit": max_spread},
            "session_allowed": {"passed": bool(market.get("session_allowed", True)), "value": market.get("session_allowed", True)},
            "liquidity_ok": {"passed": bool(market.get("liquidity_ok", True)), "value": market.get("liquidity_ok", True)},
            "microstructure_ok": {"passed": bool(market.get("microstructure_ok", True)), "value": market.get("microstructure_ok", True)},
            "required_data_ok": {"passed": bool(market.get("required_data_ok", True)), "reasons": market.get("required_data_reasons", [])},
            "market_conditions_ok": {"passed": not bool(market.get("market_condition_reasons", [])), "reasons": market.get("market_condition_reasons", [])},
            "microstructure_reasons_clear": {"passed": not bool(market.get("microstructure_reasons", [])), "reasons": market.get("microstructure_reasons", [])},
            "planned_entry_tolerance": {"passed": planned_entry_ok, "value": market.get("execution_price", proposal.entry), "limit": market.get("entry_tolerance", 0)},
            "completed_candle_or_breakout": {"passed": completed_or_breakout, "value": market.get("completed_candle"), "breakout": market.get("breakout_event")},
            "stop_distance_valid": {"passed": stop_ok, "value": stop_distance, "limit": min_stop_distance},
            "reward_risk_valid": {"passed": reward_risk_ok, "value": proposal.reward_risk, "limit": required_rr},
            "event_window_clear": {"passed": event_window_ok, "high_impact_news": market.get("high_impact_news"), "setup_exclusion": market.get("setup_event_exclusion")},
            "deterministic_confluence": {"passed": confluence_ok, "value": score, "limit": self.min_confluence},
            "news_safe": {"passed": news_safe, "value": market.get("high_impact_news")},
            "setup_cooldown_clear": {"passed": setup_cooldown_ok, "recent": setup_recent},
            "lifecycle_clear": {"passed": lifecycle_ok,
                                "block": (lifecycle_block or {}) if isinstance(lifecycle_block, dict) else lifecycle_block},
            "lag_confidence_ok": {"passed": lag_ok, "value": lag_confidence, "limit": min_lag_confidence},
            "rolling_net_expectancy": {"passed": net_ev_ok, "value": net_ev_value,
                                        "stats": {k: net_ev_stats.get(k) for k in
                                                  ("sample_count", "ev_gross", "friction_r")}},
            "regime_changepoint_clear": {"passed": changepoint_ok,
                                          "value": changepoint_probability},
            "walk_forward_clear": {"passed": walk_forward_ok, "status": walk_forward_status},
            "walk_forward_efficiency_clear": {"passed": wfe_ok, "wfe": wfe_value,
                                               "floor": self.min_walk_forward_efficiency},
            "overfit_gate_clear": {"passed": overfit_ok, "risk_level": overfit_risk_level},
            "data_drift_clear": {"passed": drift_ok, "status": drift_status},
            "selective_activation_clear": {"passed": cas_ok, "reason": cas_reason},
        }

        # ── Live Health Monitor (Blind Spot audit) ───────────────────────────
        if _LiveHealthMonitor is not None:
            try:
                _health_monitor = _LiveHealthMonitor(
                    account_data={"balance": market.get("balance", 0), "daily_pnl": market.get("daily_pnl", 0)},
                    positions=market.get("open_positions", []),
                    market_data={
                        "price_velocity": market.get("price_velocity"),
                        "correlation_heat": market.get("correlation_heat", 0),
                        "tail_risk_heat": market.get("tail_risk_heat", 0),
                    },
                    execution_history=market.get("recent_executions", []),
                    equity_history=market.get("equity_history", []),
                    backtest_metrics=market.get("backtest_metrics", {}),
                    slippage_history=market.get("slippage_history", []),
                )
                _health_report = _health_monitor.run_full_check()
                rule_trace["live_health_score"] = {
                    "passed": _health_report.health_score >= 0.5,
                    "value": _health_report.health_score,
                    "alerts": len(_health_report.alerts),
                    "critical": _health_report.critical_count,
                }
                if _health_report.critical_count > 0:
                    reasons.append(f"live health critical: {_health_report.critical_count} critical alerts")
                elif _health_report.health_score < 0.3:
                    reasons.append(f"live health score too low: {_health_report.health_score:.2f}")
            except Exception as _health_exc:
                log.debug("LiveHealthMonitor skipped: %s", _health_exc)

        return ValidationResult(not reasons, reasons, score, rule_trace)


class PortfolioRiskArbiter:
    def __init__(
        self,
        store: TradingStore,
        daily_loss_fraction: float = .03,
        weekly_loss_fraction: float = .06,
        max_drawdown_fraction: float = .10,
        max_open_risk_fraction: float = .03,
        max_correlated_risk_fraction: float = .02,
        max_net_usd_beta_fraction: float = 0.025,
        max_single_currency_fraction: float = 0.03,
    ):
        self.store = store
        self.daily_loss_fraction, self.weekly_loss_fraction = daily_loss_fraction, weekly_loss_fraction
        self.max_drawdown_fraction, self.max_open_risk_fraction = max_drawdown_fraction, max_open_risk_fraction
        self.max_correlated_risk_fraction = min(max_correlated_risk_fraction, max_open_risk_fraction)
        self.max_net_usd_beta_fraction = max_net_usd_beta_fraction
        self.max_single_currency_fraction = max_single_currency_fraction

    def permit(
        self,
        equity: float,
        new_risk: float,
        bucket_risk: float = 0.0,
        bucket: str = "",
        correlated_risk: float = 0.0,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        use_netting: bool = True,
    ) -> Tuple[bool, str]:
        try:
            eq = float(equity)
            nr = float(new_risk)
            br = float(bucket_risk)
            cr = float(correlated_risk)
        except (TypeError, ValueError, OverflowError):
            return False, "invalid non-numeric risk parameters"
        if not (math.isfinite(eq) and math.isfinite(nr) and math.isfinite(br) and math.isfinite(cr)):
            return False, "non-finite risk parameters"
        if eq <= 0 or nr < 0 or br < 0 or cr < 0:
            return False, "out-of-bounds risk parameters"
        equity, new_risk, bucket_risk, correlated_risk = eq, nr, br, cr
        now = datetime.now(timezone.utc)

        def _peak_mutator(stored_peak: Any) -> float:
            if stored_peak is None:
                return equity
            peak_value = float(stored_peak or equity)
            return equity if equity > peak_value else peak_value

        peak = float(self.store.update_state("equity_peak", _peak_mutator, default=None) or equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        if drawdown >= self.max_drawdown_fraction:
            self.store.set_kill_switch(True, f"maximum drawdown reached: {drawdown:.2%}")
            return False, "maximum drawdown kill switch"
        daily = self.store.realized_loss(now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
        weekly = self.store.realized_loss(week_start.isoformat())
        if daily >= equity * self.daily_loss_fraction: return False, "daily loss limit"
        if weekly >= equity * self.weekly_loss_fraction: return False, "weekly loss limit"
        if self.store.open_risk() + new_risk > equity * self.max_open_risk_fraction: return False, "portfolio open-risk limit"

        # FX Netting & Dollar Beta validation (opt-in when symbol and side are provided)
        is_hedging = False
        if symbol and side and use_netting:
            from core.fx_portfolio_netting import evaluate_fx_netting_permit
            open_pos = self.store.open_positions_for_netting()
            ok, netting_reason, details = evaluate_fx_netting_permit(
                open_pos, symbol, side, new_risk, equity,
                max_net_usd_beta_fraction=self.max_net_usd_beta_fraction,
                max_single_currency_fraction=self.max_single_currency_fraction,
            )
            if not ok:
                return False, netting_reason
            is_hedging = bool(details.get("is_hedging", False))

        current_bucket = max(bucket_risk, self.store.open_bucket_risk(bucket) if bucket else 0.0)
        if not is_hedging:
            if current_bucket + new_risk > equity * self.max_open_risk_fraction: return False, "correlated bucket risk limit"
            if correlated_risk + new_risk > equity * self.max_correlated_risk_fraction: return False, "rolling correlated exposure risk limit"
        else:
            if current_bucket + new_risk > equity * self.max_open_risk_fraction * 1.5: return False, "correlated bucket risk limit"
        return True, "approved"


def safe_risk_fraction(percent_or_fraction: float, cap: float = .01) -> float:
    """Accept a UI percentage or fraction and return a capped fraction."""
    value = float(percent_or_fraction)
    if value > 1: value /= 100.0
    # UI's 1.0 means 1%; retain compatibility with callers passing 0.01.
    if value == 1.0: value = .01
    return max(0.0, min(value, cap))


def exposure_buckets(symbol: str, side: str) -> set[str]:
    """Map a trade to conservative base/quote-driver buckets."""
    s, direction = symbol.upper(), side.upper()
    if s.startswith("XAU") or "GOLD" in s:
        return {"GOLD_LONG" if direction == "BUY" else "GOLD_SHORT", "USD_SHORT" if direction == "BUY" else "USD_LONG"}
    base, quote = s[:3], s[3:6]
    if len(base) != 3 or len(quote) != 3:
        return {s}
    return {f"{base}_{'LONG' if direction == 'BUY' else 'SHORT'}", f"{quote}_{'SHORT' if direction == 'BUY' else 'LONG'}"}


# Specification alias (P22 / UAQP)
TradingConstraintEngine = DeterministicValidator

