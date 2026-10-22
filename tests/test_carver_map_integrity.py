"""Documentation truth for docs/CARVER_FRAMEWORK_MAP.md.

Contract element 20 ("every published number is generated from the tree, not
typed by hand; a stale count is a bug") and element 21 (portable artifacts).
The map cites repo files and line numbers; if those move, the map is wrong,
and this test is how it says so.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "CARVER_FRAMEWORK_MAP.md"

PY_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.py)(?::(\d+))?`")
BARE_LINE = re.compile(r"`:(\d+)`")
TOKEN = re.compile(r"`([^`]+)`")
PATH_TOKEN = re.compile(r"([A-Za-z0-9_./-]+\.py)(?::(\d+))?$")
LINE_TOKEN = re.compile(r":(\d+)$")


def _doc_text():
    assert DOC.exists(), f"missing {DOC}"
    return DOC.read_text(encoding="utf-8")


def _citations():
    """Yield (doc_line, path, line_number) for every citation in the map.

    The map writes `path:12`/`:34`/`:56`, so a bare `:34` refers to the
    path named most recently on that line.
    """
    current = None
    for lineno, line in enumerate(_doc_text().splitlines(), 1):
        for token in TOKEN.findall(line):
            path_match = PATH_TOKEN.fullmatch(token)
            if path_match:
                current = path_match.group(1)
                if path_match.group(2):
                    yield lineno, current, int(path_match.group(2))
                continue
            line_match = LINE_TOKEN.fullmatch(token)
            if line_match and current:
                yield lineno, current, int(line_match.group(1))


def test_every_cited_path_exists():
    missing = sorted({path for _, path, _ in _citations()} |
                     {path for path, _ in PY_PATH.findall(_doc_text())})
    missing = [p for p in missing if not (REPO / p).exists()]
    assert not missing, f"map cites files that do not exist: {missing}"


def test_every_cited_line_is_inside_the_file():
    failures = []
    for lineno, path, number in _citations():
        target = REPO / path
        if not target.exists():
            continue
        total = len(target.read_text(encoding="utf-8", errors="ignore").splitlines())
        if number > total:
            failures.append(f"doc line {lineno}: {path}:{number} is past EOF ({total} lines)")
    assert not failures, failures


def test_cited_lines_contain_what_the_map_claims():
    """A citation that points at the wrong line is a stale claim, not a typo."""
    failures = []
    by_doc_line = {}
    for doc_line, path, number in _citations():
        by_doc_line.setdefault(doc_line, []).append((path, number))

    for doc_line, citations in by_doc_line.items():
        identifiers = _identifiers_on_doc_line(doc_line)
        if not identifiers:
            continue
        cited_text = []
        for path, number in citations:
            target = REPO / path
            if not target.exists():
                continue
            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
            if number <= len(lines):
                cited_text.append(lines[number - 1])
        if not cited_text:
            continue
        joined = "\n".join(cited_text)
        for ident in sorted(identifiers):
            if ident not in joined:
                failures.append(
                    f"doc line {doc_line}: {ident} is not at the cited line(s) "
                    f"{[(p, n) for p, n in citations]}"
                )
    assert not failures, failures


def _identifiers_on_doc_line(doc_line: int) -> set:
    """Backticked snake_case names on a doc line (not file paths, not prose)."""
    line = _doc_text().splitlines()[doc_line - 1]
    found = set()
    for token in TOKEN.findall(line):
        if token.endswith(".py") or token.startswith(":"):
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]*_[a-z0-9_]+", token):
            continue
        found.add(token)
    return found


def test_map_stays_portable():
    text = _doc_text()
    assert "C:\\" not in text
    assert "C:/" not in text
    assert "/Users/" not in text
    assert "/home/" not in text


def test_map_names_the_modules_it_describes():
    text = _doc_text()
    assert "core/forecast_sizing.py" in text
    assert "research/fluey_fitness.py" in text
