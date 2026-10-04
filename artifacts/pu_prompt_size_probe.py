"""PU-0161 read-only probe: how big were the s11 prompts, and did the R2
whole-request budget (96,000 chars) actually hold?

Queries trading_state.db in read-only mode. Marker-footed output.
"""
import json
import sqlite3

DB = "file:trading_state.db?mode=ro"
MARKER = "END-MARKER-PU-0161-PROBE"


def main() -> None:
    con = sqlite3.connect(DB, uri=True)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT created_at, trace_type, payload
        FROM raw_traces
        WHERE trace_type = 'llm_prompt'
        ORDER BY created_at DESC
        LIMIT 40
        """
    ).fetchall()

    print(f"llm_prompt traces scanned: {len(rows)}")
    sizes = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        msgs = payload.get("messages") or []
        total = sum(len(str(m.get("content", ""))) for m in msgs)
        n_hist = max(0, len(msgs) - 2)
        sizes.append((r["created_at"], len(msgs), n_hist, total))
        flag = "OVER-96K" if total > 96_000 else ""
        print(
            f"  {r['created_at']}  msgs={len(msgs):>2} hist={n_hist:>2} "
            f"total_chars={total:>7} {flag}"
        )

    if sizes:
        totals = [s[3] for s in sizes]
        over = [t for t in totals if t > 96_000]
        print(
            f"\nmin={min(totals)} max={max(totals)} "
            f"avg={sum(totals)//len(totals)} over96k={len(over)}/{len(totals)}"
        )

    # llm_requests status + prompt size join for s11 window
    try:
        reqs = con.execute(
            """
            SELECT created_at, status, strategy, model, response_chars
            FROM llm_requests
            WHERE created_at >= '2026-10-02T17:00'
            ORDER BY created_at
            """
        ).fetchall()
        print(f"\nllm_requests since 2026-10-02T17:00: {len(reqs)}")
        for r in reqs:
            print(
                f"  {r['created_at']} {r['status']:<12} {r['strategy']:<10} "
                f"{r['model']} resp={r['response_chars']}"
            )
    except Exception as exc:
        print(f"llm_requests probe failed: {exc}")

    con.close()
    print(MARKER)


if __name__ == "__main__":
    main()
