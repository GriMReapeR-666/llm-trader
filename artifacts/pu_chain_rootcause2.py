"""PU-0161 root-cause probe part 2: why does COMPLETED row 13947 mismatch?

FAILED-row breaks are explained (begin_checkpoint seeds from last COMPLETED,
verifier walks by id). This isolates the COMPLETED-row mismatch.

Read-only. Markers: END-MARKER-PU-0161-ROOTCAUSE2
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

MARKER = "END-MARKER-PU-0161-ROOTCAUSE2"
PH = json.dumps({"_placeholder": True})


def h(prev: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}{payload}".encode()).hexdigest()


def main() -> int:
    p = GraphStatePersistence()
    with p._connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, cycle_num, status, payload_json, hash_chain "
            "FROM checkpoints ORDER BY id ASC").fetchall()]

    idx = {r["id"]: i for i, r in enumerate(rows)}
    target_id = 13947
    i = idx.get(target_id)
    if i is None:
        print("id {} not retained".format(target_id))
        print(MARKER)
        return 0

    print("window around {}:".format(target_id))
    for j in range(max(0, i - 5), min(len(rows), i + 4)):
        r = rows[j]
        prev = rows[j - 1]["hash_chain"] if j > 0 else None
        expected = h(prev, r["payload_json"]) if prev else None
        flag = "OK" if expected == r["hash_chain"] else "MISMATCH"
        if r["id"] == target_id:
            flag += "  <== target"
        print("  id={} cyc={:<4} status={:<9} ph={} len(payload)={:<6} {}".format(
            r["id"], r["cycle_num"], r["status"],
            r["payload_json"] == PH, len(r["payload_json"]), flag))
        print("      stored   {}".format(r["hash_chain"]))
        if expected:
            print("      id-walk  {}".format(expected))

    t = rows[i]
    prev = rows[i - 1]["hash_chain"]
    print("\n--- hypotheses for id={}".format(target_id))
    print("H1 payload mutated after hashing:")
    print("    h(prev, current_payload) == stored ? {}".format(
        h(prev, t["payload_json"]) == t["hash_chain"]))

    # H2: hash was computed while predecessor had a DIFFERENT hash
    # (predecessor later rewritten by complete_checkpoint).
    # Reconstruct: predecessor as a placeholder-hash row seeded from the
    # last COMPLETED before it.
    last_completed_before = None
    for r in rows:
        if r["id"] >= rows[i - 1]["id"]:
            break
        if r["status"] == "COMPLETED":
            last_completed_before = r["hash_chain"]
    if last_completed_before:
        alt_prev = h(last_completed_before, rows[i - 1]["payload_json"])
        print("H2 alt prev (predecessor reseeded from last COMPLETED): {}".format(
            alt_prev[:20]))
        print("    h(alt_prev, payload) == stored ? {}".format(
            h(alt_prev, t["payload_json"]) == t["hash_chain"]))

    # H3: target hashed against its own predecessor's *placeholder* form
    # (i.e. target completed BEFORE predecessor completed -> out-of-order).
    if rows[i - 1]["payload_json"] == PH:
        print("H3 predecessor is placeholder (out-of-order completion likely)")
    else:
        print("H3 predecessor is a real payload (not placeholder)")

    # H4: target's payload at hash time differed from stored payload ->
    # detect by checking whether ANY plausible prev produces the stored hash.
    # Try GENESIS and the anchor.
    with p._connect() as con:
        ar = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_hash'").fetchone()
        ai = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_id'").fetchone()
    anchor_hash = ar["value"] if ar else None
    anchor_id = int(ai["value"]) if ai else None
    for name, cand in (("anchor_hash", anchor_hash), ("GENESIS", "GENESIS")):
        if cand:
            print("H4 seed={} : h(seed,payload)==stored ? {}".format(
                name, h(cand, t["payload_json"]) == t["hash_chain"]))

    # H5: target hash equals predecessor's hash (duplicate-hash signature,
    # same as the FAILED-row bug).
    print("H5 stored == prev stored ? {}".format(
        t["hash_chain"] == prev))

    # Search: does stored equal h(X, payload) for the stored hash of ANY row?
    hits = [r["id"] for r in rows
            if h(r["hash_chain"], t["payload_json"]) == t["hash_chain"]]
    print("H6 stored == h(<row id>, payload) for rows: {}".format(hits))

    # H7: maybe complete_checkpoint ran when predecessor row did not exist
    # (id gap) -> prev would be an earlier row.
    for back in range(1, 6):
        if i - back < 0:
            break
        cand = rows[i - back]["hash_chain"]
        if h(cand, t["payload_json"]) == t["hash_chain"]:
            print("H7 stored == h(row id={}, payload) -> predecessor at hash "
                  "time was id={}".format(rows[i - back]["id"],
                                          rows[i - back]["id"]))

    print("\nchain_report: {}".format(p.verify_chain_integrity()))
    print(MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
