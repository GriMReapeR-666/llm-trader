# core/quantitative_metrics_service.py
"""Modular QuantitativeMetricsEngine Runtime Lifecycle & State Persistence Service.

Provides a dedicated, decoupled lifecycle manager for QuantitativeMetricsEngine.
Manages persistent state across restarts, performs incremental deduplicated
synchronization with the durable trade journal, tracks live equity curves,
and exposes institutional performance and risk metrics (EV, SQN, DSR, Kelly).
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from core.metrics import QuantitativeMetricsEngine


logger = logging.getLogger("QuantitativeMetricsService")


class QuantitativeMetricsService:
    """Standalone lifecycle service managing QuantitativeMetricsEngine state and live syncing."""

    def __init__(
        self,
        store: Any = None,
        initial_balance: float = 0.0,
        risk_free_rate: float = 0.04,
        state_file: Optional[str] = None,
        auto_load: bool = True,
    ):
        self.store = store
        self.initial_balance = float(initial_balance)
        self.risk_free_rate = float(risk_free_rate)
        self.state_file = state_file
        self.synced_tickets: Set[int] = set()
        self.last_sync_time: Optional[str] = None

        self.engine = QuantitativeMetricsEngine(
            initial_balance=self.initial_balance,
            risk_free_rate=self.risk_free_rate,
        )

        if auto_load:
            self.load_state()

    def update_equity(self, current_equity: float, save: bool = True) -> None:
        """Record a live equity observation and update peak/drawdown tracking."""
        try:
            eq = float(current_equity)
        except (TypeError, ValueError):
            return
        if not math.isfinite(eq) or eq <= 0:
            return

        self.engine.update_equity(eq)
        if save:
            self.save_state()

    def record_closed_trade(
        self,
        ticket: int,
        pnl: float,
        r_multiple: Optional[float] = None,
        risk_amount: Optional[float] = None,
        save: bool = True,
    ) -> bool:
        """Record a single closed trade, deduplicating by ticket number."""
        try:
            t_int = int(ticket)
        except (TypeError, ValueError):
            return False

        if t_int in self.synced_tickets:
            return False

        self.engine.record_trade(pnl=pnl, r_multiple=r_multiple, risk_amount=risk_amount)
        self.synced_tickets.add(t_int)
        if save:
            self.save_state()
        return True

    def sync_from_journal(self, store: Any = None, limit: int = 500) -> int:
        """Incrementally sync new closed trades from the durable journal.

        Queries broker-reconciled trades with closed_at IS NOT NULL; a
        self-declared journal row is not evidence. Ingests honest parameters:
        pnl, label r_multiple (if available), and planned risk_amount.
        Returns the number of new trades synced.
        """
        target_store = store or self.store
        if target_store is None:
            return 0

        new_count = 0
        try:
            db_path = str(getattr(target_store, "path", "") or "")
            if not db_path:
                return 0
            try:
                from research.research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            tickets = sorted(broker_reconciled_tickets(db_path))
            if not tickets:
                logger.debug("Journal sync skipped: no broker-reconciled trades")
                return 0
            marks = ",".join("?" for _ in tickets)
            with target_store._connect() as con:
                has_labels = bool(
                    con.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_labels'"
                    ).fetchone()
                )
                if has_labels:
                    query = """
                        SELECT t.ticket AS ticket, t.pnl AS pnl, t.risk_amount AS risk_amount,
                               t.closed_at AS closed_at,
                               (SELECT l.r_multiple FROM trade_labels l WHERE l.ticket = t.ticket LIMIT 1) AS label_r
                        FROM trades t
                        WHERE t.closed_at IS NOT NULL
                          AND t.ticket IN (%s)
                        ORDER BY t.closed_at ASC
                        LIMIT ?
                    """ % marks
                else:
                    query = """
                        SELECT t.ticket AS ticket, t.pnl AS pnl, t.risk_amount AS risk_amount,
                               t.closed_at AS closed_at,
                               NULL AS label_r
                        FROM trades t
                        WHERE t.closed_at IS NOT NULL
                          AND t.ticket IN (%s)
                        ORDER BY t.closed_at ASC
                        LIMIT ?
                    """ % marks
                rows = con.execute(query, (*tickets, int(limit))).fetchall()

            for row in rows:
                try:
                    ticket = int(row["ticket"])
                except (TypeError, ValueError):
                    continue

                if ticket in self.synced_tickets:
                    continue

                pnl = float(row["pnl"] or 0.0)
                risk_amt = float(row["risk_amount"] or 0.0) if row["risk_amount"] is not None else None
                r_mult = float(row["label_r"]) if row["label_r"] is not None else None

                self.engine.record_trade(pnl=pnl, r_multiple=r_mult, risk_amount=risk_amt)
                self.synced_tickets.add(ticket)
                new_count += 1

            if new_count > 0:
                self.last_sync_time = datetime.now(timezone.utc).isoformat()
                self.save_state()

        except Exception as exc:
            logger.debug("Journal sync skipped or encountered non-fatal issue: %s", exc)

        return new_count

    def get_metrics_summary(self, backtest_trials: int = 1) -> Dict[str, Any]:
        """Compute institutional metrics summary (EV, SQN, Sharpe, Sortino, Kelly)."""
        summary = self.engine.compute_summary(backtest_trials=backtest_trials)
        summary["synced_trades_count"] = len(self.synced_tickets)
        summary["last_sync_time"] = self.last_sync_time
        return summary

    def to_dict(self) -> Dict[str, Any]:
        """Serialize complete service state including engine state and synced tickets."""
        return {
            "engine_state": self.engine.to_state(),
            "synced_tickets": sorted(list(self.synced_tickets))[-2000:],  # keep tail
            "last_sync_time": self.last_sync_time,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def restore_from_dict(self, data: Dict[str, Any]) -> None:
        """Restore service and engine state from dictionary."""
        if not isinstance(data, dict):
            return
        engine_state = data.get("engine_state")
        if isinstance(engine_state, dict):
            self.engine.restore_state(engine_state)

        tickets = data.get("synced_tickets", [])
        if isinstance(tickets, (list, set, tuple)):
            self.synced_tickets = {int(t) for t in tickets if isinstance(t, (int, str)) and str(t).isdigit()}

        self.last_sync_time = data.get("last_sync_time")

    def save_state(self) -> bool:
        """Persist state to TradingStore (primary) or local state file (fallback)."""
        payload = self.to_dict()

        # 1. Primary persistence: TradingStore
        if self.store is not None:
            try:
                if hasattr(self.store, "set_state"):
                    self.store.set_state("quantitative_metrics_service", payload)
                    return True
                elif hasattr(self.store, "update_state"):
                    self.store.update_state("quantitative_metrics_service", lambda _: payload)
                    return True
            except Exception as exc:
                logger.warning("Failed saving metrics state to store: %s", exc)

        # 2. Secondary fallback: local atomic JSON file
        if self.state_file:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
                temp_dir = os.path.dirname(os.path.abspath(self.state_file))
                with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False) as tf:
                    json.dump(payload, tf, indent=2)
                    temp_path = tf.name
                os.replace(temp_path, self.state_file)
                return True
            except Exception as exc:
                logger.warning("Failed saving metrics state to file %s: %s", self.state_file, exc)

        return False

    def load_state(self) -> bool:
        """Load state from TradingStore (primary) or local state file (fallback)."""
        # 1. Try TradingStore
        if self.store is not None:
            try:
                if hasattr(self.store, "get_state"):
                    data = self.store.get_state("quantitative_metrics_service", None)
                    if isinstance(data, dict) and "engine_state" in data:
                        self.restore_from_dict(data)
                        return True
            except Exception as exc:
                logger.debug("Store metrics state load non-fatal issue: %s", exc)

        # 2. Try file fallback
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "engine_state" in data:
                    self.restore_from_dict(data)
                    return True
            except Exception as exc:
                logger.debug("File metrics state load non-fatal issue: %s", exc)

        return False
