"""
Universal Module Interface
==========================
Base classes and decorators for quant modules.
"""

import functools
import inspect
from typing import Any, Callable, Dict, List, Optional, Type
from dataclasses import dataclass, field


@dataclass
class ToolParameter:
    name: str
    param_type: str
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class ToolDefinition:
    name: str
    module_id: str
    description: str
    parameters: List[ToolParameter]
    returns: Dict[str, Any]
    tags: List[str] = field(default_factory=list)


class QuantModuleRegistry:
    """Global registry of quant modules."""

    def __init__(self) -> None:
        self._modules: Dict[str, Any] = {}
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, module: Any) -> None:
        module_id = getattr(module, "module_id", module.__class__.__name__)
        self._modules[module_id] = module

        if hasattr(module, "_tool_defs"):
            for tool in module._tool_defs:
                self._tools[tool.name] = tool

        if hasattr(module, "__init__"):
            init = module.__init__
            if hasattr(init, "__wrapped__"):
                original_init = init.__wrapped__
            else:
                original_init = init
            try:
                if not hasattr(original_init, "_registered"):
                    original_init._registered = True
            except (AttributeError, TypeError):
                pass

    def get_module(self, module_id: str) -> Optional[Any]:
        return self._modules.get(module_id)

    def list_tools(self, category: str = None) -> List[ToolDefinition]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if category in t.module_id]
        return tools


registry = QuantModuleRegistry()


def quant_module(*, id: str, name: str, version: str, category: str, tags: List[str]):
    """
    Decorator to register a quant module.
    """

    def decorator(cls):
        cls.module_id = id
        cls.module_name = name
        cls.version = version
        cls.category = category
        cls.tags = tags
        # `quant_tool` decorates methods before this class decorator runs and
        # stores definitions on those functions. Preserve that metadata when
        # initializing the class-level registry payload.
        cls._tool_defs = [
            tool
            for member in cls.__dict__.values()
            for tool in getattr(member, "_tool_defs", [])
        ]
        original_init = cls.__init__

        @functools.wraps(original_init)
        def wrapped_init(self_instance, *args, **kwargs):
            original_init(self_instance, *args, **kwargs)
            registry.register(self_instance)

        cls.__init__ = wrapped_init
        return cls

    return decorator


def quant_tool(*, module_id: str, name: str, description: str, parameters: Dict[str, Any], returns: Dict[str, Any], tags: List[str] = None):
    """
    Decorator to register a quant tool.
    """
    if tags is None:
        tags = []

    def decorator(func: Callable):
        sig = inspect.signature(func)
        tool_params = []
        for param_name, param_info in parameters.items():
            tool_params.append(
                ToolParameter(
                    name=param_name,
                    param_type=param_info.get("type", "any"),
                    required=param_info.get("required", True),
                    default=param_info.get("default"),
                    description=param_info.get("description", ""),
                )
            )

        tool = ToolDefinition(
            name=name,
            module_id=module_id,
            description=description,
            parameters=tool_params,
            returns=returns,
            tags=tags,
        )

        # Attach tool to class via closure over module_id/tool
        module_cls = registry.get_module(module_id)
        if module_cls is not None and hasattr(module_cls, "_tool_defs"):
            module_cls._tool_defs.append(tool)
        else:
            # If module is not registered yet, attach to function metadata
            if not hasattr(func, "_tool_defs"):
                func._tool_defs = []
            func._tool_defs.append(tool)

        return func

    return decorator


class QuantModule:
    """Base class for all quant modules."""

    module_id: str = "base"
    module_name: str = "Base Module"
    version: str = "0.0.0"
    category: str = "base"
    tags: List[str] = []
    _tool_defs: List[ToolDefinition] = []

    def discover_tools(self) -> List[ToolDefinition]:
        return list(self._tool_defs)
