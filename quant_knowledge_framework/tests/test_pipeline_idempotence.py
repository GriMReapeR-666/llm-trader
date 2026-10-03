"""
Regression tests for the classify-stage idempotence trap.

The original stage renamed transcriptions into the knowledge base and never
refreshed the index, so re-running `build` re-classified an empty directory,
left a stale index.json, and on Windows a same-name collision raised
FileExistsError from Path.rename.
"""

import json
import shutil

import pytest

from quant_framework.pipeline.orchestrator import KnowledgePipeline


KELLY_TEXT = "kelly criterion position sizing quarter kelly fractional kelly risk of ruin"
NOISE_TEXT = "random chatter about nothing in particular"


@pytest.fixture()
def sandbox(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "config").mkdir(parents=True)
    trans = pkg / "data" / "transcriptions"
    trans.mkdir(parents=True)

    (pkg / "config" / "sources.yaml").write_text(
        "paths:\n"
        "  transcriptions: data/transcriptions\n"
        "  knowledge_base: data/knowledge_base\n",
        encoding="utf-8",
    )
    (trans / "topic_on.txt").write_text(KELLY_TEXT, encoding="utf-8")
    (trans / "archive_me.txt").write_text(NOISE_TEXT, encoding="utf-8")

    old_cwd = tmp_path.cwd
    import os
    prev = os.getcwd()
    os.chdir(pkg)
    yield pkg
    os.chdir(prev)


def _kb_files(pkg):
    kb = pkg / "data" / "knowledge_base"
    metadata_names = {"index.json", "search_index.json"}
    return sorted(
        str(p.relative_to(kb)).replace("\\", "/")
        for p in kb.rglob("*")
        if p.is_file() and p.name not in metadata_names
    )


def _index(pkg):
    idx = json.loads((pkg / "data" / "knowledge_base" / "index.json").read_text(encoding="utf-8"))
    return {k: len(v) for k, v in idx.items() if v}


class TestClassifyIdempotence:
    def test_first_run_moves_files_and_builds_index(self, sandbox):
        result = KnowledgePipeline("config/sources.yaml")._run_classify_stage()
        assert result.success is True
        assert result.items_processed == 2
        assert "03_Portfolio_Management/topic_on.txt" in _kb_files(sandbox)
        assert "99_Archive_Unsorted/archive_me.txt" in _kb_files(sandbox)
        assert _index(sandbox) == {"03_Portfolio_Management": 1, "99_Archive_Unsorted": 1}
        # Transcriptions drained: nothing left behind.
        assert not any((sandbox / "data" / "transcriptions").rglob("*.txt"))

    def test_rerun_on_empty_dir_is_flagged_noop_with_fresh_index(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe._run_classify_stage()
        before_files = _kb_files(sandbox)
        before_index = _index(sandbox)

        rerun = pipe._run_classify_stage()
        assert rerun.success is True
        assert rerun.metadata.get("idempotent_noop") is True
        assert rerun.metadata.get("index_files") == 2
        assert _kb_files(sandbox) == before_files
        assert _index(sandbox) == before_index

    def test_rerun_when_transcriptions_dir_missing_is_noop(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe._run_classify_stage()
        shutil.rmtree(sandbox / "data" / "transcriptions")
        before_files = _kb_files(sandbox)

        rerun = pipe._run_classify_stage()
        assert rerun.success is True
        assert rerun.metadata.get("idempotent_noop") is True
        assert _kb_files(sandbox) == before_files

    def test_identical_duplicate_is_skipped(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe._run_classify_stage()
        # Same file name and same content reappears in transcriptions.
        (sandbox / "data" / "transcriptions" / "topic_on.txt").write_text(KELLY_TEXT, encoding="utf-8")

        result = pipe._run_classify_stage()
        assert result.metadata.get("duplicates_skipped") == 1
        # Knowledge base unchanged: exactly the original two files, no suffixes.
        assert _kb_files(sandbox) == [
            "03_Portfolio_Management/topic_on.txt",
            "99_Archive_Unsorted/archive_me.txt",
        ]
        assert _index(sandbox) == {"03_Portfolio_Management": 1, "99_Archive_Unsorted": 1}

    def test_same_name_different_content_gets_suffix(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe._run_classify_stage()
        # Same file name, different (but still portfolio-flavored) content.
        (sandbox / "data" / "transcriptions" / "topic_on.txt").write_text(
            "risk parity portfolio allocation correlation volatility parity inverse volatility heat cap",
            encoding="utf-8",
        )

        result = pipe._run_classify_stage()
        assert result.metadata.get("collisions_renamed") == 1
        assert "03_Portfolio_Management/topic_on.txt" in _kb_files(sandbox)
        assert "03_Portfolio_Management/topic_on_1.txt" in _kb_files(sandbox)
        assert _index(sandbox) == {"03_Portfolio_Management": 2, "99_Archive_Unsorted": 1}

    def test_index_is_refreshed_by_active_stage_too(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        result = pipe._run_classify_stage()
        assert "idempotent_noop" not in result.metadata  # active run, not a no-op
        # index.json exists and matches disk without running the index stage.
        assert _index(sandbox) == {"03_Portfolio_Management": 1, "99_Archive_Unsorted": 1}
