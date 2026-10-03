"""
Regression tests for the lazy default agent (PEP 562 module __getattr__).

The historical `agent = QuantAgent()` at module import time made
`import quant_framework` CWD-dependent (config lookup + data/ directory
creation as import side effects). The default agent must now be constructed
only on first access, with a package-root config fallback.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

QKF_ROOT = Path(__file__).resolve().parents[1]
SRC = QKF_ROOT / "src"

SUBPROC_CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
import quant_framework                       # must not construct an agent
import quant_framework.agent.interface as m

agents_at_import = [k for k, v in vars(m).items() if v.__class__.__name__ == "QuantAgent"]
print("agents_at_import:", agents_at_import)

from quant_framework.agent import QuantAgent
explicit = QuantAgent()
print("explicit_config_none:", explicit.config is None)

a = m.agent
print("lazy_config_none:", a.config is None)
print("lazy_is_cached:", m.agent is a)

try:
    m.does_not_exist
    print("unknown_attr_raised: False")
except AttributeError:
    print("unknown_attr_raised: True")

m.reset_default_agent()
print("agent_gone_after_reset:", "agent" not in vars(m))
b = m.agent
print("fresh_after_reset:", b is not a)
"""


class TestLazyDefaultAgent:
    def test_import_from_foreign_cwd_constructs_no_agent(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", SUBPROC_CODE, str(SRC)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        lines = dict(
            line.split(": ", 1) for line in proc.stdout.splitlines() if ": " in line
        )
        assert lines["agents_at_import"] == "[]"
        # Explicit instantiation keeps the documented CWD-relative behavior.
        assert lines["explicit_config_none"] == "True"
        # Lazy singleton finds the package-root config from a foreign CWD.
        assert lines["lazy_config_none"] == "False"
        assert lines["lazy_is_cached"] == "True"
        assert lines["unknown_attr_raised"] == "True"
        assert lines["agent_gone_after_reset"] == "True"
        assert lines["fresh_after_reset"] == "True"

    def test_inprocess_lazy_access_and_reset(self):
        import quant_framework.agent.interface as m

        m.reset_default_agent()
        try:
            assert "agent" not in vars(m)
            a = m.agent
            assert a.__class__.__name__ == "QuantAgent"
            assert m.agent is a
            m.reset_default_agent()
            assert "agent" not in vars(m)
            assert m.agent is not a
        finally:
            m.reset_default_agent()

    def test_unknown_attribute_raises(self):
        import quant_framework.agent.interface as m

        with pytest.raises(AttributeError):
            m.definitely_not_an_attribute
