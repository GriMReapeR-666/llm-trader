"""Journal-driven research gates and chronological validation utilities."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
import sqlite3
from typing import Any, Dict, List

MIN_RESEARCH_SAMPLE = 100
# Hard floors for research eligibility (still never grants execution authority).
MIN_PROMOTION_EXPECTANCY_R = 0.1   # Must have positive edge (> 0.1R avg) to promote
MIN_PROMOTION_PROFIT_FACTOR = 1.2
MIN_PROMOTION_SHARPE = 0.5         # Minimum Sharpe ratio for promotion
MIN_PROMOTION_WIN_RATE = 0.40      # Minimum win rate (40%)
MAX_PROMOTION_STRESS_RUIN = 0.25
MAX_PROMOTION_STRESS_DD_P95 = 0.35

try:
    from research.adaptive_models import fractional_bayesian_kelly
except ImportError:
    from research.adaptive_models import fractional_bayesian_kelly

try:
    from operations.journal_audit import audit
except ImportError:
    from operations.journal_audit import audit

try:
    from intelligence.feature_schema import SCHEMA_HASHES, values_hash
except ImportError:
    from intelligence.feature_schema import SCHEMA_HASHES, values_hash

try:
    from core.provenance import SYNTHETIC_SOURCES, is_synthetic
    from core.provenance import validate_data_source as _validate_source
except ImportError:  # pragma: no cover - flat-layout fallback
    from provenance import SYNTHETIC_SOURCES, is_synthetic
    from provenance import validate_data_source as _validate_source

try:
    from operations.provenance_quarantine import quarantined_tickets
except ImportError:  # pragma: no cover - flat-layout fallback
    from provenance_quarantine import quarantined_tickets



def _con(path: str):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def walk_forward_splits(rows: List[Dict[str, Any]], train_minimum: int = 100, test_size: int = 25, step: int = 25) -> List[Dict[str, List[int]]]:
    """Chronological folds only; callers must still embargo overlapping labels."""
    if (not isinstance(rows, list)
            or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
                   for value in (train_minimum, test_size, step))):
        return []
    folds = []
    for end in range(train_minimum, len(rows) - test_size + 1, step):
        folds.append({"train": list(range(0, end)), "test": list(range(end, end + test_size))})
    return folds


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _interval(row: Dict[str, Any]) -> tuple[datetime, datetime]:
    """The label spans opening through closure; that is the leakage horizon."""
    start = _time(row.get("label_start", row.get("opened_at", row.get("closed_at"))))
    end = _time(row.get("label_end", row.get("closed_at", row.get("opened_at"))))
    return (start, end) if start <= end else (end, start)


def _finite_feature_values(values: Any) -> bool:
    """Require a flat, finite point-in-time scalar feature snapshot."""
    if not isinstance(values, dict):
        return False
    for value in values.values():
        if value is None or isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return False
    return True


# Single source of truth: core/provenance.py. Kept as a module-level name
# because callers and tests import it from here.
SYNTHETIC_SOURCE_MARKERS = set(SYNTHETIC_SOURCES)
# Backtest-engine rows are fixtures by construction. The canonical journal
# gained 50 all-winner EURUSD rows (tickets 200000+, Jan 1-5 2025) from a
# backtest fixture run; admitting them as live evidence inflated walk-forward
# Sharpe and every meta IS/OOS comparison. Those markers are in the set above.


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _broker_confirmed_tickets(con: Any, tables: Any) -> Optional[set]:
    """Tickets the journal independently records as submitted/reconciled.

    Returns the set of tickets with positive broker evidence, or ``None`` when
    no reconciliation source exists at all -- the caller then admits nothing.
    Evidence is the order-intent submission record (``mt5_ticket``) or an
    execution report carrying the broker's own ticket. A row that merely claims
    ``source="broker"`` is not evidence; only these tables are.
    """
    confirmed: set = set()
    found_source = False
    if 'order_intents' in tables:
        found_source = True
        for row in con.execute(
                "SELECT mt5_ticket FROM order_intents WHERE mt5_ticket IS NOT NULL"):
            ticket = _as_int(row[0])
            if ticket is not None:
                confirmed.add(ticket)
    if 'broker_execution_reports' in tables:
        found_source = True
        for column in ("order_ticket", "position_ticket", "deal_ticket"):
            for row in con.execute(
                    f"SELECT {column} FROM broker_execution_reports "
                    f"WHERE {column} IS NOT NULL"):
                ticket = _as_int(row[0])
                if ticket is not None:
                    confirmed.add(ticket)
    return confirmed if found_source else None


def _is_synthetic_row(payload: Any, exit_reason: Any, provenance: Any = None) -> bool:
    """Detect fixture rows (defense in depth).

    A row is synthetic when any of these hold: the ``trades.provenance``
    column names a fixture source, the payload carries a truthy ``synthetic``
    flag, its declared source is a known fixture generator, or the close reason
    is the generator's reserved marker. The column is checked first because it
    is written under a schema constraint; the payload heuristics remain for
    rows written before that column existed.
    """
    if is_synthetic(provenance):
        return True
    if isinstance(payload, dict):
        if payload.get("synthetic") is True:
            return True
        source = payload.get("source")
        if isinstance(source, str) and source.strip().lower() in SYNTHETIC_SOURCE_MARKERS:
            return True
        features = payload.get("proposal_features")
        if isinstance(features, dict):
            feature_source = features.get("source")
            if (isinstance(feature_source, str)
                    and feature_source.strip().lower() in SYNTHETIC_SOURCE_MARKERS):
                return True
    if isinstance(exit_reason, str) and exit_reason.strip().lower() == "synthetic_close":
        return True
    return False


def broker_reconciled_tickets(path: str = "trading_state.db") -> set:
    """Public accessor: tickets with independent broker evidence.

    Returns an empty set when the journal records no reconciliation source at
    all. Callers MUST treat empty as "no admissible evidence" rather than as
    "no rows to worry about" - a journal row asserting ``source="broker"`` is
    self-declared and proves nothing. Any promotion, safety or performance gate
    that reads the ``trades`` table directly bypasses this and will happily grade
    unverified or synthetic rows.
    """
    con = _con(path)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return _broker_confirmed_tickets(con, tables) or set()
    finally:
        con.close()


def labelled_dataset(path: str = "trading_state.db") -> Dict[str, Any]:
    """Extract closed, deterministically labelled, point-in-time research rows.

    Rows without a broker-reconciled close, deterministic label, or a known
    feature schema are excluded and counted.  Feature payloads are copied from
    the proposal snapshot captured before entry; no close-time information is
    allowed into the feature vector.

    Synthetic fixture rows (``research/demo_sample_generator.py``) are always
    excluded regardless of schema/label validity so random data can never
    satisfy sample gates or promotion evidence.
    """
    if not os.path.exists(path):
        return {"rows": [], "excluded": {"database_missing": 1}, "sample_size": 0}
    excluded: Dict[str, int] = defaultdict(int)
    output: List[Dict[str, Any]] = []
    with _con(path) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'trades' not in tables:
            return {"rows": [], "excluded": {"trades_table_missing": 1}, "sample_size": 0,
                    "point_in_time": True, "cost_model": "journal broker-reconciled fields only"}
        # Labels are created by the durable close/reconciliation path.  A
        # missing table must fail closed rather than treating unlabeled trades
        # as negative examples.
        if 'trade_labels' not in tables:
            return {"rows": [], "excluded": {"trade_labels_table_missing": 1}, "sample_size": 0,
                    "point_in_time": True, "cost_model": "journal broker-reconciled fields only"}
        # Broker reconciliation must be PROVEN, not self-declared. A trade row
        # asserting payload.source="broker" and a well-formed feature snapshot
        # proves nothing: every field on the row is written by the same party
        # that would be forging it. The only independent evidence is the
        # journal's own submission record. Absent that, admit nothing -- a
        # fabricated close otherwise becomes a positive expectancy that
        # suppresses the non-positive-expectancy risk throttle.
        brokered = _broker_confirmed_tickets(con, tables)
        if brokered is None:
            return {"rows": [], "excluded": {"no_broker_reconciliation_source": 1},
                    "sample_size": 0, "point_in_time": True,
                    "cost_model": "journal broker-reconciled fields only"}
        # Rows whose provenance was never declared are set aside explicitly
        # (operations/provenance_quarantine.py).  A quarantined row is not
        # evidence: it can neither satisfy a sample floor nor contribute a
        # return, and it is counted under its own name so the operator can
        # see the difference between "starved" and "quarantined".
        quarantined = quarantined_tickets(path)
        rows = con.execute("""SELECT t.*, l.label, l.r_multiple AS label_r, l.label_rule
            FROM trades t LEFT JOIN trade_labels l ON l.ticket=t.ticket
            WHERE t.closed_at IS NOT NULL AND t.pnl IS NOT NULL ORDER BY t.opened_at, t.ticket""").fetchall()
        has_provenance = "provenance" in {r[1] for r in con.execute("PRAGMA table_info(trades)")}
    for row in rows:
        if _as_int(row["ticket"]) in quarantined:
            excluded["quarantined_row"] += 1
            continue
        if _as_int(row["ticket"]) not in brokered:
            excluded["unbrokered_trade"] += 1
            continue
        if not row["label"]:
            excluded["missing_deterministic_label"] += 1
            continue
        try:
            opened_at = _time(row["opened_at"])
            closed_at = _time(row["closed_at"])
        except (TypeError, ValueError, OverflowError):
            excluded["malformed_label_horizon"] += 1
            continue
        if closed_at < opened_at:
            excluded["invalid_label_horizon"] += 1
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            excluded["malformed_trade_payload"] += 1
            continue
        provenance = row["provenance"] if has_provenance else None
        if _is_synthetic_row(payload, row["exit_reason"], provenance):
            excluded["synthetic_row_excluded"] += 1
            continue
        features = payload.get("proposal_features") or {}
        version = features.get("feature_schema_version")
        feature_hash = features.get("feature_schema_hash")
        if version not in SCHEMA_HASHES or feature_hash != SCHEMA_HASHES[version]:
            excluded["unknown_or_legacy_feature_schema"] += 1
            continue
        feature_values = features.get("feature_values") or {}
        if not feature_values or features.get("feature_values_hash") != values_hash(feature_values):
            excluded["missing_or_mismatched_point_in_time_values_hash"] += 1
            continue
        if not _finite_feature_values(feature_values):
            excluded["nonfinite_or_malformed_point_in_time_values"] += 1
            continue
        completed_bar_time = features.get("completed_bar_time")
        if completed_bar_time:
            try:
                if _time(completed_bar_time) > opened_at:
                    excluded["future_feature_timestamp"] += 1
                    continue
            except (TypeError, ValueError, OverflowError):
                excluded["malformed_feature_timestamp"] += 1
                continue
        try:
            risk = float(row["risk_amount"] or 0)
            pnl = float(row["pnl"])
        except (TypeError, ValueError, OverflowError):
            excluded["nonfinite_trade_outcome"] += 1
            continue
        if not math.isfinite(risk) or risk <= 0 or not math.isfinite(pnl):
            excluded["invalid_risk_amount"] += 1
            continue
        try:
            label_r = float(row["label_r"]) if row["label_r"] is not None else pnl / risk
        except (TypeError, ValueError, OverflowError, ZeroDivisionError):
            excluded["nonfinite_trade_outcome"] += 1
            continue
        if not math.isfinite(label_r):
            excluded["nonfinite_trade_outcome"] += 1
            continue
        output.append({
            "ticket": int(row["ticket"]), "strategy": row["strategy"], "symbol": row["symbol"],
            "side": row["side"], "setup_id": row["setup_id"] or "legacy_unclassified",
            "opened_at": opened_at.isoformat(), "closed_at": closed_at.isoformat(),
            "label_start": opened_at.isoformat(), "label_end": closed_at.isoformat(),
            "label": row["label"], "label_rule": row["label_rule"],
            "provenance": provenance,
            "r_multiple": label_r,
            "pnl": pnl, "risk_amount": risk,
            "feature_schema_version": version, "feature_schema_hash": feature_hash,
            "feature_values_hash": features.get("feature_values_hash"),
            "features": features,
        })
    return {"rows": output, "excluded": dict(excluded), "sample_size": len(output),
            "point_in_time": True, "cost_model": "journal broker-reconciled fields only"}


def purged_embargoed_splits(rows: List[Dict[str, Any]], folds: int = 5, embargo_seconds: int = 0) -> List[Dict[str, List[int]]]:
    """K-fold CV that removes overlapping label horizons and a future embargo.

    Rows must be in chronological order and expose `opened_at`/`closed_at` (or
    label_start/label_end).  Returned index sets make the purge auditable.
    """
    if (not isinstance(rows, list) or not isinstance(folds, int) or isinstance(folds, bool)
            or folds < 2 or len(rows) < folds
            or not isinstance(embargo_seconds, int) or isinstance(embargo_seconds, bool)
            or embargo_seconds < 0):
        return []
    try:
        intervals = [_interval(row) for row in rows]
    except (TypeError, ValueError, OverflowError):
        return []
    result = []
    for fold in range(folds):
        start = fold * len(rows) // folds
        end = (fold + 1) * len(rows) // folds
        test = list(range(start, end))
        test_start = min(intervals[index][0] for index in test)
        test_end = max(intervals[index][1] for index in test)
        embargo_end = test_end + timedelta(seconds=max(0, int(embargo_seconds)))
        train, purged, embargoed = [], [], []
        for index, (label_start, label_end) in enumerate(intervals):
            if index in test:
                continue
            if label_start <= test_end and label_end >= test_start:
                purged.append(index)
            elif test_end < label_start <= embargo_end:
                embargoed.append(index)
            else:
                train.append(index)
        result.append({"train": train, "test": test, "purged": purged, "embargoed": embargoed})
    return result


def purged_walk_forward_splits(rows: List[Dict[str, Any]], train_minimum: int = 100,
                               test_size: int = 25, step: int = 25, embargo_seconds: int = 0) -> List[Dict[str, List[int]]]:
    """Chronological walk-forward folds with explicit overlapping-label purge."""
    if (not isinstance(rows, list) or not isinstance(embargo_seconds, int)
            or isinstance(embargo_seconds, bool) or embargo_seconds < 0):
        return []
    try:
        intervals = [_interval(row) for row in rows]
    except (TypeError, ValueError, OverflowError):
        return []
    result = []
    for raw in walk_forward_splits(rows, train_minimum, test_size, step):
        test_start = min(intervals[index][0] for index in raw["test"])
        test_end = max(intervals[index][1] for index in raw["test"])
        embargo_end = test_end + timedelta(seconds=max(0, int(embargo_seconds)))
        train, purged, embargoed = [], [], []
        for index in raw["train"]:
            label_start, label_end = intervals[index]
            if label_start <= test_end and label_end >= test_start:
                purged.append(index)
            elif test_end < label_start <= embargo_end:
                embargoed.append(index)
            else:
                train.append(index)
        result.append({"train": train, "test": raw["test"], "purged": purged, "embargoed": embargoed})
    return result


def setup_metrics(path: str = "trading_state.db") -> List[Dict[str, Any]]:
    # Use the leakage-safe labelled dataset, not raw closed rows. Legacy or
    # unlabelled trades must never appear eligible for research promotion.
    dataset = labelled_dataset(path)
    rows = dataset.get("rows", [])
    groups = defaultdict(list)
    for row in rows:
        groups[(row["strategy"], row["symbol"], row["side"], row["setup_id"])].append(dict(row))
    result = []
    for (strategy, symbol, side, setup_id), group in groups.items():
        rs = [float(r["r_multiple"]) for r in group if float(r["risk_amount"] or 0) > 0]
        wins, losses = [r for r in rs if r > 0], [r for r in rs if r < 0]
        average_win, average_loss = (sum(wins) / len(wins) if wins else 0.0), (abs(sum(losses) / len(losses)) if losses else 0.0)
        kelly = fractional_bayesian_kelly(len(wins), len(losses), average_win, average_loss)
        gross_wins, gross_losses = sum(wins), abs(sum(losses))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else None
        result.append({"strategy": strategy, "symbol": symbol, "side": side, "setup_id": setup_id, "closed_trades": len(group),
                       "r_observations": len(rs), "expectancy_r": sum(rs) / len(rs) if rs else 0.0,
                       "win_rate": len(wins) / len(rs) if rs else 0.0, "kelly": kelly,
                       "profit_factor": profit_factor,
                       "gross_wins_r": gross_wins, "gross_losses_r": gross_losses,
                       "walk_forward_folds": len(walk_forward_splits(group)),
                       "purged_walk_forward_folds": len(purged_walk_forward_splits(group)),
                       "point_in_time": True,
                       "cost_model": dataset.get("cost_model", "journal broker-reconciled fields only")})
    return result


def _setup_floor_reasons(metric: Dict[str, Any], *, minimum_closed: int) -> List[str]:
    """Deterministic numeric floors for promotion eligibility (advisory only)."""
    reasons: List[str] = []
    if not isinstance(metric, dict):
        return ["metric_malformed"]
    try:
        closed_trades = int(metric.get("closed_trades") or 0)
        observations = int(metric.get("r_observations") or 0)
    except (TypeError, ValueError, OverflowError):
        return ["metric_sample_count_malformed"]
    if closed_trades < minimum_closed:
        reasons.append(f"closed_trades<{minimum_closed}")
    if observations < minimum_closed:
        reasons.append(f"r_observations<{minimum_closed}")
    if int(metric.get("purged_walk_forward_folds") or 0) <= 0:
        reasons.append("purged_walk_forward_folds<=0")
    try:
        expectancy = float(metric.get("expectancy_r") or 0.0)
    except (TypeError, ValueError, OverflowError):
        expectancy = float("nan")
    if not math.isfinite(expectancy) or expectancy <= MIN_PROMOTION_EXPECTANCY_R:
        reasons.append(f"expectancy_r<={MIN_PROMOTION_EXPECTANCY_R}")
    profit_factor = metric.get("profit_factor")
    if profit_factor is None:
        # No losses in sample: require strictly positive expectancy already checked;
        # still demand at least one win observation for a defined edge.
        try:
            gross_wins = float(metric.get("gross_wins_r") or 0.0)
        except (TypeError, ValueError, OverflowError):
            gross_wins = float("nan")
        if not math.isfinite(gross_wins) or gross_wins <= 0:
            reasons.append("profit_factor_undefined_without_wins")
    else:
        try:
            pf = float(profit_factor)
        except (TypeError, ValueError, OverflowError):
            reasons.append("profit_factor_nonfinite")
        else:
            if not math.isfinite(pf) or pf < MIN_PROMOTION_PROFIT_FACTOR:
                reasons.append(f"profit_factor<{MIN_PROMOTION_PROFIT_FACTOR}")
    # Sharpe ratio floor
    try:
        sharpe = float(metric.get("sharpe_ratio") or 0.0)
    except (TypeError, ValueError, OverflowError):
        sharpe = float("nan")
    if not math.isfinite(sharpe) or sharpe < MIN_PROMOTION_SHARPE:
        reasons.append(f"sharpe_ratio<{MIN_PROMOTION_SHARPE}")
    # Win rate floor
    try:
        win_rate = float(metric.get("win_rate") or 0.0)
    except (TypeError, ValueError, OverflowError):
        win_rate = float("nan")
    if not math.isfinite(win_rate) or win_rate < MIN_PROMOTION_WIN_RATE:
        reasons.append(f"win_rate<{MIN_PROMOTION_WIN_RATE}")
    return reasons


def _stress_floor_reasons(stress: Dict[str, Any]) -> List[str]:
    """Require cost-buffered stress not to imply catastrophic path risk."""
    status = str(stress.get("status") or "")
    if status == "INSUFFICIENT_DATA":
        return ["stress_insufficient_data"]
    if status == "INVALID_INPUT":
        return ["stress_invalid_input"]
    if status != "READY_FOR_REVIEW":
        return [f"stress_status={status or 'missing'}"]
    reasons: List[str] = []
    scenarios = stress.get("scenarios") if isinstance(stress, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        return ["stress_scenarios_missing"]
    # Evaluate the mid cost buffer when present; otherwise the first scenario.
    chosen = None
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        try:
            buffer = float(item.get("cost_buffer_r") or 0.0)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(buffer) and abs(buffer - 0.05) < 1e-9:
            chosen = item
            break
    if chosen is None:
        chosen = scenarios[0]
    mc = chosen.get("monte_carlo") if isinstance(chosen, dict) else None
    if not isinstance(mc, dict):
        return ["stress_monte_carlo_malformed"]
    if str(mc.get("status")) != "READY_FOR_REVIEW":
        return ["stress_monte_carlo_not_ready"]
    try:
        ruin = float(mc.get("ruin_probability") or 0.0)
        dd_p95 = float(mc.get("max_drawdown_p95") or 0.0)
        median_eq = float(mc.get("ending_equity_median") or 0.0)
    except (TypeError, ValueError, OverflowError):
        return ["stress_metrics_malformed"]
    if not math.isfinite(ruin) or ruin > MAX_PROMOTION_STRESS_RUIN:
        reasons.append(f"stress_ruin>{MAX_PROMOTION_STRESS_RUIN}")
    if not math.isfinite(dd_p95) or dd_p95 > MAX_PROMOTION_STRESS_DD_P95:
        reasons.append(f"stress_max_drawdown_p95>{MAX_PROMOTION_STRESS_DD_P95}")
    if not math.isfinite(median_eq) or median_eq < 1.0:
        reasons.append("stress_ending_equity_median<1")
    return reasons


def promotion_gate(path: str = "trading_state.db", minimum_closed_per_setup: int = 100) -> Dict[str, Any]:
    threshold_valid = (isinstance(minimum_closed_per_setup, int)
                       and not isinstance(minimum_closed_per_setup, bool)
                       and minimum_closed_per_setup >= MIN_RESEARCH_SAMPLE)
    effective_threshold = minimum_closed_per_setup if threshold_valid else MIN_RESEARCH_SAMPLE
    integrity = audit(path)
    dataset = labelled_dataset(path)
    metrics = setup_metrics(path)
    from research.research_stress import stress_report
    stress = stress_report(path, minimum_samples=effective_threshold)
    stress_reasons = _stress_floor_reasons(stress)
    integrity_passed = bool(integrity.get("passed", False))

    annotated: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []
    for metric in metrics:
        floor_reasons = _setup_floor_reasons(metric, minimum_closed=effective_threshold)
        if not threshold_valid:
            floor_reasons = [*floor_reasons, "configuration_invalid"]
        if not integrity_passed:
            floor_reasons = [*floor_reasons, "journal_integrity_failed"]
        floor_reasons = [*floor_reasons, *stress_reasons]
        row = {**metric, "floor_reasons": floor_reasons, "floors_passed": not floor_reasons}
        annotated.append(row)
        if not floor_reasons:
            eligible.append({k: v for k, v in row.items() if k != "kelly"})

    configuration_error = None if threshold_valid else (
        f"minimum_closed_per_setup must be an integer >= {MIN_RESEARCH_SAMPLE}")
    floors = {
        "minimum_closed_per_setup": effective_threshold,
        "min_expectancy_r": MIN_PROMOTION_EXPECTANCY_R,
        "min_profit_factor": MIN_PROMOTION_PROFIT_FACTOR,
        "max_stress_ruin": MAX_PROMOTION_STRESS_RUIN,
        "max_stress_drawdown_p95": MAX_PROMOTION_STRESS_DD_P95,
        "require_integrity": True,
        "require_purged_walk_forward": True,
    }
    return {
        "integrity_passed": integrity_passed,
        "dataset": {"sample_size": dataset.get("sample_size", 0),
                    "excluded": dataset.get("excluded", {}),
                    "point_in_time": bool(dataset.get("point_in_time", False)),
                    "cost_model": dataset.get("cost_model")},
        "minimum_closed_per_setup": effective_threshold,
        "requested_minimum_closed_per_setup": minimum_closed_per_setup,
        "configuration_valid": threshold_valid,
        "configuration_error": configuration_error,
        "floors": floors,
        "stress": {"status": stress.get("status"), "sample_size": stress.get("sample_size"),
                   "reasons": stress_reasons, "execution_authority": False},
        "setup_metrics": annotated,
        "eligible_setups": eligible,
        "validation_required": (
            "integrity_passed AND purged_walk_forward_folds>0 AND "
            f"expectancy_r>{MIN_PROMOTION_EXPECTANCY_R} AND "
            f"(profit_factor>={MIN_PROMOTION_PROFIT_FACTOR} or undefined-with-wins) AND stress floors"
        ),
        "allowed_to_influence_execution": False,
        "reason": "Research artifacts remain advisory until correctly labelled point-in-time data, purged walk-forward results, numeric floors, realistic costs, and operator approval are recorded.",
    }
