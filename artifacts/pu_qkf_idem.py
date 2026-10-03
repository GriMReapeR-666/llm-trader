import os, sys, json, shutil, tempfile

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import tempfile

base = tempfile.mkdtemp(prefix="qkf_idem_")
pkg = os.path.join(base, "pkg")
os.makedirs(os.path.join(pkg, "config"))
os.makedirs(os.path.join(pkg, "data", "transcriptions"))

with open(os.path.join(pkg, "config", "sources.yaml"), "w", encoding="utf-8") as f:
    f.write("paths:\n  transcriptions: data/transcriptions\n  knowledge_base: data/knowledge_base\n")

with open(os.path.join(pkg, "data", "transcriptions", "topic_on.txt"), "w", encoding="utf-8") as f:
    f.write("kelly criterion position sizing quarter kelly fractional kelly risk of ruin")
with open(os.path.join(pkg, "data", "transcriptions", "archive_me.txt"), "w", encoding="utf-8") as f:
    f.write("random chatter about nothing in particular")

os.chdir(pkg)

from quant_framework.pipeline.orchestrator import KnowledgePipeline
from quant_framework.storage import KnowledgeBase

def snapshot(tag):
    kb_dir = "data/knowledge_base"
    tree = {}
    for root, dirs, files in os.walk(kb_dir):
        rel = os.path.relpath(root, kb_dir)
        if rel != "." and files:
            tree[rel] = sorted(files)
    idx = json.load(open(os.path.join(kb_dir, "index.json"), encoding="utf-8"))
    idx_counts = {k: len(v) for k, v in sorted(idx.items()) if v}
    trans = sorted(os.listdir("data/transcriptions"))
    print(f"[{tag}] transcriptions_left={trans}")
    for t, fs in tree.items():
        print(f"[{tag}] kb/{t}: {fs}")
    print(f"[{tag}] index_counts={idx_counts}")

pipe = KnowledgePipeline("config/sources.yaml")

print("=== RUN 1: fresh classify ===")
r1 = pipe._run_classify_stage()
print("items:", r1.items_processed, "success:", r1.success, "errors:", r1.errors)
print("noop?", r1.metadata.get("idempotent_noop", False))
snapshot("run1")

print("\n=== RUN 2: re-run over empty transcriptions dir (the old trap) ===")
r2 = pipe._run_classify_stage()
print("noop?", r2.metadata.get("idempotent_noop"), "| reason:", r2.metadata.get("reason"))
print("index_files:", r2.metadata.get("index_files"), "success:", r2.success)
snapshot("run2")

print("\n=== RUN 3: duplicate content reappears (identical) ===")
with open(os.path.join(pkg, "data", "transcriptions", "topic_on.txt"), "w", encoding="utf-8") as f:
    f.write("kelly criterion position sizing quarter kelly fractional kelly risk of ruin")
r3 = pipe._run_classify_stage()
print("noop?", r3.metadata.get("idempotent_noop", False), "| dup_skipped:", r3.metadata.get("duplicates_skipped"), "| items:", r3.items_processed)
snapshot("run3")

print("\n=== RUN 4: same name, different content, same topic -> suffix rename ===")
with open(os.path.join(pkg, "data", "transcriptions", "topic_on.txt"), "w", encoding="utf-8") as f:
    f.write("risk parity portfolio allocation correlation volatility parity inverse volatility heat cap")
r4 = pipe._run_classify_stage()
print("collisions_renamed:", r4.metadata.get("collisions_renamed"), "| items:", r4.items_processed)
snapshot("run4")

print("\n=== RUN 5: full pipeline stage sequence still coherent (classify + index) ===")
r5 = pipe._run_index_stage()
print("index stage files:", r5.items_processed, "success:", r5.success)

print("\nIDEM-DONE base=" + base)
