"""
Pytest configuration and path setup.
Enables transparent importing of all domain packages (core, intelligence, operations, research, data, ui).
"""
import sys
import os

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)


import pytest


import shutil

# Single import root (C1): install the canonicalising finder before any
# repository module is imported, so bare and package-qualified imports share
# module state and tests/test_single_import_root.py can guard the invariant.
from core._import_root import install as _install_import_root
_install_import_root()

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
# Hermetic broker tests: no test may attach a real MT5 terminal.
#
# With a terminal running (Windows) or the macOS bridge up, tests that call
# MetaTrader5.initialize() attached to the operator's real account (seen in
# the bridge log during a full run). Point the bridge at nothing and make
# initialize()/login() refuse on the module object itself, which every
# import path shares. Tests that need a fake broker inject one. Opt in to a
# real terminal explicitly with LLM_TRADER_TEST_ALLOW_TERMINAL=1.
# ---------------------------------------------------------------------------
if os.environ.get("LLM_TRADER_TEST_ALLOW_TERMINAL") != "1":
    os.environ["MT5_BRIDGE_KEY"] = ""
    os.environ["MT5_BRIDGE_KEY_FILE"] = os.devnull
    os.environ["MT5_BRIDGE_PORT"] = "9"  # discard port; nothing listens
    try:
        import MetaTrader5 as _mt5_for_tests
    except Exception:
        _mt5_for_tests = None
    if _mt5_for_tests is not None:
        def _terminal_blocked_in_tests(*_args, **_kwargs):
            return False
        _mt5_for_tests.initialize = _terminal_blocked_in_tests
        _mt5_for_tests.login = _terminal_blocked_in_tests


# ---------------------------------------------------------------------------
# Tests must not modify tracked files.
#
# A full run used to rewrite committed evidence (trial ledgers, caches,
# scheduler reports). The sandbox fixtures below cover the known writers;
# this check catches every other one: the session fails and lists the files.
# Runs on the xdist controller (or a non-xdist session) only.
# ---------------------------------------------------------------------------
def _tracked_changes():
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", root_dir, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except Exception:
        return None
    return set(out.splitlines())


def pytest_sessionstart(session):
    if not hasattr(session.config, "workerinput"):
        session.config._tracked_before = _tracked_changes()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_tracked_before", None)
    if before is None or hasattr(session.config, "workerinput"):
        return
    after = _tracked_changes() or set()
    changed = sorted(after - before)
    if changed:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = ["TESTS MODIFIED TRACKED FILES (restore with: git checkout -- <file>):"]
        lines += [f"  {line}" for line in changed[:50]]
        if reporter:
            reporter.write_line("\n".join(lines), red=True)
        session.exitstatus = 1

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
_RUNTIME_STATE_FILES = ("supervisor_state.json", "risk_state.json", "trade_manager_state.json")


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
        # The singleton loaded the repo-root autonomy_state.json when it was
        # created, and trading_allowed() now re-reads state on every check,
        # so start each test from clean defaults with no store attached.
        fresh = autonomy_mod.AutonomyManager(
            state_file=str(tmp_path / "autonomy_fresh_state.json"),
            control_file=str(tmp_path / "autonomy_fresh_control"),
        )
        monkeypatch.setattr(aut, "_state", fresh._state)
        monkeypatch.setattr(aut, "store", None)
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
def isolated_kill_switch_latch(tmp_path, monkeypatch):
    """Give every test a fresh news kill-switch latch.

    The latch is enforced on the validator and gateway (Phase 3), and it
    persists by design, so one test that trips it would otherwise block
    entries for every later test in the same xdist worker.
    """
    import sys as _sys

    path = str(tmp_path / "d9_kill_switch_latch.json")
    monkeypatch.setenv("D9_LATCH_PATH", path)
    yield
    registry = getattr(_sys, "_d9_kill_switch_latch_registry", None)
    if isinstance(registry, dict):
        for token in [t for t in registry if t.endswith(path)]:
            registry.pop(token, None)


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

    import core.state_persistence as _sp
    import core.trading_safety as _ts
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
_ARTIFACTS_SANDBOX_ROOT = {"path": None}


