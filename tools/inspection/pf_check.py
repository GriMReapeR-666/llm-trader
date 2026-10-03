import sqlite3, sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("trading_state.db")
cur = con.cursor()
cur.execute("SELECT payload FROM trades WHERE payload IS NOT NULL AND payload != '' LIMIT 3")
for r in cur.fetchall():
    d = json.loads(r[0])
    pf = d.get("proposal_features", {})
    print("proposal_features keys:", list(pf.keys())[:15])
    for k in ["confidence", "entry_confidence", "score", "threshold", "fusion_score"]:
        if k in pf:
            print(f"  {k}: {pf[k]}")
con.close()
