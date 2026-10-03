"""PU-0139: pre-launch candle-state check (read-only).

Session 3 never reached get_decision because every cycle hit the
duplicate-candle gate (newest completed candle was already analyzed).  This
probe checks whether a FRESH completed candle exists right now, so the next
session will actually assemble a prompt.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

DB = "trading_state.db"
now = datetime.now(timezone.utc)
out = {"now_utc": now.isoformat(timespec="seconds")}

# Newest completed M15/H1 candle (a candle is complete when the next period
# has started).
def newest_completed(minutes: int) -> str:
    anchor = now.replace(second=0, microsecond=0)
    floor_now = anchor - timedelta(minutes=anchor.minute % minutes)
    return (floor_now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")

out["newest_completed_m15"] = newest_completed(15)
out["newest_completed_h1"] = newest_completed(60)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def _completed_utc(minutes: int) -> datetime:
    """Newest completed candle as an aware UTC datetime (same rule as the
    string version: complete when the next period has started)."""
    anchor = now.replace(second=0, microsecond=0)
    floor_now = anchor - timedelta(minutes=anchor.minute % minutes)
    return (floor_now - timedelta(minutes=minutes)).replace(tzinfo=timezone.utc)


def _scraped_as_utc(ts_text: str) -> datetime:
    """Normalize a naive scraped candle label to UTC.

    Snapshot writers on this box use mixed clock bases: some stamp candle
    labels in machine-local naive time (UTC+5:30), others in UTC. A
    local-naive label parses as a FUTURE UTC instant ('2026-10-01 14:00'
    written at 08:30 UTC) and, compared raw, blocked the fresh-candle gate
    for the whole offset (5.5h). A label parsing to the future is therefore
    re-interpreted as machine-local and converted to UTC.
    """
    try:
        naive = datetime.strptime(ts_text, "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    as_utc = naive.replace(tzinfo=timezone.utc)
    if as_utc > now + timedelta(minutes=30):
        local_tz = datetime.now().astimezone().tzinfo
        as_utc = naive.replace(tzinfo=local_tz).astimezone(timezone.utc)
    return as_utc

out["max_llm_request_at"] = con.execute("SELECT MAX(created_at) AS m FROM llm_requests").fetchone()["m"]

# Find where analyzed candles are recorded: scan tables for candle-like
# timestamps in text columns (read-only, bounded).
candle_re = re.compile(r"20\d\d-\d\d-\d\d \d\d:\d\d")
newest_analyzed = None
analyzed_source = None
for tbl_row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
    tbl = tbl_row["name"]
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info('{tbl}')").fetchall()]
    except Exception:
        continue
    if not cols:
        continue
    # Only cheap text-ish tables; skip huge FTS/audit tables.
    if any(s in tbl for s in ("_fts", "history", "returns", "ticks", "cache")):
        continue
    text_cols = [c for c in cols if c.lower() in ("task_key", "key", "cycle_key", "trigger_key", "correlation_id", "payload", "result", "error", "claim_key")]
    if not text_cols:
        continue
    try:
        rows = con.execute(
            f"SELECT {', '.join(chr(34) + c + chr(34) for c in text_cols)} FROM {tbl} ORDER BY rowid DESC LIMIT 400"
        ).fetchall()
    except Exception:
        continue
    for row in rows:
        for c in text_cols:
            val = row[c]
            if not isinstance(val, str):
                continue
            for m in candle_re.finditer(val):
                ts = m.group(0)
                if newest_analyzed is None or ts > newest_analyzed:
                    newest_analyzed = ts
                    analyzed_source = f"{tbl}.{c}"
if newest_analyzed:
    out["newest_analyzed_candle"] = newest_analyzed
    out["analyzed_source"] = analyzed_source
    analyzed_utc = _scraped_as_utc(newest_analyzed)
    out["newest_analyzed_candle_utc"] = analyzed_utc.isoformat(timespec="seconds")
    out["fresh_m15_available"] = _completed_utc(15) > analyzed_utc
    out["fresh_h1_available"] = _completed_utc(60) > analyzed_utc

con.close()
print(json.dumps(out, indent=2))
print("PU-0139-OK")
