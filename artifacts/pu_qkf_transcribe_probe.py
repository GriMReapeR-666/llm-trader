"""PU-0081: verify de-duplicated transcribe stage.

Sandbox: fake raw_downloads with downloader-style transcript.txt + subtitles.
Checks: harvest names, parse-once, no whisper without media, idempotent re-runs,
no re-harvest after classify, stale-subtitle re-parse, whisper path via stub.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from quant_framework.pipeline.orchestrator import KnowledgePipeline
from quant_framework.transcribers.whisper import SubtitleParser, TranscriptionResult

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("- " + str(detail)) if detail else "")


KELLY = "kelly criterion position sizing quarter kelly fractional kelly risk of ruin"
NOISE = "random chatter about nothing in particular"

root = Path(tempfile.mkdtemp(prefix="pu0081_"))
orig = os.getcwd()
pkg = root / "pkg"
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
(rd / "chan2" / "vidC").mkdir(parents=True)

# vidA: downloader-style srt + fresh transcript.txt (transcript written after srt).
# The srt parses to exactly the same text as transcript.txt (what the real
# downloader produces), so a stale re-parse must not change content.
(rd / "chan1" / "vidA" / "vidA.en.srt").write_text(
    "1\n00:00:01,000 --> 00:00:02,000\nkelly criterion position sizing\n\n2\n00:00:02,000 --> 00:00:03,000\nquarter kelly fractional kelly risk of ruin\n",
    encoding="utf-8",
)
(rd / "chan1" / "vidA" / "transcript.txt").write_text(KELLY, encoding="utf-8")
# vidB: srt only (no transcript.txt) -> must be parsed exactly once.
(rd / "chan1" / "vidB" / "vidB.srt").write_text(
    "1\n00:00:01,000 --> 00:00:02,000\nrandom chatter\n",
    encoding="utf-8",
)
# vidC: vtt + fresh transcript.txt.
(rd / "chan2" / "vidC" / "vidC.en.vtt").write_text(
    "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nrandom chatter\n",
    encoding="utf-8",
)
(rd / "chan2" / "vidC" / "transcript.txt").write_text(NOISE, encoding="utf-8")

# Count subtitle parses by wrapping the staticmethods.
calls = {"srt": 0, "vtt": 0}
orig_srt, orig_vtt = SubtitleParser.parse_srt, SubtitleParser.parse_vtt


def srt(fp):
    calls["srt"] += 1
    return orig_srt(fp)


def vtt(fp):
    calls["vtt"] += 1
    return orig_vtt(fp)


SubtitleParser.parse_srt = staticmethod(srt)
SubtitleParser.parse_vtt = staticmethod(vtt)

os.chdir(pkg)
pipe = KnowledgePipeline("config/sources.yaml")

# No media anywhere: get_transcriber must never be called (no whisper model load).
with mock.patch(
    "quant_framework.pipeline.orchestrator.get_transcriber",
    side_effect=AssertionError("whisper loaded with no media present"),
):
    r1 = pipe.run_transcribe_stage()

check("run1 harvested 3", r1.items_processed == 3, r1.metadata)
check(
    "run1 parsed only vidB srt",
    r1.metadata.get("subtitles_parsed") == 1 and calls == {"srt": 1, "vtt": 0},
    calls,
)
check("run1 no media / no whisper", "media_files" not in r1.metadata)
tdir = pkg / "data" / "transcriptions"
names = sorted(p.name for p in tdir.glob("*.txt"))
check("run1 dest names", names == ["chan1_vidA.txt", "chan1_vidB.txt", "chan2_vidC.txt"], names)
check("vidB transcript created next to srt", (rd / "chan1" / "vidB" / "transcript.txt").exists())
check("run1 success, no errors", r1.success and not r1.errors, r1.errors)

r2 = pipe.run_transcribe_stage()
check("run2 no reparse", calls == {"srt": 1, "vtt": 0}, calls)
check(
    "run2 already_collected 3, nothing reprocessed",
    r2.metadata.get("already_collected") == 3 and r2.items_processed == 0,
    r2.metadata,
)

# Classify drains transcriptions into the KB...
rc = pipe._run_classify_stage()
check(
    "classify moved 3, dir drained",
    rc.items_processed == 3 and not any(tdir.rglob("*.txt")),
    {"moved": rc.items_processed, "dups": rc.metadata.get("duplicates_skipped")},
)

r3 = pipe.run_transcribe_stage()
check(
    "run3 no re-harvest after classify",
    r3.items_processed == 0 and r3.metadata.get("already_classified") == 3,
    r3.metadata,
)
check("run3 no reparse", calls == {"srt": 1, "vtt": 0}, calls)

# Case A: stale subtitle whose parsed content is IDENTICAL to the existing
# transcript -> re-parsed once, but harvest must still skip (already classified).
srt_a = rd / "chan1" / "vidA" / "vidA.en.srt"
old = (rd / "chan1" / "vidA" / "transcript.txt").stat().st_mtime
os.utime(srt_a, (old + 100, old + 100))
r4 = pipe.run_transcribe_stage()
check(
    "run4 reparsed stale srt exactly once",
    r4.metadata.get("subtitles_parsed") == 1 and calls["srt"] == 2,
    (r4.metadata.get("subtitles_parsed"), calls),
)
check(
    "run4 no re-harvest (identical content)",
    r4.items_processed == 0 and r4.metadata.get("already_classified") == 3,
    r4.metadata,
)

# Case B: stale subtitle with CHANGED content -> re-parse + refresh in
# transcriptions (classify will later move the updated copy into the KB).
(rd / "chan1" / "vidA" / "vidA.en.srt").write_text(
    "1\n00:00:01,000 --> 00:00:02,000\nkelly updated content\n",
    encoding="utf-8",
)
srt_newer = (rd / "chan1" / "vidA" / "vidA.en.srt").stat().st_mtime
os.utime(
    rd / "chan1" / "vidA" / "transcript.txt",
    (srt_newer - 50, srt_newer - 50),
)
r4b = pipe.run_transcribe_stage()
check(
    "run4b reparse + re-harvest on changed content",
    r4b.metadata.get("subtitles_parsed") == 1
    and calls["srt"] == 3
    and r4b.items_processed == 1
    and "refreshed" not in r4b.metadata  # classify drained dir: fresh copy, not overwrite
    and (tdir / "chan1_vidA.txt").read_text(encoding="utf-8")
    == SubtitleParser.parse_srt(str(rd / "chan1" / "vidA" / "vidA.en.srt")),
    r4b.metadata,
)

# Media path: one mp3 present -> stub transcriber invoked, output chan1_vidD.txt.
(rd / "chan1" / "vidD").mkdir()
(rd / "chan1" / "vidD" / "vidD.mp3").write_bytes(b"\x00" * 16)


def fake_get_transcriber(config):
    class Stub:
        def transcribe(self, input_path, **kw):
            # Absolute path in, absolute path out: exercises the abspath
            # normalization in _flatten_rel_path (pkg cwd is relative).
            return TranscriptionResult(
                source_path=str(Path(input_path).resolve()),
                text="stub whisper output",
                language="en",
                duration=1.0,
                segments=[],
            )

    return Stub()


with mock.patch(
    "quant_framework.pipeline.orchestrator.get_transcriber", side_effect=fake_get_transcriber
):
    r5 = pipe.run_transcribe_stage()
check("run5 media detected", r5.metadata.get("media_files") == 1, r5.metadata)
check(
    "run5 whisper output collected once",
    (tdir / "chan1_vidD.txt").exists()
    and (tdir / "chan1_vidD.txt").read_text(encoding="utf-8") == "stub whisper output"
    and r5.items_processed == 1
    and not r5.errors,
    (r5.metadata, r5.errors),
)
with mock.patch(
    "quant_framework.pipeline.orchestrator.get_transcriber", side_effect=fake_get_transcriber
):
    r5b = pipe.run_transcribe_stage()
check(
    "run5b fresh media output skipped on rerun",
    r5b.metadata.get("media_skipped") == 1 and r5b.items_processed == 0,
    r5b.metadata,
)

print(json.dumps({"parse_calls": calls, "all_pass": all(ok for _, ok in results), "n": len(results)}))
print("VERDICT:", "GREEN" if all(ok for _, ok in results) else "RED")
shutil.rmtree(root, ignore_errors=True)
os.chdir(orig)
print("PROBE-DONE")
