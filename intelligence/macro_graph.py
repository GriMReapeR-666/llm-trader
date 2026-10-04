"""Local, source-linked macro causal graph and event-reaction journal.

Edges are hypotheses, not trading permissions.  They require explicit review
before appearing in a macro context and always retain source/confidence/time.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
try:
    from intelligence.macro_source_policy import assess_source
except ImportError:
    from intelligence.macro_source_policy import assess_source


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MacroGraph:
    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path
        self._init()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        # Wait for locks instead of failing mid-cycle when the dual-velocity
        # executor, reflection lane, or TUI hold the database briefly.
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init(self) -> None:
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS macro_sources (
              source_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, title TEXT NOT NULL,
              source_url TEXT, publisher TEXT, published_at TEXT, rights_note TEXT,
              content_hash TEXT NOT NULL, version TEXT, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS macro_entities (
              entity_id INTEGER PRIMARY KEY, name TEXT NOT NULL, entity_type TEXT NOT NULL,
              created_at TEXT NOT NULL, UNIQUE(name, entity_type)
            );
            CREATE TABLE IF NOT EXISTS macro_edges (
              edge_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              source_entity INTEGER NOT NULL, target_entity INTEGER NOT NULL, relation TEXT NOT NULL,
              sign TEXT NOT NULL, confidence REAL NOT NULL, source_id TEXT NOT NULL,
              valid_from TEXT, valid_until TEXT, contradiction_of INTEGER, status TEXT NOT NULL,
              review_by TEXT, reviewed_at TEXT, notes TEXT NOT NULL,
              FOREIGN KEY(source_entity) REFERENCES macro_entities(entity_id),
              FOREIGN KEY(target_entity) REFERENCES macro_entities(entity_id),
              FOREIGN KEY(source_id) REFERENCES macro_sources(source_id),
              FOREIGN KEY(contradiction_of) REFERENCES macro_edges(edge_id)
            );
            CREATE INDEX IF NOT EXISTS idx_macro_edges_lookup ON macro_edges(source_entity,target_entity,status);
            CREATE TABLE IF NOT EXISTS macro_edge_history (
              history_id INTEGER PRIMARY KEY, edge_id INTEGER NOT NULL, changed_at TEXT NOT NULL,
              action TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT, payload TEXT NOT NULL,
              FOREIGN KEY(edge_id) REFERENCES macro_edges(edge_id)
            );
            CREATE INDEX IF NOT EXISTS idx_macro_edge_history_edge
              ON macro_edge_history(edge_id, changed_at);
            CREATE TABLE IF NOT EXISTS event_reactions (
              id INTEGER PRIMARY KEY, event_source_id TEXT NOT NULL, symbol TEXT NOT NULL,
              observed_at TEXT NOT NULL, actual REAL, forecast REAL, previous REAL,
              surprise REAL, windows TEXT NOT NULL, source_quality TEXT NOT NULL,
              FOREIGN KEY(event_source_id) REFERENCES macro_sources(source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_event_reactions_lookup ON event_reactions(symbol, observed_at);
            CREATE TABLE IF NOT EXISTS event_reaction_trackers (
              tracker_key TEXT PRIMARY KEY, event_source_id TEXT NOT NULL, symbol TEXT NOT NULL,
              event_time TEXT NOT NULL, actual REAL, forecast REAL, previous REAL,
              anchor_price REAL NOT NULL, anchor_observed_at TEXT NOT NULL,
              source_quality TEXT NOT NULL, status TEXT NOT NULL, final_reaction_id INTEGER,
              FOREIGN KEY(event_source_id) REFERENCES macro_sources(source_id),
              FOREIGN KEY(final_reaction_id) REFERENCES event_reactions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_event_reaction_trackers_due
              ON event_reaction_trackers(symbol, status, anchor_observed_at);
            CREATE TABLE IF NOT EXISTS event_reaction_measurements (
              tracker_key TEXT NOT NULL, window_name TEXT NOT NULL, target_seconds INTEGER NOT NULL,
              observed_at TEXT NOT NULL, observed_seconds REAL NOT NULL, observed_price REAL NOT NULL,
              return_fraction REAL NOT NULL, PRIMARY KEY(tracker_key, window_name),
              FOREIGN KEY(tracker_key) REFERENCES event_reaction_trackers(tracker_key)
            );
            """)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    def ingest_sources_bulk(self, items: Iterable[Dict[str, Any]]) -> List[str]:
        """Ingest many sources in one transaction.

        The per-cycle calendar/news loops previously opened one connection per
        event; with 20-40 events per pulse this dominated ingestion latency and
        multiplied lock contention. Content-hash dedupe semantics are identical
        to ``ingest_source``: an existing digest returns its existing id.
        """
        rows: List[tuple] = []
        ids_in_order: List[str] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            payload = item.get("payload")
            if not title or not isinstance(payload, dict):
                continue
            policy = assess_source(str(item.get("source_url") or ""), str(item.get("publisher") or ""),
                                   str(item.get("rights_note") or ""))
            payload = {**payload, "source_policy": policy}
            encoded = self._json(payload)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            source_id = f"macro:{digest[:24]}"
            rights_note = str(item.get("rights_note") or "") or policy["retention"]
            rows.append((
                source_id, utcnow(), title[:500], str(item.get("source_url") or "")[:2000],
                str(item.get("publisher") or "")[:500], str(item.get("published_at") or ""),
                rights_note[:1000], digest, str(item.get("version") or "")[:100], encoded,
            ))
            ids_in_order.append(source_id)
        if not rows:
            return []
        with self._con() as con:
            con.executemany(
                "INSERT OR IGNORE INTO macro_sources(source_id,captured_at,title,source_url,publisher,published_at,rights_note,content_hash,version,payload) VALUES(?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return ids_in_order

    @staticmethod
    def _normalize_as_of(value: Any = None) -> str:
        if value is None or value == "":
            return datetime.now(timezone.utc).isoformat()
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            raise ValueError("macro retrieval as_of must be a valid timestamp") from None

    def ingest_source(self, *, title: str, payload: Dict[str, Any], source_url: str = "",
                      publisher: str = "", published_at: str = "", rights_note: str = "",
                      version: str = "") -> str:
        if not isinstance(payload, dict):
            raise ValueError("macro source payload must be an object")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("macro source title is required")
        policy = assess_source(source_url, publisher, rights_note)
        payload = {**payload, "source_policy": policy}
        encoded = self._json(payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        source_id = f"macro:{digest[:24]}"
        rights_note = rights_note or policy["retention"]
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO macro_sources(source_id,captured_at,title,source_url,publisher,published_at,rights_note,content_hash,version,payload) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (source_id, utcnow(), title[:500], source_url[:2000], publisher[:500], published_at, rights_note[:1000], digest, version[:100], encoded))
        return source_id

    def entity(self, name: str, entity_type: str) -> int:
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO macro_entities(name,entity_type,created_at) VALUES(?,?,?)", (name[:200], entity_type[:100], utcnow()))
            row = con.execute("SELECT entity_id FROM macro_entities WHERE name=? AND entity_type=?", (name[:200], entity_type[:100])).fetchone()
        return int(row["entity_id"])

    def propose_edge(self, *, source_name: str, source_type: str, target_name: str, target_type: str,
                     relation: str, sign: str, confidence: float, source_id: str, notes: str = "",
                     valid_from: str = "", valid_until: str = "", contradiction_of: Optional[int] = None) -> int:
        if sign not in {"POSITIVE", "NEGATIVE", "CONDITIONAL", "UNKNOWN"}:
            raise ValueError("sign must be POSITIVE, NEGATIVE, CONDITIONAL, or UNKNOWN")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("confidence must be finite and within [0, 1]") from None
        if not math.isfinite(confidence_value) or not 0 <= confidence_value <= 1:
            raise ValueError("confidence must be finite and within [0, 1]")
        if not all(isinstance(value, str) and value.strip() for value in
                   (source_name, source_type, target_name, target_type, relation, source_id)):
            raise ValueError("macro edge identity and relation fields are required")
        now = utcnow()
        source_entity = self.entity(source_name, source_type)
        target_entity = self.entity(target_name, target_type)
        with self._con() as con:
            source_row = con.execute("SELECT payload FROM macro_sources WHERE source_id=?", (source_id,)).fetchone()
            if not source_row:
                raise ValueError("macro edge requires an ingested source")
            try:
                source_policy = json.loads(source_row["payload"] or "{}").get("source_policy", {})
            except (TypeError, ValueError, json.JSONDecodeError):
                source_policy = {}
            if source_policy.get("allowed") is not True:
                raise ValueError("macro edge source is not approved by source policy")
            cursor = con.execute("""INSERT INTO macro_edges(created_at,updated_at,source_entity,target_entity,relation,sign,confidence,source_id,valid_from,valid_until,contradiction_of,status,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, now, source_entity, target_entity, relation[:200], sign,
                confidence_value, source_id, valid_from or None, valid_until or None,
                contradiction_of, "CANDIDATE", notes[:2000]))
            edge_id = int(cursor.lastrowid)
            con.execute("INSERT INTO macro_edge_history(edge_id,changed_at,action,status,reviewer,payload) VALUES(?,?,?,?,?,?)",
                        (edge_id, now, "PROPOSED", "CANDIDATE", None,
                         self._json({"source_id": source_id, "relation": relation, "sign": sign,
                                     "confidence": confidence_value, "notes": notes})))
        return edge_id

    def propose_lag_aware_edge(self, *, source_name: str, source_type: str, target_name: str, target_type: str,
                               relation: str, sign: str, confidence: float, source_id: str,
                               min_lag_seconds: float = 0.0, max_lag_seconds: float = 86400.0,
                               decay_half_life_seconds: float = 43200.0, notes: str = "") -> int:
        """Propose a candidate causal edge enriched with propagation delay distribution metadata."""
        lag_meta = {
            "lag_aware": True,
            "min_lag_seconds": float(min_lag_seconds),
            "max_lag_seconds": float(max_lag_seconds),
            "decay_half_life_seconds": float(decay_half_life_seconds),
            "user_notes": notes,
        }
        encoded_notes = json.dumps(lag_meta)
        return self.propose_edge(
            source_name=source_name, source_type=source_type,
            target_name=target_name, target_type=target_type,
            relation=relation, sign=sign, confidence=confidence,
            source_id=source_id, notes=encoded_notes,
        )

    def review_edge(self, edge_id: int, *, approve: bool, reviewer: str) -> None:
        reviewer = str(reviewer or "").strip()
        if not reviewer:
            raise ValueError("reviewer is required")
        status = "APPROVED" if approve else "RETIRED"
        with self._con() as con:
            now = utcnow()
            row = con.execute("SELECT edge_id,source_id,relation,sign,confidence,notes FROM macro_edges WHERE edge_id=? AND status='CANDIDATE'", (edge_id,)).fetchone()
            if row:
                changed = con.execute("UPDATE macro_edges SET status=?,updated_at=?,review_by=?,reviewed_at=? WHERE edge_id=? AND status='CANDIDATE'",
                                      (status, now, reviewer[:200], now, edge_id)).rowcount
                if changed:
                    con.execute("INSERT INTO macro_edge_history(edge_id,changed_at,action,status,reviewer,payload) VALUES(?,?,?,?,?,?)",
                                (edge_id, now, "REVIEWED", status, reviewer[:200], self._json(dict(row))))
            else:
                changed = 0
        if not changed:
            raise ValueError("only a candidate macro edge can be reviewed once")

    def add_edge(self, source: str, target: str, relation: str = "influences",
                 sign: str = "POSITIVE", confidence: float = 0.8, source_id: str = "") -> int:
        """Convenience method to propose and approve an edge immediately."""
        if not source_id:
            source_id = self.ingest_source(title=f"Edge {source}->{target}", payload={"note": "auto_edge"})
        norm_sign = "POSITIVE" if str(sign).upper() in ("1", "POSITIVE", "+1") else (
            "NEGATIVE" if str(sign).upper() in ("-1", "NEGATIVE", "INVERSE") else "CONDITIONAL"
        )
        edge_id = self.propose_edge(
            source_name=source, source_type="CONCEPT",
            target_name=target, target_type="CONCEPT",
            relation=relation, sign=norm_sign,
            confidence=confidence, source_id=source_id,
        )
        self.review_edge(edge_id, approve=True, reviewer="system")
        return edge_id

    def revise_edge(self, edge_id: int, *, reviewer: str, sign: Optional[str] = None,
                    confidence: Optional[float] = None, notes: Optional[str] = None,
                    valid_until: Optional[str] = None) -> int:
        """Amend an edge and require a fresh review before retrieval.

        The previous values are written to the append-only history ledger;
        approved context cannot change silently.  Revision intentionally
        returns the edge to ``CANDIDATE`` and therefore removes it from
        ``approved_edges`` until an operator reviews it again.
        """
        reviewer = str(reviewer or "").strip()
        if not reviewer:
            raise ValueError("reviewer is required")
        if sign is not None and sign not in {"POSITIVE", "NEGATIVE", "CONDITIONAL", "UNKNOWN"}:
            raise ValueError("invalid edge sign")
        with self._con() as con:
            row = con.execute("SELECT * FROM macro_edges WHERE edge_id=?", (int(edge_id),)).fetchone()
            if not row:
                raise ValueError("macro edge not found")
            before = dict(row)
            now = utcnow()
            after_sign = sign or row["sign"]
            if confidence is not None:
                try:
                    after_confidence = float(confidence)
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("confidence must be finite and within [0, 1]") from None
                if not math.isfinite(after_confidence) or not 0 <= after_confidence <= 1:
                    raise ValueError("confidence must be finite and within [0, 1]")
            else:
                after_confidence = float(row["confidence"])
            after_notes = str(notes)[:2000] if notes is not None else row["notes"]
            after_valid_until = valid_until if valid_until is not None else row["valid_until"]
            con.execute("""UPDATE macro_edges SET sign=?,confidence=?,notes=?,valid_until=?,status='CANDIDATE',
                         updated_at=?,review_by=NULL,reviewed_at=NULL WHERE edge_id=?""",
                        (after_sign, after_confidence, after_notes, after_valid_until, now, int(edge_id)))
            after = {"sign": after_sign, "confidence": after_confidence,
                     "notes": after_notes, "valid_until": after_valid_until}
            con.execute("INSERT INTO macro_edge_history(edge_id,changed_at,action,status,reviewer,payload) VALUES(?,?,?,?,?,?)",
                        (int(edge_id), now, "REVISED", "CANDIDATE", reviewer[:200],
                         self._json({"before": before, "after": after})))
        return int(edge_id)

    def apply_trade_outcome_feedback(self, edge_id: int, *, r_multiple: float, ticket: int,
                                     label: str = "") -> Dict[str, Any]:
        """Update causal-edge confidence from a closed-trade R-multiple.

        Calls ``revise_edge`` (returns edge to CANDIDATE) so the outcome is
        auditable. Callers may immediately re-run autonomous validation to
        restore AUTO_APPROVED status when evidence still supports the edge.
        """
        try:
            r_value = float(r_multiple)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("r_multiple must be finite") from None
        if not math.isfinite(r_value):
            raise ValueError("r_multiple must be finite")
        with self._con() as con:
            row = con.execute(
                "SELECT edge_id, confidence, status, sign FROM macro_edges WHERE edge_id=?",
                (int(edge_id),),
            ).fetchone()
        if not row:
            raise ValueError("macro edge not found")
        old_confidence = float(row["confidence"])
        # Bounded learning step: ~5% confidence per 1R, capped at ±15%.
        delta = max(-0.15, min(0.15, r_value * 0.05))
        new_confidence = max(0.05, min(0.99, round(old_confidence + delta, 4)))
        notes = json.dumps({
            "trade_outcome_feedback": True,
            "ticket": int(ticket),
            "label": str(label or ""),
            "r_multiple": r_value,
            "confidence_delta": delta,
            "previous_status": row["status"],
        }, default=str)
        self.revise_edge(
            int(edge_id),
            reviewer=f"TRADE_OUTCOME:{int(ticket)}",
            confidence=new_confidence,
            notes=notes,
        )
        return {
            "edge_id": int(edge_id),
            "ticket": int(ticket),
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "r_multiple": r_value,
            "label": str(label or ""),
            "status": "CANDIDATE",
        }

    def edge_history(self, edge_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM macro_edge_history WHERE edge_id=? ORDER BY changed_at ASC, history_id ASC LIMIT ?",
                               (int(edge_id), max(0, int(limit)))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item["payload"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["payload"] = {}
            result.append(item)
        return result

    def summary(self) -> Dict[str, Any]:
        """Graph-level counters: active (approved) causal edges and pending candidates."""
        with self._con() as con:
            active = con.execute(
                "SELECT COUNT(*) AS n FROM macro_edges WHERE status IN ('APPROVED','AUTO_APPROVED')"
            ).fetchone()["n"]
            pending = con.execute(
                "SELECT COUNT(*) AS n FROM macro_edges WHERE status='CANDIDATE'"
            ).fetchone()["n"]
        return {"active_edges": int(active), "pending_candidates": int(pending)}

    def approved_edges(self, entity_names: Iterable[str], limit: int = 20, *, as_of: Any = None) -> List[Dict[str, Any]]:
        names = [str(name) for name in entity_names if name]
        if not names:
            return []
        cutoff = self._normalize_as_of(as_of)
        placeholders = ",".join("?" for _ in names)
        sql = f"""SELECT e.*,a.name AS source_name,a.entity_type AS source_type,b.name AS target_name,b.entity_type AS target_type,s.title,s.source_url,s.published_at
                 FROM macro_edges e JOIN macro_entities a ON a.entity_id=e.source_entity
                 JOIN macro_entities b ON b.entity_id=e.target_entity JOIN macro_sources s ON s.source_id=e.source_id
                 WHERE e.status IN ('APPROVED','AUTO_APPROVED') AND e.created_at<=? AND (e.reviewed_at IS NULL OR e.reviewed_at='' OR e.reviewed_at<=?)
                   AND (e.valid_from IS NULL OR e.valid_from='' OR e.valid_from<=?)
                   AND (e.valid_until IS NULL OR e.valid_until='' OR e.valid_until>=?)
                   AND (a.name IN ({placeholders}) OR b.name IN ({placeholders}))
                 ORDER BY e.reviewed_at DESC LIMIT ?"""
        with self._con() as con:
            rows = con.execute(sql, (cutoff, cutoff, cutoff, cutoff, *names, *names, limit)).fetchall()
        return [dict(row) for row in rows]

    def record_event_reaction(self, event_source_id: str, symbol: str, *, actual: Any, forecast: Any,
                              previous: Any, windows: Dict[str, Any], source_quality: str) -> int:
        def number(value: Any) -> Optional[float]:
            try:
                parsed = float(value) if value not in (None, "") else None
                return parsed if parsed is None or math.isfinite(parsed) else None
            except (TypeError, ValueError):
                return None
        if not isinstance(event_source_id, str) or not event_source_id.strip():
            raise ValueError("event source ID is required")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("event reaction symbol is required")
        if not isinstance(windows, dict):
            raise ValueError("event reaction windows must be an object")
        if not isinstance(source_quality, str) or not source_quality.strip():
            raise ValueError("event reaction source quality is required")
        actual_value, forecast_value, previous_value = number(actual), number(forecast), number(previous)
        surprise = actual_value - forecast_value if actual_value is not None and forecast_value is not None else None
        with self._con() as con:
            cursor = con.execute("INSERT INTO event_reactions(event_source_id,symbol,observed_at,actual,forecast,previous,surprise,windows,source_quality) VALUES(?,?,?,?,?,?,?,?,?)",
                                (event_source_id, symbol, utcnow(), actual_value, forecast_value, previous_value, surprise,
                                 self._json(windows), source_quality[:200]))
        return int(cursor.lastrowid)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            parsed = float(value) if value not in (None, "") else None
            return parsed if parsed is None or math.isfinite(parsed) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_time(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        elif value not in (None, ""):
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        else:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    @staticmethod
    def _event_time(event: Dict[str, Any]) -> Optional[datetime]:
        for key in ("time", "timestamp", "datetime", "scheduled_at", "date"):
            if event.get(key) not in (None, ""):
                try:
                    return MacroGraph._parse_time(event[key])
                except (TypeError, ValueError, OverflowError):
                    return None
        return None

    def schedule_event_reaction(self, event_source_id: str, symbol: str, event: Dict[str, Any], *,
                                anchor_price: float, observed_at: Any = None) -> Optional[str]:
        """Start a reaction tracker after an actual release is observed.

        Calendar payloads may be revised, delayed, or lack a machine-readable
        actual.  This deliberately does nothing until a numeric actual exists,
        and labels the first captured price as a *post-release anchor* rather
        than pretending it is a tick at the scheduled release time.
        """
        actual = self._number(event.get("actual"))
        event_time = self._event_time(event)
        anchor = self._number(anchor_price)
        now = self._parse_time(observed_at) or datetime.now(timezone.utc)
        # A macro level with no forecast/previous value (for example a FRED
        # fallback observation) is not a timestamped release surprise.
        if (actual is None or (self._number(event.get("forecast")) is None and self._number(event.get("previous")) is None)
                or event_time is None or event_time > now or anchor is None or anchor <= 0):
            return None
        tracker_key = hashlib.sha256(f"{event_source_id}|{symbol}".encode("utf-8")).hexdigest()[:32]
        with self._con() as con:
            con.execute("""INSERT OR IGNORE INTO event_reaction_trackers(
                tracker_key,event_source_id,symbol,event_time,actual,forecast,previous,anchor_price,
                anchor_observed_at,source_quality,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    tracker_key, event_source_id, symbol, event_time.isoformat(), actual,
                    self._number(event.get("forecast")), self._number(event.get("previous")), anchor,
                    now.isoformat(), "post_release_anchor; calendar actual available", "TRACKING",
                ))
        return tracker_key

    def capture_due_event_reactions(self, symbol: str, price: float, *, observed_at: Any = None,
                                    windows: Dict[str, int] | None = None) -> Dict[str, Any]:
        """Capture each due window from a point-in-time observed price.

        This is a measurement service, not a signal.  A late process restart
        records the actual elapsed time and never backfills invented 1-minute
        prices.  When all windows are measured, a single source-linked reaction
        summary is written for macro retrieval.
        """
        now = self._parse_time(observed_at) or datetime.now(timezone.utc)
        observed_price = self._number(price)
        if observed_price is None or observed_price <= 0:
            return {"scheduled": 0, "measured": 0, "completed": 0, "reason": "invalid observed price"}
        if windows is None:
            windows = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        if not isinstance(windows, dict) or not windows:
            return {"scheduled": 0, "measured": 0, "completed": 0, "reason": "invalid reaction windows"}
        normalized_windows: Dict[str, int] = {}
        for name, seconds in windows.items():
            if not isinstance(name, str) or not name.strip():
                return {"scheduled": 0, "measured": 0, "completed": 0, "reason": "invalid reaction window name"}
            try:
                numeric_seconds = float(seconds)
            except (TypeError, ValueError, OverflowError):
                return {"scheduled": 0, "measured": 0, "completed": 0, "reason": "invalid reaction window duration"}
            if not math.isfinite(numeric_seconds) or numeric_seconds <= 0 or int(numeric_seconds) != numeric_seconds:
                return {"scheduled": 0, "measured": 0, "completed": 0, "reason": "invalid reaction window duration"}
            normalized_windows[name.strip()[:64]] = int(numeric_seconds)
        windows = normalized_windows
        with self._con() as con:
            trackers = con.execute("SELECT * FROM event_reaction_trackers WHERE symbol=? AND status='TRACKING'", (symbol,)).fetchall()
            measured = completed = 0
            for tracker in trackers:
                anchor_at = self._parse_time(tracker["anchor_observed_at"])
                if anchor_at is None:
                    continue
                elapsed = (now - anchor_at).total_seconds()
                max_window_s = max(windows.values()) if windows else 86400
                if elapsed > max(max_window_s * 3, 7 * 86400):
                    con.execute("UPDATE event_reaction_trackers SET status='EXPIRED' WHERE tracker_key=?", (tracker["tracker_key"],))
                    continue
                existing = {row["window_name"] for row in con.execute("SELECT window_name FROM event_reaction_measurements WHERE tracker_key=?", (tracker["tracker_key"],)).fetchall()}
                for name, seconds in windows.items():
                    if name in existing or elapsed < seconds:
                        continue
                    return_fraction = (observed_price - float(tracker["anchor_price"])) / float(tracker["anchor_price"])
                    con.execute("INSERT INTO event_reaction_measurements(tracker_key,window_name,target_seconds,observed_at,observed_seconds,observed_price,return_fraction) VALUES(?,?,?,?,?,?,?)",
                                (tracker["tracker_key"], name, int(seconds), now.isoformat(), elapsed, observed_price, return_fraction))
                    measured += 1
                count = con.execute("SELECT COUNT(*) FROM event_reaction_measurements WHERE tracker_key=?", (tracker["tracker_key"],)).fetchone()[0]
                if count >= len(windows):
                    rows = con.execute("SELECT window_name,target_seconds,observed_seconds,observed_price,return_fraction,observed_at FROM event_reaction_measurements WHERE tracker_key=? ORDER BY target_seconds", (tracker["tracker_key"],)).fetchall()
                    actual, forecast = tracker["actual"], tracker["forecast"]
                    surprise = float(actual) - float(forecast) if actual is not None and forecast is not None else None
                    cursor = con.execute("INSERT INTO event_reactions(event_source_id,symbol,observed_at,actual,forecast,previous,surprise,windows,source_quality) VALUES(?,?,?,?,?,?,?,?,?)",
                                         (tracker["event_source_id"], symbol, now.isoformat(), actual, forecast,
                                          tracker["previous"], surprise,
                                          self._json({row["window_name"]: dict(row) for row in rows}),
                                          tracker["source_quality"]))
                    reaction_id = int(cursor.lastrowid)
                    con.execute("UPDATE event_reaction_trackers SET status='COMPLETE',final_reaction_id=? WHERE tracker_key=?", (reaction_id, tracker["tracker_key"]))
                    completed += 1
        return {"scheduled": len(trackers), "measured": measured, "completed": completed, "observed_at": now.isoformat()}

    def recent_event_reactions(self, symbol: str = "", limit: int = 10, *, as_of: Any = None) -> List[Dict[str, Any]]:
        cutoff = self._normalize_as_of(as_of)
        with self._con() as con:
            if symbol:
                rows = con.execute("""SELECT r.*,s.title,s.published_at FROM event_reactions r
                    JOIN macro_sources s ON s.source_id=r.event_source_id
                    WHERE r.symbol=? AND r.observed_at<=? ORDER BY r.observed_at DESC LIMIT ?""", (symbol, cutoff, limit)).fetchall()
            else:
                rows = con.execute("""SELECT r.*,s.title,s.published_at FROM event_reactions r
                    JOIN macro_sources s ON s.source_id=r.event_source_id
                    WHERE r.observed_at<=? ORDER BY r.observed_at DESC LIMIT ?""", (cutoff, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["windows"] = json.loads(item["windows"])
            result.append(item)
        return result

    def due_post_event_reviews(self, symbol: str, *, hours: int = 48, limit: int = 20) -> List[Dict[str, Any]]:
        """Return completed reactions old enough for a 48-hour candidate review."""
        cutoff = datetime.now(timezone.utc).timestamp() - max(1, int(hours)) * 3600
        with self._con() as con:
            rows = con.execute("""SELECT r.*,s.title,s.published_at FROM event_reactions r
                JOIN macro_sources s ON s.source_id=r.event_source_id
                WHERE r.symbol=? ORDER BY r.observed_at DESC LIMIT ?""", (symbol, limit)).fetchall()
        due = []
        for row in rows:
            observed = self._parse_time(row["observed_at"])
            if observed and observed.timestamp() <= cutoff:
                item = dict(row)
                item["windows"] = json.loads(item["windows"] or "{}")
                due.append(item)
        return due

    def similar_event_reactions(self, symbol: str, *, actual: Any = None,
                                forecast: Any = None, previous: Any = None,
                                limit: int = 5, as_of: Any = None) -> Dict[str, Any]:
        """Retrieve dated reactions by quantitative surprise fingerprint."""
        cutoff = self._normalize_as_of(as_of)
        current_actual, current_forecast, current_previous = map(self._number, (actual, forecast, previous))
        current_surprise = current_actual - current_forecast if current_actual is not None and current_forecast is not None else None
        with self._con() as con:
            rows = con.execute("""SELECT r.*,s.title,s.published_at,s.source_url FROM event_reactions r
                JOIN macro_sources s ON s.source_id=r.event_source_id WHERE r.symbol=? AND r.observed_at<=?
                ORDER BY r.observed_at DESC LIMIT 300""", (symbol, cutoff)).fetchall()
        ranked = []
        for row in rows:
            surprise = row["surprise"]
            if current_surprise is None or surprise is None:
                score = 0.0
            else:
                scale = max(abs(current_surprise), abs(float(surprise)), 1e-9)
                score = 1.0 - min(1.0, abs(current_surprise - float(surprise)) / scale)
                if (current_surprise >= 0) != (float(surprise) >= 0):
                    score *= 0.25
            item = dict(row)
            item["windows"] = json.loads(item["windows"] or "{}")
            item["fingerprint_score"] = round(score, 6)
            item["difference"] = {"surprise_delta": (float(surprise) - current_surprise) if surprise is not None and current_surprise is not None else None,
                                  "actual_delta": (float(row["actual"]) - current_actual) if row["actual"] is not None and current_actual is not None else None}
            ranked.append(item)
        return {"symbol": symbol, "current": {"actual": current_actual, "forecast": current_forecast,
                    "previous": current_previous, "surprise": current_surprise},
                "results": sorted(ranked, key=lambda item: item["fingerprint_score"], reverse=True)[:max(0, limit)],
                "retrieval_as_of": cutoff,
                "uncertainty": "Similarity is descriptive; regime, liquidity, source quality, and timing differences must be reviewed.",
                "execution_authority": False}

    def prune_stale_edges(
        self,
        max_age_days: int = 90,
        min_confidence: float = 0.20,
        actor: str = "operator:stale_pruner",
    ) -> Dict[str, Any]:
        """Prune stale or low-confidence macro causal edges.

        Scans active/candidate edges in ``macro_edges`` and marks them PRUNED if:
        - The edge has not been updated in more than ``max_age_days``, OR
        - The edge confidence is below ``min_confidence``.

        Each pruned edge appends an audit record to ``macro_edge_history`` with
        the reason and previous state.

        Returns a dictionary summarizing scanned and pruned edge counts.
        """
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        actor = str(actor or "operator:stale_pruner").strip()
        pruned_ids: List[int] = []

        with self._con() as con:
            rows = con.execute(
                "SELECT edge_id, updated_at, status, confidence, relation, sign FROM macro_edges WHERE status IN ('APPROVED', 'CANDIDATE', 'AUTO_APPROVED')"
            ).fetchall()

            for row in rows:
                updated_dt = self._parse_time(row["updated_at"])
                age_days = (now_dt - updated_dt).total_seconds() / 86400.0 if updated_dt else 999999.0
                conf = float(row["confidence"]) if row["confidence"] is not None else 0.0

                is_stale_age = age_days > max_age_days
                is_low_conf = conf < min_confidence

                if is_stale_age or is_low_conf:
                    reason_parts = []
                    if is_stale_age:
                        reason_parts.append(f"age_{age_days:.1f}d_exceeds_{max_age_days}d")
                    if is_low_conf:
                        reason_parts.append(f"confidence_{conf:.2f}_below_{min_confidence:.2f}")
                    reason = "; ".join(reason_parts)

                    edge_id = int(row["edge_id"])
                    con.execute(
                        "UPDATE macro_edges SET status='PRUNED', updated_at=?, review_by=?, reviewed_at=? WHERE edge_id=?",
                        (now_str, actor[:200], now_str, edge_id),
                    )
                    con.execute(
                        "INSERT INTO macro_edge_history(edge_id, changed_at, action, status, reviewer, payload) VALUES(?,?,?,?,?,?)",
                        (
                            edge_id,
                            now_str,
                            "PRUNED",
                            "PRUNED",
                            actor[:200],
                            self._json({
                                "reason": reason,
                                "previous_status": row["status"],
                                "confidence": conf,
                                "age_days": round(age_days, 2),
                                "relation": row["relation"],
                                "sign": row["sign"],
                            }),
                        ),
                    )
                    pruned_ids.append(edge_id)

        return {
            "scanned_count": len(rows),
            "pruned_count": len(pruned_ids),
            "pruned_edge_ids": pruned_ids,
            "max_age_days": max_age_days,
            "min_confidence": min_confidence,
            "actor": actor,
            "execution_authority": False,
        }

    def ingest_live_release_surprise(
        self,
        event_title: str,
        currency: str,
        actual: float,
        forecast: float,
        previous: Optional[float] = None,
        timestamp: Optional[Any] = None,
        publisher: str = "official calendar",
        source_url: str = "",
        tolerance: float = 0.0001,
    ) -> Dict[str, Any]:
        """Ingest an empirical macroeconomic release surprise and classify its shock.

        Computes delta surprise = actual - forecast.
        Categorizes shock as:
        - IN_LINE (abs(surprise) <= tolerance)
        - HAWKISH_SHOCK (surprise > tolerance)
        - DOVISH_SHOCK (surprise < -tolerance)

        Ingests source into ``macro_sources`` with structured metadata and policy assessment.
        Returns a dictionary containing source_id, surprise metrics, and shock categorization.
        Execution authority is strictly False.
        """
        if not isinstance(event_title, str) or not event_title.strip():
            raise ValueError("event_title is required")
        if not isinstance(currency, str) or not currency.strip():
            raise ValueError("currency is required")

        actual_val = self._number(actual)
        forecast_val = self._number(forecast)
        prev_val = self._number(previous)

        if actual_val is None or forecast_val is None:
            raise ValueError("actual and forecast values must be valid finite numbers")

        surprise = actual_val - forecast_val
        if abs(surprise) <= tolerance:
            shock_category = "IN_LINE"
        elif surprise > tolerance:
            shock_category = "HAWKISH_SHOCK"
        else:
            shock_category = "DOVISH_SHOCK"

        event_time = self._parse_time(timestamp) or datetime.now(timezone.utc)
        event_time_iso = event_time.isoformat()

        title = f"Release: {currency.upper().strip()} {event_title.strip()} [Actual: {actual_val}, Forecast: {forecast_val}]"
        payload = {
            "event_title": event_title.strip(),
            "currency": currency.upper().strip(),
            "actual": actual_val,
            "forecast": forecast_val,
            "previous": prev_val,
            "surprise": round(surprise, 6),
            "shock_category": shock_category,
            "event_time": event_time_iso,
        }

        source_id = self.ingest_source(
            title=title,
            payload=payload,
            source_url=source_url,
            publisher=publisher,
            published_at=event_time_iso,
        )

        return {
            "source_id": source_id,
            "event_title": event_title.strip(),
            "currency": currency.upper().strip(),
            "actual": actual_val,
            "forecast": forecast_val,
            "previous": prev_val,
            "surprise": round(surprise, 6),
            "shock_category": shock_category,
            "published_at": event_time_iso,
            "execution_authority": False,
        }
