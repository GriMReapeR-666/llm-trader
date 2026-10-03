"""
Quant Agent Interface
=====================
Provides agent-executable tools for the quant knowledge framework.
"""

import os
import re
import sys
import json
import logging
import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from quant_framework.config import Config
from quant_framework.storage import KnowledgeBase, KnowledgeClassifier

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    name: str
    category: str
    description: str
    parameters: Dict[str, Any]
    returns: Dict[str, Any]
    module_path: str
    function_name: str


@dataclass
class ToolResult:
    success: bool
    tool_name: str
    result: Any = None
    error: Optional[str] = None


class QuantAgent:
    """
    Universal agent interface for quant knowledge operations.
    """

    def __init__(self, config_path: str = "config/sources.yaml"):
        self.config = Config(config_path) if Path(config_path).exists() else None
        self.knowledge_base = KnowledgeBase(self.config) if self.config else None
        self.classifier = KnowledgeClassifier(self.config) if self.config else None
        self._tool_registry: Dict[str, ToolDefinition] = {}
        self._load_builtin_tools()
        self._load_module_registry_tools()

    def _load_builtin_tools(self) -> None:
        self._tool_registry.update(
            {
                "knowledge.search": ToolDefinition(
                    name="knowledge.search",
                    category="quant_utilities",
                    description="Search the quant knowledge base",
                    parameters={"query": {"type": "string", "required": True}},
                    returns={"results": {"type": "array"}},
                    module_path="quant_framework.storage.knowledge_base",
                    function_name="KnowledgeBase.search",
                ),
                "knowledge.classify": ToolDefinition(
                    name="knowledge.classify",
                    category="quant_utilities",
                    description="Classify text into quant topics",
                    parameters={"text": {"type": "string", "required": True}},
                    returns={"topic": {"type": "string"}, "confidence": {"type": "float"}},
                    module_path="quant_framework.storage.classifier",
                    function_name="KnowledgeClassifier.classify_text",
                ),
                "knowledge.list_topics": ToolDefinition(
                    name="knowledge.list_topics",
                    category="quant_utilities",
                    description="List all available topics",
                    parameters={},
                    returns={"topics": {"type": "array"}},
                    module_path="quant_framework.storage.knowledge_base",
                    function_name="KnowledgeBase.get_by_topic",
                ),
                "knowledge.get_content": ToolDefinition(
                    name="knowledge.get_content",
                    category="quant_utilities",
                    description="Get content of a specific knowledge base file",
                    parameters={"path": {"type": "string", "required": True}},
                    returns={"content": {"type": "string"}},
                    module_path="quant_framework.storage.knowledge_base",
                    function_name="KnowledgeBase.get_content",
                ),
                "pipeline.run": ToolDefinition(
                    name="pipeline.run",
                    category="quant_utilities",
                    description="Run the complete knowledge extraction pipeline",
                    parameters={"channels": {"type": "array", "required": False}},
                    returns={"stages": {"type": "object"}},
                    module_path="quant_framework.pipeline",
                    function_name="KnowledgePipeline.run_full_pipeline",
                ),
                "knowledge.smoke_test": ToolDefinition(
                    name="knowledge.smoke_test",
                    category="quant_utilities",
                    description=(
                        "Run the QKF smoke test (pytest suite + E2E validation battery "
                        "+ pipeline probes) and return the GREEN/RED verdict. "
                        "No network access; sandboxed."
                    ),
                    parameters={
                        "timeout": {"type": "int", "required": False, "default": 600},
                    },
                    returns={
                        "verdict": {"type": "string"},
                        "steps": {"type": "array"},
                        "report_path": {"type": "string"},
                    },
                    module_path="quant_framework.agent.interface",
                    function_name="QuantAgent._run_smoke_test",
                ),
            }
        )

    # Mapping from registry tool name to the QuantModule method that backs it.
    # Module ids equal the tool name minus its last dotted segment, so a
    # single rpartition() resolves the owning module instance.
    _MODULE_TOOL_METHODS = {
        "regime_detection.hmm.fit": "fit",
        "regime_detection.hmm.classify": "classify",
        "regime_detection.hurst.calculate": "calculate",
        "strategy_development.wfo.optimize": "optimize",
        "strategy_development.dsr.calculate": "calculate",
        "strategy_development.triple_barrier.label": "label",
        "portfolio_management.kelly.calculate": "calculate",
        "portfolio_management.hrp.optimize": "optimize",
        "order_execution.spread.calculate": "calculate",
        "order_execution.almgren_chriss.calculate": "calculate",
        "quant_utilities.expectancy.calculate": "calculate",
        "quant_utilities.expectancy.r_multiple": "r_multiple",
        "quant_utilities.drawdown.analyze": "analyze",
    }

    def _load_module_registry_tools(self) -> None:
        """Bridge quant module registry tools into the agent tool registry.

        Every tool defined on a QuantModule via @quant_tool becomes
        agent-callable: discover_tools() lists it and execute_tool() runs it
        against the module instance held in the module registry.
        """
        try:
            # Imported lazily: quant_framework/__init__ imports this package
            # first, so a module-level import here would be circular.
            from quant_framework.modules import registry as module_registry
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("quant module registry unavailable: %s", exc)
            return

        for tool in module_registry.list_tools():
            if tool.name in self._tool_registry:
                continue
            if tool.name not in self._MODULE_TOOL_METHODS:
                logger.warning("no dispatch mapping for module tool %s; skipping", tool.name)
                continue
            self._tool_registry[tool.name] = ToolDefinition(
                name=tool.name,
                category=tool.module_id.split(".")[0],
                description=tool.description,
                parameters={
                    p.name: {
                        "type": p.param_type,
                        "required": p.required,
                        "default": p.default,
                        "description": p.description,
                    }
                    for p in tool.parameters
                },
                returns=tool.returns,
                module_path="quant_framework.modules",
                function_name=self._MODULE_TOOL_METHODS[tool.name],
            )

    def _execute_module_tool(self, tool_name: str, **kwargs) -> Any:
        from quant_framework.modules import registry as module_registry

        module_id, _sep, _tool_suffix = tool_name.rpartition(".")
        module = module_registry.get_module(module_id)
        if module is None:
            raise LookupError(f"Module not registered: {module_id}")
        method = getattr(module, self._MODULE_TOOL_METHODS[tool_name])
        return method(**kwargs)

    def discover_tools(self, category: str = None, tag: str = None) -> List[Dict[str, Any]]:
        tools = []
        for tool_def in self._tool_registry.values():
            if category and tool_def.category != category:
                continue
            tools.append(
                {
                    "name": tool_def.name,
                    "category": tool_def.category,
                    "description": tool_def.description,
                    "parameters": tool_def.parameters,
                    "returns": tool_def.returns,
                }
            )
        return tools

    def _resolve_tool_function(self, module_path: str, function_name: str):
        import importlib
        module = importlib.import_module(module_path)
        parts = function_name.split(".")
        obj = module
        for part in parts:
            obj = getattr(obj, part)
        return obj

    def _qkf_root(self) -> Path:
        """Directory containing config/ and smoke_test.py.

        Prefers the loaded config's location; falls back to the package-root
        convention (src layout: parents[3] from this file).
        """
        if self.config is not None:
            return Path(self.config.config_path).resolve().parent.parent
        return Path(self._package_root_config()).resolve().parent.parent

    def _run_smoke_test(self, timeout: int = 600) -> Dict[str, Any]:
        """Run smoke_test.py as a subprocess and summarize its verdict.

        The runner exits 0 only when every step is green and writes a
        marker-footed report next to artifacts/qkf_smoke_report.txt.
        """
        import subprocess

        qkf_root = self._qkf_root()
        smoke_script = qkf_root / "smoke_test.py"
        if not smoke_script.exists():
            return {"success": False, "error": f"smoke_test.py not found at {smoke_script}"}

        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(smoke_script)],
                cwd=str(qkf_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"smoke test timed out after {timeout}s"}

        output = (proc.stdout or "") + (proc.stderr or "")
        m = re.search(r"SMOKE-VERDICT:\s*(GREEN|RED)", output)
        verdict = m.group(1) if m else ("GREEN" if proc.returncode == 0 else "RED")
        steps = [
            line.strip()
            for line in output.splitlines()
            if line.startswith("[PASS]") or line.startswith("[FAIL]")
        ]
        report_path = qkf_root.parent / "artifacts" / "qkf_smoke_report.txt"
        result: Dict[str, Any] = {
            "success": proc.returncode == 0 and verdict == "GREEN",
            "verdict": verdict,
            "exit_code": proc.returncode,
            "steps": steps,
            "report_path": str(report_path),
        }
        if proc.returncode != 0:
            # Include the tail so the caller can see which step failed without
            # re-running the suite.
            result["output_tail"] = output.strip().splitlines()[-30:]
        return result

    def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        if tool_name not in self._tool_registry:
            return ToolResult(success=False, tool_name=tool_name, error=f"Tool not found: {tool_name}").__dict__

        tool_def = self._tool_registry[tool_name]
        try:
            # Module-registry tools dispatch to a QuantModule instance held in
            # the module registry, not to a module-level function, so they must
            # be routed before the generic function resolution below.
            if tool_name in self._MODULE_TOOL_METHODS:
                result = self._execute_module_tool(tool_name, **kwargs)
                return ToolResult(success=True, tool_name=tool_name, result=result).__dict__

            if tool_name == "knowledge.smoke_test":
                result = self._run_smoke_test(**kwargs)
                result["tool_ok"] = bool(result.get("success"))
                return ToolResult(success=result["tool_ok"], tool_name=tool_name, result=result).__dict__

            func = self._resolve_tool_function(tool_def.module_path, tool_def.function_name)

            if "knowledge.search" in tool_name:
                result = func(self.knowledge_base, **kwargs)
            elif "knowledge.classify" in tool_name:
                raw = func(self.classifier, kwargs.get("text", ""))
                if isinstance(raw, tuple) and len(raw) == 3:
                    result = {"topic": raw[0], "confidence": raw[1], "keywords": raw[2]}
                else:
                    result = raw
            elif "knowledge.list_topics" in tool_name:
                topic = kwargs.get("topic", "")
                if topic:
                    raw = func(self.knowledge_base, topic)
                    result = {"topics": raw}
                else:
                    result = {"topics": list(self.knowledge_base.index.keys())}
            elif "knowledge.get_content" in tool_name:
                result = func(self.knowledge_base, kwargs.get("path", ""))
            else:
                result = func(**kwargs)

            return ToolResult(success=True, tool_name=tool_name, result=result).__dict__
        except Exception as e:
            return ToolResult(success=False, tool_name=tool_name, error=str(e)).__dict__

    def chain_tools(self, pipeline: List[Dict[str, Any]], context: Dict[str, Any] = None) -> Dict[str, Any]:
        if context is None:
            context = {}
        results = []
        for i, step in enumerate(pipeline):
            tool_name = step.get("tool")
            params = step.get("params", {})
            save_as = step.get("save_as")
            resolved_params = self._resolve_params(params, context)
            result = self.execute_tool(tool_name, **resolved_params)
            results.append({"step": i, "tool": tool_name, "result": result})
            if save_as:
                context[save_as] = result
        return {"pipeline_results": results, "final_context": context}

    def _resolve_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        resolved = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                parts = value[1:].split(".", 1)
                if len(parts) == 2:
                    ref_step, ref_key = parts
                    ref_data = context.get(ref_step, {})
                    resolved[key] = ref_data.get("result", {}).get(ref_key) if isinstance(ref_data, dict) else ref_data
                else:
                    resolved[key] = context.get(value[1:])
            else:
                resolved[key] = value
        return resolved

    def register_tool(self, tool: ToolDefinition) -> None:
        self._tool_registry[tool.name] = tool


