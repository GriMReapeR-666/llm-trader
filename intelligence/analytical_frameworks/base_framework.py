"""Base framework and universal module interface specification for analytical engines.

Conforms strictly to UNIVERSAL_MODULE_INTERFACE_SPEC.md:
- Standardized Module Metadata (id, name, version, category, tags, formulas, parameters)
- Standardized Tool Execution protocol
- Pydantic/dataclass schema definitions
- Global registry integration
"""
from __future__ import annotations

from dataclasses import dataclass, field
import functools
import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Type, Union
from pydantic import BaseModel, Field

log = logging.getLogger("AnalyticalFrameworkBase")


@dataclass
class UniversalToolParameter:
    name: str
    param_type: str
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class UniversalToolDefinition:
    name: str
    module_id: str
    description: str
    parameters: List[UniversalToolParameter]
    returns: Dict[str, Any]
    tags: List[str] = field(default_factory=list)
    handler: Optional[Callable[..., Any]] = None

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        if self.handler is None:
            raise NotImplementedError(f"Tool {self.name} has no executable handler registered.")
        return self.handler(**kwargs)


class AnalyticalModuleRegistry:
    """Central registry for all analytical framework modules and executable tools."""

    def __init__(self) -> None:
        self._modules: Dict[str, Any] = {}
        self._tools: Dict[str, UniversalToolDefinition] = {}

    def register_module(self, module_instance: Any) -> None:
        module_id = getattr(module_instance, "module_id", module_instance.__class__.__name__)
        self._modules[module_id] = module_instance
        if hasattr(module_instance, "get_tools"):
            for tool in module_instance.get_tools():
                self._tools[tool.name] = tool

    def get_module(self, module_id: str) -> Optional[Any]:
        return self._modules.get(module_id)

    def get_tool(self, tool_name: str) -> Optional[UniversalToolDefinition]:
        return self._tools.get(tool_name)

    def list_modules(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": getattr(m, "module_id", k),
                "name": getattr(m, "module_name", k),
                "version": getattr(m, "version", "1.0.0"),
                "category": getattr(m, "category", "analytical_framework"),
                "tags": getattr(m, "tags", []),
                "tools_count": len(getattr(m, "get_tools", lambda: [])()),
            }
            for k, m in self._modules.items()
        ]

    def list_tools(self, category: Optional[str] = None) -> List[UniversalToolDefinition]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if category.lower() in t.module_id.lower()]
        return tools


GLOBAL_ANALYTICAL_REGISTRY = AnalyticalModuleRegistry()


class BaseAnalyticalModule:
    """Base class for Volumes 01 to 07 Analytical Framework Engines."""

    module_id: str = "base.analytical"
    module_name: str = "Base Analytical Module"
    version: str = "1.0.0"
    category: str = "analytical_framework"
    tags: List[str] = []
    mathematics_formulas: List[str] = []

    def __init__(self) -> None:
        self._tools: List[UniversalToolDefinition] = []
        self._register_tools()
        GLOBAL_ANALYTICAL_REGISTRY.register_module(self)

    def _register_tools(self) -> None:
        """Subclasses discover and register methods decorated with @analytical_tool."""
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if hasattr(attr, "_is_analytical_tool"):
                tool_def: UniversalToolDefinition = getattr(attr, "_tool_def")
                tool_def.handler = attr
                self._tools.append(tool_def)

    def get_tools(self) -> List[UniversalToolDefinition]:
        return self._tools

    def describe(self) -> Dict[str, Any]:
        return {
            "module_id": self.module_id,
            "name": self.module_name,
            "version": self.version,
            "category": self.category,
            "tags": self.tags,
            "formulas": self.mathematics_formulas,
            "tools": [t.name for t in self._tools],
        }


def analytical_tool(
    name: str,
    description: str,
    parameters: Dict[str, Any],
    returns: Dict[str, Any],
    tags: Optional[List[str]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to expose methods as universal analytical tools."""
    param_list: List[UniversalToolParameter] = []
    for p_name, p_info in parameters.items():
        param_list.append(
            UniversalToolParameter(
                name=p_name,
                param_type=p_info.get("type", "string"),
                required=p_info.get("required", True),
                default=p_info.get("default", None),
                description=p_info.get("description", ""),
            )
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._is_analytical_tool = True
        func._tool_def = UniversalToolDefinition(
            name=name,
            module_id="",  # Bound dynamically on class init
            description=description,
            parameters=param_list,
            returns=returns,
            tags=tags or [],
        )
        return func

    return decorator
