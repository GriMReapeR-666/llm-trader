"""SQLite + JSON persistence: signals, strategies (versioned), deployments, events, runs."""
from __future__ import annotations
import json, sqlite3, time, uuid
from .models import HOME

DB = HOME / "qp.db"
RUNS = HOME / "runs"; RUNS.mkdir(parents=True, exist_ok=True)


def conn():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, ts REAL, source TEXT, symbol TEXT, action TEXT, payload TEXT, handled INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS strategies(name TEXT, version INTEGER, spec TEXT, notes TEXT, ts REAL, PRIMARY KEY(name, version));
    CREATE TABLE IF NOT EXISTS deployments(name TEXT PRIMARY KEY, strategy TEXT, version INTEGER, broker TEXT, mode TEXT, active INTEGER, ts REAL);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, level TEXT, kind TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
    """)
    return c


def log_event(kind, detail, level="info"):
    with conn() as c: c.execute("INSERT INTO events(ts,level,kind,detail) VALUES(?,?,?,?)", (time.time(), level, kind, json.dumps(detail, default=str)))


def recent_events(n=50, kind=None):
    with conn() as c:
        q = "SELECT * FROM events" + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC LIMIT ?"
        rows = c.execute(q, ((kind, n) if kind else (n,))).fetchall()
    return [dict(r) for r in rows]


def kv_get(k, default=None):
    with conn() as c: r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def kv_set(k, v):
    with conn() as c: c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))


# strategies
def save_strategy(name, spec, notes=""):
    with conn() as c:
        r = c.execute("SELECT MAX(version) m FROM strategies WHERE name=?", (name,)).fetchone()
        v = (r["m"] or 0) + 1
        c.execute("INSERT INTO strategies VALUES(?,?,?,?,?)", (name, v, json.dumps(spec), notes, time.time()))
    return v


def get_strategy(name, version=None):
    with conn() as c:
        r = c.execute("SELECT * FROM strategies WHERE name=? " + ("AND version=?" if version else "ORDER BY version DESC LIMIT 1"),
                      (name, version) if version else (name,)).fetchone()
    if not r: raise KeyError(f"strategy '{name}' v{version or 'latest'} not found")
    return json.loads(r["spec"]), r["version"]


def list_strategies():
    with conn() as c:
        rows = c.execute("SELECT name, MAX(version) latest, COUNT(*) versions FROM strategies GROUP BY name").fetchall()
    return [dict(r) for r in rows]


# signals
def add_signal(source, symbol, action, payload):
    with conn() as c:
        cur = c.execute("INSERT INTO signals(ts,source,symbol,action,payload) VALUES(?,?,?,?,?)",
                        (time.time(), source, symbol, action, json.dumps(payload)))
    return cur.lastrowid


def get_signals(n=20, only_unhandled=False):
    with conn() as c:
        rows = c.execute("SELECT * FROM signals " + ("WHERE handled=0 " if only_unhandled else "") + "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d["payload"] = json.loads(d["payload"]); out.append(d)
    return out


def mark_handled(sid):
    with conn() as c: c.execute("UPDATE signals SET handled=1 WHERE id=?", (sid,))


# deployments
def set_deployment(name, strategy, version, broker, mode, active=True):
    with conn() as c: c.execute("INSERT OR REPLACE INTO deployments VALUES(?,?,?,?,?,?,?)", (name, strategy, version, broker, mode, int(active), time.time()))


def get_deployments(active_only=True):
    with conn() as c: rows = c.execute("SELECT * FROM deployments" + (" WHERE active=1" if active_only else "")).fetchall()
    return [dict(r) for r in rows]


# runs
def save_run(run: dict) -> str:
    rid = uuid.uuid4().hex[:8]; run["run_id"] = rid
    (RUNS / f"{rid}.json").write_text(json.dumps(run, default=str))
    return rid


def load_run(rid: str) -> dict:
    p = RUNS / f"{rid}.json"
    if not p.exists(): raise KeyError(f"run {rid} not found")
    return json.loads(p.read_text())


def list_runs(strategy=None, n=30):
    out = []
    for p in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = json.loads(p.read_text())
        if strategy and r.get("strategy") != strategy: continue
        out.append({k: r.get(k) for k in ("run_id", "strategy", "version", "broker", "scenario", "dataset", "segment", "kind")}
                   | {"metrics": {k: r["metrics"].get(k) for k in ("net_profit", "return_pct", "max_drawdown_pct", "sharpe", "profit_factor", "trades")} if "metrics" in r else None})
        if len(out) >= n: break
    return out


def holdout_uses(strategy: str, dataset: str) -> int:
    return kv_get(f"holdout:{strategy}:{dataset}", 0)


def magic_for(name: str) -> int:
    """Stable (cross-process) MT5 magic number for a strategy/deployment name."""
    import zlib
    return zlib.crc32(name.encode()) % 10**6
