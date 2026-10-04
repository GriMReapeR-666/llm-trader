"""Single import root: bare and package-qualified imports share one module.

The repo historically puts ``core/``, ``intelligence/``, ``operations/``,
``research/``, ``data/`` and ``ui/`` directly on ``sys.path`` (88 insert
sites), so the same file is importable both as ``kill_switch_latch`` and as
``data.kill_switch_latch``.  Python treats those as two distinct modules — two
singletons, two locks, two copies of every module-level global — which is how
a news-side kill-switch latch ended up invisible to the decision graph
(AUDIT_2026-10-04 C1: 25 double-loaded production modules).

``install()`` registers a meta-path finder that resolves every bare import of
a file inside those six roots to its canonical package-qualified module, so
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
#: exactly these; tests/conftest.py mirrors the list).
ROOTS = ("core", "intelligence", "operations", "research", "data", "ui")

_lock = threading.Lock()
_finder: Optional["_CanonicalFinder"] = None


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_alias_map(repo_root: str) -> Dict[str, str]:
    """bare import name -> canonical package-qualified module name.

    ``ROOTS`` is walked in reverse because the bootstrap inserts each root at
    ``sys.path[0]``; the last inserted (``ui``) therefore resolves first
    today.  ``setdefault`` keeps that same winner if a bare name ever exists
    in two roots.
    """
    aliases: Dict[str, str] = {}
    for root in reversed(ROOTS):
        base = os.path.join(repo_root, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
            rel = os.path.relpath(dirpath, repo_root).replace(os.sep, "/")  # e.g. core/harness
            bare_prefix = rel.split("/", 1)[1] if "/" in rel else ""
            if bare_prefix:
                # The directory itself is importable (package or namespace
                # package): bare "harness" -> "core.harness".
                aliases.setdefault(bare_prefix, rel.replace("/", "."))
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                stem = filename[:-3]
                if stem == "__init__":
                    continue
                bare = f"{bare_prefix}.{stem}" if bare_prefix else stem
                canonical = f"{rel.replace('/', '.')}.{stem}"
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
sys.modules.setdefault("core._import_root", sys.modules[__name__])
sys.modules.setdefault("_import_root", sys.modules[__name__])
