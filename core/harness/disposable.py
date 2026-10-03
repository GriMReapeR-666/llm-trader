"""Disposable and Reversible Effect Management for Trading Harness.

Provides temporal composability primitives (Disposable, CompositeDisposable,
AsyncDisposable) to ensure that any plugin, event subscription, socket,
temporary cache, or background task is cleanly and reversibly torn down
without memory or resource leaks.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, List, Optional, Union

logger = logging.getLogger(__name__)


class Disposable:
    """Encapsulates a reversible cleanup operation."""

    def __init__(self, dispose_fn: Optional[Callable[[], Any]] = None, name: str = "AnonymousDisposable"):
        self._dispose_fn = dispose_fn
        self._name = name
        self._disposed = False

    @property
    def is_disposed(self) -> bool:
        return self._disposed

    def dispose(self) -> None:
        """Execute the disposal callback if not already disposed."""
        if self._disposed:
            return
        self._disposed = True
        if self._dispose_fn is not None:
            try:
                res = self._dispose_fn()
                if inspect.isawaitable(res):
                    logger.warning(
                        "Async disposable '%s' invoked synchronously; "
                        "schedule via async loop or AsyncDisposable.",
                        self._name,
                    )
            except Exception as exc:
                logger.error("Error during disposal of '%s': %s", self._name, exc, exc_info=True)

    def __enter__(self) -> 'Disposable':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.dispose()

    def __repr__(self) -> str:
        return f"<Disposable name={self._name!r} disposed={self._disposed}>"


class CompositeDisposable(Disposable):
    """A container holding multiple disposables that are all disposed together in LIFO order."""

    def __init__(self, *disposables: Disposable, name: str = "CompositeDisposable"):
        super().__init__(name=name)
        self._items: List[Disposable] = list(disposables)

    def add(self, disposable: Union[Disposable, Callable[[], Any]]) -> Disposable:
        """Add a disposable or a callable to the composite collection."""
        if self._disposed:
            if isinstance(disposable, Disposable):
                disposable.dispose()
            elif callable(disposable):
                try:
                    disposable()
                except Exception as exc:
                    logger.error("Error disposing added item on closed CompositeDisposable: %s", exc)
            return Disposable(name="AlreadyDisposed")

        if isinstance(disposable, Disposable):
            disp = disposable
        elif callable(disposable):
            disp = Disposable(disposable)
        else:
            raise TypeError(f"Expected Disposable or Callable, got {type(disposable)}")

        self._items.append(disp)
        return disp

    def remove(self, disposable: Disposable) -> bool:
        """Remove a disposable from tracking without disposing it."""
        try:
            self._items.remove(disposable)
            return True
        except ValueError:
            return False

    def dispose(self) -> None:
        """Dispose all tracked items in reverse order of addition (LIFO)."""
        if self._disposed:
            return
        self._disposed = True
        for item in reversed(self._items):
            try:
                item.dispose()
            except Exception as exc:
                logger.error("Error disposing item in CompositeDisposable '%s': %s", self._name, exc)
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
