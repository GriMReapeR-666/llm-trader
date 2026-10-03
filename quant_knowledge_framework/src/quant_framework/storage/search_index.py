"""
Tokenized Inverted Index for the Knowledge Base
================================================
Replaces the naive O(files x size) substring scan in KnowledgeBase.search
with a tokenized inverted index persisted as search_index.json next to
index.json.

Design:
- Tokens are lowercase alphanumeric sequences (len >= 2).
- Postings map token -> sorted list of integer doc ids; doc ids index into a
  stable `doc_list` of knowledge-base file paths (same string form as
  index.json entries).
- Matching is prefix-based: a query token matches any indexed token that
  starts with it, so "volat" finds "volatility"/"volatilities" without
  shipping a stemmer.
- Multi-token queries AND their tokens together; single-token queries OR
  their prefix matches, each doc scored by the number of matched terms.
- The index syncs itself against the on-disk KB: if the set of .txt/.md
  files (name + mtime + size) is unchanged, loading is a no-op; otherwise
  only new/changed files are (re)tokenized and removed files are dropped.
  build_index() performs the same sync right after rewriting index.json.
"""

import json
import re
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_INDEX_FILENAME = "search_index.json"


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens of length >= 2, in document order."""
    return _TOKEN_RE.findall(text.lower())


class _InvertedIndex:
    """In-memory inverted index with incremental on-disk synchronization."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.doc_list: List[str] = []
        self.doc_meta: Dict[str, Tuple[float, int]] = {}  # path -> (mtime, size)
        self.postings: Dict[str, List[int]] = {}

    # -- persistence ---------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.base_path / _INDEX_FILENAME

    def load(self) -> bool:
        """Load the persisted index. Returns True when a valid index loaded."""
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            doc_list = data["doc_list"]
            doc_meta = {d: tuple(m) for d, m in data["doc_meta"].items()}
            postings = {t: list(ids) for t, ids in data["postings"].items()}
        except Exception:
            return False
        self.doc_list, self.doc_meta, self.postings = doc_list, doc_meta, postings
        return True

    def save(self) -> None:
        data = {
            "doc_list": self.doc_list,
            "doc_meta": {d: list(m) for d, m in self.doc_meta.items()},
            "postings": self.postings,
        }
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    # -- synchronization -----------------------------------------------------

    def _scan_kb(self) -> Dict[str, Tuple[float, int]]:
        """Current KB files (same candidates as build_index: *.txt, *.md)."""
        current: Dict[str, Tuple[float, int]] = {}
        if not self.base_path.is_dir():
            return current
        for topic_dir in self.base_path.iterdir():
            if not topic_dir.is_dir():
                continue
            for f in list(topic_dir.glob("*.txt")) + list(topic_dir.glob("*.md")):
                try:
                    stat = f.stat()
                    current[str(f)] = (stat.st_mtime, stat.st_size)
                except OSError:
                    continue
        return current

    def _tokenize_file(self, path: str) -> List[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return tokenize(f.read())
        except OSError as exc:
            logger.warning("search index: cannot read %s: %s", path, exc)
            return []

    def sync(self, force_rebuild: bool = False) -> Dict[str, int]:
        """Bring the in-memory index in line with the KB directory.

        Returns counts: {"added", "updated", "removed", "unchanged"}.
        """
        current = self._scan_kb()

        if force_rebuild or not self.doc_meta:
            added = len(current)
            self.doc_list, self.doc_meta, self.postings = [], {}, {}
            doc_ids: Dict[str, int] = {}
            for path in sorted(current):
                doc_ids[path] = len(self.doc_list)
                self.doc_list.append(path)
                self.doc_meta[path] = current[path]
            self.postings = {}
            for path, doc_id in doc_ids.items():
                for token in set(self._tokenize_file(path)):
                    self.postings.setdefault(token, []).append(doc_id)
            return {"added": added, "updated": 0, "removed": 0, "unchanged": 0}

        old_paths = set(self.doc_meta)
        new_paths = set(current)
        removed = old_paths - new_paths
        added = new_paths - old_paths
        changed = {p for p in (old_paths & new_paths) if self.doc_meta[p] != current[p]}
        if not (added or removed or changed):
            return {"added": 0, "updated": 0, "removed": 0,
                    "unchanged": len(current)}

        dropped = removed | changed
        old_doc_list = list(self.doc_list)

        # Drop postings entries for removed/changed docs.
        if dropped:
            dropped_ids = {old_doc_list.index(p) for p in dropped}
            for token in list(self.postings):
                kept = [i for i in self.postings[token] if i not in dropped_ids]
                if kept:
                    self.postings[token] = kept
                else:
                    del self.postings[token]

        # Re-compact doc_list/doc_meta and remap surviving OLD int doc ids
        # to their NEW positions.
        survivor_pairs = [(old_id, p) for old_id, p in enumerate(old_doc_list)
                          if p not in dropped]
        remap = {old_id: new_id for new_id, (old_id, _p) in enumerate(survivor_pairs)}
        self.doc_list = [p for _old_id, p in survivor_pairs]
        self.doc_meta = {p: current[p] for p in self.doc_list}
        self.postings = {
            token: sorted(new_id for i in ids
                          if (new_id := remap.get(i)) is not None)
            for token, ids in self.postings.items()
        }

        # Tokenize and append new/changed docs.
        for path in sorted(added | changed):
            doc_id = len(self.doc_list)
            self.doc_list.append(path)
            self.doc_meta[path] = current[path]
            for token in set(self._tokenize_file(path)):
                postings = self.postings.setdefault(token, [])
                import bisect
                bisect.insort(postings, doc_id)

        return {"added": len(added), "updated": len(changed),
                "removed": len(removed),
                "unchanged": len(current) - len(added) - len(changed)}

    # -- querying ------------------------------------------------------------

    def search(self, query: str, limit: Optional[int] = None) -> List[str]:
        """Prefix-match search; multi-token queries AND their terms together.

        Results are ranked by the number of matched query terms, then by doc
        order. `limit=None` (default) returns every match, matching the
        historical substring-scan contract.
        """
        terms = tokenize(query)
        if not terms:
            return []

        matched_docs: Optional[Dict[int, int]] = None  # doc_id -> match count
        for term in terms:
            hits: set = set()
            for token, ids in self.postings.items():
                if token.startswith(term):
                    hits.update(ids)
            if not hits:
                return []
            if matched_docs is None:
                matched_docs = {doc_id: 1 for doc_id in hits}
            else:
                matched_docs = {
                    doc_id: count + 1
                    for doc_id, count in matched_docs.items()
                    if doc_id in hits
                }
                if not matched_docs:
                    return []

        assert matched_docs is not None
        ranked = sorted(matched_docs.items(), key=lambda kv: (-kv[1], kv[0]))
        if limit is not None:
            ranked = ranked[:limit]
        return [self.doc_list[doc_id] for doc_id, _ in ranked]


def build_or_sync_index(base_path: Path, force_rebuild: bool = False) -> _InvertedIndex:
    idx = _InvertedIndex(base_path)
    if not force_rebuild:
        idx.load()
    stats = idx.sync(force_rebuild=force_rebuild)
    if stats["added"] or stats["updated"] or stats["removed"] or force_rebuild:
        idx.save()
    return idx
