"""
Pytest configuration and path setup.
Enables transparent importing of all domain packages (core, intelligence, operations, research, data, ui).
"""
import sys
import os

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

for subpkg in ["core", "intelligence", "operations", "research", "data", "ui"]:
    pkg_path = os.path.join(root_dir, subpkg)
    if os.path.isdir(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)

# Single import root (C1): install the canonicalising finder before any
# repository module is imported, so bare and package-qualified imports share
# module state and tests/test_single_import_root.py can guard the invariant.
from core._import_root import install as _install_import_root
_install_import_root()


import pytest


import shutil

# ---------------------------------------------------------------------------
# Test-env pin: never load the real 421M Laya checkpoint inside pytest.
#
# models/laya/model.safetensors exists in the checkout; without this pin any
# auto-mode triage/decision test silently pays ~29s load + ~19s per decide()
# inside xdist workers (measured: one 105s test, gw memory-crash mechanism).
# FAST_TEST makes laya adapters resolve None; tests wanting laya-path behavior
# inject fakes explicitly. Production is unaffected (env not set there).
# ---------------------------------------------------------------------------
os.environ.setdefault("FAST_TEST", "1")

# ---------------------------------------------------------------------------
# Hermetic Yahoo Finance tests.
#
# data/yahoo_finance_provider.py honors LLM_TRADER_YF_OFFLINE=1 by returning
# empty history/quotes without touching the network. Before this pin, wiring
# the zero-lag macro provider to real quotes would have made its unit tests
# (test_zero_lag_macro_provider.py) hit yfinance per symbol. Production is
# unaffected (env not set there); tests wanting live-fetch behavior inject
# fake providers or clear the env var explicitly.
# ---------------------------------------------------------------------------
os.environ.setdefault("LLM_TRADER_YF_OFFLINE", "1")

# ---------------------------------------------------------------------------
# Hermetic OpenBB tests.
#
# data/openbb_data_bridge.py honours LLM_TRADER_OPENBB_OFFLINE=1 (and FAST_TEST,
# set above) by skipping `from openbb import obb` entirely. That import builds
# OpenBB's extension package index -- 31 extensions, printed as "Extensions to
# add: ..." then "Building..." -- at IMPORT time, before any call is made.
#
# Under pytest that cost is pure waste and, on a memory-constrained host, fatal.
# With -n auto (12 workers on a 12-CPU box) the workers exhaust RAM and the run
# dies as MemoryError inside the openai/openbb import, [WinError 1455] (paging
# file too small) when xdist respawns a dead worker, or a native access violation
# inside torch -- whichever allocation loses the race. No test asserts that
# OPENBB_AVAILABLE is True (test_openbb_data_bridge.py patches it to False in
# one case), and the bridge reports degraded rather than raising when it is
# absent, so pinning is safe. Production is unaffected: this file is pytest-only.
# ---------------------------------------------------------------------------
os.environ.setdefault("LLM_TRADER_OPENBB_OFFLINE", "1")

# ---------------------------------------------------------------------------
# Hermetic LLM-provider tests.
#
# Code paths that default to generation through llm_router (e.g. AlphaDiscovery
# in core/alpha_discovery.py) must never reach the live API from a unit test
# just because a developer machine has a real key exported. Tests that want
# provider-backed behavior inject fake routers or set keys explicitly.
# Production is unaffected (this block only runs under pytest).
# ---------------------------------------------------------------------------
for _k in ("OPENROUTER_API_KEY", "TOKENROUTER_KEY", "TOKENROUTER_API_KEY",
           "ROUTESME_KEY", "ROUTESME_API_KEY", "CEREBRAS_API_KEY",
           "NVIDIA_KEY", "NVIDIA_API_KEY"):
    os.environ.pop(_k, None)

# ---------------------------------------------------------------------------
# Meta-loop singleton isolation (production-corpus protection).
#
# The production meta_corpus.db is append-only hash-chained evidence. Tests
# that exercise the close->meta_loop ingestion path seed synthetic tickets
# (9999x_*) through the get_meta_loop() singleton; before this guard, every
# pytest run leaked rows into the live corpus (Sep-19: rows 128-136, one
# chain break). Setting the isolation env vars at conftest import time —
# before any test module can trigger the first get_meta_loop() — routes the
# whole pytest process (and each xdist worker separately, via the PID-suffixed
# dir) at a throwaway corpus/state. Production must never set these vars.
# ---------------------------------------------------------------------------
import tempfile

