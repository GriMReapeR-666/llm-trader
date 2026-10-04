"""PU-0161 root-cause probe: WHY does the checkpoint chain break?

Hypothesis (to be confirmed or refuted):
  begin_checkpoint() seeds prev_hash from the last **COMPLETED** row
  ("SELECT ... WHERE status='COMPLETED' ... ORDER BY id DESC LIMIT 1"),
  but verify_chain_integrity() walks **every** row in id order, chaining
  from the immediately preceding row regardless of status.
  Any run of FAILED rows therefore hashes against a different predecessor
  than the verifier expects -> false "broken at <id>" tamper signal.

This probe checks the stored hashes against both derivations.

Read-only. Markers: END-MARKER-PU-0161-ROOTCAUSE
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

MARKER = "END-MARKER-PU-0161-ROOTCAUSE"
PLACEHOLDER = json.dumps({"_placeholder": True})


def h(prev: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}{payload}".encode()).hexdigest()


def main() -> int:
    p = GraphStatePersistence()
    with p._connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, cycle_num, status, payload_json, hash_chain "
            "FROM checkpoints ORDER BY id ASC").fetchall()]
        anchor_hash = None
        ar = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_hash'").fetchone()
        ai = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_id'").fetchone()
        anchor_hash = ar["value"] if ar else None
        anchor_id = int(ai["value"]) if ai else None

    if not rows:
        print("no rows")
        print(MARKER)
        return 0

    print("rows: {}  ids {}..{}".format(len(rows), rows[0]["id"], rows[-1]["id"]))
    print("anchor: id={} hash={}...".format(anchor_id,
          (anchor_hash or "")[:16]))
    print("placeholder payload == stored payload for FAILED rows? "
          "see per-row detail below\n")

    # Track the last COMPLETED hash as begin_checkpoint() does.
    last_completed_hash = anchor_hash  # predecessor outside the window
    stats = {"link_ok": 0, "link_bad": 0, "completed": 0, "failed": 0,
             "running": 0}
    bad_ids = []

    for i, row in enumerate(rows):
        prev_by_id = None
        if i == 0:
            # walk seed: anchor (window starts right after it) or unknown
            prev_by_id = anchor_hash if anchor_id == row["id"] - 1 else None
        else:
            prev_by_id = rows[i - 1]["hash_chain"]

        st = (row["status"] or "").upper()
        stats[st.lower()] = stats.get(st.lower(), 0) + 1

        # Derivation A: what begin_checkpoint() would have used
        # (hash of last COMPLETED row at insert time).
        is_placeholder = row["payload_json"] == PLACEHOLDER
        if prev_by_id is not None:
            expected_id_walk = h(prev_by_id, row["payload_json"])
            matches_id_walk = row["hash_chain"] == expected_id_walk
        else:
            matches_id_walk = None

        if matches_id_walk is False:
            stats["link_bad"] += 1
            bad_ids.append((row["id"], st, is_placeholder))
        elif matches_id_walk is True:
            stats["link_ok"] += 1

        # Derivation B: hash seeded from last COMPLETED (write path)
        if last_completed_hash is not None:
            expected_completed = h(last_completed_hash, row["payload_json"])
            if row["hash_chain"] == expected_completed:
                row["_matches_completed_seed"] = True
            else:
                row["_matches_completed_seed"] = False
        if st == "completed":
            stats["completed"] += 1
            last_completed_hash = row["hash_chain"]

    print("id-walk link check: ok={} bad={}".format(
        stats["link_ok"], stats["link_bad"]))
    print("status counts: completed={} failed={} running={}".format(
        stats.get("completed", 0), stats.get("failed", 0),
        stats.get("running", 0)))

    if bad_ids:
        print("\nfirst id-walk mismatches:")
        for rid, st, is_ph in bad_ids[:8]:
            print("  id={} status={} placeholder_payload={}".format(
                rid, st, is_ph))
        first_bad = bad_ids[0][0]
        idx = next(i for i, r in enumerate(rows) if r["id"] == first_bad)
        print("\n--- detail around first mismatch id={} ---".format(first_bad))
        for j in range(max(0, idx - 3), min(len(rows), idx + 3)):
            r = rows[j]
            prev = anchor_hash if (j == 0 and anchor_id == r["id"] - 1) else (
                rows[j - 1]["hash_chain"] if j > 0 else None)
            note = ""
            if j == idx:
                note = "  <== FIRST MISMATCH"
            print("  id={} status={:<9} ph={} seed_from_prev={}".format(
                r["id"], r["status"], r["payload_json"] == PLACEHOLDER,
                (prev or "GENESIS")[:12]))
            print("        stored   {}".format(r["hash_chain"]))
            if prev is not None:
                print("        id-walk {}".format(h(prev, r["payload_json"])))
            print("        matches last-COMPLETED seed? {}".format(
                r.get("_matches_completed_seed")))

        # Does the row's hash equal sha256(last_completed_before_it + payload)?
        print("\n--- write-path hypothesis test for id={} ---".format(first_bad))
        # last completed strictly before first_bad
        prior_completed = None
        for r in rows:
            if r["id"] >= first_bad:
                break
            if r["status"] == "COMPLETED":
                prior_completed = r["hash_chain"]
        target = next(r for r in rows if r["id"] == first_bad)
        if prior_completed:
            cand = h(prior_completed, target["payload_json"])
            print("  stored == h(last_COMPLETED before it, payload)? {}".format(
                cand == target["hash_chain"]))
        else:
            print("  no prior COMPLETED row in window "
                  "(seed would be GENESIS/anchor)")

    # Integrity verdict of the DATA (independent of hash linkage):
    print("\n--- data integrity (payload side) ---")
    parse_ok = 0
    parse_bad = 0
    for r in rows:
        try:
            json.loads(r["payload_json"])
            parse_ok += 1
        except Exception:
            parse_bad += 1
    print("payload_json parses: ok={} bad={}".format(parse_ok, parse_bad))

    print("\nchain_report: {}".format(p.verify_chain_integrity()))
    print(MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
