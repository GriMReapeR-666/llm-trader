# Gate 0 Phase 2: Control Plane, Singletons & Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate split singletons across module imports (C1), lock down the API control plane with token authentication and CORS/Host restrictions (C2), and make portfolio open-risk gating atomic with pending order reservations (K1, K2).

**Architecture:**
1. Standardize all `kill_switch_latch` imports to `data.kill_switch_latch` and create an automated guard test that imports the core application and verifies no module is loaded twice under bare and package names.
2. In `ui/gui_api.py`, implement auto-generating persistent token authentication, enforce `Authorization: Bearer` / `X-API-Key` on all mutating routes and WebSockets, restrict CORS origins to local operator ports, and validate the `Host` header to protect against DNS rebinding.
3. In `core/trading_safety.py`, implement thread-safe risk reservations in `TradingStore` so that `open_risk()` includes unexpired pending order reservations, making `PortfolioRiskArbiter.permit()` check-and-reserve atomic.

**Tech Stack:** Python 3.11, FastAPI, Starlette, pytest, SQLite, threading.

**Spec:** `docs/AUDIT_2026-10-04.md` (C1, C2) and `docs/AUDIT_2026-10-04_DEEP.md` (K1, K2).

## Global Constraints
- Shell commands must be prefixed with `etk` (or `rtk`).
- Fail closed on missing, invalid, or expired authentication tokens.
- No new external pip packages (use standard library and existing FastAPI/Starlette dependencies).

---

### Task 1: Fix Split Kill-Switch Latch & Double-Import Seams (Finding C1)

**Files:**
- Modify: `data/realtime_news_ingest.py:29-33`
- Modify: `research/d9_escalation_latch_risk.py:20-25`
- Create: `tests/test_double_import_guard.py`

**Interfaces:**
- Consumes: `data.kill_switch_latch.get_kill_switch_latch`
- Produces: Unified singleton instance across news ingest and decision graph; guard test asserting singleton identity.

- [ ] **Step 1: Write failing test verifying shared latch singleton between news ingest and graph nodes**
Create `tests/test_double_import_guard.py`:
```python
import sys
import pytest

def test_kill_switch_latch_singleton_identity():
    import data.realtime_news_ingest as rni
    import intelligence.graph_nodes as gn
    import data.kill_switch_latch as ksl

    latch_news = rni.get_kill_switch_latch()
    latch_graph = gn.get_kill_switch_latch()
    latch_direct = ksl.get_kill_switch_latch()

    assert latch_news is latch_graph, "News ingest and graph nodes must share the same KillSwitchLatch instance"
    assert latch_news is latch_direct, "News ingest and data.kill_switch_latch must share the same instance"

def test_no_duplicate_kill_switch_latch_in_sys_modules():
    import data.realtime_news_ingest
    import intelligence.graph_nodes
    
    # Check that kill_switch_latch is not loaded under two distinct module objects
    modules = [m for name, m in sys.modules.items() if name in ("kill_switch_latch", "data.kill_switch_latch") and m is not None]
    if len(modules) > 1:
        assert modules[0] is modules[1], "kill_switch_latch is loaded twice as distinct module objects"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_double_import_guard.py -v`
Expected: FAIL

- [ ] **Step 3: Normalize imports in `data/realtime_news_ingest.py` and `research/d9_escalation_latch_risk.py`**
In `data/realtime_news_ingest.py`:
Replace:
```python
try:
    from kill_switch_latch import get_kill_switch_latch
except ImportError:
    from data.kill_switch_latch import get_kill_switch_latch
```
With:
```python
from data.kill_switch_latch import get_kill_switch_latch
```
In `research/d9_escalation_latch_risk.py`:
Replace:
```python
from kill_switch_latch import KillSwitchLatch
from kill_switch_latch import CONFIRM_WINDOW_S, REARM_COOLDOWN_S
```
With:
```python
from data.kill_switch_latch import KillSwitchLatch
from data.kill_switch_latch import CONFIRM_WINDOW_S, REARM_COOLDOWN_S
```

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_double_import_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add data/realtime_news_ingest.py research/d9_escalation_latch_risk.py tests/test_double_import_guard.py
git commit -m "fix(safety): unify kill_switch_latch import to prevent split singletons (C1)"
```

---

### Task 2: Secure API Control Plane with Auth, CORS Restriction & Host Validation (Finding C2)

**Files:**
- Modify: `ui/gui_api.py:86-145`
- Create: `tests/test_gui_api_security.py`

**Interfaces:**
- Consumes: FastAPI request headers, `GUI_API_TOKEN`, `artifacts/gui_api.key`
- Produces: Protected mutating routes requiring Bearer token, restricted CORS origins, Host header protection.

- [ ] **Step 1: Write failing security tests for API control routes**
Create `tests/test_gui_api_security.py`:
```python
import pytest
from starlette.testclient import TestClient
from ui.gui_api import app, get_configured_api_token