def artifacts_sandbox_path(path):
    """Map a logical ``artifacts/...`` path to this test's sandboxed path.

    Tests that assert a default-path artifact was written (``result["artifact
    _path"]``) need to follow the write to its real destination; the guard
    deliberately makes ``os.path.exists(<repo>/artifacts/x.json)`` false.  Use
    this to resolve the logical path instead of reaching around the guard.
    """
    root = _ARTIFACTS_SANDBOX_ROOT["path"]
    if root is None or not path:
        return path
    try:
        resolved = os.path.normcase(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError):
        return path
    artifacts_dir = os.path.normcase(os.path.abspath(os.path.join(root_dir, "artifacts")))
    prefix = artifacts_dir + os.sep
    if not resolved.startswith(prefix):
        return path
    return os.path.join(root, os.path.relpath(resolved, artifacts_dir))


@pytest.fixture(autouse=True)
def sandbox_artifacts_caches(tmp_path, monkeypatch):
    """No test may read or write the repo's shared ``artifacts/`` caches.

    ``artifacts/`` holds live caches and evidence written by real engine runs
    (the alternative-nowcast snapshot, COT/EIA/FX calendar caches, mined
    alphas, the meta-RL and PPO state, the trial ledger DB).  Every test run was
    silently rewriting them: those files are tracked in git, so a suite run
    produced a diff in production data, and a test could equally have PASSED
    only because a previous run had left a warm cache behind.  Test results were
    therefore a function of whatever happened to be on disk.

    ``sandbox_ambient_runtime_state`` deliberately scoped itself to paths whose
    parent IS the repo root, and artifacts/ was left alone.  But the artifacts
    writers do not route through ``state_persistence`` — they use a bare
    ``open()`` + ``json.dump`` (data/alternative_data_provider.py:437) and
    ``pathlib.Path(...).write_text`` (core/alpha_ensemble.py:259,
    core/auto_quant_tools.py:1279) — so widening that hook's predicate would
    have caught almost nothing.  This fixture intercepts the IO primitives.

    Both ``builtins.open`` and ``io.open`` are patched: they are the same
    function object, but ``pathlib.Path.open``/``write_text`` resolve ``io.open``
    directly, so patching only the ``builtins`` name leaves every Path-based
    writer untouched.

    Three further write paths resolve through none of those names and are
    hooked separately, because each was found by watching the real
    artifacts/ tree change under a green suite:

    * ``core/file_utils.safe_atomic_write`` stages a temp file and commits it
      with ``os.replace`` (core/file_utils.py:46) — the whole atomic JSON
      export path, e.g. strategy_metrics.json.
    * ``sqlite3.connect("file:<path>?mode=ro", uri=True)`` (core/trial_ledger
      .py:405) — a URI is not a path, so ``abspath`` of it never matches the
      artifacts prefix, yet the open still materialises real ``-wal``/``-shm``
      index files beside the shared database.
    * subprocesses, which start with a fresh unpatched ``open``.  Only
      environment variables cross that boundary; see the note at the
      LLM_TRADER_POST_SESSION_REPORT backstop below.

    SCOPE: ``<repo>/artifacts`` ONLY.  Runtime state that lives at the repo
    ROOT is deliberately not covered and must be pointed at tmp_path by the
    test itself.  ``strategy_registry.json``, ``lessons.jsonl``,
    ``incidents.jsonl``, ``approvals_outbox.jsonl`` and
    ``approval_decisions.jsonl`` are all module-level constants defaulting to
    the repo root (operations/strategy_factory.py:35-37,
    operations/agents/learning_agent.py:23-25), and a test that constructs the
    owning object without overriding them writes production data.  That is not
    hypothetical: tests/test_gui_api_security.py appended an ``id: ""`` record
    to the tracked registry on every run until it was given an explicit
    tmp_path factory.  If you add a root-level state file, wire the test to
    tmp_path rather than widening this predicate - a blanket root redirect
    would also capture source files the suite legitimately reads.

    The one exception is a shared root CACHE that tests construct through no
    argument at all: ``api_cache.json`` (data/api_manager.py:188).  That one is
    covered by a fellow fixture rather than by path shape -
    ``isolate_shared_api_cache`` below repoints the module constant, so the
    "wire the test to tmp_path yourself" rule above does not apply to it.

    Only paths that resolve inside ``<repo>/artifacts`` are rewritten; every
    other path (sources, tmp_path, the test's own databases) passes through
    untouched.  Sub-directories are mirrored and parents are created on write,
    so ``artifacts/backtest_results/x.json`` keeps its shape.
    """
    import builtins
    import io
    import sqlite3

    artifacts_dir = os.path.normcase(os.path.abspath(os.path.join(root_dir, "artifacts")))
    # Deliberately NOT named "artifacts", and deliberately NOT pre-created:
    # several tests build their own ``tmp_path / "artifacts"`` tree (and call
    # .mkdir() themselves), which collided with a pre-created directory of the
    # same name.  Directories are created lazily on write instead.
    sandbox = tmp_path / "_artifacts_sandbox"

    def _rel(path):
        """Relative path if *path* is inside artifacts/, else None."""
        try:
            raw = os.fspath(path)
        except TypeError:
            return None
        if isinstance(raw, bytes):
            return None
        if not raw:
            return None
        try:
            resolved = os.path.normcase(os.path.abspath(raw))
        except (ValueError, OSError):
            return None
        if resolved == artifacts_dir:
            return ""
        prefix = artifacts_dir + os.sep
        if not resolved.startswith(prefix):
            return None
        return os.path.relpath(resolved, artifacts_dir)

    def _sandboxed(path):
        rel = _rel(path)
        if rel is None:
            return None
        return str(sandbox / rel) if rel else str(sandbox)

    def _ensure_parent(target):
        parent = os.path.dirname(target)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass

    real_open = builtins.open

    def open_sandboxed(file, mode="r", *args, **kwargs):
        target = _sandboxed(file)
        if target is None:
            return real_open(file, mode, *args, **kwargs)
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            _ensure_parent(target)
        return real_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_sandboxed)
    monkeypatch.setattr(io, "open", open_sandboxed)

    # core/file_utils.safe_atomic_write writes via tempfile.mkstemp +
    # os.fdopen + os.replace, none of which resolve through open() names, so
    # every atomic JSON export (strategy_metrics.json, the registry, mined
    # alphas...) landed in the shared tree.  Redirect the rename itself; the
    # staging temp file stays beside the real destination, which is harmless
    # because it is unlinked by the replace.
    real_replace = os.replace

    def replace_sandboxed(src, dst, *args, **kwargs):
        target = _sandboxed(dst)
        if target is None:
            return real_replace(src, dst, *args, **kwargs)
        _ensure_parent(target)
        return real_replace(src, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace_sandboxed)

    # Directory creation and deletion must be redirected for the same reason
    # as the rename: tests build and clean up artifacts trees with
    # Path.mkdir()/Path.unlink(), which resolve through os.mkdir/os.unlink and
    # never touch open().  Deliberately NOT redirecting os.stat or
    # os.path.exists: those answer "does the REAL artefact exist", which is
    # exactly what the guard's own tests assert on, and production gates real
    # behaviour on them (core/alpha_ensemble.py:160).  Redirecting presence
    # made six of those assertions vacuous in the other direction.
    real_makedirs = os.makedirs
    real_mkdir = os.mkdir
    real_unlink = os.unlink

    def _mkdir_target(path):
        return _sandboxed(path) or path

    def makedirs_sandboxed(name, mode=0o777, exist_ok=False):
        return real_makedirs(_mkdir_target(name), mode, exist_ok)

    def mkdir_sandboxed(path, mode=0o777, *args, **kwargs):
        # pathlib.mkdir() calls os.mkdir(path) with NO exist_ok argument and
        # handles FileExistsError itself by walking up to the parent; the
        # recursive Path.mkdir(parents=True) retry therefore reaches this hook
        # twice for the same directory.  Swallowing it here keeps that loop
        # from erroring out on the second pass.
        target = _mkdir_target(path)
        if target is not path:
            _ensure_parent(target)
        try:
            return real_mkdir(target, mode, *args, **kwargs)
        except FileExistsError:
            if os.path.isdir(target):
                return None
            if args and args[0]:
                return None
            raise

    def unlink_sandboxed(path, *args, **kwargs):
        return real_unlink(_mkdir_target(path), *args, **kwargs)

    monkeypatch.setattr(os, "makedirs", makedirs_sandboxed)
    monkeypatch.setattr(os, "mkdir", mkdir_sandboxed)
    monkeypatch.setattr(os, "unlink", unlink_sandboxed)
    monkeypatch.setattr(os, "remove", unlink_sandboxed)

    # .db artefacts (llm_decisions.db, trial_ledger.db) never touch open().
    # sqlite3 URIs ("file:<path>?mode=ro", uri=True) must be unwrapped first:
    # abspath() of the URI string does not start with the artifacts prefix, so
    # the plain check misses them, and a read-only open still materialises the
    # real -wal/-shm index files next to the shared database.
    real_connect = sqlite3.connect

    def connect_sandboxed(database, *args, **kwargs):
        raw = database
        query = ""
        if isinstance(raw, str) and raw.startswith("file:"):
            body = raw[len("file:"):]
            cut = min([i for i in (body.find("?"), body.find("#")) if i != -1],
                      default=-1)
            if cut != -1:
                query = body[cut:]
                body = body[:cut]
            target = _sandboxed(body)
            if target is None:
                return real_connect(database, *args, **kwargs)
            _ensure_parent(target)
            kwargs["uri"] = True
            return real_connect("file:%s%s" % (target, query), *args, **kwargs)
        target = _sandboxed(raw)
        if target is None:
            return real_connect(database, *args, **kwargs)
        _ensure_parent(target)
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect_sandboxed)

    # Subprocesses are the one leak the patches above cannot reach: a child
    # interpreter starts with a fresh, unpatched open().  Environment
    # variables *are* inherited, so this is the only lever that covers them.
    # Without it the post-session report hook spawns
    # scripts/scheduler_tuning_report.py, which writes
    # artifacts/scheduler_tuning_report.json into the shared tree (plus a
    # timestamped archive) every time a test process shuts down.  A test that
    # genuinely wants the hook sets the variable itself, which wins.
    monkeypatch.setenv("LLM_TRADER_POST_SESSION_REPORT", "0")

    _ARTIFACTS_SANDBOX_ROOT["path"] = str(sandbox)
    try:
        yield
    finally:
        _ARTIFACTS_SANDBOX_ROOT["path"] = None


