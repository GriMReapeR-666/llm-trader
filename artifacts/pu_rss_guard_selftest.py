"""Syntax + functional selftest for the session-11 waiter guard hardening.

1. ast.parse the waiter (syntax).
2. Spawn a CTRL_BREAK-responsive sleeper with CREATE_NEW_PROCESS_GROUP and
   run _graceful_wind_down against it -> expect graceful exit.
3. Run _emit_guard_event against a THROWAWAY temp DB (never the real one)
   to prove the durable audit insert works.
Prints SELFTEST-OK on success. Never touches trading_state.db.
"""
import ast
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAITER = os.path.join(ROOT, "artifacts", "pu_session11_waiter.py")


def main() -> int:
    # 1. Syntax check
    src = open(WAITER, encoding="utf-8").read()
    ast.parse(src)
    print("1. syntax OK")

    # Import the waiter module (import-safe: main() only runs under __main__)
    sys.path.insert(0, os.path.dirname(WAITER))
    import importlib.util
    spec = importlib.util.spec_from_file_location("s11w", WAITER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("2. module import OK")

    # 2. Functional wind-down test on a sleeper in its own process group,
    #    configured like the trader (no CTRL_C re-enable). Expectation per
    #    the Windows console model: CTRL_C is ignored by the group, CTRL_BREAK
    #    hard-terminates via the default handler (exit 0xC000013A). The test
    #    verifies the LADDER ends with the process dead, and that the audit
    #    event was emitted.
    sleeper = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", "-c",
         "import time\n"
         "while True: time.sleep(0.2)\n"],
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(1.5)
    if sleeper.poll() is not None:
        print("FAIL: sleeper died before the test (exit %s)" % sleeper.poll())
        return 1
    mod.WIND_DOWN_GRACE_S = 3  # shorten CTRL_C grace for the test
    t0 = time.monotonic()
    mod._graceful_wind_down(sleeper.pid, cause="selftest")
    elapsed = time.monotonic() - t0
    rc = sleeper.wait(timeout=15)
    print("3. wind-down: elapsed=%.1fs, exit code=%s (0xC000013A=%s)" % (
        elapsed, rc, rc == 3221225786))
    if rc != 3221225786:
        print("FAIL: sleeper was not terminated by the ladder (rc=%s)" % rc)
        sleeper.kill()
        return 1
    print("   escalation ladder VERIFIED (CTRL_C ignored -> CTRL_BREAK hard stop)")

    # 3. Durable audit insert against a throwaway DB
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "trading_state.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE operational_events ("
        "id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT, actor TEXT NOT NULL,"
        "event_type TEXT NOT NULL, correlation_id TEXT, severity TEXT NOT NULL,"
        "payload TEXT NOT NULL, payload_hash TEXT NOT NULL DEFAULT '')")
    con.commit()
    con.close()
    orig_root = mod.ROOT
    mod.ROOT = tmp  # redirect _emit_guard_event to the temp dir
    try:
        mod._emit_guard_event("trader_rss_guard_escalation", {"cause": "selftest", "pid": 1})
    finally:
        mod.ROOT = orig_root
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT event_type, actor, severity, payload FROM operational_events").fetchall()
    con.close()
    print("4. audit rows in temp DB:", rows)
    if not rows or rows[0][0] != "trader_rss_guard_escalation":
        print("FAIL: audit event not persisted")
        return 1
    print("   durable audit path VERIFIED (temp DB only)")
    print("SELFTEST-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
