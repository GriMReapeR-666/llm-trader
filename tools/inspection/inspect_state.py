import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("trading_state.db")
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)
for t in tables:
    try:
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt} rows, cols={cols}")
    except Exception as e:
        print(f"  {t}: {e}")
con.close()