@pytest.fixture(autouse=True)
def sandbox_root_databases(tmp_path, monkeypatch):
    """No test may open the shared ``*.db`` databases at the repo root.

    ``sandbox_ambient_runtime_state`` covers the JSON side of the repo root;
    SQLite was not, and SQLite is where the heavyweight live state lives.
    An instrumented full-suite run (``sqlite3.connect`` traced per test) showed
    226 opens of root-level databases from the suite alone:

    * ``local_agent_memory.db`` - 131, the default in
      intelligence/local_agent_memory.py:94, so every test that constructed a
      memory store read and rewrote the operator's real memory;
    * ``trading_state.db`` - 78, both through ``TradingStore`` (already
      redirected by the ambient sandbox) and through the bare
      ``sqlite3.connect("trading_state.db", timeout=2)" at
      operations/strategy_factory.py:305, which the ambient sandbox never saw
      because it does not route through ``state_persistence``;
    * ``control_plane.db`` - 7, built as an ABSOLUTE path from ``__file__`` at
      operations/autonomy.py:30 (the ambient sandbox redirects the autonomy
      ``control_file`` but not this one);
    * ``agent_events.db`` - 2, ``alerts_outbox.db`` - 2.

    The hook chains onto ``sandbox_artifacts_caches``: it captures whatever
    ``sqlite3.connect`` is installed at setup (that fixture runs first), so an
    artifacts path is still rewritten by the artifacts sandbox before it ever
    reaches the root check here.

    Same destination as the ambient JSON sandbox (``tmp_path/
    ambient_runtime_state/<basename>``) on purpose: a test that writes a row
    through ``TradingStore`` and reads it back through a raw connect must land
    on the same file, or the two sandboxes would disagree about what the test
    wrote.  ``:memory:`` and ``file:`` URIs pass through, and so does every
    path that is not inside the repo root - a test's own tmp_path databases
    are untouched.
    """
    import sqlite3

    real_connect = sqlite3.connect
    sandbox = tmp_path / "ambient_runtime_state"
    root = os.path.normcase(root_dir)

    def _target(database):
        """Sandboxed path if *database* resolves inside the repo root, else None."""
        try:
            raw = os.fspath(database)
        except TypeError:
            return None
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8", "replace")
            except Exception:
                return None
        if not raw or ":memory:" in raw or raw.startswith("file:"):
            return None
        try:
            resolved = os.path.normcase(os.path.abspath(raw))
        except (ValueError, OSError):
            return None
        if os.path.dirname(resolved) != root:
            return None
        return str(sandbox / os.path.basename(resolved))

    def connect_sandboxed(database, *args, **kwargs):
        target = _target(database)
        if target is None:
            return real_connect(database, *args, **kwargs)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect_sandboxed)


