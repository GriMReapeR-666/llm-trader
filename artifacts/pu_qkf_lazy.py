import os, sys, subprocess, tempfile, json

qkf = os.getcwd()
src = os.path.join(qkf, "src")

print("QKF root:", qkf)

code = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import quant_framework                       # package import must not construct an agent
import quant_framework.agent.interface as m

agents_at_import = [k for k, v in vars(m).items() if v.__class__.__name__ == "QuantAgent"]
print("agents_at_import:", agents_at_import)

# Explicit instantiation keeps the documented CWD-relative behavior:
from quant_framework.agent import QuantAgent
explicit = QuantAgent()
print("explicit_config_none:", explicit.config is None)

# First access of the module-level singleton builds it lazily; from a foreign
# CWD it must find the config via the package-root fallback.
a = m.agent
print("lazy_config_none:", a.config is None)
kb_count = sum(len(v) for v in a.knowledge_base.index.values()) if a.knowledge_base else 0
print("lazy_kb_files:", kb_count)
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
print("SUBPROC-OK")
"""

with tempfile.TemporaryDirectory(prefix="qkf_lazy_cwd_") as tmp:
    env = dict(os.environ, PYTHONPATH=src, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code, src],
        cwd=tmp, capture_output=True, text=True, env=env, timeout=120,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print("STDERR:", proc.stderr[-2000:])
    print("subprocess exit:", proc.returncode)

print("LAZY-DONE")
