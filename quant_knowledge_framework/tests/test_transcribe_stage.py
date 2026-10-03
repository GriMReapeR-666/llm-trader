"""
Regression tests for the transcribe-stage de-duplication.

The original stage re-parsed every .srt/.vtt under raw_downloads on each run,
overwrote the downloader's transcript.txt with identical content, loaded a
whisper model even in transcript_only mode, and emitted numbered
transcript_NNNN.txt copies on every run.  The stage now harvests the
downloader's transcript.txt, parses only missing/stale subtitles, and runs
whisper only when real audio/video files exist.
"""

import os
from pathlib import Path
from unittest import mock

import pytest

from quant_framework.pipeline.orchestrator import KnowledgePipeline
from quant_framework.transcribers.whisper import SubtitleParser, TranscriptionResult


KELLY_TEXT = "kelly criterion position sizing quarter kelly fractional kelly risk of ruin"
NOISE_TEXT = "random chatter about nothing in particular"


@pytest.fixture()
def sandbox(tmp_path):
    """Sandbox with a downloader-shaped raw_downloads tree."""
    pkg = tmp_path / "pkg"
    (pkg / "config").mkdir(parents=True)
    (pkg / "config" / "sources.yaml").write_text(
        "paths:\n"
        "  raw_downloads: data/raw_downloads\n"
        "  transcriptions: data/transcriptions\n"
        "  knowledge_base: data/knowledge_base\n",
        encoding="utf-8",
    )

    rd = pkg / "data" / "raw_downloads"
    (rd / "chan1" / "vidA").mkdir(parents=True)
    (rd / "chan1" / "vidB").mkdir(parents=True)
    # Downloader-style output: subtitle + parsed transcript.txt next to it.
    (rd / "chan1" / "vidA" / "vidA.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nkelly criterion position sizing\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nquarter kelly fractional kelly risk of ruin\n",
        encoding="utf-8",
    )
    (rd / "chan1" / "vidA" / "transcript.txt").write_text(KELLY_TEXT, encoding="utf-8")
    # Subtitle only: no transcript.txt yet.
    (rd / "chan1" / "vidB" / "vidB.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nrandom chatter\n",
        encoding="utf-8",
    )

    prev = os.getcwd()
    os.chdir(pkg)
    yield pkg
    os.chdir(prev)


class TestHarvestDownloaderTranscripts:
    def test_harvests_transcripts_with_stable_names(self, sandbox):
        result = KnowledgePipeline("config/sources.yaml").run_transcribe_stage()
        tdir = sandbox / "data" / "transcriptions"
        names = sorted(p.name for p in tdir.glob("*.txt"))
        # <channel>_<video_id>.txt, not the generic transcript.txt and not
        # ambiguous numbered copies.
        assert names == ["chan1_vidA.txt", "chan1_vidB.txt"]
        assert (tdir / "chan1_vidA.txt").read_text(encoding="utf-8") == KELLY_TEXT
        assert result.items_processed == 2
        assert result.success and not result.errors

    def test_subtitle_without_transcript_is_parsed_next_to_it(self, sandbox):
        KnowledgePipeline("config/sources.yaml").run_transcribe_stage()
        transcript = sandbox / "data" / "raw_downloads" / "chan1" / "vidB" / "transcript.txt"
        assert transcript.exists()
        assert transcript.read_text(encoding="utf-8") == "random chatter"

    def test_fresh_transcript_is_not_reparsed(self, sandbox):
        calls = []
        orig = SubtitleParser.parse_srt

        def counting(fp):
            calls.append(fp)
            return orig(fp)

        with mock.patch.object(SubtitleParser, "parse_srt", staticmethod(counting)):
            KnowledgePipeline("config/sources.yaml").run_transcribe_stage()
        # vidA's srt is older than its transcript.txt: skipped entirely.
        assert len(calls) == 1
        assert calls[0].endswith("vidB.srt")

    def test_stale_subtitle_reparsed_once(self, sandbox):
        pkg = sandbox
        srt = pkg / "data" / "raw_downloads" / "chan1" / "vidA" / "vidA.en.srt"
        transcript = pkg / "data" / "raw_downloads" / "chan1" / "vidA" / "transcript.txt"
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe.run_transcribe_stage()  # warm-up: establishes vidB's transcript
        # Make the subtitle newer than the transcript (content unchanged).
        old = transcript.stat().st_mtime
        os.utime(srt, (old + 100, old + 100))

        result = pipe.run_transcribe_stage()
        assert result.metadata.get("subtitles_parsed") == 1
        # Content identical to the fresh transcript -> harvest still skips.
        assert result.metadata.get("already_collected") == 2

    def test_changed_subtitle_content_is_reparsed_and_reharvested(self, sandbox):
        pkg = sandbox
        srt = pkg / "data" / "raw_downloads" / "chan1" / "vidA" / "vidA.en.srt"
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe.run_transcribe_stage()  # warm-up: establishes vidB's transcript
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nkelly updated content\n",
            encoding="utf-8",
        )
        result = pipe.run_transcribe_stage()
        assert result.metadata.get("subtitles_parsed") == 1
        assert result.items_processed == 1
        out = sandbox / "data" / "transcriptions" / "chan1_vidA.txt"
        assert out.read_text(encoding="utf-8") == "kelly updated content"

    def test_missing_raw_downloads_is_safe_noop(self, sandbox):
        import shutil

        shutil.rmtree(sandbox / "data" / "raw_downloads")
        result = KnowledgePipeline("config/sources.yaml").run_transcribe_stage()
        assert result.success
        assert result.items_processed == 0
        assert (sandbox / "data" / "transcriptions").exists()


