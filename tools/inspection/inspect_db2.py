import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("trading_state.db")
# List all tables
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
for t in tables:
    print(f"\n=== {t} schema ===")
    cur = con.execute(f"SELECT sql FROM sqlite_master WHERE name='{t}'")
    schema = cur.fetchone()
    if schema:
        print(schema[0])
    cnt = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"Rows: {cnt}")
con.close()
