import os, sys, json, traceback

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
base = os.getcwd()
sys.path.insert(0, os.path.join(base, "src"))


def section(title):
    print("\n=== " + title + " ===")


def get_registry(M):
    reg = getattr(M, "registry", None)
    if reg is None:
        try:
            from quant_framework.modules.interface import registry as reg2
            reg = reg2
        except Exception:
            reg = None
    return reg


def tool_names_of(reg):
    try:
        tools = reg.list_tools()
        names = []
        for t in tools:
            if isinstance(t, dict):
                names.append(t.get("name", str(t)))
            else:
                names.append(getattr(t, "name", str(t)))
        return len(tools), names
    except Exception as e:
        return -1, [repr(e)]


section("PLAIN IMPORT of quant_framework.modules")
try:
    import quant_framework.modules as M
    reg = get_registry(M)
    if reg is None:
        print("no registry object found on package")
    else:
        n, names = tool_names_of(reg)
        print("plain-import registry tool count:", n)
        print("names:", sorted(names))
except Exception:
    traceback.print_exc()

section("EXPLICIT SUBMODULE IMPORT")
for mod in ["regime_detection", "strategy_development", "portfolio_management",
            "order_execution", "quant_utilities"]:
    try:
        __import__("quant_framework.modules." + mod)
        print("imported", mod, "OK")
    except Exception as e:
        print("import", mod, "FAILED:", repr(e))
try:
    n, names = tool_names_of(reg)
    print("registry tool count after explicit import:", n)
    for nm in sorted(names):
        print("  tool:", nm)
except Exception:
    traceback.print_exc()

section("AGENT BRIDGE (does QuantAgent see module tools?)")
try:
    from quant_framework.agent.interface import QuantAgent
    a = QuantAgent()
    tool_names = None
    for attr in ("tools", "_tools", "tool_names", "available_tools"):
        v = getattr(a, attr, None)
        if v is not None:
            try:
                tool_names = []
                for t in v:
                    if isinstance(t, dict):
                        tool_names.append(t.get("name", str(t)))
                    elif isinstance(t, str):
                        tool_names.append(t)
                    else:
                        tool_names.append(getattr(t, "name", str(t)))
            except Exception:
                tool_names = [repr(v)]
            print("agent attr used:", attr)
            break
    print("agent tool names:", tool_names)
    keywords = ("regime", "portfolio", "kelly", "hrp", "expectancy", "hurst",
                "order", "utility", "drawdown", "hmm")
    hits = [n for n in (tool_names or []) if any(k in str(n).lower() for k in keywords)]
    print("module-registry tools visible to agent:", hits if hits else "NONE")
except Exception:
    traceback.print_exc()

section("DATA: raw_downloads")
rd = os.path.join(base, "data", "raw_downloads")
if os.path.isdir(rd):
    entries = sorted(os.listdir(rd))
    print("entries:", len(entries))
    for e in entries[:15]:
        print("  -", e)
else:
    print("MISSING data/raw_downloads")

section("DATA: knowledge_base + index.json")
kb = os.path.join(base, "data", "knowledge_base")
per_dir = {}
n = 0
for root, dirs, files in os.walk(kb):
    rel = os.path.relpath(root, kb)
    per_dir[rel] = len(files)
    n += len(files)
print("total kb files:", n)
for k in sorted(per_dir)[:20]:
    print("  ", k, "->", per_dir[k])
idx = os.path.join(kb, "index.json")
if os.path.exists(idx):
    with open(idx, encoding="utf-8") as f:
        d = json.load(f)
    print("index.json type:", type(d).__name__, "len:", len(d))
    if isinstance(d, dict):
        keys = list(d.keys())[:10]
        print("sample keys:", keys)
        for k in keys[:3]:
            v = d[k]
            print("  ", k, "->", (list(v.keys()) if isinstance(v, dict) else str(v)[:150]))
    elif isinstance(d, list) and d:
        print("first item:", str(d[0])[:250])
else:
    print("index.json MISSING")

print("\nPROBE2-DONE")
