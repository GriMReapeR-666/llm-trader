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


def test_trading_safety_is_one_module():
    bare = importlib.import_module("trading_safety")
    packaged = importlib.import_module("core.trading_safety")
    assert bare is packaged, "trading_safety loaded under two names"
