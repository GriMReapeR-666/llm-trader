"""Distributed Lock — cross-process locking for trade execution safety."""
from __future__ import annotations

import logging
import os
import time
import threading
from typing import Optional

log = logging.getLogger(__name__)

_locks: dict = {}
_lock_mutex = threading.Lock()


class _FileLock:
    def __init__(self, name: str, timeout: float = 30.0):
        self.name = name
        self.timeout = timeout
        self._lock_file = None
        self._path = os.path.join(os.environ.get("TEMP", "/tmp"), f"mt5_trade_{name}.lock")

    def acquire(self) -> bool:
        try:
            start = time.time()
            while time.time() - start < self.timeout:
                try:
                    fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode())
                    os.close(fd)
                    self._lock_file = True
                    return True
                except FileExistsError:
                    # Check stale lock
                    try:
                        age = time.time() - os.path.getmtime(self._path)
                        if age > self.timeout:
                            os.remove(self._path)
                            continue
                    except OSError:
                        pass
                    time.sleep(0.05)
            return False
        except Exception as e:
            log.debug("FileLock acquire failed: %s", e)
            return False

    def release(self):
        try:
            if self._lock_file and os.path.exists(self._path):
                os.remove(self._path)
            self._lock_file = False
        except Exception:
            pass


def get_trade_lock(name: str = "default", timeout: float = 30.0):
    """Get a distributed trade lock. Returns a context manager."""
    with _lock_mutex:
        if name not in _locks:
            _locks[name] = _FileLock(name, timeout)
        return _locks[name]


class TradeLock:
    """Context manager for trade execution locking."""
    def __init__(self, name: str = "default", timeout: float = 30.0):
        self._lock = get_trade_lock(name, timeout)

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *args):
        self._lock.release()
