"""Deterministic broker-to-journal daily reconciliation.

This compares broker facts with the authoritative local journal; it never
creates missing trades, closes records by assumption, or treats manual broker
activity as bot activity.  Differences are durable operator work items.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from trading_safety import SCALP_MAGIC, SWING_MAGIC


BOT_MAGICS = {SWING_MAGIC, SCALP_MAGIC}

# Provenance of the "broker" side of a reconciliation.
#
# ``research_validation.broker_reconciled_tickets()`` admits a journal close
# only when something *outside* the journal vouches for it.  Adapting the
# journal's own ``trades`` rows into "broker" deals (the ``--paper`` CLI path)
# therefore compares the journal against itself: it can never disagree, and it
# can never be evidence.  Such input is labelled so this module refuses to mint
# reconciled evidence from it instead of laundering a self-asserted close into
# a positive expectancy.
PAPER_BROKER_SOURCE = "paper_broker"
JOURNAL_DERIVED_SOURCE = "journal_closed_rows"
RECONCILED_CLOSE_EVENT = "RECONCILED_CLOSE"

# A journal ``trades`` row always carries these; a PaperBroker close record
# carries none of them (it has ``price_close``/``close_time`` instead).
_JOURNAL_ROW_FIELDS = ("closed_at", "opened_at", "exit_price", "exit_reason", "setup_id", "payload")
_BROKER_CLOSE_FIELDS = ("price_close", "close_time", "price_open")


def _value(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _number(item: Any, name: str) -> float | None:
    raw = _value(item, name, None)
    if raw is None or raw == "":
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _position_id(deal: Any) -> int | None:
    for field in ("position_id", "position", "ticket"):
        try:
            value = int(_value(deal, field, 0) or 0)
            if value:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _deal_time(deal: Any) -> datetime | None:
    """Return a UTC deal timestamp from MT5-like or JSON snapshot fields."""
    raw_msc = _value(deal, "time_msc", None)
    if raw_msc not in (None, "", 0, "0"):
        try:
            value = float(raw_msc)
            if value != value or abs(value) == float("inf"):
                return None
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    raw_epoch = _value(deal, "time", None)
    if raw_epoch not in (None, "", 0, "0"):
        try:
            value = float(raw_epoch)
            if value != value or abs(value) == float("inf"):
                return None
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    raw_text = _value(deal, "timestamp", _value(deal, "deal_time", None))
    if raw_text not in (None, ""):
        try:
            parsed = raw_text if isinstance(raw_text, datetime) else datetime.fromisoformat(str(raw_text).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _window(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("reconciliation window must contain datetime values")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _read_only_uri(path: str) -> str:
    """``file:`` URI for a read-only open.

    Built through pathlib rather than string concatenation: a journal path
    containing ``?`` or ``#`` would otherwise truncate the URI at the query
    boundary and silently open the wrong database.
    """
    from pathlib import Path
    return Path(os.path.abspath(path)).as_uri()


def _evidence_recorded(store: Any, intent_key: str) -> Optional[bool]:
    """True/False when the ledger can be read; None when it cannot.

    ``broker_execution_reports`` is append-only (SQLite triggers reject
    UPDATE/DELETE), so re-running reconciliation would otherwise pile up a
    duplicate evidence row per pass.  When the ledger cannot be inspected we
    answer ``None`` and the caller skips the write: a duplicate is a recoverable
    annoyance, a fabricated broker ticket is not.
    """
    path = getattr(store, "path", None)
    if not isinstance(path, str) or not os.path.exists(path):
        return None
    try:
        with sqlite3.connect(_read_only_uri(path) + "?mode=ro", uri=True, timeout=5) as con:
            row = con.execute(
                "SELECT 1 FROM broker_execution_reports WHERE intent_key=? LIMIT 1", (intent_key,)
            ).fetchone()
        return row is not None
    except (sqlite3.Error, OSError, ValueError):
        return None


def _mint_reconciled_evidence(
    store: Any,
    matched: Dict[int, int],
    broker: Dict[int, Dict[str, Any]],
    local: Dict[int, Any],
    discrepant: set[int],
) -> Dict[str, int]:
    """Append broker execution evidence for positions the journal and broker agree on.

    ``research_validation.broker_reconciled_tickets()`` treats this table as the
    only independent proof a journal close was confirmed outside the journal.
    Nothing in the reconciliation path wrote it, so every downstream
    broker-reconciled gate (CPCV ``>=40`` net-of-cost trades, WFA ``>=20``
    reconciled R-multiples) stayed shut even when reconciliation had just proved
    the two sides agree.

    This is not a relaxation of those gates.  A row is written only for a
    position that MATCHED a journal ticket on ticket *and* on net P&L;
    unmatched broker activity and P&L mismatches stay operator work items, and
    journal-replayed "deals" are refused outright because they prove nothing
    about the journal's own honesty.
    """
    tally = {"written": 0, "already_recorded": 0, "refused_discrepant": 0,
             "refused_journal_derived": 0, "unverifiable_ledger": 0}
    record = getattr(store, "record_execution_report", None)
    if not callable(record):
        return tally
    for position_ticket, local_ticket in sorted(matched.items()):
        if int(position_ticket) in discrepant:
            tally["refused_discrepant"] += 1
            continue
        detail = broker.get(position_ticket) or {}
        # A position whose deals came back out of the journal is not evidence.
        if any(str(_value(deal, "reconciliation_source", "") or "") == JOURNAL_DERIVED_SOURCE
               for deal in detail.get("deals", [])):
            tally["refused_journal_derived"] += 1
            continue
        intent_key = f"recon:{position_ticket}:{local_ticket}"
        recorded = _evidence_recorded(store, intent_key)
        if recorded is None:
            tally["unverifiable_ledger"] += 1
            continue
        if recorded:
            tally["already_recorded"] += 1
            continue
        broker_pnl = round(float(detail.get("net_pnl", 0.0)), 8)
        journal_pnl = round(float(local[local_ticket]["pnl"] or 0), 8)
        try:
            record(
                event_type=RECONCILED_CLOSE_EVENT,
                intent_key=intent_key,
                order_ticket=int(local_ticket) if int(local_ticket) == int(position_ticket) else None,
                deal_ticket=detail.get("deal_ticket"),
                position_ticket=int(position_ticket),
                filled_price=detail.get("filled_price"),
                filled_volume=detail.get("filled_volume"),
                commission=float(detail.get("commission", 0.0)),
                swap=float(detail.get("swap", 0.0)),
                payload={
                    "manager": "daily_reconciliation",
                    "local_ticket": int(local_ticket),
                    "broker_net_pnl": broker_pnl,
                    "journal_net_pnl": journal_pnl,
                    "pnl_difference": round(journal_pnl - broker_pnl, 8),
                    "deal_count": int(detail.get("deal_count", 0)),
                    "magic": int(_value(local[local_ticket], "magic", 0) or 0),
                    "source": str(detail.get("reconciliation_source") or "broker"),
                    "note": "broker/journal agreement on a closed position",
                },
            )
        except (TypeError, ValueError, OverflowError, sqlite3.Error, OSError):
            tally["unverifiable_ledger"] += 1
            continue
        tally["written"] += 1
    return tally


def compare_day(store: Any, broker_deals: Iterable[Any], start: datetime, end: datetime,
                *, require_timestamps: bool = False,
                emit_execution_evidence: bool = True) -> Dict[str, Any]:
    """Build an auditable discrepancy report for one UTC interval.

    Only deals tagged with this system's magic numbers are compared to the bot
    journal.  Non-bot activity is counted and reported separately, never
    absorbed into bot P&L or managed by the bot.

    ``emit_execution_evidence`` appends the broker-execution rows that
    downstream broker-reconciled gates read; set it False for a dry run that
    must not touch the ledger.
    """
    start_utc, end_utc = _window(start), _window(end)
    if end_utc <= start_utc:
        raise ValueError("reconciliation end must be after start")
    broker: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {"net_pnl": 0.0, "deal_count": 0, "close_seen": False, "deals": []})
    unowned = 0
    owned_seen = 0
    malformed_owned_deals: List[Dict[str, Any]] = []
    outside_window = 0
    missing_timestamp = 0
    for deal in broker_deals or []:
        magic = _value(deal, "magic")
        try:
            owned = int(magic) in BOT_MAGICS
        except (TypeError, ValueError):
            owned = False
        position = _position_id(deal)
        deal_time = _deal_time(deal)
        if deal_time is None:
            missing_timestamp += 1
            if require_timestamps and owned:
                owned_seen += 1
                malformed_owned_deals.append({"position_ticket": position,
                                              "reason": "missing_or_invalid_deal_timestamp"})
                continue
        elif not (start_utc <= deal_time < end_utc):
            outside_window += 1
            continue
        if not owned:
            unowned += 1
            continue
        owned_seen += 1
        if position is None:
            malformed_owned_deals.append({"reason": "missing_or_invalid_position_ticket"})
            continue
        values = {name: _number(deal, name) for name in ("profit", "commission", "swap", "fee")}
        if any(value is None for value in values.values()):
            malformed_owned_deals.append({"position_ticket": position,
                                          "reason": "nonfinite_or_malformed_cost_or_pnl"})
            continue
        detail = broker[position]
        detail["net_pnl"] += sum(float(value) for value in values.values())
        detail["deal_count"] += 1
        # Last close deal wins for the fill facts; they only describe how the
        # broker closed, and the net P&L above stays the reconciled quantity.
        detail["deal_ticket"] = _as_int(_value(deal, "deal_ticket", _value(deal, "ticket", None)))
        detail["filled_price"] = _number(deal, "price")
        detail["filled_volume"] = _number(deal, "volume")
        detail["commission"] = float(values["commission"])
        detail["swap"] = float(values["swap"])
        detail["reconciliation_source"] = str(_value(deal, "reconciliation_source", "") or "")
        detail["deals"].append(deal)
        # MT5 DEAL_ENTRY_OUT is normally 1.  Accept textual snapshots so this
        # remains replayable without importing MetaTrader5.
        entry = _value(deal, "entry")
        if require_timestamps and entry in (None, ""):
            malformed_owned_deals.append({"position_ticket": position,
                                          "reason": "missing_deal_entry_marker"})
            continue
        detail["close_seen"] = detail["close_seen"] or entry == 1 or str(entry).upper() in {"OUT", "DEAL_ENTRY_OUT"}

    local_rows = store.closed_trade_rows_between(start.isoformat(), end.isoformat())
    local = {int(row["ticket"]): row for row in local_rows}
    broker_closed = {ticket: detail for ticket, detail in broker.items() if detail["close_seen"]}
    # A broker position ticket is not guaranteed to equal the order/deal
    # ticket stored by the journal.  Resolve through the append-only execution
    # ledger when possible, and fail closed on ambiguous mappings.
    broker_to_local: Dict[int, int] = {}
    missing_journal: List[int] = []
    mapping_details: List[Dict[str, Any]] = []
    matched_local: set[int] = set()
    for position_ticket in sorted(broker_closed):
        candidates: List[int] = []
        if position_ticket in local:
            candidates = [position_ticket]
        else:
            resolver = getattr(store, "trade_tickets_for_position", None)
            if callable(resolver):
                try:
                    candidates = [int(value) for value in (resolver(position_ticket) or [])]
                except (TypeError, ValueError):
                    candidates = []
            candidates = [value for value in candidates if value in local]
        candidates = sorted(set(candidates))
        if len(candidates) == 1 and candidates[0] not in matched_local:
            broker_to_local[position_ticket] = candidates[0]
            matched_local.add(candidates[0])
            mapping_details.append({"position_ticket": position_ticket,
                                    "local_ticket": candidates[0], "status": "MATCHED"})
        else:
            missing_journal.append(position_ticket)
            mapping_details.append({"position_ticket": position_ticket,
                                    "candidate_local_tickets": candidates,
                                    "status": "AMBIGUOUS_OR_MISSING"})
    absent_broker = sorted(ticket for ticket in local if ticket not in matched_local)
    pnl_differences: List[Dict[str, Any]] = []
    for position_ticket, local_ticket in sorted(broker_to_local.items()):
        broker_pnl = round(float(broker_closed[position_ticket]["net_pnl"]), 8)
        journal_pnl = round(float(local[local_ticket]["pnl"] or 0), 8)
        if abs(broker_pnl - journal_pnl) > 0.01:
            pnl_differences.append({"position_ticket": position_ticket,
                                    "local_ticket": local_ticket,
                                    "broker_net_pnl": broker_pnl,
                                    "journal_net_pnl": journal_pnl,
                                    "difference": round(journal_pnl - broker_pnl, 8)})
    discrepancies = {
        "broker_closed_missing_journal": missing_journal,
        "journal_closed_missing_broker": absent_broker,
        "matched_position_net_pnl_differences": pnl_differences,
        "position_ticket_mapping": mapping_details,
        "malformed_owned_deals": malformed_owned_deals,
        "missing_or_invalid_timestamps": missing_timestamp if require_timestamps else 0,
        "outside_window_deal_count": outside_window,
    }
    clean = not missing_journal and not absent_broker and not pnl_differences and not malformed_owned_deals
    if not clean and hasattr(store, "record_execution_review"):
        if missing_journal:
            for pt in missing_journal:
                store.record_execution_review(f"recon_missing_journal_{pt}", pt, "HIGH", f"Broker position {pt} closed but missing in local journal", {"position_ticket": pt})
        if absent_broker:
            for lt in absent_broker:
                store.record_execution_review(f"recon_absent_broker_{lt}", lt, "HIGH", f"Local trade {lt} closed but absent in broker history", {"local_ticket": lt})
        if pnl_differences:
            for diff in pnl_differences:
                store.record_execution_review(f"recon_pnl_diff_{diff['local_ticket']}", diff['local_ticket'], "MEDIUM", f"P&L mismatch of {diff['difference']} for ticket {diff['local_ticket']}", diff)

    evidence = {"written": 0, "already_recorded": 0, "refused_discrepant": 0,
                "refused_journal_derived": 0, "unverifiable_ledger": 0}
    if emit_execution_evidence and broker_to_local:
        evidence = _mint_reconciled_evidence(
            store, broker_to_local, broker_closed, local,
            {int(item["position_ticket"]) for item in pnl_differences})

    report = {
        "period_start": start_utc.isoformat(),
        "period_end": end_utc.isoformat(),
        "status": "CLEAN" if clean else "ATTENTION",
        "broker": {
            "owned_deal_count": owned_seen,
            "valid_owned_deal_count": sum(item["deal_count"] for item in broker.values()),
            "owned_closed_positions": len(broker_closed),
            "owned_net_pnl": round(sum(item["net_pnl"] for item in broker_closed.values()), 8),
            "unowned_or_manual_deal_count": unowned,
            "malformed_owned_deal_count": len(malformed_owned_deals),
            "missing_or_invalid_timestamp_count": missing_timestamp if require_timestamps else 0,
            "outside_window_deal_count": outside_window,
        },
        "reconciled_execution_evidence": evidence,
        "journal": {"closed_trade_count": len(local), "net_pnl": round(sum(float(row["pnl"] or 0) for row in local.values()), 8)},
        "discrepancies": discrepancies,
        "period_filter": {"require_timestamps": bool(require_timestamps),
                          "start": start_utc.isoformat(), "end": end_utc.isoformat()},
        "execution_authority": False,
    }
    report["alert_payload"] = format_discrepancy_alert(report)
    store.record_daily_reconciliation(report)
    return report


def format_discrepancy_alert(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Format an operational alert payload if reconciliation found discrepancies.

    Returns None if status is CLEAN. When ATTENTION is flagged, produces a structured
    alert dictionary with severity and categorized discrepancy counts.
    """
    if not isinstance(report, dict) or report.get("status") == "CLEAN":
        return None
    discrepancies = report.get("discrepancies", {})
    missing_j = discrepancies.get("broker_closed_missing_journal", [])
    absent_b = discrepancies.get("journal_closed_missing_broker", [])
    pnl_diffs = discrepancies.get("matched_position_net_pnl_differences", [])
    malformed = discrepancies.get("malformed_owned_deals", [])

    return {
        "event_type": "RECONCILIATION_DISCREPANCY",
        "severity": "CRITICAL" if (missing_j or absent_b) else "WARNING",
        "period_start": report.get("period_start"),
        "period_end": report.get("period_end"),
        "summary": (
            f"Daily reconciliation flagged ATTENTION: "
            f"{len(missing_j)} missing in journal, {len(absent_b)} absent from broker, "
            f"{len(pnl_diffs)} P&L differences, {len(malformed)} malformed deals."
        ),
        "details": discrepancies,
        "execution_authority": False,
    }


