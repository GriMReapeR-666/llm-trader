"""Macro asset price ingestion for causal edge evidence grounding.

Fetches genuine historical market observations (DXY, GOLD, WTI_OIL, EQUITIES,
NOMINAL_BOND_YIELD, VIX, FX majors) and ingests them into ``macro_observations``
stamped as ``USABLE_AS_EVIDENCE`` with clean 1:1 timestamps and non-zero variance.

This unblocks intermarket causal edges (e.g. DXY -> GOLD, DXY -> WTI_OIL) that
require measurable time series on both source and target endpoints before
AutonomousCausalValidator can evaluate them.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.yahoo_finance_provider import YahooFinanceProvider
from intelligence.macro_observations import ensure_schema
from intelligence.observation_provenance import (
    VERDICT_USABLE,
    audit_series,
    edge_evidence_coverage,
    quarantine,
)

log = logging.getLogger("MacroPriceIngest")

DEFAULT_MACRO_SYMBOLS: Dict[str, str] = {
    "DXY": "DX-Y.NYB",
    "GOLD": "GC=F",
    "WTI_OIL": "CL=F",
    "EQUITIES": "^GSPC",
    "NOMINAL_BOND_YIELD": "^TNX",
    "VIX": "^VIX",
    "EUR": "EURUSD=X",
    "JPY": "JPY=X",
    "CAD": "CAD=X",
    "AUD": "AUDUSD=X",
    "CHF": "CHF=X",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest_macro_prices(
    db_path: str = "trading_state.db",
    symbols_map: Optional[Dict[str, str]] = None,
    period: str = "2y",
    limit_per_series: int = 500,
) -> Dict[str, Any]:
    """Fetch and ingest price observations for catalog macro entities.

    Idempotent: skips already ingested (series, observed_at) dates.
    """
    ensure_schema(db_path)
    symbols = symbols_map or DEFAULT_MACRO_SYMBOLS
    yf = YahooFinanceProvider()

    inserted_total = 0
    entities_done: List[str] = []
    stamp = utcnow()

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        # Get existing (series, observed_at) to ensure idempotency
        existing = {
            (str(r["series"]), str(r["observed_at"]))
            for r in con.execute(
                "SELECT series, observed_at FROM macro_observations WHERE provider='YAHOO_FINANCE'"
            ).fetchall()
        }

        for entity, symbol in symbols.items():
            try:
                data = yf.get_historical_data(symbol, interval="1d", period=period)
                if data is None or (hasattr(data, "empty") and data.empty) or (isinstance(data, list) and not data):
                    log.warning("no price data returned for %s (%s)", entity, symbol)
                    continue

                # Support both List[Dict] and DataFrame formats
                parsed_bars: List[Dict[str, Any]] = []
                if isinstance(data, list):
                    for b in data:
                        ts = b.get("time", 0)
                        dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        parsed_bars.append({
                            "date": dt_str,
                            "open": float(b.get("open", 0.0)),
                            "high": float(b.get("high", 0.0)),
                            "low": float(b.get("low", 0.0)),
                            "close": float(b.get("close", 0.0)),
                            "volume": float(b.get("tick_volume", b.get("real_volume", 0.0))),
                        })
                elif hasattr(data, "iterrows"):
                    for idx, row in data.iterrows():
                        dt_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                        parsed_bars.append({
                            "date": dt_str,
                            "open": float(row.get("Open", row.get("Close", 0.0))),
                            "high": float(row.get("High", row.get("Close", 0.0))),
                            "low": float(row.get("Low", row.get("Close", 0.0))),
                            "close": float(row.get("Close", 0.0)),
                            "volume": float(row.get("Volume", 0.0)),
                        })

                if len(parsed_bars) < 5:
                    log.warning("insufficient bars for %s (%s): %d", entity, symbol, len(parsed_bars))
                    continue

                # Deduplicate by date and sort chronologically
                seen_dates = set()
                deduped_bars = []
                for b in parsed_bars:
                    if b["date"] not in seen_dates:
                        seen_dates.add(b["date"])
                        deduped_bars.append(b)
                deduped_bars.sort(key=lambda x: x["date"])

                if limit_per_series and len(deduped_bars) > limit_per_series:
                    deduped_bars = deduped_bars[-limit_per_series:]

                rows_to_insert = []
                for i, bar in enumerate(deduped_bars):
                    dt_str = bar["date"]
                    if (symbol, dt_str) in existing:
                        continue

                    val = bar["close"]
                    prev_val = deduped_bars[i - 1]["close"] if i > 0 else None
                    pct_change = (
                        round(((val - prev_val) / prev_val) * 100.0, 4)
                        if prev_val and prev_val > 0
                        else None
                    )

                    payload = json.dumps(bar)
                    rows_to_insert.append(
                        (
                            stamp,
                            entity,
                            "YAHOO_FINANCE",
                            symbol,
                            dt_str,
                            round(val, 4),
                            round(prev_val, 4) if prev_val is not None else None,
                            pct_change,
                            f"yahoo:{symbol}:{dt_str}",
                            payload,
                            0,
                            VERDICT_USABLE,
                        )
                    )
                    existing.add((symbol, dt_str))

                if rows_to_insert:
                    con.executemany(
                        """INSERT INTO macro_observations(
                            captured_at, entity, provider, series, observed_at, value,
                            previous, surprise, source_id, payload, execution_authority,
                            data_provenance
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        rows_to_insert,
                    )
                    con.commit()
                    inserted_total += len(rows_to_insert)

                entities_done.append(entity)
                log.info("ingested %d observations for %s (%s)", len(rows_to_insert), entity, symbol)
            except Exception as exc:
                log.warning("failed ingesting prices for %s (%s): %s", entity, symbol, exc)

        # Run quarantine audit pass to align provenance verdicts
        quarantine(con)
        coverage = edge_evidence_coverage(con)

    return {
        "status": "SUCCESS",
        "rows_inserted": inserted_total,
        "entities_ingested": entities_done,
        "edge_coverage": coverage,
        "execution_authority": False,
    }


if __name__ == "__main__":
    import pprint
    res = ingest_macro_prices()
    pprint.pprint(res)
