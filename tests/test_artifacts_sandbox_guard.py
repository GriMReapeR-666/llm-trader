"""The shared-state sandboxes in conftest must actually intercept.

An autouse guard that silently fails to intercept is worse than none: it looks
like protection in the diff while tests keep rewriting the live caches.  These
tests assert the interception itself, through the same bare ``open()`` and
``sqlite3.connect`` calls the production writers use — they do NOT route
through ``state_persistence``, which is exactly why widening that hook was not
enough.

Coverage is broader than ``artifacts/``: the root-level shared files that
sandbox_artifacts_caches cannot reach by path shape (the api_cache.json
response cache, the compliance audit database, the guardian alert outbox)
are pinned in the section at the bottom of this file, because each is
redirected by repointing a module constant or a singleton default rather
than by matching a directory prefix.

``os.open`` + ``os.fdopen`` are the low-level descriptors and are NOT patched
(the guard patches the ``builtins.open`` and ``io.open`` NAMES).  That is what
lets these tests put a real file in the shared tree and then prove a test
cannot see it.
"""
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS = os.path.join(ROOT, "artifacts")

# Captured at import time, BEFORE the sandbox fixture patches these names.
# Several tests here deliberately plant a REAL file in the shared tree with
# os.open/os.fdopen (the low-level descriptors the guard does not intercept)
# and must clean it up; going through the patched os.remove would target the
# sandbox instead and leave the probe behind.
_REAL_REMOVE = os.remove
_REAL_OPEN = os.open
_REAL_RMDIR = os.rmdir


def test_bare_open_write_never_reaches_the_repo():
    target = os.path.join(ARTIFACTS, "guard_probe_should_not_exist.json")
    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump({"written": "by a test"}, fh)
        assert not os.path.exists(target), (
            "a test wrote %s into the repo's shared artifacts/" % target)
    finally:
        if os.path.exists(target):
            os.remove(target)


