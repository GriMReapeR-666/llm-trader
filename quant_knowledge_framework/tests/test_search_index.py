"""
Tests for the tokenized inverted search index that replaces the naive
substring scan in KnowledgeBase.search. Covers tokenization, prefix and
AND matching, persistence round-trips, incremental synchronization
(including the doc-id remap compaction), and KnowledgeBase integration.
"""

import json

import pytest

from quant_framework.storage.search_index import (
    _InvertedIndex,
    build_or_sync_index,
    tokenize,
)


class FakeConfig:
    def __init__(self, kb_path):
        self._kb = str(kb_path)

    def get(self, key, default=None):
        if key == "paths.knowledge_base":
            return self._kb
        return default


@pytest.fixture()
def kb_dir(tmp_path):
    d = tmp_path / "kb"
    (d / "01_Topic").mkdir(parents=True)
    (d / "01_Topic" / "a.txt").write_text("volatility clustering and risk parity allocation", encoding="utf-8")
    (d / "01_Topic" / "b.txt").write_text("kelly criterion position sizing with volatility targeting", encoding="utf-8")
    (d / "01_Topic" / "c.txt").write_text("hurst exponent regime persistence", encoding="utf-8")
    return d


class TestTokenize:
    def test_basic(self):
        assert tokenize("Kelly Criterion: R-multiple!") == ["kelly", "criterion", "multiple"]

    def test_drops_single_chars_and_symbols(self):
        assert tokenize("a I 22 b&c") == ["22"]

    def test_empty(self):
        assert tokenize("") == []


class TestMatching:
    def test_prefix_match_real(self, kb_dir):
        idx = build_or_sync_index(kb_dir)
        hits = idx.search("volat")
        assert sorted(hits) == [str(kb_dir / "01_Topic" / "a.txt"), str(kb_dir / "01_Topic" / "b.txt")]

    def test_multi_token_and(self, kb_dir):
        idx = build_or_sync_index(kb_dir)
        # "kelly criterion" only appears together in b.txt
        assert idx.search("kelly criterion") == [str(kb_dir / "01_Topic" / "b.txt")]
        # AND is order-independent
        assert idx.search("criterion kelly") == idx.search("kelly criterion")

    def test_unknown_term_no_results(self, kb_dir):
        idx = build_or_sync_index(kb_dir)
        assert idx.search("zzznotaterm") == []
        assert idx.search("kelly zzznotaterm") == []  # AND fails

    def test_empty_query(self, kb_dir):
        idx = build_or_sync_index(kb_dir)
        assert idx.search("") == []
        assert idx.search("!!!") == []

    def test_limit(self, kb_dir):
        idx = build_or_sync_index(kb_dir)
        all_hits = idx.search("volat")
        limited = idx.search("volat", limit=1)
        assert len(limited) == 1 and limited[0] in all_hits


class TestPersistence:
    def test_round_trip_same_results(self, kb_dir):
        first = build_or_sync_index(kb_dir)
        assert (kb_dir / "search_index.json").exists()
        second = build_or_sync_index(kb_dir)  # loads from disk
        assert second.doc_list == first.doc_list
        assert second.search("volat") == first.search("volat")

    def test_unchanged_sync_is_noop(self, kb_dir):
        idx = build_or_sync_index(kb_dir)
        stats = idx.sync()
        assert stats == {"added": 0, "updated": 0, "removed": 0, "unchanged": 3}


class TestIncrementalSync:
    def test_add_change_remove_detected(self, kb_dir):
        idx = build_or_sync_index(kb_dir)

        # Add
        new_file = kb_dir / "01_Topic" / "d.txt"
        new_file.write_text("zzunique zzunique incremental", encoding="utf-8")
        stats = idx.sync()
        assert stats["added"] == 1
        assert idx.search("zzunique") == [str(new_file)]

        # Change (same name, new content loses the probe term)
        new_file.write_text("different topic entirely", encoding="utf-8")
        stats = idx.sync()
        assert stats["updated"] == 1
        assert idx.search("zzunique") == []
        assert idx.search("different") == [str(new_file)]

        # Remove
        new_file.unlink()
        stats = idx.sync()
        assert stats["removed"] == 1
        assert idx.search("different") == []

    def test_removal_compaction_keeps_valid_paths(self, kb_dir):
        """Exercise the doc-id remap: remove a middle doc, results must be real files."""
        idx = build_or_sync_index(kb_dir)
        removed = kb_dir / "01_Topic" / "b.txt"
        removed.unlink()
        idx.sync()
        for query in ("volat", "kelly", "criterion", "hurst", "regime"):
            for path in idx.search(query):
                assert __import__("pathlib").Path(path).exists(), path

    def test_sync_via_persisted_state(self, kb_dir):
        first = build_or_sync_index(kb_dir)
        (kb_dir / "01_Topic" / "e.txt").write_text("second generation probe", encoding="utf-8")
        second = build_or_sync_index(kb_dir)  # loads persisted, then syncs
        assert second.search("generation") == [str(kb_dir / "01_Topic" / "e.txt")]
        assert second.search("volat") == first.search("volat")


class TestKnowledgeBaseIntegration:
    def test_search_signature_and_results(self, kb_dir, tmp_path):
        from quant_framework.storage import KnowledgeBase

        kb = KnowledgeBase(FakeConfig(kb_dir))
        results = kb.search("volat")
        assert isinstance(results, list)
        assert all(isinstance(r, str) for r in results)
        assert any(r.endswith("b.txt") for r in results)

    def test_build_index_refreshes_search_index(self, kb_dir):
        from quant_framework.storage import KnowledgeBase

        kb = KnowledgeBase(FakeConfig(kb_dir))
        assert kb.search("freshprobe") == []
        (kb_dir / "01_Topic" / "f.txt").write_text("freshprobe content here", encoding="utf-8")
        kb.build_index()
        hits = kb.search("freshprobe")
        assert len(hits) == 1 and hits[0].endswith("f.txt")

    def test_corrupt_index_file_recovers(self, kb_dir):
        (kb_dir / "search_index.json").write_text("{not json", encoding="utf-8")
        idx = build_or_sync_index(kb_dir)
        assert len(idx.search("hurst")) == 1
