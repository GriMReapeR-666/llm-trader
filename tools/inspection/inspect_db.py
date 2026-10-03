import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("trading_state.db")
cur = con.execute("SELECT sql FROM sqlite_master WHERE name='trade_labels'")
print("=== trade_labels schema ===")
print(cur.fetchone()[0])
print("\n=== row counts ===")
print("Total trades:", con.execute("SELECT COUNT(*) FROM trade_labels").fetchone()[0])
print("With r_multiple:", con.execute("SELECT COUNT(*) FROM trade_labels WHERE r_multiple IS NOT NULL").fetchone()[0])
print("With confidence:", con.execute("SELECT COUNT(*) FROM trade_labels WHERE confidence IS NOT NULL AND confidence != ''").fetchone()[0])
cols = [d[0] for d in con.execute("SELECT * FROM trade_labels LIMIT 1").description]
print("\n=== columns ===")
print(cols)
print("\n=== sample ===")
for r in con.execute("SELECT * FROM trade_labels LIMIT 2").fetchall():
    print(dict(zip(cols, r)))
con.close()
