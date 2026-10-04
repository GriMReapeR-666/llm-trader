"""PU-0161 root-cause probe part 3: enumerate candidate predecessors for the
COMPLETED row 13947 whose id-walk link fails.

Tests every plausible predecessor state, including placeholder forms that a
row would have carried while still RUNNING (i.e. out-of-order completion).

Read-only. Markers: END-MARKER-PU-0161-ROOTCAUSE3
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

MARKER = "END-MARKER-PU-0161-ROOTCAUSE3"
PH = json.dumps({"_placeholder": True})


def h(prev: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}{payload}".encode()).hexdigest()


def main() -> int:
    gp = GraphStatePersistence()
    with gp._connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, cycle_num, status, payload_json, hash_chain "
            "FROM checkpoints ORDER BY id ASC").fetchall()]
        ar = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_hash'").fetchone()
        ai = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_id'").fetchone()
    anchor_hash = ar["value"] if ar else None
    anchor_id = int(ai["value"]) if ai else None

    idx = {r["id"]: i for i, r in enumerate(rows)}
    target_id = 13947
    i = idx[target_id]
    t = rows[i]
    payload = t["payload_json"]

    # Interleaving signature across the whole window.
    print("cycle_num sequence (first 30): {}".format(
        [r["cycle_num"] for r in rows[:30]]))
    cyc1 = [r["id"] for r in rows if r["cycle_num"] == 1]
    print("rows with cycle_num==1: {} (count={})".format(cyc1[:20], len(cyc1)))
    other = [r["cycle_num"] for r in rows if r["cycle_num"] != 1]
    print("non-1 cycle_nums: {}".format(other[:25]))

    print("\ncandidate predecessor states for id={}".format(target_id))
    cands = {}
    if anchor_hash and anchor_id == rows[0]["id"] - 1:
        cands["anchor_hash"] = anchor_hash
    cands["GENESIS"] = "GENESIS"
    for r in rows[:i]:
        cands["stored:{}".format(r["id"])] = r["hash_chain"]
        # placeholder form this row had while RUNNING, seeded from the last
        # COMPLETED row that existed before it
        lastc = None
        for q in rows:
            if q["id"] >= r["id"]:
                break
            if q["status"] == "COMPLETED":
                lastc = q["hash_chain"]
        seed = lastc or anchor_hash or "GENESIS"
        cands["running:{}(seed={})".format(
            r["id"], (lastc or "anchor/GEN")[:8])] = h(seed, PH)

    hits = []
    for name, cand in cands.items():
        if h(cand, payload) == t["hash_chain"]:
            hits.append(name)

    print("  matching predecessors: {}".format(hits if hits else "NONE"))

    # Verdict helpers: is the payload data itself sound?
    print("\npayload parse check over window: ", end="")
    bad = 0
    for r in rows:
        try:
            json.loads(r["payload_json"])
        except Exception:
            bad += 1
    print("bad={}/{}".format(bad, len(rows)))

    # Did the verifier stop early? Report how far it gets if we RE-SEED at
    # each mismatch instead of aborting — i.e. how many links are genuinely
    # sound once known-write-path quirks are skipped.
    print("\nfull-window link audit (id-walk, no abort):")
    prev = anchor_hash if (anchor_hash and anchor_id == rows[0]["id"] - 1) else None
    bad_ids = []
    for r in rows:
        if prev is None:
            prev = r["hash_chain"]
            continue
        if h(prev, r["payload_json"]) != r["hash_chain"]:
            bad_ids.append((r["id"], r["status"]))
        prev = r["hash_chain"]
    print("  mismatched ids ({}): {}".format(len(bad_ids), bad_ids))

    # Classify: FAILED mismatches explained by write-path asymmetry?
    explained = [x for x in bad_ids if x[1] == "FAILED"]
    unexplained = [x for x in bad_ids if x[1] != "FAILED"]
    print("  FAILED (explained by last-COMPLETED seeding): {}".format(
        [x[0] for x in explained]))
    print("  non-FAILED (needs explanation): {}".format(
        [x[0] for x in unexplained]))

    print("\nchain_report: {}".format(gp.verify_chain_integrity()))
    print(MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
