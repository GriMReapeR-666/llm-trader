"""Freeze ``datetime`` in every loaded copy of a module.

Several core modules are loaded under two names (``core.x`` and bare ``x``,
roadmap P1-C1), and a class may come from either copy, so patching one
module object is not enough.
"""
import sys
from datetime import datetime


def frozen_datetime(utc_now: datetime, local_now: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_now.astimezone(tz) if tz else local_now

        @classmethod
        def utcnow(cls):
            return utc_now.replace(tzinfo=None)
    return _Frozen


def freeze(monkeypatch, path_suffix: str, utc_now: datetime, local_now=None):
    local_now = local_now or utc_now.replace(tzinfo=None)
    frozen = frozen_datetime(utc_now, local_now)
    suffixes = (path_suffix, path_suffix.replace("/", "\\"))
    copies = [m for m in list(sys.modules.values())
              if (getattr(m, "__file__", "") or "").endswith(suffixes)]
    assert copies, f"{path_suffix} not loaded"
    for module in copies:
        monkeypatch.setattr(module, "datetime", frozen)
    return frozen
