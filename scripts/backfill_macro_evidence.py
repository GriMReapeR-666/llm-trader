"""CLI wrapper for the historical macro evidence backfill.

Defaults to a pure dry run (read-only evaluation, zero writes). Pass --apply
to execute. The trader process must NOT be running (WAL contention and the
exactly-once gate both expect exclusive maintenance access).

Usage:
    python -X utf8 scripts/backfill_macro_evidence.py                # dry run
    python -X utf8 scripts/backfill_macro_evidence.py --apply        # execute
    python -X utf8 scripts/backfill_macro_evidence.py --symbol EURUSD --max-releases 20

The report is printed as JSON and mirrors what the scheduled macro evidence
stage emits, so backfilled and live evidence are structurally identical.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="trading_state.db", help="journal path (default: trading_state.db)")
    parser.add_argument("--symbol", default="EURUSD", help="symbol whose tick ledger anchors reactions (default: EURUSD)")
    parser.add_argument("--apply", action="store_true", help="execute the backfill (default: dry run)")
    parser.add_argument("--max-releases", type=int, default=None, help="cap eligible releases (testing/shadow runs)")
    args = parser.parse_args()

    for path in ("intelligence", "operations", "core", "research", "."):
        if path not in sys.path:
            sys.path.insert(0, path)

    from operations.agent_operations import AgentOperations
    from operations.macro_evidence_backfill import backfill_macro_evidence_from_history
    from intelligence.macro_graph import MacroGraph
    from intelligence.trading_memory import TradingMemory

    report = backfill_macro_evidence_from_history(
        AgentOperations(args.db),
        TradingMemory(args.db),
        MacroGraph(args.db),
        args.symbol,
        apply=args.apply,
        max_releases=args.max_releases,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
