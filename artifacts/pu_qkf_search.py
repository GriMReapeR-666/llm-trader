import os, sys, json, time

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from pathlib import Path
from quant_framework.config import Config
from quant_framework.storage import KnowledgeBase
from quant_framework.agent import QuantAgent

kb_dir = Path("data/knowledge_base")
idx_path = kb_dir / "search_index.json"
if idx_path.exists():
    idx_path.unlink()
print("clean slate: removed old search_index.json:", not idx_path.exists())

cfg = Config("config/sources.yaml")
kb = KnowledgeBase(cfg)

# 1) cold build (tokenize all files)
t0 = time.perf_counter()
results_cold = kb.search("volatility")
t_cold = time.perf_counter() - t0
print(f"cold build+search 'volatility': {t_cold*1000:.1f} ms -> {len(results_cold)} files")

# 2) warm search (loaded from disk, synced no-op)
kb2 = KnowledgeBase(Config("config/sources.yaml"))
t0 = time.perf_counter()
results_warm = kb2.search("volatility")
t_warm = time.perf_counter() - t0
print(f"warm search 'volatility': {t_warm*1000:.1f} ms -> {len(results_warm)} files")
print("same results cold/warm:", results_cold == results_warm)

# 3) prefix search
print("prefix 'volat':", len(kb2.search("volat")), "files")
print("prefix 'kelly':", len(kb2.search("kelly")), "files")
print("multi-token 'kelly criterion':", len(kb2.search("kelly criterion")), "files")

# 4) incremental sync: add a file directly, search must find it without build_index
newf = kb_dir / "05_Quant_Utilities" / "zz_probe_added.txt"
newf.write_text("zzuniqueprobe token incremental sync check zzuniqueprobe", encoding="utf-8")
t0 = time.perf_counter()
inc = kb2.search("zzuniqueprobe")
t_inc = time.perf_counter() - t0
print(f"incremental add detected in {t_inc*1000:.1f} ms:", len(inc) == 1)
newf.unlink()
inc2 = kb2.search("zzuniqueprobe")
print("removal detected:", len(inc2) == 0)

# 5) correctness vs old substring scan (10 real queries)
queries = ["volatility", "kelly criterion", "hurst exponent", "hidden markov",
           "risk parity", "walk forward", "slippage", "cointegration", "regime", "the"]
def old_substring_scan(query):
    ql = query.lower()
    out = []
    for topic, files in kb2.index.items():
        for fp in files:
            if not fp.endswith(('.txt', '.md')):
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    if ql in f.read().lower():
                        out.append(fp)
            except Exception:
                pass
    return sorted(out)

print("\ncorrectness vs old scan:")
for q in queries:
    new = sorted(kb2.search(q))
    old = old_substring_scan(q)
    if q == "the":
        # multi-token AND: 'the' matches docs containing the token 'the'
        rel = f"new={len(new)} (AND semantics, old={len(old)} substring)"
        print(f"  '{q}': {rel}")
        continue
    print(f"  '{q}': new={len(new)} old={len(old)} equal={set(new) == set(old)}")

# 6) agent end-to-end (API unchanged)
agent = QuantAgent("config/sources.yaml")
t0 = time.perf_counter()
r = agent.execute_tool("knowledge.search", query="hurst")
t_agent = time.perf_counter() - t0
print(f"\nagent knowledge.search 'hurst': success={r['success']} "
      f"n={len(r['result'])} in {t_agent*1000:.1f} ms")

chain = agent.chain_tools([
    {"tool": "knowledge.search", "params": {"query": "kelly criterion"}, "save_as": "hits"},
])
n_hits = len(chain["pipeline_results"][0]["result"]["result"])
print("chained knowledge.search hits:", n_hits)

print("\nSEARCH-DONE")
