import os, sys, json
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
QKF = os.getcwd()
sys.path.insert(0, os.path.join(QKF, "src"))
from pathlib import Path

KB = Path(QKF) / "data" / "knowledge_base"
sidx = json.load(open(KB / "search_index.json", encoding="utf-8"))
doc_list = set(sidx["doc_list"])
disk = {str(p) for p in KB.rglob("*") if p.is_file() and p.suffix in (".txt", ".md")}

only_index = sorted(doc_list - disk)
only_disk = sorted(disk - doc_list)
print("doc_list:", len(doc_list), "disk:", len(disk))
print("in index but not on disk:", len(only_index))
for p in only_index[:10]:
    print("  idx-only:", p)
print("on disk but not in index:", len(only_disk))
for p in only_disk[:10]:
    print("  disk-only:", p)
print("DIFF-DONE")
