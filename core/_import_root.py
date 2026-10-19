"""Single import root: bare and package-qualified imports share one module.

The repo historically puts ``core/``, ``intelligence/``, ``operations/``,
``research/``, ``data/`` and ``ui/`` directly on ``sys.path`` (88 insert
sites), so the same file is importable both as ``kill_switch_latch`` and as
``data.kill_switch_latch``.  Python treats those as two distinct modules — two
singletons, two locks, two copies of every module-level global — which is how
a news-side kill-switch latch ended up invisible to the decision graph
(AUDIT_2026-10-04 C1: 25 double-loaded production modules).

``tests/`` is a seventh aliased root for the same reason: pytest prepends
``tests/`` to ``sys.path`` when it collects a bare test module, so a helper
imported both ways (``import test_x`` and ``from tests.test_x import ...``)
would otherwise run twice and give the two spellings divergent module state.

``install()`` registers a meta-path finder that resolves every bare import of
a file inside those seven roots to its canonical package-qualified module, so
both import spellings yield the SAME module object.  The ``sys.path`` inserts
become harmless, and a guard test fails loudly if any repository file ever
appears twice in ``sys.modules`` again.

Usage (bootstrap order matters — install before the first repo import):

    from core._import_root import install
    install()
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import sys
import threading
from typing import Dict, Optional

#: Packages that are double-loadable via bare imports (mt5_trader.py inserts
#: exactly these; tests/conftest.py mirrors the list).  ``tests`` is listed
#: last because pytest, not the bootstrap, prepends it — see the module
#: docstring; it is a package (tests/__init__.py), so pytest's own canonical
#: name for a collected test file is already ``tests.<stem>``.
ROOTS = ("core", "intelligence", "operations", "research", "data", "ui", "tests")

_lock = threading.Lock()
_finder: Optional["_CanonicalFinder"] = None


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_alias_map(repo_root: str) -> Dict[str, str]:
    """bare import name -> canonical package-qualified module name.

    ``ROOTS`` is walked in reverse because each root is prepended to
    ``sys.path`` in declaration order; the last declared (``tests``) therefore
    resolves first today.  ``setdefault`` keeps that same winner if a bare name
    ever exists in two roots — and as of the ``tests`` root there are no such
    collisions (verified: no test-file name or tests/ subdirectory shadows a
    production module or root).
    """
    aliases: Dict[str, str] = {}
    for root in reversed(ROOTS):
        base = os.path.join(repo_root, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
            rel = os.path.relpath(dirpath, repo_root).replace(os.sep, "/")  # e.g. core/harness
            # DOTTED, not slashed: a nested directory must key on the module
            # path it is actually imported by.  Keying "tests/components" on
            # slashes builds a key no import statement can ever match, so every
            # module below a depth-2 directory silently escaped
            # canonicalisation and split into two objects again (C1 in a
            # subpackage).
            dotted = rel.replace("/", ".")
            bare_prefix = dotted.split(".", 1)[1] if "." in dotted else ""
            if bare_prefix:
                # The directory itself is importable (package or namespace
                # package): bare "harness" -> "core.harness".
                aliases.setdefault(bare_prefix, dotted)
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                stem = filename[:-3]
                if stem == "__init__":
                    continue
                bare = f"{bare_prefix}.{stem}" if bare_prefix else stem
                canonical = f"{rel.replace('/', '.')}.{stem}"
                # The ``canonical != bare`` guard keeps the finder's recursion
                # terminating by construction rather than by layout accident:
                # if a root ever grew a subdirectory that re-entered an
                # existing root name (``core/tests/``), a self-alias would make
                # find_spec import itself forever.  No such path exists today.
                if canonical != bare:
                    aliases.setdefault(bare, canonical)
    return aliases


class _AliasLoader(importlib.abc.Loader):
    """Hands back the already-loaded canonical module under its bare name."""

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module  # the canonical module object; never a new one

    def exec_module(self, module):
        # The canonical module already ran under its package-qualified name.
        return None


class _CanonicalFinder(importlib.abc.MetaPathFinder):
    def __init__(self, aliases: Dict[str, str]):
        self._aliases = aliases

    def find_spec(self, fullname, path=None, target=None):
        canonical = self._aliases.get(fullname)
        if canonical is None:
            return None
        module = sys.modules.get(canonical)
        if module is None:
            # Canonical names always contain a root prefix and are never
            # themselves alias keys, so this recursion terminates in normal
            # import machinery.
            module = importlib.import_module(canonical)
        # Reconcile the parent package attribute.  Eager package __init__
        # chains (e.g. data/__init__ importing realtime_news_ingest, which
        # imports the latch) can re-enter the canonical import and leave
        # ``data.kill_switch_latch`` bound to a second, stale module object
        # while sys.modules holds the aliased one.  ``import a.b as c`` and
        # ``getattr(a, "b")`` read the ATTRIBUTE, so a stale binding would
        # silently split the singleton again.
        parent, _, child = canonical.rpartition(".")
        parent_module = sys.modules.get(parent) if parent else None
        if parent_module is not None and getattr(parent_module, child, None) is not module:
            setattr(parent_module, child, module)
        # Importing the canonical module above may have registered the BARE
        # name in sys.modules as a side effect (data/kill_switch_latch.py
        # self-aliases at module scope).  importlib._bootstrap._find_spec
        # re-checks sys.modules AFTER each finder returns and, when the name
        # is present, DISCARDS our alias spec in favour of
        # sys.modules[fullname].__spec__ — the canonical SourceFile spec.
        # Consuming that spec re-executes the file and mints a SECOND module
        # object under the bare import, while the parent package attribute
        # keeps the first object: the C1 split.  Remove the entry here; the
        # alias load consuming our spec immediately re-registers the very
        # same canonical object under the bare name (import machinery holds
        # the name's module lock across find_spec and that load, and
        # find_spec calls serialise on the global import lock).
        sys.modules.pop(fullname, None)
        is_pkg = hasattr(module, "__path__")
        origin = getattr(getattr(module, "__spec__", None), "origin", None)
        spec = importlib.util.spec_from_loader(
            fullname, _AliasLoader(module), origin=origin, is_package=is_pkg,
        )
        if spec is not None and is_pkg:
            spec.submodule_search_locations = list(module.__path__)
        return spec


def install(repo_root: Optional[str] = None):
    """Install the canonicalisation finder (idempotent, thread-safe)."""
    global _finder
    with _lock:
        for existing in sys.meta_path:
            if isinstance(existing, _CanonicalFinder):
                _finder = existing
                return existing
        root = repo_root or _repo_root()
        _finder = _CanonicalFinder(_build_alias_map(root))
        sys.meta_path.insert(0, _finder)
        return _finder


# Both spellings of THIS module must be one object regardless of which
# imported us first (the guard test asserts no repo file loads twice).
_here = sys.modules[__name__]
sys.modules.setdefault("core._import_root", _here)
sys.modules.setdefault("_import_root", _here)
_core_pkg = sys.modules.get("core")
if _core_pkg is not None and getattr(_core_pkg, "_import_root", None) is not _here:
    setattr(_core_pkg, "_import_root", _here)