def _infer_paper_source(item: Any) -> str:
    """Classify an adapted record as a real paper fill or a replayed journal row.

    Deliberately conservative: anything carrying journal fields is treated as
    journal-derived even if it also looks like a broker record, because the
    failure mode that matters is minting evidence from the journal itself.
    """
    present = {name for name in _JOURNAL_ROW_FIELDS if _value(item, name, None) not in (None, "")}
    if present:
        return JOURNAL_DERIVED_SOURCE
    if any(_value(item, name, None) not in (None, "") for name in _BROKER_CLOSE_FIELDS):
        return PAPER_BROKER_SOURCE
    return JOURNAL_DERIVED_SOURCE


def adapt_paper_deals(paper_history: Iterable[Any], *, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Adapt paper broker closed history records into standard reconciliation deals.

    Translates paper position dictionaries or objects into MT5-like deal structures
    so paper trading sessions can be audited against TradingStore journal trades with
    the exact same mathematical rigor as live broker deals.

    ``source`` overrides provenance detection; when omitted each record is
    classified by :func:`_infer_paper_source` and stamped onto the deal as
    ``reconciliation_source`` so :func:`compare_day` can tell an independent
    broker record from one replayed out of the journal it is meant to check.
    """
    import time
    deals: List[Dict[str, Any]] = []
    for item in paper_history or []:
        ticket = _value(item, "ticket") or _value(item, "position") or _value(item, "order")
        if not ticket:
            continue
        try:
            pos_id = int(ticket)
        except (TypeError, ValueError):
            continue
        magic = int(_value(item, "magic", 0) or 0)
        profit = float(_value(item, "profit", 0.0) or 0.0)
        commission = float(_value(item, "commission", 0.0) or 0.0)
        swap = float(_value(item, "swap", 0.0) or 0.0)
        fee = float(_value(item, "fee", 0.0) or 0.0)
        t = _value(item, "close_time") or _value(item, "time") or _value(item, "timestamp") or time.time()

        deals.append({
            "position_id": pos_id,
            "ticket": pos_id,
            "magic": magic,
            "profit": profit,
            "commission": commission,
            "swap": swap,
            "fee": fee,
            "entry": 1,  # DEAL_ENTRY_OUT
            "time": t,
            "symbol": str(_value(item, "symbol", "")),
            "volume": float(_value(item, "volume", 0.0) or 0.0),
            "price": _value(item, "price_close", _value(item, "price")),
            "reconciliation_source": str(source or _infer_paper_source(item)),
        })
    return deals

