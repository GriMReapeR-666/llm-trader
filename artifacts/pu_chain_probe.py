"""PU-0161 hash-chain investigation: why did checkpoint 13907 break, and is
the CrashRecovery journal intact NOW?

Read-only. Markers: END-MARKER-PU-0161-CHAIN
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for sub in ("core", "intelligence", "operations", "research", "data", "ui"):
    p = os.path.join(ROOT, sub)
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

from core.graph_state_persistence import GraphStatePersistence  # noqa: E402

TARGET = 13907
MARKER = "END-MARKER-PU-0161-CHAIN"


def main() -> int:
    # Read-only: open the journal the same way the code does, then verify.
    persistence = GraphStatePersistence()
    print("db_path: {}".format(persistence.db_path))

    report = persistence.verify_chain_integrity()
    print("verify_chain_integrity():")
    for k in ("valid", "checkpoints", "broken_at", "pruning_boundary",
              "head_verified", "anchored"):
        print("   {:<18} {}".format(k, report.get(k)))

    with persistence._connect() as con:
        total = con.execute("SELECT COUNT(*) c FROM checkpoints").fetchone()["c"]
        first = con.execute("SELECT id FROM checkpoints ORDER BY id ASC LIMIT 1").fetchone()
        last = con.execute("SELECT id FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()
        print("\nrows: {}  id range: {}..{}".format(
            total, first["id"] if first else None, last["id"] if last else None))

        for key in ("chain_anchor_id", "chain_anchor_hash"):
            row = con.execute(
                "SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            print("metadata[{}] = {}".format(key, row["value"] if row else None))

        # Where does 13907 sit relative to the retained window?
        r13907 = con.execute(
            "SELECT id, cycle_num, status, timestamp FROM checkpoints WHERE id=?",
            (TARGET,)).fetchone()
        print("\ncheckpoint {}: {}".format(
            TARGET, dict(r13907) if r13907 else "NOT RETAINED (pruned)"))

        if r13907 and first:
            print("  -> id {} is {}".format(
                TARGET,
                "INSIDE retained window" if TARGET >= first["id"]
                else "BELOW retained window (first retained id={})".format(first["id"])))

        # Recompute the chain around 13907 by hand to find the exact link that
        # disagrees, and dump both sides for diagnosis.
        rows = con.execute(
            "SELECT id, cycle_num, status, payload_json, hash_chain "
            "FROM checkpoints ORDER BY id ASC").fetchall()

    if not rows:
        print("\nno rows")
        print(MARKER)
        return 0

    # Reproduce the walk exactly as verify_chain_integrity does.
    persistence2 = GraphStatePersistence()
    prev_hash = None
    first_id = rows[0]["id"]
    anchor_row_hash = None
    with persistence2._connect() as con:
        ar = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_hash'").fetchone()
        ai = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_id'").fetchone()
        anchor_row_hash = ar["value"] if ar else None
        anchor_id = int(ai["value"]) if ai else None

    if first_id == 1:
        prev_hash = "GENESIS"
    elif anchor_row_hash is not None:
        prev_hash = anchor_row_hash
    else:
        prev_hash = None

    print("\nrecomputing chain over {} rows...".format(len(rows)))
    broken = None
    inspected = []
    for row in rows:
        if prev_hash is None:
            prev_hash = row["hash_chain"]
            continue
        expected = persistence2._compute_hash(prev_hash, row["payload_json"])
        if row["hash_chain"] != expected:
            broken = row["id"]
            inspected.append((row["id"], prev_hash, row["hash_chain"], expected))
            break
        prev_hash = row["hash_chain"]

    print("first mismatch id: {}".format(broken))
    if broken is not None:
        rid, prev, actual, expected = inspected[0]
        print("  prev_hash    = {}".format(prev))
        print("  stored hash  = {}".format(actual))
        print("  expected     = {}".format(expected))
        print("  stored==expected? {}".format(actual == expected))

        with persistence2._connect() as con:
            row = con.execute(
                "SELECT id, cycle_num, status, timestamp, payload_json, hash_chain "
                "FROM checkpoints WHERE id=?", (rid,)).fetchone()
            prev_row = con.execute(
                "SELECT id, cycle_num, status, timestamp, payload_json, hash_chain "
                "FROM checkpoints WHERE id=?", (rid - 1,)).fetchone()
        print("\n  row {} : cycle={} status={} ts={}".format(
            rid, row["cycle_num"], row["status"], row["timestamp"]))
        print("  row {} : cycle={} status={} ts={}".format(
            rid - 1, prev_row["cycle_num"], prev_row["status"], prev_row["timestamp"])
            if prev_row else "  row {} absent".format(rid - 1))
        # Did the payload change under a retained hash (true tamper) or is the
        # link simply recomputed differently (algorithm drift)?
        if row and prev_row:
            recomputed_from_prev = persistence2._compute_hash(
                prev_row["hash_chain"], row["payload_json"])
            print("  _compute_hash(prev_row.hash_chain, row.payload) = {}".format(
                recomputed_from_prev))
            print("  matches stored? {}".format(recomputed_from_prev == row["hash_chain"]))

    print("\nchain_report.valid = {}".format(report.get("valid")))
    print(MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