# --- Lazy module-level default agent (PEP 562) -----------------------------
# The historical `agent = QuantAgent()` ran at import time with a CWD-relative
# config path, making `import quant_framework` behave differently depending on
# the working directory (and creating data/ directories as an import side
# effect). The default agent is now constructed on first ACCESS instead, and
# falls back to the package-root config when the CWD has none.

_default_agent: Optional["QuantAgent"] = None


def _package_root_config() -> str:
    # src layout: <root>/src/quant_framework/agent/interface.py -> parents[3] = <root>
    return str(Path(__file__).resolve().parents[3] / "config" / "sources.yaml")


def _get_default_agent() -> "QuantAgent":
    global _default_agent
    if _default_agent is None:
        for candidate in ("config/sources.yaml", _package_root_config()):
            if Path(candidate).exists():
                _default_agent = QuantAgent(candidate)
                break
        else:
            logger.warning(
                "no config/sources.yaml found relative to CWD or package root; "
                "default agent runs without a knowledge base"
            )
            _default_agent = QuantAgent()
        globals()["agent"] = _default_agent  # cache for plain attribute access
    return _default_agent


def reset_default_agent() -> None:
    """Drop the cached default agent so the next access constructs a fresh one."""
    global _default_agent
    _default_agent = None
    globals().pop("agent", None)


def __getattr__(name: str):
    if name == "agent":
        return _get_default_agent()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
