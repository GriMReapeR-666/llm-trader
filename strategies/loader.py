"""Dynamic strategy loader — Discovers and loads strategy plugins.

Scans the ``strategies/`` directory for Python modules containing classes
that inherit from ``BaseStrategy``.  Each discovered strategy is instantiated,
validated, and registered.

Safety:
- Only modules in the ``strategies/`` directory are loaded.
- Modules that raise errors during import are logged and skipped.
- No ``exec`` or ``eval`` — only standard ``importlib``.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# The strategies directory
STRATEGIES_DIR = Path(__file__).parent


def _discover_strategy_classes(module) -> List[Type[BaseStrategy]]:
    """Find all BaseStrategy subclasses in a module."""
    classes = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseStrategy)
            and attr is not BaseStrategy
            and not getattr(attr, "__abstractmethods__", None)  # skip abstract
        ):
            classes.append(attr)
    return classes


class StrategyLoader:
    """Discovers, loads, and manages strategy plugins.

    Usage::

        loader = StrategyLoader()
        strategies = loader.discover()
        for name, strategy in strategies.items():
            print(f"Loaded: {name} ({strategy.description})")
    """

    def __init__(self, strategies_dir: Optional[str] = None):
        self.strategies_dir = Path(strategies_dir or STRATEGIES_DIR)
        self._loaded: Dict[str, BaseStrategy] = {}
        self._modules: Dict[str, object] = {}
        self._errors: Dict[str, str] = {}

    def discover(self) -> Dict[str, BaseStrategy]:
        """Scan the strategies directory and load all valid strategy plugins.

        Returns a dict mapping strategy names to instantiated strategy objects.
        """
        if not self.strategies_dir.exists():
            logger.warning("Strategies directory not found: %s", self.strategies_dir)
            return {}

        # Add strategies dir to sys.path if not already there
        str_dir = str(self.strategies_dir)
        if str_dir not in sys.path:
            sys.path.insert(0, str_dir)

        for py_file in sorted(self.strategies_dir.glob("*.py")):
            # Skip __init__.py, base.py, loader.py, registry.py, template files
            if py_file.name.startswith("_") or py_file.name in (
                "base.py", "loader.py", "registry.py",
            ):
                continue
            if py_file.name.endswith("_template.py"):
                continue

            module_name = py_file.stem
            try:
                self._load_module(module_name, py_file)
            except Exception as exc:
                logger.error("Failed to load strategy module %s: %s", module_name, exc)
                self._errors[module_name] = str(exc)

        return dict(self._loaded)

    def _load_module(self, module_name: str, py_file: Path) -> None:
        """Import a single strategy module and instantiate its strategies."""
        if module_name in self._loaded:
            return

        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if not spec or not spec.loader:
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        self._modules[module_name] = module

        # Find strategy classes
        strategy_classes = _discover_strategy_classes(module)
        for cls in strategy_classes:
            try:
                instance = cls()
                if not instance.name or instance.name == "unnamed":
                    instance.name = module_name
                self._loaded[instance.name] = instance
                logger.info(
                    "Loaded strategy: %s (%s) — %s",
                    instance.name,
                    instance.description or "no description",
                    instance.symbols or "all symbols",
                )
                instance.on_load()
            except Exception as exc:
                logger.error(
                    "Failed to instantiate %s from %s: %s",
                    cls.__name__,
                    module_name,
                    exc,
                )
                self._errors[f"{module_name}.{cls.__name__}"] = str(exc)

    def get(self, name: str) -> Optional[BaseStrategy]:
        """Get a loaded strategy by name."""
        return self._loaded.get(name)

    def list_strategies(self) -> List[Dict[str, any]]:
        """List all loaded strategies with their metadata."""
        result = []
        for name, strategy in self._loaded.items():
            meta = strategy.metadata()
            result.append({
                "name": name,
                "description": meta.description,
                "symbols": meta.symbols,
                "timeframe": meta.timeframe,
                "stage": meta.stage,
                "version": meta.version,
                "author": meta.author,
            })
        return result

    @property
    def loaded_count(self) -> int:
        return len(self._loaded)

    @property
    def errors(self) -> Dict[str, str]:
        return dict(self._errors)

    def reload(self) -> Dict[str, BaseStrategy]:
        """Reload all strategies (useful during development)."""
        # Unload existing
        for name, strategy in self._loaded.items():
            try:
                strategy.on_unload()
            except Exception:
                pass
        self._loaded.clear()
        self._modules.clear()
        self._errors.clear()

        # Re-discover
        return self.discover()


# Module-level convenience
_loader: Optional[StrategyLoader] = None


def discover_strategies(strategies_dir: Optional[str] = None) -> Dict[str, BaseStrategy]:
    """Discover all strategies in the strategies directory.

    This is the primary entry point for loading strategy plugins.
    """
    global _loader
    _loader = StrategyLoader(strategies_dir)
    return _loader.discover()


def get_loader() -> Optional[StrategyLoader]:
    """Get the global strategy loader instance."""
    return _loader