client = TestClient(app)

def test_unauthenticated_request_to_control_route_is_rejected():
    # Calling mutating endpoint without token must return 401
    resp = client.post("/api/control/approve", json={"actor": "attacker", "decision": "APPROVED"})
    assert resp.status_code == 401
    assert "unauthorized" in resp.text.lower()

def test_authenticated_request_to_control_route_is_admitted():
    token = get_configured_api_token()
    assert token is not None and len(token) >= 16
    resp = client.post(
        "/api/control/strategy_stage",
        json={"strategy": "test_strat", "stage": "CANDIDATE"},
        headers={"Authorization": f"Bearer {token}"}
    )
    # Status should not be 401
    assert resp.status_code != 401

def test_cors_does_not_wildcard_all_origins():
    # Preflight from malicious origin must not get allow-origin: *
    resp = client.options(
        "/api/control/approve",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        }
    )
    allow_origin = resp.headers.get("access-control-allow-origin", "")
    assert allow_origin != "*"
    assert "evil.example.com" not in allow_origin

def test_host_header_validation_rejects_rebinding():
    token = get_configured_api_token()
    resp = client.post(
        "/api/control/strategy_stage",
        json={"strategy": "test_strat", "stage": "CANDIDATE"},
        headers={"Authorization": f"Bearer {token}", "Host": "evil.example.com"}
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_gui_api_security.py -v`
Expected: FAIL

- [ ] **Step 3: Implement persistent token auto-generation, strict auth middleware, CORS allowlist, and Host check**
In `ui/gui_api.py`:
1. Auto-generate a secure token into `artifacts/gui_api.key` if neither env nor file exists:
```python
def get_configured_api_token() -> str:
    token = os.environ.get("GUI_API_TOKEN") or os.environ.get("GUI_API_KEY")
    if token:
        return token.strip()
    key_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "gui_api.key")
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    # Auto-generate secure token
    import secrets
    new_token = f"gui_{secrets.token_hex(24)}"
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(new_token)
    except Exception as exc:
        logger.warning("Could not persist auto-generated GUI API token: %s", exc)
    return new_token
```
2. Update CORS origins to only allow localhost / 127.0.0.1 on operator ports:
```python
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
```
3. Update `api_auth_middleware` to validate `Host` and require auth on control routes:
```python
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "testserver"}

@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    # 1. Host header validation (DNS rebinding protection)
    raw_host = request.headers.get("host", "").split(":")[0]
    if raw_host and raw_host not in ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"error": f"Forbidden: Host '{raw_host}' not permitted."})

    # 2. Control plane authentication
    path = request.url.path
    is_control_route = any(path.startswith(p) for p in ("/api/control", "/api/config", "/api/internal"))
    if is_control_route:
        if not is_request_authenticated(request.headers, request.query_params):
            return JSONResponse(status_code=401, content={"error": "Unauthorized: invalid or missing API token"})

    return await call_next(request)
```

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_gui_api_security.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add ui/gui_api.py tests/test_gui_api_security.py
git commit -m "fix(security): enforce API token authentication, CORS allowlist, and Host check (C2)"
```

---

### Task 3: Atomic Open-Risk Cap & Pending Risk Reservations (Findings K1, K2)

**Files:**
- Modify: `core/trading_safety.py:828-842, 1350-1375`
- Create: `tests/test_portfolio_risk_concurrency.py`

