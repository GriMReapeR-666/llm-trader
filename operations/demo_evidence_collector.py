"""Demo-account evidence collector.

The research gates want a *broker-reconciled* track record: a sample of
closed trades whose fills were witnessed outside the journal itself.  This
module is the piece that accumulates that sample for the demo (paper) venue:

* every venue fill -- open and close -- is journalled with
  ``paper_broker`` provenance and the venue's own numbers (price, volume,
  realized P&L), so a fill that happens while the engine's reconciler is
  asleep is still on record;
* every captured fill is reconciled against a broker history, which is what
  turns a self-asserted close into broker-confirmed evidence;
* the accumulated sample is reported against the 200-trade floor with the
  statistics a reviewer needs: expectancy, win rate, R-multiples, drawdown.

Three rules keep the sample honest.

1. A fill is recorded exactly as the venue reported it.  Nothing here invents
   a price, a volume, a deal ticket or a P&L; a venue record that lacks a
   field leaves that field empty.
2. The journal never vouches for itself.  Reconciling journal rows against
   journal rows proves nothing, so journal-derived input is refused as
   "broker history" (see ``operations/daily_reconciliation.py``).
3. The collector fills gaps; it does not fight the engine for ownership.  The
   trade row is written only when the engine has not written it yet, and the
   setup-lifecycle state machine stays with the engine's reconciler, which is
   the component that owns setup transitions.

Provenance is declared at the write site: every row this module writes names
``paper_broker``, so a demo fill can never be read as a broker fill or as a
fixture.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.provenance import validate_data_source

#: The provenance token every demo fill is journalled with.
PAPER_PROVENANCE = "paper_broker"

#: The sample floor the research gates ask for.
TARGET_TRADES = 200

#: Venue-fill event types written to the append-only execution ledger.
FILL_OPEN_EVENT = "PAPER_FILL_OPEN"
FILL_CLOSE_EVENT = "PAPER_FILL_CLOSE"

#: The event type the engine's own paper bridge writes for a close.  The
#: collector must not add a second attestation for a fill that is already
#: attested -- the ledger is evidence, and duplicated evidence reads as
#: duplicated fills.
ENGINE_FILL_EVENT = "PAPER_FILL"

#: Two P&L numbers within this many account units describe the same close.
PNL_TOLERANCE = 0.01


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _iso(stamp: float) -> str:
    """UTC ISO timestamp for an epoch second (the venue's clock)."""
    try:
        return datetime.fromtimestamp(float(stamp), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(timezone.utc).isoformat()


def _price_tolerance(price: float) -> float:
    """How close a close price must be to a level to count as *that* level."""
    return max(1e-9, abs(float(price)) * 1e-6)


@dataclass(frozen=True)
class VenueFill:
    """One fill, exactly as the venue reported it.

    Nothing in here is derived from the journal: the venue saw the fill, so
    the venue's numbers are the ones recorded.
    """

    ticket: int
    symbol: str
    side: str
    volume: float
    price: float
    open_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    profit: float
    magic: int
    status: str
    filled_at: float
    open_time: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_close(self) -> bool:
        return self.status != "OPEN"


def normalize_fill(fill: Any) -> Optional[VenueFill]:
    """Turn a venue fill record into a :class:`VenueFill`.

    Returns ``None`` when the record cannot identify a fill -- no ticket, no
    volume or no price.  A malformed record is never repaired: a fill whose
    price is unreadable is not a fill that can be evidenced, and guessing one
    would put a fabricated number into the track record.
    """
    if fill is None:
        return None
    ticket = _as_int(_value(fill, "ticket", 0) or _value(fill, "position", 0)
                     or _value(fill, "position_id", 0) or _value(fill, "order", 0), 0)
    volume = _as_float(_value(fill, "volume", 0.0), 0.0)
    open_price = _as_float(_value(fill, "price_open", 0.0), 0.0)
    close_price = _as_float(_value(fill, "price_close", 0.0), 0.0)
    price = _as_float(_value(fill, "price", 0.0), 0.0)
    status = str(_value(fill, "status", "") or "").upper()
    if close_price:
        status = status or "CLOSED"
    else:
        status = status or "OPEN"
    if status in ("CLOSED", "PARTIAL"):
        fill_price = close_price or price or open_price
    else:
        fill_price = price or open_price
    if ticket <= 0 or volume <= 0 or fill_price <= 0:
        return None
    filled_at = _value(fill, "filled_at", None)
    if filled_at is None:
        filled_at = _value(fill, "close_time", None)
    if filled_at is None:
        filled_at = _value(fill, "time", None)
    try:
        filled_at = float(filled_at) if filled_at not in (None, "") else time.time()
    except (TypeError, ValueError, OverflowError):
        filled_at = time.time()
    stop_loss = _value(fill, "sl", None)
    take_profit = _value(fill, "tp", None)
    open_time = _value(fill, "open_time", None)
    if open_time in (None, ""):
        open_time = _value(fill, "opened_at", None)
    if open_time in (None, ""):
        open_time = None
    else:
        try:
            open_time = float(open_time)
        except (TypeError, ValueError, OverflowError):
            open_time = None
    return VenueFill(
        ticket=int(ticket),
        symbol=str(_value(fill, "symbol", "") or ""),
        side=str(_value(fill, "side", "") or "").upper(),
        volume=float(volume),
        price=float(fill_price),
        open_price=float(open_price or fill_price),
        stop_loss=float(stop_loss) if stop_loss not in (None, "") else None,
        take_profit=float(take_profit) if take_profit not in (None, "") else None,
        profit=_as_float(_value(fill, "profit", 0.0), 0.0),
        magic=_as_int(_value(fill, "magic", 0), 0),
        status=status,
        filled_at=float(filled_at),
        open_time=open_time,
        raw=dict(fill) if isinstance(fill, dict) else {},
    )


class DemoEvidenceCollector:
    """Accumulate a broker-reconciled demo track record.

    The collector is fed venue fills (``observe_fill``) and reconciles them
    against a broker history (``reconcile``); ``track_record`` reports what
    has been accumulated so far against the 200-trade floor.

    It is deliberately tolerant on input and strict on output: a malformed
    fill is rejected and counted, a fill that cannot be attributed to an
    intent is journalled but flagged as unattributed, and nothing is ever
    written that claims evidence the venue did not give.
    """

    def __init__(self, store: Any, *, target_trades: int = TARGET_TRADES,
                 provenance: str = PAPER_PROVENANCE) -> None:
        self.store = store
        self.target_trades = max(1, int(target_trades))
        self.provenance = validate_data_source(provenance, kind="journal", field="provenance")
        #: Fills the collector refused, with the reason.  Never silently lost.
        self.rejected: List[Dict[str, Any]] = []
        #: What the collector saw, keyed by ticket.
        self.captured: Dict[int, Dict[str, Any]] = {}
        self._memory: Any = None

    # ------------------------------------------------------------------
    # capture
    # ------------------------------------------------------------------
    def observe_fill(self, fill: Any) -> Dict[str, Any]:
        """Venue entry point: journal one fill.

        Never raises.  A venue must not lose a fill because the journal is
        unhappy, and a rejected fill must not disappear silently either: it
        is counted in :attr:`rejected`.
        """
        try:
            record = normalize_fill(fill)
        except Exception as exc:  # pragma: no cover - defensive
            self._reject(fill, f"malformed venue record: {exc}")
            return {"status": "REJECTED", "reason": "malformed_venue_record"}
        if record is None:
            self._reject(fill, "incomplete venue record")
            return {"status": "REJECTED", "reason": "incomplete_venue_record"}
        try:
            if record.is_close:
                return self.record_close_fill(record)
            return self.record_open_fill(record)
        except Exception as exc:
            self._reject(record.raw or {"ticket": record.ticket}, f"journal write failed: {exc}")
            return {"status": "ERROR", "ticket": record.ticket, "reason": str(exc)}

    def record_open_fill(self, fill: VenueFill) -> Dict[str, Any]:
        """Journal the opening fill of a demo position."""
        intent = self._resolve_intent(fill.ticket)
        proposal = self._resolve_proposal(intent)
        row = self._trade_row(fill.ticket)
        wrote_row = False
        if row is None:
            wrote_row = self._write_open_row(fill, intent, proposal)
        evidence = self._write_fill_evidence(FILL_OPEN_EVENT, fill, (FILL_OPEN_EVENT,))
        self.captured[fill.ticket] = {
            "ticket": fill.ticket, "status": "OPEN", "symbol": fill.symbol,
            "side": fill.side, "entry": fill.price, "volume": fill.volume,
            "opened_at": fill.filled_at, "pnl": 0.0,
            "attributed": bool(intent),
        }
        return {"status": "JOURNALLED", "ticket": fill.ticket, "trade_row": wrote_row,
                "venue_evidence": evidence, "attributed": bool(intent)}

    def record_close_fill(self, fill: VenueFill) -> Dict[str, Any]:
        """Journal the closing fill of a demo position."""
        intent = self._resolve_intent(fill.ticket)
        proposal = self._resolve_proposal(intent)
        row = self._trade_row(fill.ticket)
        wrote_row = False
        if row is None:
            # The engine never journalled this position (it may have been
            # running when the fill happened).  The venue's own record is
            # enough to reconstruct it -- minus the decision context, which
            # is why the row is flagged unattributed below.
            wrote_row = self._write_open_row(fill, intent, proposal)
            row = self._trade_row(fill.ticket)
        # The intent is linked to the ticket after the submission returns,
        # which is usually after the fill.  Pick the decision up now so the
        # close is labelled against the risk that was actually approved.
        self._enrich_row(fill.ticket, intent, proposal, row)
        row = self._trade_row(fill.ticket)
        closed = self._write_close_row(fill, row)
        evidence = self._write_fill_evidence(
            FILL_CLOSE_EVENT, fill, (FILL_CLOSE_EVENT, ENGINE_FILL_EVENT))
        previous = self.captured.get(fill.ticket, {})
        self.captured[fill.ticket] = {
            "ticket": fill.ticket, "status": fill.status, "symbol": fill.symbol,
            "side": fill.side, "entry": previous.get("entry", fill.open_price),
            "exit": fill.price, "volume": fill.volume, "opened_at": previous.get("opened_at", fill.filled_at),
            "closed_at": fill.filled_at, "pnl": fill.profit,
            "attributed": bool(intent),
        }
        return {"status": "JOURNALLED", "ticket": fill.ticket, "trade_row": wrote_row,
                "closed": closed, "venue_evidence": evidence, "attributed": bool(intent)}

    # ------------------------------------------------------------------
    # reconciliation
    # ------------------------------------------------------------------
    def reconcile(self, broker_deals: Iterable[Any], *,
                  start: Optional[datetime] = None,
                  end: Optional[datetime] = None,
                  source: str = "broker") -> Dict[str, Any]:
        """Reconcile the captured fills against a broker history.

        ``broker_deals`` is the outside record: the deal list of an MT5
        account (a real demo account) or, in paper mode, the venue's own book
        adapted to the same shape.  Two checks run:

        * the day-level comparison in ``operations.daily_reconciliation``,
          which mints broker-confirmed evidence for positions the journal and
          the broker agree on and turns every disagreement into an operator
          work item;
        * a fill-level check of the fills this collector captured, so a fill
          the broker never saw is reported as unwitnessed instead of quietly
          counting towards the track record.

        The fill-level check is session-scoped: it compares what *this*
        process witnessed against the broker history.  The day-level
        comparison reads the journal and therefore covers every session, so a
        restart never loses the reconciliation -- only the per-fill witness
        detail, which is reported as such.
        """
        from operations.daily_reconciliation import compare_day

        deals = list(broker_deals or [])
        end_utc = end or datetime.now(timezone.utc)
        start_utc = start or (end_utc - timedelta(days=1))
        report = compare_day(self.store, deals, start_utc, end_utc,
                              require_timestamps=False, emit_execution_evidence=True)
        broker_by_position = self._broker_positions(deals)
        matched: List[int] = []
        mismatched: List[Dict[str, Any]] = []
        unwitnessed: List[int] = []
        for ticket, captured in sorted(self.captured.items()):
            if captured.get("status") == "OPEN":
                continue
            journal_pnl = round(float(captured.get("pnl", 0.0) or 0.0), 8)
            broker_pnl = broker_by_position.get(ticket)
            if broker_pnl is None:
                unwitnessed.append(ticket)
                continue
            if abs(broker_pnl - journal_pnl) > PNL_TOLERANCE:
                mismatched.append({"ticket": ticket, "venue_net_pnl": journal_pnl,
                                   "broker_net_pnl": round(broker_pnl, 8),
                                   "difference": round(journal_pnl - broker_pnl, 8)})
            else:
                matched.append(ticket)
        broker_only = sorted(set(broker_by_position) - set(self.captured))
        return {
            "source": source,
            "period_start": start_utc.isoformat(),
            "period_end": end_utc.isoformat(),
            "broker_history": {
                "deal_count": len(deals),
                "position_count": len(broker_by_position),
            },
            "fills": {
                "scope": "session",
                "captured": len(self.captured),
                "closed": sum(1 for item in self.captured.values() if item.get("status") != "OPEN"),
                "matched": matched,
                "pnl_mismatches": mismatched,
                "unwitnessed": unwitnessed,
                "broker_positions_without_fill": broker_only,
            },
            "reconciliation": report,
            "execution_authority": False,
        }

    def reconcile_venue_history(self, venue_history: Iterable[Any], *,
                                start: Optional[datetime] = None,
                                end: Optional[datetime] = None) -> Dict[str, Any]:
        """Reconcile against the paper venue's own book.

        In paper mode there is no MT5 account to ask, so the venue's book is
        the outside record.  It is adapted with the same helper the daily
        reconciliation uses, which stamps each record with its provenance so
        a journal row replayed as a "deal" is refused rather than laundered
        into evidence.
        """
        from operations.daily_reconciliation import adapt_paper_deals

        deals = adapt_paper_deals(venue_history or [])
        return self.reconcile(deals, start=start, end=end, source="paper_venue_book")

    # ------------------------------------------------------------------
    # report
    # ------------------------------------------------------------------
    def track_record(self) -> Dict[str, Any]:
        """Report the accumulated demo track record against the 200-trade floor.

        The headline number is the *broker-reconciled* sample size: closed,
        labelled, broker-confirmed rows that a research gate would admit.
        Everything else -- expectancy, win rate, drawdown -- describes that
        sample and is never presented as a bigger one.
        """
        from research.research_validation import broker_reconciled_tickets, labelled_dataset

        path = getattr(self.store, "path", "trading_state.db")
        self._enrich_rows()
        rows = self._closed_rows()
        brokered = broker_reconciled_tickets(path)
        dataset = labelled_dataset(path)
        admissible = [row for row in dataset.get("rows", [])
                      if str(row.get("provenance") or "") == self.provenance]
        stats = _describe([{"pnl": row["pnl"], "risk": row["risk_amount"],
                            "r_multiple": row.get("r_multiple"),
                            "symbol": row.get("symbol", ""),
                            "setup_id": row.get("setup_id", ""),
                            "closed_at": row.get("closed_at", "")}
                           for row in admissible])
        reconciled = [row for row in rows if int(row["ticket"]) in brokered]
        unreconciled = [int(row["ticket"]) for row in rows if int(row["ticket"]) not in brokered]
        progress = len(admissible) / float(self.target_trades)
        return {
            "target_trades": self.target_trades,
            "provenance": self.provenance,
            "closed_fills": len(rows),
            "broker_reconciled_fills": len(reconciled),
            "unreconciled_fills": unreconciled,
            "admissible_rows": len(admissible),
            "progress": round(min(1.0, progress), 6),
            "remaining": max(0, self.target_trades - len(admissible)),
            "ready": len(admissible) >= self.target_trades,
            "statistics": stats,
            "excluded": dataset.get("excluded", {}),
            "captured_this_session": len(self.captured),
            "rejected_fills": self.rejected,
            "execution_authority": False,
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(getattr(self.store, "path", "trading_state.db"), timeout=10.0)
        con.row_factory = sqlite3.Row
        return con

    def _reject(self, fill: Any, reason: str) -> None:
        self.rejected.append({"reason": reason, "fill": fill if isinstance(fill, dict) else str(fill)})

    def _trade_row(self, ticket: int) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM trades WHERE ticket=?", (int(ticket),)).fetchone()
        return dict(row) if row else None

    def _closed_rows(self) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT ticket, symbol, strategy, setup_id, side, volume, entry, exit_price, "
                "pnl, risk_amount, closed_at, provenance FROM trades "
                "WHERE closed_at IS NOT NULL AND pnl IS NOT NULL AND provenance=? "
                "ORDER BY closed_at, ticket", (self.provenance,)).fetchall()
        return [dict(row) for row in rows]

    def _resolve_intent(self, ticket: int) -> Dict[str, Any]:
        """Find the order intent that produced this fill.

        The intent carries the setup key, the stop, the target and the risk
        amount the order was approved with.  A fill without an intent is
        still a fill -- it is simply not attributable to a decision, and the
        report says so instead of inventing a decision for it.
        """
        try:
            with self._connect() as con:
                row = con.execute(
                    "SELECT intent_key, status, payload FROM order_intents "
                    "WHERE mt5_ticket=? ORDER BY updated_at DESC LIMIT 1",
                    (int(ticket),)).fetchone()
        except sqlite3.Error:
            return {}
        if not row:
            return {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        return {"intent_key": row["intent_key"], "status": row["status"],
                "payload": payload if isinstance(payload, dict) else {}}

    def _resolve_proposal(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """The immutable hypothesis behind the intent, if it is still there."""
        setup_key = str((intent.get("payload") or {}).get("setup_key") or "")
        if not setup_key:
            return {}
        try:
            proposal = self.store.latest_proposal(setup_key)
        except Exception:
            return {}
        return proposal if isinstance(proposal, dict) else {}

    def _write_open_row(self, fill: VenueFill, intent: Dict[str, Any],
                        proposal: Dict[str, Any]) -> bool:
        """Write the trade row for a fill the engine has not journalled yet.

        The row is written directly instead of through
        ``TradingStore.record_open_trade`` because that method also drives
        the setup-lifecycle state machine.  This collector must never be the
        component that moves a setup instance: the engine's reconciler owns
        those transitions and raises when a state does not allow them, and a
        bookkeeping helper that can break the engine's state machine is worse
        than one that leaves a gap for the reconciler to fill.
        """
        payload_intent = intent.get("payload") or {}
        features = proposal.get("features") if isinstance(proposal.get("features"), dict) else {}
        entry = fill.price
        stop_loss = fill.stop_loss if fill.stop_loss else _as_float(payload_intent.get("sl"), 0.0)
        take_profit = fill.take_profit if fill.take_profit else _as_float(payload_intent.get("tp"), 0.0)
        volume = fill.volume
        risk_amount = _as_float(payload_intent.get("risk_amount"), 0.0)
        risk_source = "intent"
        if risk_amount <= 0 and stop_loss:
            # No approved risk to hand: fall back to the price distance the
            # position actually risked, and remember that it was derived so a
            # later enrichment can replace it with the approved number.
            risk_amount = abs(entry - stop_loss) * volume
            risk_source = "derived"
        symbol = fill.symbol or str(payload_intent.get("symbol") or "")
        side = fill.side or str(payload_intent.get("side") or "")
        strategy = str(payload_intent.get("strategy") or proposal.get("strategy") or "scalp")
        magic = fill.magic or _as_int(payload_intent.get("magic"), 0)
        if not magic:
            from core.trading_safety import SCALP_MAGIC, SWING_MAGIC
            magic = SWING_MAGIC if strategy == "swing" else SCALP_MAGIC
        setup_id = str(features.get("setup_id") or "unclassified_v1")
        payload = {
            "intent_key": str(intent.get("intent_key") or ""),
            "venue": PAPER_PROVENANCE,
            "observed_by": "demo_evidence_collector",
            "fill_price": entry,
            "proposal_features": features,
            "feature_schema_version": features.get("feature_schema_version"),
            "feature_schema_hash": features.get("feature_schema_hash"),
            "setup_id": setup_id,
            "risk_source": risk_source,
        }
        with self._connect() as con:
            cursor = con.execute(
                "INSERT OR IGNORE INTO trades(ticket,magic,strategy,symbol,side,volume,entry,"
                "stop_loss,take_profit,risk_amount,opened_at,payload,setup_id,provenance) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(fill.ticket), int(magic), strategy, symbol, side, float(volume), float(entry),
                 float(stop_loss) if stop_loss else None, float(take_profit) if take_profit else None,
                 float(risk_amount), _iso(fill.open_time if fill.open_time else fill.filled_at),
                 json.dumps(payload, default=str), setup_id, self.provenance))
            return bool(cursor.rowcount)

    def _enrich_rows(self) -> int:
        """Attach the decision context to rows written before the intent was linked.

        The venue fills inside the order submission, so the intent that
        explains the fill is usually recorded a moment later.  A row without
        the decision context is an execution fact but not research evidence,
        so the collector looks the intent up again once it exists.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM trades WHERE provenance=?", (self.provenance,)).fetchall()
        enriched = 0
        for row in rows:
            ticket = int(row["ticket"])
            intent = self._resolve_intent(ticket)
            if not intent:
                continue
            proposal = self._resolve_proposal(intent)
            if self._enrich_row(ticket, intent, proposal, dict(row)):
                enriched += 1
        return enriched

    def _enrich_row(self, ticket: int, intent: Dict[str, Any], proposal: Dict[str, Any],
                    row: Optional[Dict[str, Any]]) -> bool:
        """Attach the decision context (features, setup id, approved risk) to one row.

        Only the collector's own rows are touched: a row the engine wrote is
        the engine's record, and rewriting it would replace a decision
        context with a reconstruction of one.
        """
        if not row:
            return False
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("observed_by") != "demo_evidence_collector":
            return False
        features = proposal.get("features") if isinstance(proposal.get("features"), dict) else {}
        setup_id = str(features.get("setup_id") or payload.get("setup_id") or "")
        changed = False
        if features and not payload.get("proposal_features"):
            payload["proposal_features"] = features
            payload["feature_schema_version"] = features.get("feature_schema_version")
            payload["feature_schema_hash"] = features.get("feature_schema_hash")
            changed = True
        if intent.get("intent_key") and not payload.get("intent_key"):
            payload["intent_key"] = str(intent["intent_key"])
            changed = True
        approved_risk = _as_float((intent.get("payload") or {}).get("risk_amount"), 0.0)
        row_risk = _as_float(row.get("risk_amount"), 0.0)
        replace_risk = (approved_risk > 0 and payload.get("risk_source") == "derived"
                        and abs(approved_risk - row_risk) > 1e-12)
        if replace_risk:
            payload["risk_source"] = "intent"
        if not changed and not replace_risk:
            return False
        with self._connect() as con:
            if replace_risk:
                con.execute("UPDATE trades SET risk_amount=?, payload=?, setup_id=? WHERE ticket=?",
                            (approved_risk, json.dumps(payload, default=str),
                             setup_id or row.get("setup_id"), int(ticket)))
            else:
                con.execute("UPDATE trades SET payload=?, setup_id=? WHERE ticket=?",
                            (json.dumps(payload, default=str),
                             setup_id or row.get("setup_id"), int(ticket)))
        if replace_risk and row.get("closed_at"):
            # The label carries the R multiple, so it has to be recomputed
            # against the risk that was actually approved.
            self._relabel(row, approved_risk)
        return True

    def _relabel(self, row: Dict[str, Any], risk_amount: float) -> None:
        try:
            self.memory.label_closed_trade(
                int(row["ticket"]), float(row.get("pnl") or 0.0), float(risk_amount),
                str(row.get("exit_reason") or ""))
        except Exception:
            pass

    def _write_close_row(self, fill: VenueFill, row: Optional[Dict[str, Any]]) -> bool:
        """Close the journalled row with the venue's own numbers.

        The row is only closed once: if the engine's reconciler already
        closed it, this is a no-op, because two writers disagreeing about a
        close is worse than one writer being late.
        """
        if not row or row.get("closed_at"):
            return False
        exit_price = float(fill.price)
        pnl = float(fill.profit)
        reason = self._exit_reason(row, fill)
        closed_at = _iso(fill.filled_at)
        with self._connect() as con:
            cursor = con.execute(
                "UPDATE trades SET closed_at=?,exit_price=?,pnl=?,exit_reason=?,gross_pnl=? "
                "WHERE ticket=? AND closed_at IS NULL",
                (closed_at, exit_price, pnl, reason, pnl, int(fill.ticket)))
            if not cursor.rowcount:
                return False
        observed_at = datetime.fromtimestamp(fill.filled_at, tz=timezone.utc)
        try:
            self.store.record_position_observation(
                int(fill.ticket), exit_price, pnl, source="paper_venue_close",
                mark_quality="BROKER_EXECUTION", observed_at=observed_at)
        except Exception:
            pass
        try:
            self.store.log_lifecycle_event(int(fill.ticket), "", "CLOSED", "demo_evidence_collector", {
                "exit_price": exit_price, "pnl": pnl, "reason": reason,
                "venue": PAPER_PROVENANCE,
            })
        except Exception:
            pass
        risk_amount = _as_float(row.get("risk_amount"), 0.0)
        try:
            self.memory.label_closed_trade(int(fill.ticket), pnl, risk_amount, reason)
        except Exception:
            pass
        return True

    @staticmethod
    def _exit_reason(row: Dict[str, Any], fill: VenueFill) -> str:
        """Name the exit from the venue's own numbers.

        The stop and the target on the trade row are the levels the venue was
        told to honour, so a close at either of them *is* a stop or a target
        exit.  Anything else is a plain venue close.  The wording matters:
        the deterministic labeller reads "target" and "stop" out of it.
        """
        exit_price = float(fill.price)
        stop = _as_float(row.get("stop_loss"), 0.0)
        target = _as_float(row.get("take_profit"), 0.0)
        if target and abs(exit_price - target) <= _price_tolerance(target):
            return "paper venue take profit"
        if stop and abs(exit_price - stop) <= _price_tolerance(stop):
            return "paper venue stop loss"
        return "paper venue close"

    def _write_fill_evidence(self, event_type: str, fill: VenueFill,
                             known_event_types: Sequence[str]) -> bool:
        """Append the venue's fill facts to the execution ledger.

        ``broker_execution_reports`` is append-only and is the one table
        ``research_validation.broker_reconciled_tickets`` accepts as proof
        that a fill happened outside the journal's own word, so the venue
        record belongs here.  The ledger is append-only, so a fill that is
        already attested for this ticket is left alone.
        """
        if self._venue_evidence_exists(fill.ticket, known_event_types):
            return False
        self.store.record_execution_report(
            event_type=event_type,
            intent_key=f"{event_type.lower()}:{int(fill.ticket)}",
            position_ticket=int(fill.ticket),
            filled_price=float(fill.price),
            filled_volume=float(fill.volume),
            retcode=10009,
            payload={
                "manager": "demo_evidence_collector",
                "venue": PAPER_PROVENANCE,
                "provenance": self.provenance,
                "symbol": fill.symbol,
                "side": fill.side,
                "magic": int(fill.magic),
                "price_open": float(fill.open_price),
                "venue_net_pnl": float(fill.profit),
                "status": fill.status,
                "filled_at": float(fill.filled_at),
                "note": "paper venue fill, independent of the journal trades table",
            },
        )
        return True

    def _venue_evidence_exists(self, ticket: int, event_types: Sequence[str]) -> bool:
        """Has this fill already been attested for this ticket?"""
        types = tuple(event_types) or (FILL_OPEN_EVENT,)
        placeholders = ",".join("?" for _ in types)
        try:
            with self._connect() as con:
                row = con.execute(
                    "SELECT 1 FROM broker_execution_reports WHERE position_ticket=? "
                    f"AND event_type IN ({placeholders}) LIMIT 1",
                    (int(ticket), *types)).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    @staticmethod
    def _broker_positions(deals: Iterable[Any]) -> Dict[int, float]:
        """Net P&L per position from a broker deal list."""
        positions: Dict[int, float] = {}
        for deal in deals or []:
            ticket = 0
            for name in ("position_id", "position", "ticket"):
                ticket = _as_int(_value(deal, name, 0), 0)
                if ticket:
                    break
            if not ticket:
                continue
            net = (_as_float(_value(deal, "profit", 0.0), 0.0)
                   + _as_float(_value(deal, "commission", 0.0), 0.0)
                   + _as_float(_value(deal, "swap", 0.0), 0.0)
                   + _as_float(_value(deal, "fee", 0.0), 0.0))
            positions[ticket] = positions.get(ticket, 0.0) + net
        return positions

    @property
    def memory(self) -> Any:
        """The journal's memory store, for the deterministic trade label."""
        if self._memory is None:
            from intelligence.trading_memory import TradingMemory
            self._memory = TradingMemory(getattr(self.store, "path", "trading_state.db"))
        return self._memory


def _describe(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Expectancy, win rate and drawdown for a set of closed trades."""
    if not rows:
        return {"count": 0, "net_pnl": 0.0, "win_rate": 0.0, "expectancy": 0.0,
                "expectancy_r": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
                "best": 0.0, "worst": 0.0}
    ordered = sorted(rows, key=lambda item: (str(item.get("closed_at") or ""), str(item.get("symbol") or "")))
    pnls = [float(item.get("pnl") or 0.0) for item in ordered]
    rs = []
    for item in ordered:
        r_multiple = item.get("r_multiple")
        if r_multiple is None:
            risk = float(item.get("risk") or 0.0)
            r_multiple = float(item.get("pnl") or 0.0) / risk if risk > 0 else 0.0
        rs.append(float(r_multiple))
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "count": len(pnls),
        "net_pnl": round(sum(pnls), 8),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(pnls) - len(wins) - len(losses),
        "win_rate": round(len(wins) / float(len(pnls)), 6),
        "expectancy": round(sum(pnls) / float(len(pnls)), 8),
        "expectancy_r": round(sum(rs) / float(len(rs)), 8),
        "profit_factor": round(gross_win / gross_loss, 8) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "max_drawdown": round(max_drawdown, 8),
        "best": round(max(pnls), 8),
        "worst": round(min(pnls), 8),
    }


def demo_track_record(path: str = "trading_state.db", *,
                      target_trades: int = TARGET_TRADES) -> Dict[str, Any]:
    """Report the demo track record of a journal without collecting anything.

    Read-only from the operator's point of view: the journal is opened the
    way the engine opens it, and nothing is written to it.
    """
    from core.trading_safety import TradingStore

    collector = DemoEvidenceCollector(TradingStore(path), target_trades=target_trades)
    return collector.track_record()


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Demo-account evidence collector")
    parser.add_argument("--db", default=os.getenv("LLM_TRADER_JOURNAL_PATH", "trading_state.db"))
    parser.add_argument("--target", type=int, default=TARGET_TRADES,
                        help="sample floor the track record is measured against")
    args = parser.parse_args(argv)
    report = demo_track_record(args.db, target_trades=args.target)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
