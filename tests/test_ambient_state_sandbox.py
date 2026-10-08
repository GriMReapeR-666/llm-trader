"""Regression tests: ambient repo-root runtime state is sandboxed in pytest.

The autouse ``sandbox_ambient_runtime_state`` fixture (tests/conftest.py)
re-points every read/write of a repo-root default state file
(risk_state.json, trade_manager_state.json, trading_state.db, ...) at a
per-test sandbox directory. This pins the two failure modes it kills:

1. READ leakage: a bare RiskManager() must never inherit ambient breaker
   state (real case: a paper boot wrote peak_equity=100000 vs
   start_equity=10000 -> any test constructing RiskManager() with defaults
   started in a permanent 90% peak-to-trough LEVEL 3 breaker).
2. WRITE leakage: state persisted through the default paths must land in
   the sandbox, never in the repo root's live operational files.

Explicit paths (tmp_path state files, custom DB paths) must stay untouched.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import state_persistence as sp
from core.risk_manager import RiskManager
from core.trading_safety import TradingStore


_AMBIENT = {
    "start_equity": 10000.0,
    "weekly_start_equity": 10000.0,
    "date": "2026-09-24",
    "peak_equity": 100000.0,  # the poisoned ambient breaker from a paper boot
    "consecutive_losses": 0,
}


def _seed_ambient_root_state():
    """Simulate a paper boot having written breaker state to the repo root.

    Deliberately NOT ``sp.atomic_write_json``: the autouse sandbox fixture
    intercepts that helper and would redirect the write into tmp_path, which is
    precisely what this helper must avoid. Write the absolute repo-root path
    directly.

    Note: this is a plain truncating write, and it has to be. The repo-root
    ``risk_state.json`` is shared by every test that constructs a RiskManager
    with default paths, and on Windows ``os.replace`` onto a file another xdist
    worker holds open fails with ``PermissionError: [Errno 13]``. An atomic
    replace is therefore not available here. The read side is made
    race-tolerant instead — see
    ``test_default_state_write_lands_in_sandbox_not_repo_root``.
    """
    path = os.path.join(os.getcwd(), "risk_state.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_AMBIENT, fh)
    return path


def test_bare_risk_manager_never_reads_ambient_breaker(tmp_path):
    """A bare RiskManager() starts clean even with an ambient LEVEL-3 breaker on disk."""
    seeded = _seed_ambient_root_state()
    assert os.path.exists(seeded)  # ambient state really is there

    rm = RiskManager(balance=10000.0)  # default state_file, no explicit path

    # Pre-fix, load_state() adopted peak_equity=100000 -> instant 90% DD.
    assert rm.peak_equity == pytest.approx(10000.0)
    # And the breaker check passes at flat equity.
    assert rm.check_max_drawdown(10000.0) is True
    assert rm._flatten_reason is None


def _repo_root_file_excludes(sentinel: bytes, attempts: int = 5) -> bool | None:
    """Does the repo-root risk_state.json avoid ``sentinel``?

    Returns True/False, or None if the file could not be read cleanly within
    ``attempts`` tries.

    The repo-root ``risk_state.json`` is shared by every test that builds a
    RiskManager with default paths, and on Windows a sibling xdist worker that
    is mid-write holds the file in a state where a concurrent read raises
    ``PermissionError: [Errno 13]`` (and ``open(path, "w")`` truncation means a
    reader can also observe a short or all-NUL file). This check is therefore
    corroborating evidence only, and must not be allowed to flake the build.

    The load-bearing assertion is the sandbox read in the caller, which is
    deterministic; this only proves the sentinel did not also reach the repo
    root. If the file cannot be read cleanly, report None and let the caller
    skip rather than fail.
    """
    root_path = os.path.join(os.getcwd(), "risk_state.json")
    for attempt in range(attempts):
        try:
            if not os.path.exists(root_path):
                return True
            with open(root_path, "rb") as fh:
                raw = fh.read()
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
            continue
        except OSError:
            time.sleep(0.05 * (attempt + 1))
            continue
        if len(raw) == 0 or raw == b"\x00" * len(raw):
            # Truncated / mid-write: not a meaningful observation.
            time.sleep(0.05 * (attempt + 1))
            continue
        return sentinel not in raw
    return None


def test_default_state_write_lands_in_sandbox_not_repo_root(tmp_path):
    """Default-path persistence is redirected: the repo root never sees this write.

    The load-bearing assertion is that the intercepted write landed in the
    per-test sandbox. The repo-root check is corroborating, and is expressed as
    a *provenance* question ("does the repo root contain this write's
    sentinel?") rather than byte-equality against a snapshot, because the shared
    repo-root file is concurrently rewritten by other xdist workers.
    """
    payload = {"probe": "sandbox_write", "peak_equity": 1.0}
    sentinel = b"sandbox_write"

    assert sp.atomic_write_json("risk_state.json", payload) is True

    # The intercepted write landed in the sandbox...
    assert sp.read_json_dict("risk_state.json") == payload

    # ...and the repo root did not acquire it. Tolerate an unreadable
    # repo-root file (a sibling worker holding it mid-write) by skipping.
    verdict = _repo_root_file_excludes(sentinel)
    if verdict is None:
        pytest.skip("repo-root risk_state.json unreadable: sibling xdist worker mid-write")
    assert verdict, "sandboxed write leaked into the repo-root risk_state.json"


def test_sandbox_roundtrip_is_consistent(tmp_path):
    """Write-then-read through the interception layer sees the same sandbox."""
    payload = {"probe": "roundtrip", "trade_timestamps": []}
    assert sp.atomic_write_json("trade_manager_state.json", payload) is True
    assert sp.read_json_dict("trade_manager_state.json") == payload
    # And it never materialized at the repo root.
    assert not os.path.exists(os.path.join(os.getcwd(), "trade_manager_state.json")) \
        or json.load(open(os.path.join(os.getcwd(), "trade_manager_state.json"), encoding="utf-8")) != payload


def test_default_trading_store_is_redirected():
    """TradingStore() default path resolves to the sandbox, not the live journal."""
    store = TradingStore()
    assert os.path.normcase(os.path.dirname(os.path.abspath(store.path))) != \
        os.path.normcase(os.getcwd())
    assert os.path.basename(store.path) == "trading_state.db"


def test_explicit_paths_are_never_redirected(tmp_path):
    """Tests pinning explicit tmp paths keep working untouched."""
    state_file = tmp_path / "explicit_risk_state.json"
    rm = RiskManager(balance=5000.0, state_file=str(state_file))
    rm.save_state()
    assert state_file.exists()

    db_path = tmp_path / "explicit_store.db"
    store = TradingStore(path=str(db_path))
    assert store.path == str(db_path)

    # Non-root-relative relative paths (e.g. artifacts/) are also left alone.
    assert sp.read_json_dict(os.path.join("artifacts", "does_not_exist.json")) is None


# ---------------------------------------------------------------------------
# Registration guard: the sandbox only redirects paths whose parent is the
# repo root, and it has no per-file list — coverage comes from the path
# shape. So a NEW runtime state file appearing at repo root would silently
# fall outside every guarantee this module pins unless it is a known name.
# The guard below forces that decision into the open: every root-level
# *.json must be (a) registered in STATE_REGISTRY (then it is redirected by
# the sandbox), (b) explicitly allowlisted here as operational (deliberately
# NOT redirected — it is shared live operational data tests must not own),
# or (c) recognized as test residue (test_* prefix).
# ---------------------------------------------------------------------------
from core.state_persistence import STATE_REGISTRY

# Legitimate repo-root files that are NOT per-module runtime state and are
# deliberately left untouched by the sandbox. Keep sorted. Add a file here
# ONLY with a comment naming its owner and why tests must not redirect it.
_AMBIENT_ALLOWLIST = frozenset({
    # operational configuration / publication files
    "accounts.json",          # live broker account fingerprints
    "api_cache.json",         # shared provider cache (live bot); the suite
                              # never writes it - conftest repoints
                              # api_manager.API_CACHE_FILE at tmp_path - but
                              # the file exists on disk, so it still needs an
                              # entry here or the accounting test fails
    "event_windows.json",     # macro event calendar (read-mostly)
    "journal_audit.json",     # audit output written by ops tooling
    "market_state.json",      # market-state snapshot feed for the UI
    "scanner_state.json",     # scanner service state (separate process)
    "strategy_registry.json", # strategy lifecycle registry (shared authority)
    "strategy_state.json",    # strategy runtime state (shared authority)
    "backtest_report.json",   # published backtest report
    "rl_model.json",          # serialized RL model artifact
    "opencode.json",          # tooling config
    # gitignored cross-process caches (shared by design, content self-invalidates)
    ".security_audit_cache.json",  # redacted security-scan cache, TTL + HEAD fingerprint
    # legacy residue that predates the guard; never redirected, never written
    "_risk_state_polluted_backup.json",
    "risk_state_demo.json",
})

_TEST_RESIDUE_PREFIX = "test_"


def test_no_unregistered_runtime_state_files_at_repo_root():
    """Every root *.json must be registry/allowlist/test-residue accounted for."""
    unaccounted = []
    for name in sorted(os.listdir(os.getcwd())):
        if not name.endswith(".json"):
            continue
        if name.startswith(_TEST_RESIDUE_PREFIX) or name in STATE_REGISTRY.values() \
                or name in _AMBIENT_ALLOWLIST:
            continue
        unaccounted.append(name)
    assert not unaccounted, (
        "New runtime state file(s) at repo root are not registered anywhere: "
        f"{unaccounted}. Add them to core.state_persistence.STATE_REGISTRY "
        "(so the pytest ambient-state sandbox redirects them and the store "
        "migration covers them) or to _AMBIENT_ALLOWLIST in this test with a "
        "comment naming the owning process."
    )


def test_every_registry_state_is_root_relative_so_sandbox_covers_it():
    """All STATE_REGISTRY filenames are bare root-relative names (no dirs)."""
    badly_shaped = {
        key: name for key, name in STATE_REGISTRY.items()
        if os.path.dirname(str(name)) not in ("", ".")
    }
    assert not badly_shaped, (
        "STATE_REGISTRY entries must be bare filenames so the repo-root "
        f"ambient sandbox redirects them; these are not: {badly_shaped}"
    )