**Interfaces:**
- Consumes: `TradingStore.open_risk`, `PortfolioRiskArbiter.permit`
- Produces: Atomic check-and-reserve of risk allocation with TTL.

- [ ] **Step 1: Write concurrency test demonstrating race condition when opening risk**
Create `tests/test_portfolio_risk_concurrency.py`:
```python
import concurrent.futures
from unittest.mock import MagicMock
import pytest
from core.trading_safety import TradingStore, PortfolioRiskArbiter

def test_atomic_risk_reservation_prevents_cap_breach(tmp_path):
    db_path = str(tmp_path / "test_risk.db")
    store = TradingStore(db_path)
    # Arbiter with max 3% open risk on $100,000 equity = max $3,000 open risk
    arbiter = PortfolioRiskArbiter(store, max_open_risk_fraction=0.03)
    equity = 100000.0
    risk_per_trade = 1000.0 # Each trade takes $1,000 risk -> max 3 trades allowed!

    # Launch 10 concurrent threads attempting to reserve $1,000 risk
    def try_reserve(i):
        return arbiter.permit_and_reserve(equity=equity, new_risk=risk_per_trade, reservation_id=f"order_{i}")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(try_reserve, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result()[0])

    # Exactly 3 trades should be permitted; 7 must be rejected!
    permitted_count = sum(1 for r in results if r is True)
    assert permitted_count == 3, f"Expected 3 permitted trades, but got {permitted_count} (cap breached!)"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `etk pytest tests/test_portfolio_risk_concurrency.py -v`
Expected: FAIL

- [ ] **Step 3: Implement risk reservations in `TradingStore` and `permit_and_reserve` in `PortfolioRiskArbiter`**
In `core/trading_safety.py`:
1. In `TradingStore`:
Add in-memory active reservations dictionary with lock and TTL cleanup:
```python
self._risk_reservations: Dict[str, Tuple[float, float]] = {} # id -> (amount, expires_at)
self._risk_reservations_lock = threading.Lock()

def reserve_risk(self, reservation_id: str, amount: float, ttl_seconds: float = 30.0) -> None:
    with self._risk_reservations_lock:
        now = time.time()
        self._risk_reservations[reservation_id] = (float(amount), now + ttl_seconds)

def release_risk(self, reservation_id: str) -> None:
    with self._risk_reservations_lock:
        self._risk_reservations.pop(reservation_id, None)

def reserved_risk(self) -> float:
    with self._risk_reservations_lock:
        now = time.time()
        # Clean expired
        expired = [k for k, (amt, exp) in self._risk_reservations.items() if exp < now]
        for k in expired:
            del self._risk_reservations[k]
        return sum(amt for amt, exp in self._risk_reservations.values())
```
2. Update `open_risk(self, include_reserved: bool = True) -> float`:
```python
def open_risk(self, include_reserved: bool = True) -> float:
    with self._connect() as con:
        row = con.execute("SELECT COALESCE(SUM(risk_amount),0) AS total FROM trades WHERE closed_at IS NULL").fetchone()
    trades_risk = float(row["total"])
    if include_reserved:
        trades_risk += self.reserved_risk()
    return trades_risk
```
3. In `PortfolioRiskArbiter`:
Add `_arbiter_lock = threading.Lock()` and method `permit_and_reserve`:
```python
def permit_and_reserve(
    self,
    equity: float,
    new_risk: float,
    reservation_id: str,
    bucket: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    use_netting: bool = True,
    ttl_seconds: float = 30.0,
) -> Tuple[bool, str]:
    with self._arbiter_lock:
        permitted, reason = self.permit(
            equity=equity,
            new_risk=new_risk,
            bucket=bucket,
            symbol=symbol,
            side=side,
            use_netting=use_netting,
        )
        if permitted:
            self.store.reserve_risk(reservation_id, new_risk, ttl_seconds=ttl_seconds)
        return permitted, reason
```

- [ ] **Step 4: Run test to verify it passes**
Run: `etk pytest tests/test_portfolio_risk_concurrency.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add core/trading_safety.py tests/test_portfolio_risk_concurrency.py
git commit -m "fix(risk): implement atomic open-risk reservations to prevent concurrency cap breaches (K1, K2)"
```
