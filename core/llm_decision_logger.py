"""LLM Decision Logger — SQLite-backed LLM decision logging for audit trail."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_DB_PATH = Path("trading_state.db")


class LLMDecisionLogger:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path) if db_path else _DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _ensure_table(self):
        try:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    model TEXT,
                    prompt_text TEXT,
                    response_text TEXT,
                    latency_ms REAL,
                    symbol TEXT,
                    timeframe TEXT,
                    outcome TEXT,
                    trade_ticket INTEGER
                )
            """)
            conn.commit()
        except Exception as e:
            log.debug("LLMDecisionLogger table init failed: %s", e)

    def log_decision(self, model: str = "", prompt_text: str = "", response_text: str = "",
                     latency_ms: float = 0, symbol: str = "", timeframe: str = "") -> int:
        try:
            conn = self._get_conn()
            cur = conn.execute(
                "INSERT INTO llm_decisions (ts, model, prompt_text, response_text, latency_ms, symbol, timeframe) VALUES (?,?,?,?,?,?,?)",
                (time.time(), model, prompt_text[:2000], response_text[:2000], latency_ms, symbol, timeframe),
            )
            conn.commit()
            return cur.lastrowid or 0
        except Exception as e:
            log.debug("Decision log failed: %s", e)
            return 0

    def update_outcome(self, decision_id: int, outcome: str, trade_ticket: int = 0):
        try:
            conn = self._get_conn()
            conn.execute("UPDATE llm_decisions SET outcome=?, trade_ticket=? WHERE id=?", (outcome, trade_ticket, decision_id))
            conn.commit()
        except Exception as e:
            log.debug("Decision outcome update failed: %s", e)
