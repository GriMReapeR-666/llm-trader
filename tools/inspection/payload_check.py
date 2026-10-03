import sqlite3, sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("trading_state.db")
cur = con.cursor()
cur.execute("SELECT payload FROM trades WHERE payload IS NOT NULL AND payload != '' LIMIT 5")
for r in cur.fetchall():
    try:
        d = json.loads(r[0])
        print(list(d.keys())[:20])
        for k in ["confidence", "entry_confidence", "score", "threshold"]:
            if k in d:
                print(f"  {k}: {d[k]}")
    except:
        print("  (non-JSON)")
con.close()