_META_TEST_DIR = os.path.join(
    tempfile.gettempdir(), f"meta_loop_test_{os.getpid()}"
)
os.environ.setdefault(
    "META_LOOP_DB", os.path.join(_META_TEST_DIR, "meta_corpus.db")
)
os.environ.setdefault(
    "META_LOOP_STATE", os.path.join(_META_TEST_DIR, "meta_loop_state.json")
)

# D9 kill-switch latch: PID-scoped throwaway checkpoint (and PID-scoped
# singleton identity) so no pytest run ever reads or writes the production
# artifacts/d9_kill_switch_latch.json — the latch persists by design, so a
# shared file would leak latched state across runs and into production.
os.environ.setdefault(
    "D9_LATCH_PATH", os.path.join(_META_TEST_DIR, "d9_kill_switch_latch.json")
)
os.environ.setdefault("D9_LATCH_SINGLETON", f"pytest_{os.getpid()}")

# Shared repo-root runtime state files that tests can silently pollute when
# constructing RiskManager()/TradeManager() with default paths.
_RUNTIME_STATE_FILES = ("risk_state.json", "trade_manager_state.json")


@pytest.fixture(autouse=True)
def isolate_mt5_terminal_session():
    """No test may leave the real MT5 terminal attached to the worker.

    The paper-session path calls a bare ``mt5.initialize()``
    (mt5_trader.py:1026) to obtain real market data, which attaches to
    whatever terminal the operator already has signed in. Nothing shut it
    down, so the connection survived for the rest of the worker process and
    ``core.risk_manager.RiskManager._account_equity()`` started returning the
    live account equity instead of failing closed to the constructed balance.

    Real case: ``test_llm_trader.py::TestRiskManager::test_position_sizing``
    expects 0.1 lots and got 1.0 - exactly 10x, because equity read 100000
    against a constructed balance of 10000. Which assertion failed depended
    only on which xdist worker ran which file, so the suite had an
    order-dependent failure that reproduced in some orderings and not others.

    This both detaches before the test (so a test starts from a known-clean
    terminal) and after it (so nothing leaks into the next test). Tests that
    deliberately exercise the MT5 data plane still work: they initialise
    themselves.
    """
    try:
        import MetaTrader5 as _mt5
    except Exception:
        yield
        return

    def _attached() -> bool:
        try:
            return _mt5.terminal_info() is not None
        except Exception:
            return False

    def _detach() -> None:
        try:
            _mt5.shutdown()
        except Exception:
            pass

    if _attached():
        _detach()
    yield
    if _attached():
        _detach()

