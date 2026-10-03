import sqlite3, sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("trading_state.db")
cur = con.cursor()
cur.execute("SELECT ticket, pnl, exit_reason, payload FROM trades WHERE closed_at IS NOT NULL LIMIT 10")
for r in cur.fetchall():
    d = json.loads(r[3]) if r[3] else {}
    pf = d.get("proposal_features", {})
    c = pf.get("confidence", "N/A")
    print(f"ticket={r[0]} pnl={r[1]} reason={r[2]} conf={c}")
con.close()