class TestIdempotence:
    def test_rerun_skips_already_collected(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        first = pipe.run_transcribe_stage()
        assert first.items_processed == 2

        second = pipe.run_transcribe_stage()
        assert second.items_processed == 0
        assert second.metadata.get("already_collected") == 2
        assert second.metadata.get("subtitles_parsed", 0) == 0

    def test_rerun_after_classify_does_not_reharvest(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe.run_transcribe_stage()
        rc = pipe._run_classify_stage()
        assert rc.items_processed == 2

        third = pipe.run_transcribe_stage()
        assert third.items_processed == 0
        assert third.metadata.get("already_classified") == 2
        # The transcriptions dir stays empty: no duplicate feeding into the
        # next classify run.
        assert not any((sandbox / "data" / "transcriptions").rglob("*.txt"))

    def test_full_pipeline_stays_stable_across_rebuilds(self, sandbox):
        pipe = KnowledgePipeline("config/sources.yaml")
        first = pipe.run_full_pipeline(channels=[])
        assert first["classify"].items_processed == 2

        second = pipe.run_full_pipeline(channels=[])
        assert second["transcribe"].items_processed == 0
        assert second["classify"].metadata.get("idempotent_noop") is True

        kb = sandbox / "data" / "knowledge_base"
        all_txt = sorted(p.name for p in kb.rglob("*.txt"))
        assert all_txt == ["archive_me.txt", "topic_on.txt"] or len(all_txt) == 2


class TestWhisperOnlyForMedia:
    def test_no_whisper_without_media(self, sandbox):
        with mock.patch(
            "quant_framework.pipeline.orchestrator.get_transcriber",
            side_effect=AssertionError("whisper must not load without media"),
        ):
            result = KnowledgePipeline("config/sources.yaml").run_transcribe_stage()
        assert "media_files" not in result.metadata

    @staticmethod
    def _add_media(pkg: Path) -> Path:
        media = pkg / "data" / "raw_downloads" / "chan1" / "vidD" / "vidD.mp3"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"\x00" * 16)
        return media

    def test_media_transcribed_with_stable_name(self, sandbox):
        media = self._add_media(sandbox)

        class Stub:
            def transcribe(self, input_path, **kw):
                return TranscriptionResult(
                    source_path=str(Path(input_path).resolve()),
                    text="stub whisper output",
                    language="en",
                    duration=1.0,
                    segments=[],
                )

        with mock.patch(
            "quant_framework.pipeline.orchestrator.get_transcriber", return_value=Stub()
        ):
            result = KnowledgePipeline("config/sources.yaml").run_transcribe_stage()

        assert result.metadata.get("media_files") == 1
        out = sandbox / "data" / "transcriptions" / "chan1_vidD.txt"
        assert out.read_text(encoding="utf-8") == "stub whisper output"
        assert result.items_processed == 3  # 2 transcripts + 1 media
        assert media.exists()

    def test_fresh_media_output_skipped_on_rerun(self, sandbox):
        self._add_media(sandbox)

        class Stub:
            def transcribe(self, input_path, **kw):
                return TranscriptionResult(
                    source_path=str(Path(input_path).resolve()),
                    text="stub whisper output",
                    language="en",
                    duration=1.0,
                    segments=[],
                )

        with mock.patch(
            "quant_framework.pipeline.orchestrator.get_transcriber", return_value=Stub()
        ) as factory:
            pipe = KnowledgePipeline("config/sources.yaml")
            first = pipe.run_transcribe_stage()
            second = pipe.run_transcribe_stage()

        assert first.items_processed == 3
        assert second.metadata.get("media_skipped") == 1
        assert second.items_processed == 0
        assert factory.call_count == 2  # created, but never invoked for media
