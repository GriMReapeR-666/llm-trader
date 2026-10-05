"""OS file locking utility for single-instance engine guard and ledger coordination.

Uses fcntl.flock on POSIX systems (macOS, Linux) and msvcrt.locking on Windows.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from typing import Iterator, Optional


class FileLock:
    """Exclusive non-blocking or bounded-wait OS file lock.

    M2: the lock lives on a sentinel byte far past the payload (offset 2**30)
    while the PID is stored at bytes 0..63. On Windows (mandatory locking) a
    second process locking byte 0 would also block plain readers of the PID;
    separating the regions keeps read_pid() working for the API stop path
    and /api/safety while the engine holds the lock.
    """

    _LOCK_OFFSET = 1 << 30

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._fh: Optional[any] = None
        self._acquired = False

    @property
    def is_locked(self) -> bool:
        return self._acquired

    def acquire(self, non_blocking: bool = True, timeout: float = 0.0) -> bool:
        """Acquire an exclusive lock on the file.

        If non_blocking is True, returns immediately with True if acquired, False otherwise.
        If non_blocking is False and timeout > 0, retries until acquired or timeout expires.
        """
        if self._acquired and self._fh is not None:
            return True

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.monotonic() + max(0.0, timeout)

        while True:
            try:
                fh = open(self.path, "a+b")
            except OSError:
                if non_blocking or time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)
                continue

            try:
                try:
                    import fcntl
                    flags = fcntl.LOCK_EX
                    if non_blocking:
                        flags |= fcntl.LOCK_NB
                    fcntl.flock(fh.fileno(), flags)
                    self._fh = fh
                    self._acquired = True
                    return True
                except ImportError:
                    import msvcrt
                    fh.seek(self._LOCK_OFFSET)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    self._fh = fh
                    self._acquired = True
                    return True
            except (BlockingIOError, OSError):
                try:
                    fh.close()
                except OSError:
                    pass
                if non_blocking or time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)

    def write_pid(self, pid: Optional[int] = None) -> None:
        """Write PID into the lock file while holding the lock."""
        if not self._acquired or not self._fh:
            raise RuntimeError("Cannot write PID: lock not held")
        target_pid = os.getpid() if pid is None else pid
        try:
            self._fh.seek(0)
            self._fh.truncate(0)
            self._fh.write(f"{target_pid}\n".encode("utf-8"))
            self._fh.flush()
        except OSError:
            pass

    def read_pid(self) -> Optional[int]:
        """Read PID from the lock file (best-effort)."""
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "rb") as f:
                content = f.read(64).decode("utf-8", errors="ignore").strip()
                if content:
                    first_line = content.splitlines()[0]
                    return int(first_line)
        except Exception:
            return None
        return None

    def release(self) -> None:
        """Release the lock and close the file handle."""
        if not self._acquired or not self._fh:
            return
        try:
            try:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                self._fh.seek(self._LOCK_OFFSET)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self._acquired = False

    def __enter__(self) -> FileLock:
        if not self.acquire(non_blocking=False, timeout=30.0):
            raise TimeoutError(f"Could not acquire lock on {self.path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