@pytest.fixture(autouse=True)
def isolate_shared_api_cache(tmp_path, monkeypatch):
    """No test may read or write the shared ``api_cache.json`` at the root.

    ``APIManager`` is the one shared-cache writer that does not live in
    ``artifacts/``: it defaulted to the bare relative name ``"api_cache.json"``
    (data/api_manager.py:188), so EVERY instance - regardless of which test
    constructed it - read and rewrote one file at the repo root, shared with
    the live bot and with whatever other thread is running a suite here.

    ``sandbox_artifacts_caches`` deliberately scopes itself to ``artifacts/``
    and cannot help: a blanket repo-root redirect would also capture source
    files the suite legitimately reads, and this path is derived from a
    module constant rather than passed through ``state_persistence``. So the
    cache location is a module constant (``api_manager.API_CACHE_FILE``) that
    the suite repoints at ``tmp_path``, leaving the production default intact.

    Module-scoped, not per-test: this lived as a fixture inside one test
    module at first and three others (tests/test_boot_datasheet.py:217,
    tests/test_data_ingestion_hardening.py:16,
    tests/test_openbb_data_bridge.py:78) still constructed ``APIManager()``
    with no arguments and rewrote the root cache. A shared file needs a
    suite-wide owner, or the next module added is another leak.
    """
    import data.api_manager as api_manager

    monkeypatch.setattr(
        api_manager, "API_CACHE_FILE", str(tmp_path / "api_cache.json"))


