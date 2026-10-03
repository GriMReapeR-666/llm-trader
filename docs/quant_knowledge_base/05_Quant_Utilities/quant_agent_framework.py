"""
Quant Agent Execution Framework
=================================
Provides agent-executable tools for discovering, executing, and chaining
quantitative finance modules. Agents use this framework to run quant operations
through standardized tool/function calling.
"""

from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
import traceback

from universal_quant_interface import QuantModuleRegistry, QuantTool, registry


class ModuleCategory(Enum):
    REGIME_DETECTION = "regime_detection"
    STRATEGY_DEVELOPMENT = "strategy_development"
    PORTFOLIO_MANAGEMENT = "portfolio_management"
    ORDER_EXECUTION = "order_execution"
    QUANT_UTILITIES = "quant_utilities"


@dataclass
class ToolCallResult:
    """Result of a tool execution."""
    success: bool
    tool_name: str
    result: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool_name": self.tool_name,
            "result": self.result,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }


class QuantAgent:
    """
    Universal agent interface for quant operations.
    
    Usage:
        agent = QuantAgent()
        
        # Discover available tools
        tools = agent.discover_tools(category="regime_detection")
        
        # Execute a single tool
        result = agent.execute_tool("regime_detection.hmm.classify", returns=[0.01, -0.02, ...])
        
        # Chain multiple tools
        pipeline = [
            {"tool": "data.fetch_yfinance", "params": {"ticker": "SPY", "period": "1y"}},
            {"tool": "regime_detection.hmm.classify", "params": {"returns": "$previous.returns"}},
            {"tool": "portfolio_management.kelly.calculate", "params": {"win_rate": "$previous.confidence"}}
        ]
        results = agent.chain_tools(pipeline)
    """

    def __init__(self):
        self.registry = registry
        self.execution_history: List[ToolCallResult] = []

    def discover_tools(self, category: str = None, tag: str = None) -> List[Dict[str, Any]]:
        """
        Discover available tools.
        
        Args:
            category: Filter by module category (e.g., "regime_detection")
            tag: Filter by tag (e.g., "hmm", "volatility")
            
        Returns:
            List of tool definitions with parameters and return schemas
        """
        if tag:
            tools = self.registry.find_tools_by_tag(tag)
        else:
            tools = self.registry.discover_tools(category)

        return [t.to_dict() for t in tools]

    def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """
        Execute a single tool by name.
        
        Args:
            tool_name: Full tool name (e.g., "regime_detection.hmm.classify")
            **kwargs: Tool parameters
            
        Returns:
            Tool execution result
        """
        import time
        start = time.time()

        try:
            # Find tool across all modules
            tool = None
            for module in self.registry._modules.values():
                tool = module.get_tool(tool_name)
                if tool:
                    break

            if not tool:
                return ToolCallResult(
                    success=False,
                    tool_name=tool_name,
                    error=f"Tool not found: {tool_name}"
                ).to_dict()

            # Execute
            result = tool.execute(**kwargs)
            elapsed = (time.time() - start) * 1000.0

            call_result = ToolCallResult(
                success=True,
                tool_name=tool_name,
                result=result,
                execution_time_ms=elapsed
            )

        except Exception as e:
            elapsed = (time.time() - start) * 1000.0
            call_result = ToolCallResult(
                success=False,
                tool_name=tool_name,
                error=f"{str(e)}\n{traceback.format_exc()}",
                execution_time_ms=elapsed
            )

        self.execution_history.append(call_result)
        return call_result.to_dict()

    def chain_tools(self, pipeline: List[Dict[str, Any]], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute a sequence of tools with data passing between them.
        
        Args:
            pipeline: List of tool calls in sequence
                Each item: {
                    "tool": "module.tool_name",
                    "params": {"param": value},
                    "save_as": "variable_name"  # Optional: save result to context
                }
            context: Initial context variables
            
        Returns:
            Final execution results and updated context
        """
        if context is None:
            context = {}

        results = []
        for i, step in enumerate(pipeline):
            tool_name = step.get("tool")
            params = step.get("params", {})
            save_as = step.get("save_as")

            # Resolve parameter references (e.g., "$previous.returns")
            resolved_params = self._resolve_params(params, context)

            # Execute tool
            result = self.execute_tool(tool_name, **resolved_params)

            # Store result
            results.append({
                "step": i,
                "tool": tool_name,
                "params": resolved_params,
                "result": result
            })

            # Save to context if requested
            if save_as:
                context[save_as] = result

        return {
            "pipeline_results": results,
            "final_context": context
        }

    def _resolve_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve parameter references like '$previous.returns' to actual values."""
        resolved = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                # Reference to previous result
                parts = value[1:].split(".", 1)
                if len(parts) == 2:
                    ref_step, ref_key = parts
                    # Look up in context
                    ref_data = context.get(ref_step, {})
                    if isinstance(ref_data, dict):
                        resolved[key] = ref_data.get("result", {}).get(ref_key)
                    else:
                        resolved[key] = ref_data
                else:
                    resolved[key] = context.get(value[1:])
            else:
                resolved[key] = value
        return resolved

    def get_execution_history(self) -> List[Dict[str, Any]]:
        """Get history of all tool executions."""
        return [r.to_dict() for r in self.execution_history]

    def clear_history(self):
        """Clear execution history."""
        self.execution_history.clear()


# Global agent instance
agent = QuantAgent()


# Convenience functions for direct tool execution
def run_tool(tool_name: str, **kwargs) -> Dict[str, Any]:
    """Execute a tool directly using the global agent instance."""
    return agent.execute_tool(tool_name, **kwargs)


def run_pipeline(pipeline: List[Dict[str, Any]], context: Dict[str, Any] = None) -> Dict[str, Any]:
    """Execute a tool pipeline using the global agent instance."""
    return agent.chain_tools(pipeline, context)


def discover(category: str = None, tag: str = None) -> List[Dict[str, Any]]:
    """Discover available tools using the global agent instance."""
    return agent.discover_tools(category, tag)
