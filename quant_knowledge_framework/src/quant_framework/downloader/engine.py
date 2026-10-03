"""
YouTube Transcription Downloader Engine
========================================
Handles batch downloading of YouTube subtitles/transcriptions only.
No video/audio files are downloaded.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """Metadata for a transcription-only video record."""
    video_id: str
    title: str
    channel: str
    duration: float
    upload_date: str
    description: str
    tags: List[str]
    view_count: int
    subtitle_path: Optional[str] = None
    transcript_path: Optional[str] = None


class YouTubeDownloader:
    """
    Downloads only subtitles/transcriptions from YouTube channels using yt-dlp.
    
    This mode avoids downloading video/audio files to save bandwidth and storage,
    and reduces exposure to platform TOS issues. It still extracts metadata
    and available captions/subtitles for downstream transcription and classification.
    """

    def __init__(self, config):
        self.config = config
        self.download_path = Path(config.get("paths.raw_downloads", "data/raw_downloads"))
        self.download_path.mkdir(parents=True, exist_ok=True)
        self._ensure_ytdlp()

    def _ensure_ytdlp(self):
        try:
            import yt_dlp  # noqa: F401
        except ImportError as e:
            raise ImportError("yt-dlp is required. Install it with: pip install yt-dlp") from e

    def download_channel(
        self,
        channel_url: str,
        limit: Optional[int] = None,
        progress_callback: Optional[Callable] = None
    ) -> List[VideoMetadata]:
        """Download subtitles/transcriptions for all videos in a channel."""
        import yt_dlp

        channel_dir = self.download_path / self._sanitize_filename(channel_url)
        channel_dir.mkdir(exist_ok=True)
        mode = self.config.get("download.mode", "transcript_only")
        subtitle_langs = self.config.get("download.subtitle_langs", ["en"])

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "no_color": True,
            "outtmpl": str(channel_dir / "%(id)s.%(ext)s"),
        }

        if mode == "transcript_only":
            ydl_opts.update({
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": subtitle_langs,
                "convertsubs": "srt",
            })
        else:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": subtitle_langs,
                "convertsubs": "srt",
            })

        if limit:
            ydl_opts["playlistend"] = limit

        videos: List[VideoMetadata] = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=(mode != "transcript_only"))

                entries = info.get("entries") or []
                for entry in entries:
                    if entry is None:
                        continue

                    video_id = entry.get("id", "unknown")
                    title = entry.get("title", "Unknown")

                    video_dir = channel_dir / video_id
                    video_dir.mkdir(exist_ok=True)

                    metadata_path = video_dir / "metadata.json"
                    metadata_path.write_text(
                        json.dumps(
                            {
                                "id": video_id,
                                "title": title,
                                "channel": entry.get("channel", "Unknown"),
                                "duration": float(entry.get("duration") or 0.0),
                                "upload_date": str(entry.get("upload_date") or ""),
                                "description": str(entry.get("description") or ""),
                                "tags": list(entry.get("tags") or []),
                                "view_count": int(entry.get("view_count") or 0),
                                "webpage_url": entry.get("webpage_url") or "",
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )

                    if mode == "transcript_only":
                        try:
                            ydl_opts_local = dict(ydl_opts)
                            ydl_opts_local["skip_download"] = True
                            ydl_opts_local["writesubtitles"] = True
                            ydl_opts_local["writeautomaticsub"] = True
                            ydl_opts_local["subtitleslangs"] = subtitle_langs
                            ydl_opts_local["convertsubs"] = "srt"
                            ydl_opts_local["outtmpl"] = str(video_dir / "%(id)s.%(ext)s")
                            ydl_opts_local["quiet"] = True
                            ydl_opts_local["no_warnings"] = True
                            ydl_opts_local["ignoreerrors"] = True
                            ydl_opts_local["no_color"] = True
                            with yt_dlp.YoutubeDL(ydl_opts_local) as ydl2:
                                ydl2.download([entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"])
                        except Exception as sub_err:
                            logger.warning("Subtitle fetch failed for %s: %s", video_id, sub_err)

                    subtitle_path = None
                    for lang in subtitle_langs:
                        for ext in ["srt", "vtt"]:
                            candidate = video_dir / f"{video_id}.{lang}.{ext}"
                            if candidate.exists():
                                subtitle_path = str(candidate)
                                break
                        if subtitle_path:
                            break
                    if not subtitle_path:
                        for ext in ["srt", "vtt"]:
                            candidate = video_dir / f"{video_id}.{ext}"
                            if candidate.exists():
                                subtitle_path = str(candidate)
                                break

                    transcript_path = video_dir / "transcript.txt"
                    if subtitle_path:
                        try:
                            from quant_framework.transcribers import SubtitleParser
                            text = SubtitleParser.parse_srt(subtitle_path) if subtitle_path.endswith(".srt") else SubtitleParser.parse_vtt(subtitle_path)
                            transcript_path.write_text(text, encoding="utf-8")
                        except Exception as parse_err:
                            logger.warning("Failed to parse subtitle for %s: %s", video_id, parse_err)
                            transcript_path = None

                    videos.append(
                        VideoMetadata(
                            video_id=video_id,
                            title=title,
                            channel=entry.get("channel", "Unknown"),
                            duration=float(entry.get("duration") or 0.0),
                            upload_date=str(entry.get("upload_date") or ""),
                            description=str(entry.get("description") or ""),
                            tags=list(entry.get("tags") or []),
                            view_count=int(entry.get("view_count") or 0),
                            subtitle_path=subtitle_path,
                            transcript_path=str(transcript_path) if transcript_path.exists() else None,
                        )
                    )

                    if progress_callback:
                        progress_callback(len(videos), len(entries), title)

        except Exception as e:
            logger.error("Error downloading channel %s: %s", channel_url, e)

        return videos

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "_")
        return name[:100]