@pytest.fixture(autouse=True)
def isolate_shared_root_databases(tmp_path, monkeypatch):
    """No test may read or write the shared ``*.db`` databases at the repo root.

    ``artifacts/`` has ``sandbox_artifacts_caches``; the root ``*.json`` state
    has ``sandbox_ambient_runtime_state`` (which only reaches paths that flow
    through ``state_persistence`` or ``TradingStore.__init__``).  Shared SQLite
    databases reached neither hook, so the owners below were writing the live
    files at the repo root on every test run - the same failure mode as
    ``api_cache.json``, a different API.

    Both owners are module singletons built with a bare default, so the test
    has no argument to override:

    * ``core.compliance_audit.get_audit()`` constructs
      ``ComplianceAudit(db_path="compliance_audit.db")" once and caches it in
      the module global ``_AUDIT``; every ``audit.log(...)`` in a test
      appended a row to the shared root database.
    * ``core.external_guardian._page_human`` calls ``enqueue()`` with no path
      of its own, so the outbox location is the module constant
      ``GUARDIAN_ALERTS_DB``.

    The default is redirected into ``tmp_path`` and the singleton is dropped so
    the next ``get_audit()`` rebuilds against the sandbox - a singleton already
    bound to the real path would otherwise keep writing there for the rest of
    the session.  Tests that pass an explicit path are untouched, and
    production is untouched (the default argument is still the real filename;
    only the argument-free call sites get redirected).
    """
    try:
        from core import compliance_audit as ca_mod

        real_init = ca_mod.ComplianceAudit.__init__

        def compliance_audit_init(self, db_path=None, *args, **kwargs):
            if db_path in (None, "compliance_audit.db"):
                db_path = str(tmp_path / "compliance_audit.db")
            return real_init(self, db_path, *args, **kwargs)

        monkeypatch.setattr(ca_mod.ComplianceAudit, "__init__", compliance_audit_init)
        # Discover every loaded spelling of the module (bare + package-qualified
        # can be distinct objects when a root is on sys.path) and drop any
        # singleton already bound to the real path.
        for mod in list(sys.modules.values()):
            if mod is not None and getattr(mod, "__name__", "").endswith("compliance_audit") \
                    and hasattr(mod, "_AUDIT"):
                monkeypatch.setattr(mod, "_AUDIT", None)
    except Exception:
        pass
    try:
        from core import external_guardian as eg_mod

        monkeypatch.setattr(
            eg_mod, "GUARDIAN_ALERTS_DB", str(tmp_path / "guardian_alerts.db"))
    except Exception:
        pass



