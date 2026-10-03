"""One-off audit of the built PRD: anchor integrity, structure, encoding.

Design: stdlib only, read-only. Run after scripts/build_prd_doc.py.
    python scripts/audit_prd_doc.py
"""
from __future__ import annotations

import collections
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "SYSTEM_SOURCE_OF_TRUTH_PRD.html")


def main() -> int:
    if not os.path.exists(DOC):
        print("FATAL: build the document first (python scripts/build_prd_doc.py)")
        return 2
    try:  # keep §-symbols printable on cp1252 consoles
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raw = io.open(DOC, encoding="utf-8").read()
    # Strip script/style bodies so JavaScript selectors are not mistaken for anchors.
    prose = re.sub(r"<script.*?</script>", "", raw, flags=re.S | re.I)
    prose = re.sub(r"<style.*?</style>", "", prose, flags=re.S | re.I)

    ids = re.findall(r'id="([^"]+)"', prose)
    targets = re.findall(r'href="#([^"]+)"', prose)
    dupes = sorted(i for i, n in collections.Counter(ids).items() if n > 1)
    broken = sorted(set(targets) - set(ids))

    headings = re.findall(r"<h2 id=\"[^\"]+\"[^>]*>(.*?)</h2>", prose, re.S)
    headings = [re.sub(r"<[^>]+>", "", h).strip() for h in headings]

    print(f"file            : {DOC}")
    print(f"size            : {len(raw):,} chars ({len(raw)/1024:.0f} KiB)")
    print(f"doctype/close   : {raw.startswith('<!DOCTYPE html>')} / {raw.rstrip().endswith('</html>')}")
    print(f"mojibake (Â€)   : {raw.count(chr(0xE2) + chr(0x80))}")
    print(f"ids / anchors   : {len(ids)} / {len(targets)}")
    print(f"duplicate ids   : {dupes or 'none'}")
    print(f"broken anchors  : {broken or 'none'}")
    print(f"tables          : {prose.count('<table>')}")
    print(f"h1 outside head : {prose.count('<h1')}")
    print("\nsections:")
    for h in headings:
        print(f"  {h}")
    return 1 if (dupes or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
