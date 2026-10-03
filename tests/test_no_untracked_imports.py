"""CRITICAL #12 - a tracked file must not import a module that git does not carry.

A fresh clone of this repository could not start: 30+ untracked source modules
are imported by tracked code, among them

    data/kill_switch_latch.py            (kill-switch latch; 3 importers)
    data/news_triage.py                   (6 importers)
    core/distributed_lock.py              (imported by core/order_execution.py)
    research/decision_models/*            (feature_block, laya_model,
                                          post_validator, ... used by
                                          intelligence/graph_nodes.py and
                                          core/harness/pipeline_v2.py)
    data_fetcher.py, state_manager.py     (repo root, imported by mt5_trader.py)

The failure mode is silent and delayed: the importing file is fine locally and
in CI, and the ImportError only appears on someone else's machine, usually
inside a `try/except` that degrades it to a disabled feature.

Committing those files is an operator decision, so the invariant is recorded as
a non-strict xfail: the suite stays green, `pytest -rx` prints the full list of
offenders every run, and the test reports XPASS the moment someone `git add`s
them. Delete the xfail marker once the tree is clean.
"""
import ast
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIRS = ("core", "data", "intelligence", "operations", "research", "brain",
               "ui", "scripts", "tui_opentui", "tests", "mcp_server")
SOURCE_EXTS = (".py", ".ts", ".tsx")


def _git(*args, timeout=300, attempts=4):
    """Run git, retrying: this tree sits on OneDrive and git flaps.

    Transient "not a git repository" and failed spawns were observed, and a
    guard that reports a false alarm is worse than no guard.
    """
    for attempt in range(attempts):
        try:
            out = subprocess.run(["git", "-C", str(ROOT), *args],
                                 capture_output=True, text=True, timeout=timeout)
            if out.returncode == 0:
                return out.stdout
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(0.5 * (attempt + 1))
    return None


def _tracked_files():
    txt = _git("ls-files")
    if txt is None:
        pytest.skip("git unavailable")
    return {ln.strip().replace("\\", "/") for ln in txt.splitlines() if ln.strip()}


def _untracked_files():
    txt = _git("status", "--porcelain", "--untracked-files=all")
    if txt is None:
        pytest.skip("git unavailable")
    out = set()
    for ln in txt.splitlines():
        if ln.startswith("?? "):
            out.add(ln[3:].strip().strip('"').replace("\\", "/"))
    return out


def _tracked_provider_paths(leaf, tracked):
    return [c for c in (
        leaf + ".py", leaf + "/__init__.py", leaf + "/index.ts", leaf + "/index.tsx",
    ) if c in tracked]


def _parse_imports(src):
    if src is None:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.append((node.module, node.lineno))
    return found


def _imports_of(path, reader=None):
    if reader is None:
        try:
            src = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            src = None
    else:
        src = reader(path)
    return _parse_imports(src)


