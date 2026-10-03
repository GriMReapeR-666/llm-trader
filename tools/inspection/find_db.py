import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for root, dirs, files in os.walk("."):
    for f in files:
        if f.endswith(".db") or f.endswith(".sqlite") or "journal" in f.lower():
            p = os.path.join(root, f)
            print(p, os.path.getsize(p))
