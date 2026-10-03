"""Download-stage sandbox probe (no network).

Stubs yt_dlp inside the probe process, then drives YouTubeDownloader
end-to-end in a sandbox cwd: metadata.json + transcript.txt layout,
VideoMetadata plumbing, transcript_only mode flags, and rerun stability.
Prints VERDICT: GREEN/RED and all_pass/n for smoke_test.py parsing.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("- " + str(detail)) if detail else "")


KELLY_SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nkelly criterion\n\n"
    "2\n00:00:02,000 --> 00:00:03,000\nposition sizing\n"
)

# ---------------------------------------------------------------------------
# Stub yt_dlp BEFORE quant_framework.downloader imports it. The engine calls
# extract_info(channel_url, download=...) and, per video, a second YoutubeDL
# instance whose .download() must materialize <video_id>.en.srt in video_dir.
# ---------------------------------------------------------------------------
calls = {"extract": [], "sub_downloads": []}


class StubYoutubeDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        calls["extract"].append({"url": url, "download": download})
        return {
            "entries": [
                {
                    "id": "vidA",
                    "title": "Kelly Criterion Talk",
                    "channel": "Quant Channel",
                    "duration": 331.0,
                    "upload_date": "20201119",
                    "description": "about kelly",
                    "tags": ["quant"],
                    "view_count": 2022,
                    "webpage_url": "https://www.youtube.com/watch?v=vidA",
                },
                {
                    "id": "vidB",
                    "title": "Volatility Basics",
                    "channel": "Quant Channel",
                    "duration": 60.0,
                    "upload_date": "20210101",
                    "description": "about vol",
                    "tags": [],
                    "view_count": 10,
                    "webpage_url": "https://www.youtube.com/watch?v=vidB",
                },
                None,  # engine must tolerate broken entries
            ]
        }

    def download(self, urls):
        video_id = urls[0].split("v=")[-1]
        calls["sub_downloads"].append(urls[0])
        # The engine's outtmpl points at the per-video dir; find it from the
        # caller's outtmpl opts (engine sets outtmpl to <video_dir>/%(id)s.%(ext)s).
        outtmpl = self.opts.get("outtmpl", "")
        video_dir = Path(outtmpl).parent
        (video_dir / f"{video_id}.en.srt").write_text(KELLY_SRT, encoding="utf-8")
        return 0


stub_ytdlp = ModuleType("yt_dlp")
stub_ytdlp.YoutubeDL = StubYoutubeDL
sys.modules["yt_dlp"] = stub_ytdlp

from quant_framework.downloader.engine import YouTubeDownloader, VideoMetadata  # noqa: E402

# Sandbox cwd with a config object handing out engine defaults.
root = Path(tempfile.mkdtemp(prefix="pu_download_"))
orig = os.getcwd()


class Cfg(SimpleNamespace):
    def get(self, key, default=None):
        return {
            "paths.raw_downloads": "data/raw_downloads",
            "download.mode": "transcript_only",
            "download.subtitle_langs": ["en"],
        }.get(key, default)


os.chdir(root)
try:
    downloader = YouTubeDownloader(Cfg())
    channel_url = "https://www.youtube.com/@QuantChannel"

    videos = downloader.download_channel(channel_url)

    channel_dir = root / "data" / "raw_downloads" / "https___www.youtube.com_@QuantChannel"
    check("channel dir created", channel_dir.is_dir())
    check("two videos returned", len(videos) == 2, [v.video_id for v in videos])
    check("broken entry skipped (no junk dirs)",
          sorted(p.name for p in channel_dir.iterdir()) == ["vidA", "vidB"],
          sorted(p.name for p in channel_dir.iterdir()))

    meta_a = json.loads((channel_dir / "vidA" / "metadata.json").read_text(encoding="utf-8"))
    check("metadata.json written with id/title/channel",
          meta_a["id"] == "vidA" and meta_a["title"] == "Kelly Criterion Talk"
          and meta_a["channel"] == "Quant Channel",
          meta_a["title"])

    transcript = channel_dir / "vidA" / "transcript.txt"
    check("transcript.txt parsed from srt",
          transcript.exists()
          and transcript.read_text(encoding="utf-8")
          == " ".join(("kelly criterion", "position sizing")),
          transcript.read_text(encoding="utf-8") if transcript.exists() else "<missing>")

    from quant_framework.transcribers import SubtitleParser
    check("transcript matches SubtitleParser output",
          transcript.read_text(encoding="utf-8") == SubtitleParser.parse_srt(str(channel_dir / "vidA" / "vidA.en.srt")))

    check("VideoMetadata plumbing",
          all(isinstance(v, VideoMetadata) for v in videos)
          and videos[0].subtitle_path is not None and videos[0].transcript_path is not None
          and videos[0].duration == 331.0)

    check("transcript_only mode skips download",
          calls["extract"] and all(c["download"] is False for c in calls["extract"]),
          calls["extract"])
    check("subtitle fetch issued per video",
          sorted(u.split("v=")[-1] for u in calls["sub_downloads"]) == ["vidA", "vidB"],
          calls["sub_downloads"])

    # Rerun stability: same result, transcript content unchanged.
    before = transcript.read_text(encoding="utf-8")
    videos_rerun = downloader.download_channel(channel_url)
    check("rerun returns same videos",
          [v.video_id for v in videos_rerun] == [v.video_id for v in videos])
    check("rerun keeps transcript content stable",
          transcript.read_text(encoding="utf-8") == before)

    # Non-transcript_only mode must ask yt-dlp to download.
    cfg_audio = Cfg()
    cfg_audio_overrides = {"download.mode": "audio"}
    class CfgAudio(Cfg):
        def get(self, key, default=None):
            if key in cfg_audio_overrides:
                return cfg_audio_overrides[key]
            return super().get(key, default)

    YouTubeDownloader(CfgAudio()).download_channel(channel_url)
    check("audio mode requests download=True",
          calls["extract"][-1]["download"] is True, calls["extract"][-1])

    print(json.dumps({
        "sub_downloads": len(calls["sub_downloads"]),
        "all_pass": all(ok for _, ok in results),
        "n": len(results),
    }))
    print("VERDICT:", "GREEN" if all(ok for _, ok in results) else "RED")
finally:
    os.chdir(orig)
    shutil.rmtree(root, ignore_errors=True)
print("PROBE-DONE")
