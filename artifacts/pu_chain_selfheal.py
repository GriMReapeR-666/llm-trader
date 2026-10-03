"""PU-0161 self-heal simulation: does retention advancing past the broken rows
make verify_chain_integrity() valid again?

Runs on a COPY of artifacts/graph_state.db — the live journal is never opened
for write. This is the whole point: we must not mutate a tamper-evident log.

Claim under test: the 5 bad links (13924, 13927, 13942, 13943, 13947) are
writer-path artifacts, not data corruption. Once retention prunes past them and
re-anchors on a correctly-written row, the retained window verifies clean.

Markers: END-MARKER-PU-0161-SELFHEAL
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DB = os.path.join(ROOT, "artifacts", "graph_state.db")
MARKER = "END-MARKER-PU-0161-SELFHEAL"
BAD_IDS = (13924, 13927, 13942, 13943, 13947)


def main() -> int:
    if not os.path.exists(LIVE_DB):
        print("live journal not found: {}".format(LIVE_DB))
        print(MARKER)
        return 1

    tmpdir = tempfile.mkdtemp(prefix="pu_chain_copy_")
    copy_path = os.path.join(tmpdir, "graph_state.db")
    shutil.copy2(LIVE_DB, copy_path)
    print("copied live journal -> {}".format(copy_path))
    print("live journal opened READ-ONLY throughout; not modified")

    con = sqlite3.connect(copy_path)
    con.row_factory = sqlite3.Row

    def report(tag: str) -> None:
        rows = con.execute(
            "SELECT id, payload_json, hash_chain FROM checkpoints ORDER BY id ASC"
        ).fetchall()
        arow = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_hash'").fetchone()
        aid = con.execute(
            "SELECT value FROM metadata WHERE key='chain_anchor_id'").fetchone()
        anchor_hash = arow["value"] if arow else None
        anchor_id = int(aid["value"]) if aid else None

        first_id = rows[0]["id"] if rows else None
        if first_id == 1:
            prev = "GENESIS"
        elif anchor_hash is not None and anchor_id == first_id - 1:
            prev = anchor_hash
        else:
            prev = None  # boundary / trust root

        broken = None
        verified = 0
        boundary = None
        for r in rows:
            if prev is None:
                boundary = r["id"]
                prev = r["hash_chain"]
                verified += 1
                continue
            import hashlib
            exp = hashlib.sha256(
                f"{prev}{r['payload_json']}".encode()).hexdigest()
            if r["hash_chain"] != exp:
                broken = r["id"]
                break
            prev = r["hash_chain"]
            verified += 1
        print("  [{}] rows={} window={}..{} anchor_id={} broken_at={} "
              "verified={} boundary={}".format(
                  tag, len(rows), first_id, rows[-1]["id"] if rows else None,
                  anchor_id, broken, verified, boundary))

    print("\n--- BEFORE (byte-identical copy) ---")
    report("before")

    # How many rows sit at/below the last known-bad id?
    worst = max(BAD_IDS)
    n_below = con.execute(
        "SELECT COUNT(*) c FROM checkpoints WHERE id <= ?", (worst,)
    ).fetchone()["c"]
    print("\nrows at/below last bad id ({}): {}".format(worst, n_below))

    # Simulate retention advancing past every broken row, and the anchor
    # re-recording on the row that complete_checkpoint() would have used
    # (the last row being pruned) — exactly the production prune path.
    anchor_row = con.execute(
        "SELECT id, hash_chain FROM checkpoints WHERE id <= ? "
        "ORDER BY id DESC LIMIT 1", (worst,)
    ).fetchone()
    print("simulating prune: new anchor -> id={} hash={}...".format(
        anchor_row["id"], anchor_row["hash_chain"][:16]))
    con.execute(
        "INSERT INTO metadata(key, value) VALUES('chain_anchor_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(anchor_row["id"]),))
    con.execute(
        "INSERT INTO metadata(key, value) VALUES('chain_anchor_hash', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (anchor_row["hash_chain"],))
    con.execute("DELETE FROM checkpoints WHERE id <= ?", (worst,))
    con.commit()

    print("--- AFTER retention advances past all 5 broken rows ---")
    report("after")

    # Confirm the data side was never the problem.
    bad_payload = 0
    for r in con.execute("SELECT payload_json FROM checkpoints"):
        try:
            import json
            json.loads(r["payload_json"])
        except Exception:
            bad_payload += 1
    print("\npayload_json unparseable after prune: {}/".format(bad_payload)
          + str(con.execute("SELECT COUNT(*) c FROM checkpoints").fetchone()["c"]))

    con.close()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("copy discarded; live journal untouched")
    print(MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