def test_bare_open_read_does_not_observe_shared_state():
    """A read of a genuinely present shared artefact must miss.

    The file is planted with ``io.open`` so it really exists on disk; if the
    sandbox intercepted reads it would be invisible, and if not the test would
    see live engine state.
    """
    target = os.path.join(ARTIFACTS, "guard_read_probe.json")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"secret": "live engine state"}, fh)
    assert os.path.exists(target), "probe was not planted"
    try:
        try:
            with open(target, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return  # sandbox is empty — the correct outcome
        assert "secret" not in data, (
            "a test read the shared artefacts file %s instead of a sandbox"
            % target)
    finally:
        if os.path.exists(target):
            _REAL_REMOVE(target)


def test_nested_subdirectory_writes_are_redirected():
    target = os.path.join(ARTIFACTS, "backtest_results", "guard_nested_probe.json")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("{}")
    assert not os.path.exists(target)


def test_sqlite_connect_is_redirected():
    target = os.path.join(ARTIFACTS, "guard_probe.db")
    conn = sqlite3.connect(target)
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    assert not os.path.exists(target), (
        "a test created the sqlite artefact %s in the shared tree" % target)


def test_paths_outside_artifacts_are_untouched(tmp_path):
    """The guard must not over-reach: tmp_path and repo sources stay readable."""
    probe = tmp_path / "fine.json"
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("{}")
    assert probe.exists(), "the sandbox blocked a tmp_path write"

    with open(os.path.join(ROOT, "pytest.ini"), encoding="utf-8") as fh:
        assert "[pytest]" in fh.read(), "the sandbox blocked a repo-root read"


def test_post_session_report_hook_is_disabled_for_tests():
    """The hook's subprocess is the one leak the ``open`` patches cannot reach.

    A child interpreter is a fresh process with its own unpatched ``open``,
    so redirecting names in-process does nothing for it.  The env var is the
    only inherited lever; if it is not forced off, every test process that
    shuts down through crash_recovery spawns scheduler_tuning_report.py,
    which rewrites the shared artifacts/scheduler_tuning_report.json.
    """
    assert os.environ.get("LLM_TRADER_POST_SESSION_REPORT") == "0", (
        "the post-session report hook is armed during tests; its subprocess "
        "writes into the shared artifacts/ tree")


def test_atomic_write_does_not_land_in_the_repo():
    """``safe_atomic_write`` uses mkstemp + os.replace, bypassing ``open``.

    core/file_utils.safe_atomic_write stages a temp file and commits it with
    ``os.replace``, neither of which resolves through the patched ``open``
    names.  Every atomic JSON export (strategy_metrics.json, the strategy
    registry, mined alphas) went through here, so it needs its own hook.
    """
    target = os.path.join(ARTIFACTS, "guard_probe_atomic.json")
    try:
        from core.file_utils import safe_atomic_write_json
        safe_atomic_write_json(target, {"written": "atomically"})
        assert not os.path.exists(target), (
            "an atomic write landed %s in the shared artifacts/ tree" % target)
    finally:
        if os.path.exists(target):
            os.remove(target)


def test_sqlite_uri_connect_is_redirected():
    """``file:<path>?mode=ro`` URIs must be unwrapped before the path check.

    ``os.path.abspath`` of a URI string does not start with the artifacts
    prefix, so the plain check misses it.  A read-only open still
    materialises ``-wal``/``-shm`` index files next to the shared database,
    which is exactly how trial_ledger.db-shm kept changing under test.
    """
    target = os.path.join(ARTIFACTS, "guard_probe_uri.db")
    planted = target + "-shm"
    try:
        conn = sqlite3.connect(target)
        try:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        # The read-only URI open mirrors core/trial_ledger.py:405.
        ro = sqlite3.connect("file:%s?mode=ro" % target, uri=True)
        try:
            ro.execute("SELECT count(*) FROM t").fetchone()
        finally:
            ro.close()
        assert not os.path.exists(target), (
            "a sqlite URI open touched %s in the shared tree" % target)
        assert not os.path.exists(planted), (
            "a sqlite URI open materialised %s in the shared tree" % planted)
    finally:
        for leftover in (target, target + "-wal", planted):
            if os.path.exists(leftover):
                _REAL_REMOVE(leftover)

def test_mkdir_and_unlink_are_redirected():
    """Directory creation and cleanup must land in the sandbox, not the repo.

    Tests build and tear down artifacts trees with Path.mkdir()/Path.unlink(),
    which resolve through os.mkdir/os.unlink and never touch open(), so both
    need their own hook.  Note the assertions are about the REAL tree:
    existence checks are deliberately not redirected, so os.path.exists here
    answers for the shared artifacts/ directory.
    """
    from pathlib import Path
    nested = Path("artifacts") / "guard_probe_dir" / "deep.json"
    real_dir = os.path.join(ARTIFACTS, "guard_probe_dir")
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.parent.mkdir(parents=True, exist_ok=True)  # idempotent, must not raise
    try:
        nested.write_text("{}", encoding="utf-8")
        nested.unlink()
        assert not os.path.exists(real_dir), (
            "a test created %s in the shared artifacts/ tree" % real_dir)
    finally:
        if os.path.isdir(real_dir):
            _REAL_RMDIR(real_dir)


# ---------------------------------------------------------------------------
# The one shared file outside artifacts/: api_cache.json at the repo root.
# It is redirected by repointing the module constant, not by path shape, so
# it needs its own interception test — and the negative check is the point:
# with the fixture off, APIManager() loads and rewrites the live cache.
# ---------------------------------------------------------------------------

def test_root_database_connect_never_reaches_the_repo(tmp_path):
    """A bare sqlite3.connect() of a root-level name must land in tmp_path.

    SQLite is the last IO primitive the suite writes through: the ambient
    sandbox only sees paths that flow through ``state_persistence`` or
    ``TradingStore``, so raw ``sqlite3.connect("trading_state.db")``-style
    calls opened the shared root databases on every run.  These assertions
    are made against the REAL tree (existence is deliberately not redirected),
    so a broken guard shows up as a file at the repo root.
    """
    import sqlite3

    name = "guard_probe_root_db.db"
    root_db = os.path.join(ROOT, name)
    try:
        con = sqlite3.connect(name)          # bare relative -> repo root if unhooked
        try:
            con.execute("CREATE TABLE t (x INTEGER)")
            con.execute("INSERT INTO t VALUES (1)")
            con.commit()
        finally:
            con.close()
        assert not os.path.exists(root_db), (
            "a test created the shared root database %s" % root_db)
        assert os.path.exists(os.path.join(str(tmp_path), "ambient_runtime_state", name)), (
            "the root database was not redirected into this test's tmp_path")
    finally:
        for leftover in (root_db, root_db + "-wal", root_db + "-shm"):
            if os.path.exists(leftover):
                _REAL_REMOVE(leftover)


def test_shared_compliance_audit_never_opens_the_repo_root_database(tmp_path):
    """get_audit() must build its singleton against this test's tmp_path.

    The singleton is created with NO argument (``core/compliance_audit.py:838``
    falls back to ``db_path="compliance_audit.db"``), so a test firing an audit
    event has no way to say "not the shared one" - it appended rows to the live
    root database on every run.  Repoint the default and drop the singleton, so
    both the read and the write land in tmp_path.
    """
    from core.compliance_audit import get_audit

    resolved = os.path.normcase(os.path.abspath(get_audit()._db_path))
    root_db = os.path.normcase(os.path.abspath(os.path.join(ROOT, "compliance_audit.db")))

    assert resolved != root_db, (
        "get_audit() opened the shared repo-root %s" % root_db)
    assert os.path.dirname(resolved) == os.path.normcase(os.path.abspath(tmp_path)), (
        "get_audit() opened its database outside this test's tmp_path: %s" % resolved)


def test_guardian_alert_outbox_never_lands_at_the_repo_root(tmp_path):
    """The Ψ trip path must page into this test's outbox, not the shared one.

    ``_page_human`` calls ``enqueue()`` with no path of its own - the outbox
    location was a hardcoded literal, so any test tripping the guardian
    inserted a row into the shared root guardian_alerts.db.
    """
    from core import external_guardian as eg

    resolved = os.path.normcase(os.path.abspath(eg.GUARDIAN_ALERTS_DB))
    root_db = os.path.normcase(os.path.abspath(os.path.join(ROOT, "guardian_alerts.db")))

    assert resolved != root_db, (
        "GUARDIAN_ALERTS_DB still points at the shared repo-root %s" % root_db)
    assert os.path.dirname(resolved) == os.path.normcase(os.path.abspath(tmp_path)), (
        "GUARDIAN_ALERTS_DB points outside this test's tmp_path: %s" % resolved)


def test_shared_api_cache_is_redirected_to_tmp_path(tmp_path):
    """APIManager() must never open the repo root's api_cache.json.

    A test that constructs APIManager() with no arguments is the common case,
    not an edge case - four test modules do it - so the cache location has to be
    forced suite-wide.  ``_load_cache`` reads the file on construction and
    ``_save_cache`` rewrites it, so pinning the resolved location is enough:
    if it pointed anywhere but tmp_path, results would depend on whatever the
    live bot happened to cache, and the suite would keep rewriting it.
    """
    from data.api_manager import APIManager

    mgr = APIManager()
    resolved = os.path.normcase(os.path.abspath(mgr.cache_file))
    root_cache = os.path.normcase(os.path.abspath(os.path.join(ROOT, "api_cache.json")))

    assert resolved != root_cache, (
        "APIManager resolved its cache to the shared repo-root %s" % root_cache)
    assert os.path.dirname(resolved) == os.path.normcase(os.path.abspath(tmp_path)), (
        "APIManager resolved its cache outside this test's tmp_path: %s" % resolved)
