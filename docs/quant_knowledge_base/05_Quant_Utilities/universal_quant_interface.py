"""
Universal Quant Module Interface
=================================
Standardized base classes and interfaces for all quantitative finance modules.
Agents use these interfaces to discover, execute, and chain quant tools.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
import inspect
import json


@dataclass
class QuantTool:
    """Universal tool interface for quant modules."""
    name: str
    module_id: str
    description: str
    parameters: Dict[str, Any]
    returns: Dict[str, Any]
    implementation: Any = None  # Callable

    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool with given parameters."""
        if self.implementation is None:
            raise NotImplementedError(f"Tool {self.name} has no implementation")
        return self.implementation(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "module_id": self.module_id,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
        }


@dataclass
class QuantModule:
    """Base class for all quant modules."""
    id: str
    name: str
    version: str
    category: str
    tags: List[str] = field(default_factory=list)
    description: str = ""
    references: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    _tools: Dict[str, QuantTool] = field(default_factory=dict)

    def register_tool(self, tool: QuantTool):
        """Register a tool exposed by this module."""
        self._tools[tool.name] = tool

    def get_tools(self) -> List[QuantTool]:
        """Get all tools exposed by this module."""
        return list(self._tools.values())

    def get_tool(self, name: str) -> Optional[QuantTool]:
        """Get a specific tool by name."""
        return self._tools.get(name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "tags": self.tags,
            "description": self.description,
            "references": self.references,
            "dependencies": self.dependencies,
            "tools": [t.to_dict() for t in self.get_tools()],
        }


class QuantModuleRegistry:
    """Global registry for all quant modules."""
    _instance = None
    _modules: Dict[str, QuantModule] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, module: QuantModule):
        """Register a module in the global registry."""
        self._modules[module.id] = module

    def get(self, module_id: str) -> Optional[QuantModule]:
        """Get a module by ID."""
        return self._modules.get(module_id)

    def list_modules(self, category: str = None) -> List[QuantModule]:
        """List all modules, optionally filtered by category."""
        modules = list(self._modules.values())
        if category:
            modules = [m for m in modules if m.category == category]
        return modules

    def discover_tools(self, category: str = None) -> List[QuantTool]:
        """Discover all tools across all modules."""
        tools = []
        for module in self.list_modules(category):
            tools.extend(module.get_tools())
        return tools

    def find_tools_by_tag(self, tag: str) -> List[QuantTool]:
        """Find tools by tag."""
        tools = []
        for module in self._modules.values():
            if tag in module.tags:
                tools.extend(module.get_tools())
        return tools


# Global registry instance
registry = QuantModuleRegistry()


def quant_tool(module_id: str, name: str, description: str, parameters: Dict, returns: Dict):
    """Decorator to register a function as a quant tool."""
    def decorator(func):
        tool = QuantTool(
            name=name,
            module_id=module_id,
            description=description,
            parameters=parameters,
            returns=returns,
            implementation=func,
        )
        # Find the module in registry and register tool
        module = registry.get(module_id)
        if module:
            module.register_tool(tool)
        return func
    return decorator


def quant_module(id: str, name: str, version: str, category: str, tags: List[str] = None):
    """Decorator to register a class as a quant module."""
    def decorator(cls):
        module = QuantModule(
            id=id,
            name=name,
            version=version,
            category=category,
            tags=tags or [],
            description=cls.__doc__ or "",
        )
        registry.register(module)
        cls._module_id = id
        return cls
    return decorator
