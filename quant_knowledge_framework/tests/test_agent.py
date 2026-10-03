from pathlib import Path

from quant_framework.agent import QuantAgent
from quant_framework.config import Config


def test_discover_tools_returns_list():
    agent = QuantAgent("config/sources.yaml")
    tools = agent.discover_tools()
    assert isinstance(tools, list)
    assert any(tool["name"] == "knowledge.search" for tool in tools)


def test_smoke_test_tool_is_discoverable():
    agent = QuantAgent("config/sources.yaml")
    tool = next(t for t in agent.discover_tools() if t["name"] == "knowledge.smoke_test")
    assert tool["category"] == "quant_utilities"
    assert "GREEN" in tool["description"]
    assert "timeout" in tool["parameters"]


def test_smoke_test_missing_script_reports_error(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "sources.yaml").write_text("paths: {}\n", encoding="utf-8")
    agent = QuantAgent(str(cfg / "sources.yaml"))
    result = agent.execute_tool("knowledge.smoke_test")
    assert result["success"] is False
    assert "smoke_test.py not found" in result["result"]["error"]


def test_qkf_root_resolves_to_package_root():
    agent = QuantAgent("config/sources.yaml")
    root = agent._qkf_root()
    # src layout: config/ and smoke_test.py live at the package root.
    assert (root / "config" / "sources.yaml").exists()
    assert (root / "smoke_test.py").exists()


def test_execute_knowledge_classify():
    agent = QuantAgent("config/sources.yaml")
    result = agent.execute_tool("knowledge.classify", text="hidden markov model regime detection")
    assert result["success"] is True
    assert result["result"]["topic"] == "01_Regime_Detection"


def test_execute_unknown_tool():
    agent = QuantAgent("config/sources.yaml")
    result = agent.execute_tool("does.not.exist")
    assert result["success"] is False
    assert "not found" in result["error"]
