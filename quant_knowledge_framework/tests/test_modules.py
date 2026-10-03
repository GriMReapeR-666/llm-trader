import pytest
import numpy as np
from quant_framework.modules.interface import QuantModule, quant_module, quant_tool, registry


@quant_module(
    id="test_module.sample",
    name="Sample Test Module",
    version="1.0.0",
    category="test",
    tags=["test"],
)
class SampleModule(QuantModule):
    @quant_tool(
        module_id="test_module.sample",
        name="test_module.sample.add",
        description="Add two numbers",
        parameters={
            "a": {"type": "float", "description": "First number"},
            "b": {"type": "float", "description": "Second number"},
        },
        returns={
            "result": {"type": "float", "description": "Sum"},
        },
    )
    def add(self, a: float, b: float) -> dict:
        return {"result": float(a + b)}


@pytest.fixture(autouse=True)
def clear_registry():
    registry._modules.clear()
    registry._tools.clear()
    yield
    registry._modules.clear()
    registry._tools.clear()


def test_sample_module_tool_registered():
    module = SampleModule()
    assert "test_module.sample.add" in [t.name for t in registry.list_tools()]


def test_sample_module_add():
    module = SampleModule()
    result = module.add(2, 3)
    assert result == {"result": 5.0}
