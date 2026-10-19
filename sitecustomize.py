"""Canonical import root for Python processes started in this repository (C1).

Python imports ``sitecustomize`` automatically at startup when the working
directory (or script directory) is on ``sys.path``.  This gives every ad-hoc
``python -c`` / ``python -m`` invocation the same single-module semantics as
the guarded entry points (``mt5_trader.py``, ``ui/gui_api.py``, pytest).
Failure here must never break an unrelated interpreter.
"""
try:
    from core._import_root import install as _install_import_root

    _install_import_root()
except Exception:  # pragma: no cover - best effort only
    pass