# ---------------------------------------------------------------------------
# Test lanes (audit remediation item 11).
#
# pytest.ini has always declared `unit`, `integration` and `slow`, but not one
# test module used any of them - 0 occurrences across the suite - so the
# audit's proposed `-m "not slow"` fast lane deselected nothing and the
# unit/integration split was cosmetic. The suite still exceeded a 10-minute
# window, which is why there was no green/red signal to act on.
#
# Hand-marking 400+ modules is churn that rots the moment a file is added, so
# the lane is derived from the module name at collection time: a new module is
# classified by existing, not by remembering to update a list.
#
# Additive only. An explicit decorator marker is never removed or overridden,
# and a plain `pytest` run still selects exactly the same tests as before -
# these markers only bite when someone passes `-m`.
# ---------------------------------------------------------------------------

# Modules that wire several subsystems together: expensive, and a failure
# there is ambiguous (which layer broke?) rather than local.
_LANE_INTEGRATION_TOKENS = ("e2e", "integration", "full", "end_to_end")

# Heavy enough to blow a per-commit budget even though they touch one
# subsystem: full backtests, harness modes, MT5/live paths, scheduler sweeps.
_LANE_SLOW_TOKENS = (
    "e2e", "integration", "full", "harness", "pipeline",
    "backtest", "microstructure", "live", "smoke",
)


def pytest_collection_modifyitems(config, items):
    """Stamp the declared-but-unused lane markers onto collected tests."""
    for item in items:
        path = getattr(item, "path", None) or getattr(item, "fspath", "")
        base = os.path.basename(str(path)).lower()

        # Integration implies slow: nothing cheap cross-wires subsystems, and
        # marking one without the other would let `-m "not slow"` still drag
        # the expensive lanes into the fast run.
        if any(token in base for token in _LANE_INTEGRATION_TOKENS):
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.slow)
            continue

        if any(token in base for token in _LANE_SLOW_TOKENS):
            item.add_marker(pytest.mark.slow)
            continue

        # `unit` is the complement, so `-m unit` is the fast lane and is not
        # silently empty the way an unused marker always is.
        item.add_marker(pytest.mark.unit)
