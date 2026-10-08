"""C1 guard: one import root — no repository file may load twice.

AUDIT_2026-10-04 C1 showed 25 production modules loaded twice under two names
(bare ``kill_switch_latch`` vs ``data.kill_switch_latch``), which split the
kill-switch latch so a news tripwire never reached the decision graph.
``core/_import_root.py`` canonicalises both spellings to one module; this test
imports the two guarded entry points and fails if any repository file appears
in ``sys.modules`` under more than one name.
"""
import importlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_module_duplicates():
    """{realpath: {module_name: ...}} for repo .py files loaded as MORE THAN
    ONE distinct module object.

    Two names pointing at the same object is the intended aliasing (bare and
    package-qualified spellings share one module); two *objects* for one file
    is the C1 split-singleton bug.
    """
    by_file = {}
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        file = getattr(module, "__file__", None)
        if not file or not file.endswith(".py"):
            continue
        try:
            file = os.path.realpath(file)
        except OSError:
            continue
        if not file.startswith(REPO_ROOT + os.sep):
            continue
        by_file.setdefault(file, {}).setdefault(id(module), []).append(name)
    return {
        f: [sorted(names) for names in objects.values()]
        for f, objects in by_file.items()
        if len(objects) > 1
    }


def test_import_root_finder_is_installed():
    from core._import_root import _CanonicalFinder, install

    finder = install()
    assert isinstance(finder, _CanonicalFinder)
    assert any(isinstance(f, _CanonicalFinder) for f in sys.meta_path)


def test_no_repository_module_loads_twice():
    # The two guarded entry points (AUDIT fix spec: import mt5_trader and
    # ui.gui_api, then fail if any repo file appears twice).
    importlib.import_module("mt5_trader")
    importlib.import_module("ui.gui_api")

    duplicates = _repo_module_duplicates()
    assert not duplicates, (
        "repository files loaded as multiple distinct module objects — "
        "a split singleton (C1) is back: %s" % duplicates
    )


def test_kill_switch_latch_is_one_module_and_one_singleton():
    """The exact C1 failure: news ingest and the decision graph must share
    the same latch accessor and the same latch instance."""
    bare = importlib.import_module("kill_switch_latch")
    packaged = importlib.import_module("data.kill_switch_latch")

    assert bare is packaged, "kill_switch_latch loaded under two names"
    assert bare.get_kill_switch_latch is packaged.get_kill_switch_latch
    assert bare.get_kill_switch_latch() is packaged.get_kill_switch_latch()

    # Statement-form bindings: ``import a.b as c`` and attribute access on
    # the parent package read the PACKAGE ATTRIBUTE, which eager __init__
    # chains can leave pointing at a stale module object.
    import kill_switch_latch as stmt_bare  # noqa: F401
    import data.kill_switch_latch as stmt_pkg  # noqa: F401
    import data as data_pkg  # noqa: F401

    assert stmt_pkg is bare, (
        "import data.kill_switch_latch as x bound a stale module object"
    )
    assert stmt_bare is bare, (
        "import kill_switch_latch as x bound a different module object"
    )
    assert getattr(data_pkg, "kill_switch_latch", None) is bare, (
        "data.kill_switch_latch package attribute is a stale module object"
    )
    # From-imports must resolve to the same function object too.
    from kill_switch_latch import get_kill_switch_latch as bare_fn
    from data.kill_switch_latch import get_kill_switch_latch as pkg_fn
    assert bare_fn is pkg_fn, "get_kill_switch_latch resolved to two functions"


def test_trading_safety_is_one_module():
    bare = importlib.import_module("trading_safety")
    packaged = importlib.import_module("core.trading_safety")
    assert bare is packaged, "trading_safety loaded under two names"


def test_tests_directory_is_an_aliased_root():
    """``tests/`` is the seventh aliased root (same C1 split, test helpers).

    pytest prepends ``tests/`` to ``sys.path`` when it collects a module
    without a package parent, so ``import test_x`` and ``from tests.test_x
    import ...`` could otherwise produce two module objects with divergent
    module-level state.
    """
    from core._import_root import ROOTS

    assert "tests" in ROOTS, f"tests/ is not canonicalised; ROOTS={ROOTS}"

    bare = importlib.import_module("test_single_import_root")
    packaged = importlib.import_module("tests.test_single_import_root")
    assert bare is packaged, "a test module loaded under two names"

    import test_single_import_root as stmt_bare  # noqa: F401
    assert stmt_bare is packaged, (
        "import test_single_import_root as x bound a different module object"
    )

    from test_single_import_root import _repo_module_duplicates as bare_fn
    from tests.test_single_import_root import _repo_module_duplicates as pkg_fn
    assert bare_fn is pkg_fn, "helper resolved to two function objects"


def test_alias_keys_are_importable_module_names():
    """No alias key may contain a path separator.

    ``_build_alias_map`` derived the bare prefix from the directory PATH, so a
    depth-2 directory (``tests/components/self_improvement_e2e``) produced the
    key ``"components/self_improvement_e2e"``.  No import statement can spell
    that, so every module below it silently escaped canonicalisation and split
    into two objects — C1 again, one directory deeper.
    """
    from core._import_root import _build_alias_map

    aliases = _build_alias_map(REPO_ROOT)
    bad = sorted(k for k in aliases if "/" in k or "\\" in k)
    assert not bad, f"unreachable alias keys (never match an import): {bad}"

    # The nested directory is actually reachable, and collapses.
    bare = importlib.import_module("components.self_improvement_e2e.test_success_loop_simulation")
    packaged = importlib.import_module(
        "tests.components.self_improvement_e2e.test_success_loop_simulation"
    )
    assert bare is packaged, (
        "a module below a nested directory loaded under two names"
    )
