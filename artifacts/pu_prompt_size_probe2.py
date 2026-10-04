"""PU-0161b read-only probe: reconcile report prompt-size metric vs raw
content chars, so the ceiling lands below the observed failure boundary.

Markers: END-MARKER-PU-0161B-PROBE
"""
import json
import sqlite3

DB = "file:trading_state.db?mode=ro"
MARKER = "END-MARKER-PU-0161B-PROBE"


def main() -> None:
    con = sqlite3.connect(DB, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT t.created_at, t.payload AS tpayload, r.status, r.response_chars
        FROM raw_traces t
        LEFT JOIN llm_requests r
          ON r.created_at >= substr(t.created_at, 1, 19)
         AND r.created_at <  datetime(substr(t.created_at, 1, 19), '+2 seconds')
        WHERE t.trace_type = 'llm_prompt'
          AND t.created_at >= '2026-10-02T17:00'
        ORDER BY t.created_at
        """
    ).fetchall()

    print(f"{'created_at':<30} {'status':<7} {'stored':>8} {'content':>8} {'resp':>5}")
    for r in rows:
        stored = len(r["tpayload"] or "")
        try:
            payload = json.loads(r["tpayload"])
            msgs = payload.get("messages") or []
            content = sum(len(str(m.get("content", ""))) for m in msgs)
        except Exception:
            content = -1
        print(
            f"{r['created_at'][:26]:<30} {str(r['status'] or '-'):<7} "
            f"{stored:>8} {content:>8} {str(r['response_chars'] or '-'):>5}"
        )
    print(MARKER)
    con.close()


if __name__ == "__main__":
    main()
