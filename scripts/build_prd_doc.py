"""Assemble the Source-of-Truth PRD HTML from shell + ordered fragments.

Design: one stdlib-only stitcher; no template engine, no dependency, no config.
Fragments live in docs/_prd_build/fragments/sec-NN.html and are inserted in
numeric order. The sidebar table of contents is derived from the <h2 id>/<h3 id>
anchors found inside the fragments, so adding a section never requires editing
two files.

Usage:
    python scripts/build_prd_doc.py            # build docs/SYSTEM_SOURCE_OF_TRUTH_PRD.html
    python scripts/build_prd_doc.py --check    # validate only (no write), exit 1 on defect
"""
from __future__ import annotations

import argparse
import glob
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(ROOT, "docs", "_prd_build")
FRAG_DIR = os.path.join(BUILD_DIR, "fragments")
OUT_PATH = os.path.join(ROOT, "docs", "SYSTEM_SOURCE_OF_TRUTH_PRD.html")

H2_RE = re.compile(r"<h2\s+id=\"([^\"]+)\"[^>]*>(.*?)</h2>", re.S)
H3_RE = re.compile(r"<h3\s+id=\"([^\"]+)\"[^>]*>(.*?)</h3>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
FORBIDDEN = ("<html", "<!doctype", "<body", "<head", "<script", "<h1")

# Sidebar grouping for the generated TOC: (label, section-number predicate)
GROUPS = [
    ("Orientation", lambda n: n <= 6),
    ("Data Fabric", lambda n: 7 <= n <= 10),
    ("Intelligence", lambda n: 11 <= n <= 14),
    ("Research & Models", lambda n: 15 <= n <= 18),
    ("Persistence & Autonomy", lambda n: 19 <= n <= 20),
    ("Operations & Surfaces", lambda n: 21 <= n <= 22),
    ("Operating the System", lambda n: n >= 23),
]
EXPECTED_SECTIONS = 27


def _text(raw: str) -> str:
    """Plain text of a heading, minus the leader badge (e.g. <span class=num>07</span>).

    Fragments store entities (e.g. &amp;), so normalise before escaping once more —
    otherwise the sidebar renders a literal "&amp;amp;".
    """
    plain = re.sub(r"\s+", " ", TAG_RE.sub("", raw)).strip()
    return html.escape(html.unescape(plain))


def _load(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

def _fragment_paths() -> list[tuple[int, str]]:
    """Ordered (section number, path) pairs for every sec-NN.html fragment."""
    found: list[tuple[int, str]] = []
    for path in glob.glob(os.path.join(FRAG_DIR, "sec-*.html")):
        match = re.match(r"sec-(\d+)", os.path.basename(path))
        if match:
            found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found



def _validate(number: int, body: str) -> list[str]:
    problems: list[str] = []
    low = body.lower()
    for token in FORBIDDEN:
        if token in low:
            problems.append(f"sec-{number:02d}: forbidden tag {token!r}")
    if not H2_RE.search(body):
        problems.append(f"sec-{number:02d}: no <h2 id=...> heading")
    for tag in ("section", "table", "div", "details", "pre"):
        if body.count(f"<{tag}") != body.count(f"</{tag}>"):
            problems.append(f"sec-{number:02d}: unbalanced <{tag}> tags")
    return problems


def _toc(fragments: list[tuple[int, str, str]]) -> str:
    out: list[str] = []
    for label, matches in GROUPS:
        entries: list[str] = []
        for number, _path, body in fragments:
            if not matches(number):
                continue
            h2 = H2_RE.search(body)
            if not h2:
                continue
            entries.append(f'<a class="lvl2" href="#{h2.group(1)}">{_text(h2.group(2))}</a>')
            for h3 in H3_RE.finditer(body):
                entries.append(f'<a class="lvl3" href="#{h3.group(1)}">{_text(h3.group(2))}</a>')
        if entries:
            out.append(f'<div class="toc-group">{label}</div>')
            out.extend(entries)
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Source-of-Truth PRD HTML")
    parser.add_argument("--check", action="store_true", help="validate fragments without writing output")
    args = parser.parse_args()

    head_path = os.path.join(BUILD_DIR, "shell_head.html")
    foot_path = os.path.join(BUILD_DIR, "shell_foot.html")
    for required in (head_path, foot_path):
        if not os.path.exists(required):
            print(f"FATAL: missing shell file {required}", file=sys.stderr)
            return 2

    fragments: list[tuple[int, str, str]] = []
    problems: list[str] = []
    for number, path in _fragment_paths():
        body = _load(path)
        if not body.strip():
            problems.append(f"sec-{number:02d}: fragment is empty")
            continue
        problems.extend(_validate(number, body))
        fragments.append((number, path, body))

    if not fragments:
        print(f"FATAL: no fragments found in {FRAG_DIR}", file=sys.stderr)
        return 2

    present = {n for n, _p, _b in fragments}
    for gap in sorted(set(range(1, EXPECTED_SECTIONS + 1)) - present):
        problems.append(f"sec-{gap:02d}: MISSING fragment (expected section {gap})")

    head = _load(head_path)
    foot = _load(foot_path)
    document = head.replace("<!--TOC-->", _toc(fragments))
    document += "\n".join(body.strip() for _n, _p, body in fragments)
    document += "\n" + foot

    if "<!DOCTYPE html>" not in document or "</html>" not in document:
        problems.append("document shell is incomplete (missing DOCTYPE or </html>)")

    # Anchor integrity is verified against the written file by scripts/audit_prd_doc.py,
    # which reads the artifact with the same encoding a browser would.
    if "<!--TOC-->" in document:
        problems.append("TOC placeholder was not substituted")
    if "&amp;amp;" in document:
        problems.append("double-escaped entity in document (see _text())")

    size = len(document)
    print(f"fragments   : {len(fragments)}  sections(h2): {document.count('<h2 id=')}")
    print(f"toc entries : {document.count('href=\"#')}")
    print(f"size        : {size:,} chars ({size/1024:.0f} KiB)")
    print(f"output      : {OUT_PATH}")

    if problems:
        print("\nDEFECTS:")
        for line in problems:
            print(f"  - {line}")
    if args.check:
        return 1 if problems else 0

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(document)
    print("written OK" + (" (with defects listed above)" if problems else " — clean"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