@pytest.fixture(scope="session", autouse=True)
def restore_runtime_state_after_session(tmp_path_factory):
    """Golden snapshot per xdist worker session — the race-free guarantee.

    The per-test snapshot/restore below CANNOT be race-free under
    pytest-xdist: worker B truncates the shared repo-root file mid-write
    while worker A takes its per-test backup, producing 0-byte backups and
    cross-worker pollution (real case: peak_equity=100000 from one worker's
    test leaked into risk_state.json and re-anchored another worker's
    RiskManager equity floor to 20000 → false PERMANENT HALT).

    Session scope closes the loophole: capture the golden state BEFORE any
    test in this worker runs, restore it AFTER all tests finish. Each xdist
    worker starts before its own tests pollute, so every worker snapshots
    and restores the same true golden — the full suite is net-zero on the
    repo-root runtime state files.
    """
    ses_tmp = tmp_path_factory.mktemp("runtime_state_golden")
    goldens = {}
    for name in _RUNTIME_STATE_FILES:
        state_dir = os.path.join(root_dir, "data", "state")
        alt = os.path.join(state_dir, name)
        path = alt if os.path.exists(alt) else os.path.join(root_dir, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                backup = str(ses_tmp / (name + ".golden"))
                shutil.copy2(path, backup)
                goldens[path] = backup
            except OSError:
                pass
    yield
    for path, backup in goldens.items():
        # Atomic restore with retries: a sibling worker may still hold the
        # file open while we replace it (Windows locks open targets).
        for attempt in range(3):
            try:
                tmp_write = path + ".restore_tmp"
                shutil.copy2(backup, tmp_write)
                os.replace(tmp_write, path)
                break
            except OSError:
                if attempt == 2:
                    pass
                else:
                    import time
                    time.sleep(0.2)


@pytest.fixture(autouse=True)
def isolate_runtime_state_singletons(tmp_path, monkeypatch):
    """Keep pytest from writing the repo-root runtime state files.

    The supervisor and autonomy kernels are process-wide singletons that
    persist to supervisor_state.json / autonomy_state.json (+ their control
    files) in the project root. Without this fixture any test touching them
    (e.g. pipeline v2 stage beats) overwrites live operational evidence with
    test residue such as the recorded 'RuntimeError: llm down' principal beat.
    """
    try:
        import operations.supervisor as supervisor_mod

        sup = supervisor_mod.get_supervisor()
        monkeypatch.setattr(sup, "state_file", str(tmp_path / "supervisor_state.json"))
    except Exception:
        pass
    try:
        import operations.autonomy as autonomy_mod

        aut = autonomy_mod.get_autonomy()
        for attr in ("state_file", "control_file"):
            if hasattr(aut, attr):
                monkeypatch.setattr(aut, attr, str(tmp_path / ("autonomy_" + attr)))
    except Exception:
        pass
    # Snapshot shared runtime state files before each test and restore them
    # afterwards. Tests that build RiskManager()/TradeManager() with default
    # paths otherwise leak circuit-breaker pollution (e.g. peak_equity=100000
    # vs start_equity=10000 → permanent 90% drawdown halt) into subsequent
    # tests and live runs.
    backups = {}
    for name in _RUNTIME_STATE_FILES:
        state_dir = os.path.join(root_dir, "data", "state")
        alt = os.path.join(state_dir, name)
        path = alt if os.path.exists(alt) else os.path.join(root_dir, name)
        if os.path.exists(path):
            backup = os.path.join(str(tmp_path), name + ".bak")
            try:
                if os.path.getsize(path) == 0:
                    # Another xdist worker has the file mid-write (open('w')
                    # truncates before the write lands). Snapshotting now would
                    # capture a 0-byte file and the restore below would clobber
                    # the real state. Skip — restore only ever rewrites content
                    # we actually observed.
                    continue
                shutil.copy2(path, backup)
                backups[path] = backup
            except OSError:
                pass
    yield
    for path, backup in backups.items():
        try:
            # Never clobber a live file with an empty backup — if the current
            # file has content but our backup is 0 bytes, something else wrote
            # real state after we snapshotted; restoring would destroy it.
            if os.path.exists(backup) and os.path.getsize(backup) == 0 \
                    and os.path.exists(path) and os.path.getsize(path) > 0:
                continue
            shutil.copy2(backup, path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Ambient repo-root runtime state: per-test read+write sandboxing.
#
# The snapshot/restore fixtures above keep risk_state.json /
# trade_manager_state.json net-unchanged, but tests still READ ambient
# breaker state through CWD/default paths at construction time: a bare
# RiskManager() picks up whatever peak_equity a prior paper boot wrote
# (real case: peak_equity=100000 vs start_equity=10000 -> instant 90%
# peak-to-trough LEVEL 3 breaker -> positions sized 0 or flattened, and
# the portfolio risk gate rejected everything). This fixture re-points the
# repo-root DEFAULT state paths at a per-test sandbox for both reads and
# writes, so no test can observe or pollute ambient runtime state. Tests
# passing explicit paths (tmp_path state_file=..., custom db) are
# unaffected, and production is untouched (fixture exists only in pytest).
# Only paths whose parent directory IS the repo root are redirected;
# artifacts/ and graph-internal state files are deliberately left alone.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def sandbox_ambient_runtime_state(tmp_path, monkeypatch):
    sandbox = tmp_path / "ambient_runtime_state"
    sandbox.mkdir(parents=True, exist_ok=True)

    def _is_ambient(path) -> bool:
        if not path:
            return False
        raw = str(path)
        # ":memory:" and SQLite URI stores are not filesystem paths. abspath()
        # resolves ":memory:" to "<root>\:memory:" - dirname == root_dir, so the
        # sandbox claimed it and rewrote it to a name whose colon is illegal on
        # Windows, turning every in-memory store into "unable to open database
        # file". Leave them alone.
        if raw == ":memory:" or raw.startswith("file:"):
            return False
        try:
            resolved = os.path.abspath(raw)
        except Exception:
            return False
        return os.path.normcase(os.path.dirname(resolved)) == os.path.normcase(root_dir)

    def _sboxed(path) -> str:
        return os.path.join(str(sandbox), os.path.basename(str(path)))

    import state_persistence as _sp
    import trading_safety as _ts
    # Dual-import discovery: conftest puts both the repo root and each package
    # dir on sys.path, so 'trading_safety' and 'core.trading_safety' (etc.) can
    # be DISTINCT module objects with distinct classes. Discover every loaded
    # identity dynamically (plus force-import the canonical ones) and patch
    # each distinct class object — mutating the class fixes every from-import
    # reference too, so interception holds no matter how a module imports it.
    def _discover_identities(basename, force_imports):
        found = []
        for mod in list(sys.modules.values()):
            if mod is not None and getattr(mod, "__name__", "").endswith(basename) \
                    and any(getattr(mod, attr, None) is not None
                            for attr in ("read_json_dict", "atomic_write_json", "TradingStore")):
                found.append(mod)
        for stmt in force_imports:
            try:
                mod = __import__(stmt, fromlist=["x"])
                if mod not in found:
                    found.append(mod)
            except Exception:
                pass
        return found

    _sp_identities = _discover_identities(
        "state_persistence", ["state_persistence", "core.state_persistence"])
    _ts_identities = _discover_identities(
        "trading_safety", ["trading_safety", "core.trading_safety"])

    real_read = _sp_identities[0].read_json_dict
    real_write = _sp_identities[0].atomic_write_json

    def read_json_dict_sandboxed(path, *args, **kwargs):
        if _is_ambient(path):
            path = _sboxed(path)
        return real_read(path, *args, **kwargs)

    def atomic_write_json_sandboxed(path, data, *args, **kwargs):
        if _is_ambient(path):
            path = _sboxed(path)
        return real_write(path, data, *args, **kwargs)

    monkeypatch.setattr(_sp, "read_json_dict", read_json_dict_sandboxed)
    monkeypatch.setattr(_sp, "atomic_write_json", atomic_write_json_sandboxed)

    real_store_init = _ts_identities[0].TradingStore.__init__

    def trading_store_init_sandboxed(self, path="trading_state.db", *args, **kwargs):
        if _is_ambient(path):
            path = _sboxed(path)
        real_store_init(self, path, *args, **kwargs)

    for mod in _sp_identities:
        monkeypatch.setattr(mod, "read_json_dict", read_json_dict_sandboxed)
        monkeypatch.setattr(mod, "atomic_write_json", atomic_write_json_sandboxed)
    _seen_store_classes = set()
    for mod in _ts_identities:
        cls = getattr(mod, "TradingStore", None)
        if cls is None or cls in _seen_store_classes:
            continue
        _seen_store_classes.add(cls)
        monkeypatch.setattr(cls, "__init__", trading_store_init_sandboxed)
    yield


@pytest.fixture(autouse=True)
def isolate_decision_bundle():
    """Clear the in-memory Laya decision bundle around each test.

    ``ui_state.last_decision_bundle`` is a process-wide singleton whose
    ``bundle_age_s`` (wall-clock seconds since publish) is embedded verbatim
    into analysis prompts. A test that leaves a bundle behind makes any
    later in-process A/B replay (pipeline v2 golden replay runs the cycle
    twice, minutes apart) diverge purely by elapsed time — hash mismatch
    with no data difference. Save/clear/restore keeps each test hermetic
    in both directions; tests that need a bundle set it in their own body.
    """
    try:
        from core.shared_state import ui_state
    except Exception:
        yield
        return
    saved = getattr(ui_state, "last_decision_bundle", None)
    ui_state.last_decision_bundle = None
    try:
        yield
    finally:
        ui_state.last_decision_bundle = saved