def _broken_clone_imports(tracked=None, untracked=None, reader=None):
    """Every (importer, lineno, module, on-disk-only paths) in the tree."""
    if tracked is None:
        tracked = _tracked_files()
    if untracked is None:
        untracked = _untracked_files()
    if reader is None:
        def reader(path):
            try:
                return (ROOT / path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None

    # Map importable module name -> untracked paths that provide it. A package
    # is provided by pkg/__init__.py, so the module name is the *directory*,
    # not the file stem; keying on the stem alone misses every untracked
    # package, which is how a whole research/decision_models/ tree slips
    # through unnoticed.
    by_stem = {}
    for path in untracked:
        if os.path.splitext(path)[1] not in SOURCE_EXTS:
            continue
        base = os.path.basename(path)
        if base in ("__init__.py", "index.ts", "index.tsx"):
            stem = os.path.basename(os.path.dirname(path))
        else:
            stem = os.path.splitext(base)[0]
        if stem:
            by_stem.setdefault(stem, []).append(path)

    offenders = []
    for path in sorted(tracked):
        if not path.endswith(".py"):
            continue
        for module, lineno in _imports_of(path, reader):
            leaf = module.split(".")[-1]
            hits = by_stem.get(leaf)
            if not hits:
                continue
            # a tracked file of the same name wins in Python; then the import
            # is satisfied and the untracked file is simply unused
            if _tracked_provider_paths(leaf, tracked):
                continue
            offenders.append((path, lineno, module, sorted(hits)))
    return offenders


# --- the guard's own machinery must work --------------------------------

def test_git_queries_are_available():
    """Sanity: the invariant below is meaningless if git cannot be queried."""
    top = _git("rev-parse", "--show-toplevel")
    if top is None:
        pytest.skip("git unavailable after retries; the clone guard cannot run")
    assert "fatal" not in top
    assert len(_tracked_files()) > 100
    assert isinstance(_broken_clone_imports(), list)


def test_scanner_flags_an_import_that_a_clone_would_lose():
    """Prove the detector can fail - a guard that cannot fail is not a guard.

    Synthetic tracked/untracked sets, so this is a real unit test of the
    matcher rather than a hope that today's tree happens to trip it.
    """
    tracked = {"app.py", "real_mod.py"}
    untracked = {"ghost_mod.py"}

    def reader(path):
        return {
            "app.py": "import ghost_mod\nimport real_mod\nimport numpy\n",
            "real_mod.py": "x = 1\n",
        }.get(path)

    found = _broken_clone_imports(tracked, untracked, reader)
    mods = [m for _, _, m, _ in found]
    assert mods == ["ghost_mod"], found


def test_scanner_ignores_third_party_and_shadowed_modules():
    """Only local modules that git is missing are offenders."""
    tracked = {"app.py", "shim.py"}
    untracked = {"shim.py"}  # untracked copy, but a tracked one shadows it

    def reader(path):
        return {
            "app.py": "import shim\nimport pandas\nimport torch\n",
            "shim.py": "x = 1\n",
        }.get(path)

    assert _broken_clone_imports(tracked, untracked, reader) == []


def test_scanner_parses_dotted_and_from_imports():
    """``from pkg.thing import X`` binds pkg.thing; ``from pkg import X`` binds pkg.

    That is the whole difference: the first needs pkg/thing.py, the second only
    needs the package. Flagging the second as needing "pkg.other" would invent a
    module that never existed.
    """
    tracked = {"app.py"}
    untracked = {"pkg/thing.py"}
    reader = lambda p: ("from pkg.thing import Thing\n"
                         "from pkg import other\nimport os.path\n")
    found = _broken_clone_imports(tracked, untracked, reader)
    mods = [m for _, _, m, _ in found]
    assert mods == ["pkg.thing"], found

    # now make the package itself untracked and the other import must trip too
    untracked2 = {"pkg/thing.py", "pkg/__init__.py"}
    found2 = _broken_clone_imports(tracked, untracked2, reader)
    mods2 = sorted(m for _, _, m, _ in found2)
    assert mods2 == ["pkg", "pkg.thing"], found2


# --- the invariant ------------------------------------------------------

@pytest.mark.xfail(
    strict=False,
    reason="32 untracked source modules are imported by tracked code; a fresh "
           "clone ImportErrors. Needs `git add` (operator decision). Run with "
           "-rx to see the current list.")
def test_no_tracked_module_imports_an_untracked_file():
    offenders = _broken_clone_imports()
    if not offenders:
        return  # XPASS: the tree was cleaned up
    detail = "\n".join(
        "    %s:%d  imports %s  ->  only on disk: %s"
        % (p, ln, mod, ", ".join(hits))
        for p, ln, mod, hits in offenders)
    pytest.fail(
        "A fresh clone cannot start: %d tracked file(s) import a module git "
        "does not carry.\n%s\n\n"
        "Fix: `git add` the listed files (needs operator review), or stop "
        "importing them." % (len(offenders), detail))
