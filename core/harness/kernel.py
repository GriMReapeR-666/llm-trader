"""Microkernel & Service Container for Trading Harness (Cordis-inspired).

Implements spatial and temporal composability:
- Spatial Composability: Hierarchical dependency injection via `TradingContext`.
- Temporal Composability: Reversible lifecycle hooks and disposal via `CompositeDisposable`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union

from core.harness.disposable import CompositeDisposable, Disposable

logger = logging.getLogger(__name__)

_MISSING = object()


class HarnessPlugin:
    """Base class for all hot-swappable trading harness plugins."""
    name: str = "base_plugin"
    version: str = "1.0.0"
    dependencies: List[str] = []

    def apply(self, ctx: 'TradingContext') -> None:
        """Mount services, registers event listeners, or injects tools into the context."""
        raise NotImplementedError(f"Plugin {self.__class__.__name__} must implement apply(ctx).")


class TradingContext:
    """Hierarchical service locator and execution context container.
    
    Provides spatial composability (dependency injection) and temporal
    composability (automatic reversible disposal of registered side effects).
    """

    def __init__(self, parent: Optional['TradingContext'] = None, name: str = "RootContext"):
        self.parent: Optional['TradingContext'] = parent
        self.name: str = name
        self._services: Dict[str, Any] = {}
        self._plugins: Dict[str, HarnessPlugin] = {}
        self._state: Dict[str, Any] = {}
        self._disposables = CompositeDisposable(name=f"{name}_Disposables")
        self._children: List['TradingContext'] = []
        self._disposed: bool = False

    @property
    def is_disposed(self) -> bool:
        return self._disposed

    def provide(self, name: str, service: Any) -> 'TradingContext':
        """Register a service in this context scope.
        
        Args:
            name: Unique service identifier (e.g. 'risk_manager', 'broker', 'memory').
            service: Service instance or callable provider.
        """
        if self._disposed:
            raise RuntimeError(f"Cannot provide service '{name}' on disposed context '{self.name}'.")
        self._services[name] = service
        logger.debug("[%s] Provided service '%s' -> %r", self.name, name, type(service).__name__)
        return self

    def get(self, name: str, default: Any = _MISSING) -> Any:
        """Resolve a service by name, traversing up the parent context chain if needed.
        
        Args:
            name: Identifier of the service to resolve.
            default: Optional fallback if the service is not found.
        """
        if name in self._services:
            return self._services[name]
        if self.parent is not None:
            return self.parent.get(name, default)
        if default is not _MISSING:
            return default
        raise KeyError(f"Service '{name}' not found in TradingContext hierarchy (scope: {self.name}).")

    def has(self, name: str) -> bool:
        """Check if a service is registered in this context or any ancestor."""
        if name in self._services:
            return True
        if self.parent is not None:
            return self.parent.has(name)
        return False

    def set_state(self, key: str, value: Any) -> 'TradingContext':
        """Set a local scoped state variable."""
        self._state[key] = value
        return self

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a scoped state variable, checking parent scopes if missing."""
        if key in self._state:
            return self._state[key]
        if self.parent is not None:
            return self.parent.get_state(key, default)
        return default

    def effect(self, cleanup_fn: Callable[[], Any], name: Optional[str] = None) -> Disposable:
        """Register a reversible side effect callback.
        
        The callback is guaranteed to be executed when this context is disposed.
        """
        if self._disposed:
            try:
                cleanup_fn()
            except Exception as exc:
                logger.error("Error executing immediate cleanup on disposed context: %s", exc)
            return Disposable(name="AlreadyDisposed")

        disp = Disposable(cleanup_fn, name=name or f"Effect_{len(self._disposables)}")
        self._disposables.add(disp)
        return disp

    def plugin(self, plugin_target: Union[HarnessPlugin, Type[HarnessPlugin]], **kwargs) -> 'TradingContext':
        """Mount a plugin into this context.
        
        Args:
            plugin_target: An instance of HarnessPlugin or a HarnessPlugin subclass.
            **kwargs: Arguments to instantiate plugin_target if it is a class.
        """
        if self._disposed:
            raise RuntimeError(f"Cannot mount plugin on disposed context '{self.name}'.")

        if isinstance(plugin_target, type) and issubclass(plugin_target, HarnessPlugin):
            plugin_instance = plugin_target(**kwargs)
        elif isinstance(plugin_target, HarnessPlugin):
            plugin_instance = plugin_target
        else:
            raise TypeError(f"Expected HarnessPlugin instance or class, got {type(plugin_target)}")

        # Check dependencies
        for dep in plugin_instance.dependencies:
            if not self.has(dep):
                raise RuntimeError(
                    f"Plugin '{plugin_instance.name}' requires service '{dep}', "
                    f"which is missing in context '{self.name}'."
                )

        plugin_instance.apply(self)
        self._plugins[plugin_instance.name] = plugin_instance
        logger.info("[%s] Mounted plugin '%s' (v%s)", self.name, plugin_instance.name, plugin_instance.version)
        return self

    def create_child(self, name: Optional[str] = None) -> 'TradingContext':
        """Create an isolated child context inheriting services from this parent context."""
        if self._disposed:
            raise RuntimeError(f"Cannot create child context from disposed context '{self.name}'.")
        child_name = name or f"{self.name}_Child_{len(self._children)}"
        child_ctx = TradingContext(parent=self, name=child_name)
        self._children.append(child_ctx)
        return child_ctx

    def dispose(self) -> None:
        """Dispose this context, tearing down all children and registered disposables."""
        if self._disposed:
            return
        self._disposed = True
        logger.debug("[%s] Disposing context and clearing resources...", self.name)

        # 1. Dispose all child contexts first
        for child in list(self._children):
            try:
                child.dispose()
            except Exception as exc:
                logger.error("Error disposing child context '%s': %s", child.name, exc)
        self._children.clear()

        # 2. Dispose all tracked effects and reversibles in LIFO order
        self._disposables.dispose()

        # 3. Clean local services and states
        self._services.clear()
        self._plugins.clear()
        self._state.clear()

        # 4. Detach from parent if active
        if self.parent is not None and not self.parent.is_disposed:
            try:
                if self in self.parent._children:
                    self.parent._children.remove(self)
            except Exception as exc:
                logger.debug("order operation failed: %s", exc)

    def __enter__(self) -> 'TradingContext':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.dispose()

    def __repr__(self) -> str:
        status = "Disposed" if self._disposed else "Active"
        services_list = ", ".join(self._services.keys()) or "None"
        return f"<TradingContext '{self.name}' [{status}] services=[{services_list}]>"
