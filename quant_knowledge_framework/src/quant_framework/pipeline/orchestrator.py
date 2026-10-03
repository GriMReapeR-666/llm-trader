"""
Knowledge Pipeline Orchestrator
=================================
Runs the full pipeline: download → transcribe → classify → store → index.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from quant_framework.config import Config
from quant_framework.downloader import YouTubeDownloader
from quant_framework.transcribers import get_transcriber, SubtitleParser
from quant_framework.storage import KnowledgeClassifier, KnowledgeBase

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    stage: str
    success: bool
    items_processed: int = 0
    output_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class KnowledgePipeline:
    """
    Main orchestration pipeline for quant knowledge extraction.
    """

    def __init__(self, config_path: str = "config/sources.yaml"):
        self.config = Config(config_path)
        self.classifier = KnowledgeClassifier(self.config)
        self.knowledge_base = KnowledgeBase(self.config)

    def run_full_pipeline(self, channels: Optional[List[str]] = None) -> Dict[str, PipelineResult]:
        results = {}

        logger.info("=" * 60)
        logger.info("STAGE 1: Downloading videos...")
        results["download"] = self._run_download_stage(channels)

        logger.info("=" * 60)
        logger.info("STAGE 2: Transcribing content...")
        results["transcribe"] = self._run_transcribe_stage()

        logger.info("=" * 60)
        logger.info("STAGE 3: Classifying content...")
        results["classify"] = self._run_classify_stage()

        logger.info("=" * 60)
        logger.info("STAGE 4: Building index...")
        results["index"] = self._run_index_stage()

        return results

    def _run_download_stage(self, channels: Optional[List[str]]) -> PipelineResult:
        result = PipelineResult(stage="download", success=True)
        downloader = YouTubeDownloader(self.config)
        channels_to_download = channels or [
            ch["url"] for ch in self.config.channels if ch.get("enabled", True)
        ]

        for channel_url in channels_to_download:
            try:
                videos = downloader.download_channel(channel_url)
                result.items_processed += len(videos)
                result.metadata[channel_url] = {
                    "videos_downloaded": len(videos),
                    "video_ids": [v.video_id for v in videos],
                }
            except Exception as e:
                result.errors.append(f"Error downloading {channel_url}: {e}")
                logger.error("Error downloading %s: %s", channel_url, e)

        result.output_path = str(self.config.get("paths.raw_downloads"))
        return result

    # Extensions whisper can consume; mirrors BaseTranscriber.transcribe_directory.
    MEDIA_EXTENSIONS = {".mp3", ".wav", ".m4a", ".mp4", ".mkv"}

    def run_transcribe_stage(self, input_dir: Optional[str] = None) -> PipelineResult:
        """Public wrapper so the CLI can run just the transcribe stage."""
        return self._run_transcribe_stage(input_dir)

    def _run_transcribe_stage(self, input_dir: Optional[str] = None) -> PipelineResult:
        """Collect ready-made transcripts into ``paths.transcriptions``.

        In transcript_only mode the downloader already parsed each video's
        subtitle into ``<video_dir>/transcript.txt``, so this stage must not
        re-parse every subtitle (or load a whisper model) on each run.  It:

        1. parses a subtitle only when its directory has no transcript yet,
           or the subtitle is newer than the transcript,
        2. copies each transcript into the transcriptions dir exactly once
           (skipping files already collected or already classified),
        3. runs whisper only when real audio/video files are present.
        """
        result = PipelineResult(stage="transcribe", success=True)
        raw_downloads = Path(input_dir or self.config.get("paths.raw_downloads", "data/raw_downloads"))
        transcription_dir = Path(self.config.get("paths.transcriptions", "data/transcriptions"))
        knowledge_base_dir = Path(self.config.get("paths.knowledge_base", "data/knowledge_base"))
        transcription_dir.mkdir(parents=True, exist_ok=True)

        if raw_downloads.exists():
            self._parse_missing_subtitles(raw_downloads, result)
            self._harvest_transcripts(raw_downloads, transcription_dir, knowledge_base_dir, result)
            self._transcribe_media(raw_downloads, transcription_dir, result)

        result.output_path = str(transcription_dir)
        return result

    def _parse_missing_subtitles(self, raw_downloads: Path, result: PipelineResult) -> None:
        """Parse only subtitles whose directory lacks a fresh transcript.txt."""
        subtitle_files = sorted(raw_downloads.rglob("*.srt")) + sorted(raw_downloads.rglob("*.vtt"))
        for subtitle_file in subtitle_files:
            transcript_file = subtitle_file.parent / "transcript.txt"
            try:
                if transcript_file.exists() and transcript_file.stat().st_mtime >= subtitle_file.stat().st_mtime:
                    continue  # downloader output already covers this subtitle
                parse = SubtitleParser.parse_srt if subtitle_file.suffix == ".srt" else SubtitleParser.parse_vtt
                transcript_file.write_text(parse(str(subtitle_file)), encoding="utf-8")
                result.metadata["subtitles_parsed"] = result.metadata.get("subtitles_parsed", 0) + 1
            except Exception as e:
                result.errors.append(f"Error parsing {subtitle_file}: {e}")

    def _harvest_transcripts(
        self,
        raw_downloads: Path,
        transcription_dir: Path,
        knowledge_base_dir: Path,
        result: PipelineResult,
    ) -> None:
        """Copy each transcript.txt into the transcriptions dir exactly once.

        Files the classify stage already moved into the knowledge base are
        recognized by name+content and skipped, so a pipeline re-run after a
        classify does not re-harvest the same transcripts again.
        """
        kb_by_name: Dict[str, Path] = {}
        if knowledge_base_dir.exists():
            kb_by_name = {p.name: p for p in knowledge_base_dir.rglob("*.txt")}

        for transcript_file in sorted(raw_downloads.rglob("transcript.txt")):
            dest_name = (
                self._flatten_rel_path(transcript_file, raw_downloads, use_dir_as_stem=True) + ".txt"
            )
            dest = transcription_dir / dest_name
            try:
                classified = kb_by_name.get(dest_name)
                if classified is not None and self._files_identical(transcript_file, classified):
                    result.metadata["already_classified"] = result.metadata.get("already_classified", 0) + 1
                    continue
                if dest.exists() and self._files_identical(transcript_file, dest):
                    result.metadata["already_collected"] = result.metadata.get("already_collected", 0) + 1
                    continue
                refreshed = dest.exists()
                shutil.copyfile(transcript_file, dest)
                result.items_processed += 1
                if refreshed:
                    result.metadata["refreshed"] = result.metadata.get("refreshed", 0) + 1
            except Exception as e:
                result.errors.append(f"Error copying {transcript_file}: {e}")

    def _transcribe_media(self, raw_downloads: Path, transcription_dir: Path, result: PipelineResult) -> None:
        """Whisper path: only runs when real audio/video files are present.

        Uses per-file transcribe() instead of transcribe_directory() because
        the downloader layout nests media under ``<channel>/<video>/``, which
        the directory-level single-level glob would miss.  An mtime guard
        skips re-transcription when the output is already fresh.
        """
        media_files = [
            p for p in sorted(raw_downloads.rglob("*"))
            if p.suffix.lower() in self.MEDIA_EXTENSIONS
        ]
        if not media_files:
            return
        result.metadata["media_files"] = len(media_files)
        transcriber = get_transcriber(self.config)
        for media_file in media_files:
            try:
                dest = transcription_dir / (
                    self._flatten_rel_path(media_file, raw_downloads, collapse_redundant=True) + ".txt"
                )
                if dest.exists() and dest.stat().st_mtime >= media_file.stat().st_mtime:
                    result.metadata["media_skipped"] = result.metadata.get("media_skipped", 0) + 1
                    continue
                transcription = transcriber.transcribe(str(media_file))
                dest.write_text(transcription.text, encoding="utf-8")
                result.items_processed += 1
            except Exception as e:
                result.errors.append(f"Error transcribing {media_file}: {e}")

    @staticmethod
    def _flatten_rel_path(
        path: Path,
        root: Path,
        use_dir_as_stem: bool = False,
        collapse_redundant: bool = False,
    ) -> str:
        """Flatten a path under ``root`` to a collision-safe single name.

        ``chan/vid/foo.mp3`` -> ``chan_vid_foo``.  With ``use_dir_as_stem``
        the file's own stem is replaced by its directory name, for generic
        downloader output: ``chan/vid/transcript.txt`` -> ``chan_vid``.
        With ``collapse_redundant`` a stem identical to its parent directory
        name is dropped (yt-dlp writes ``<id>.<ext>`` inside ``<id>/``):
        ``chan/vid/vidD.mp3`` -> ``chan_vidD``.
        """
        try:
            rel = path.relative_to(root)
        except ValueError:
            # Absolute vs relative root mismatch (either side may differ);
            # normalize both through abspath before comparing.
            rel = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
        parts = list(rel.parts[:-1])
        stem = rel.stem
        if use_dir_as_stem and parts:
            stem = parts.pop()
        elif collapse_redundant and parts and stem == parts[-1]:
            parts.pop()
        return "_".join((*parts, stem))

    def _run_classify_stage(self) -> PipelineResult:
        result = PipelineResult(stage="classify", success=True)
        transcription_dir = Path(self.config.get("paths.transcriptions"))
        knowledge_base_dir = Path(self.config.get("paths.knowledge_base"))

        # Idempotence: after the first run the transcriptions have been moved
        # into the knowledge base, so a re-run finds nothing here.  That must
        # be a safe no-op (plus an index refresh), never a silent empty
        # classification that leaves a stale index behind.
        if not transcription_dir.exists() or not any(transcription_dir.rglob("*.txt")):
            result.metadata["idempotent_noop"] = True
            result.metadata["reason"] = (
                "no transcriptions to classify (already classified or none produced)"
            )
            self.knowledge_base.build_index()
            result.metadata["index_files"] = sum(
                len(files) for files in self.knowledge_base.index.values()
            )
            result.output_path = str(knowledge_base_dir)
            return result

        classified = self.classifier.classify_directory(str(transcription_dir))
        result.items_processed = len(classified)

        for item in classified:
            source = Path(item.source_path)
            topic = item.topic
            dest_dir = knowledge_base_dir / topic
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / source.name
            try:
                if dest_file.exists():
                    # A re-transcription produced a name we already classified.
                    # Identical content: drop the duplicate (true no-op on the KB).
                    # Different content: keep both via a _1, _2, ... suffix.
                    if self._files_identical(source, dest_file):
                        source.unlink()
                        result.metadata["duplicates_skipped"] = result.metadata.get("duplicates_skipped", 0) + 1
                        continue
                    stem, suffix = dest_file.stem, dest_file.suffix
                    n = 1
                    while (dest_dir / f"{stem}_{n}{suffix}").exists():
                        n += 1
                    dest_file = dest_dir / f"{stem}_{n}{suffix}"
                    result.metadata["collisions_renamed"] = result.metadata.get("collisions_renamed", 0) + 1
                source.rename(dest_file)
                result.metadata[source.name] = {"topic": topic, "dest": str(dest_file)}
            except Exception as e:
                result.errors.append(f"Error moving {source}: {e}")

        # Keep the KB index in sync so search/stats are correct immediately
        # after the stage, without requiring a separate index rebuild.
        self.knowledge_base.build_index()
        result.output_path = str(knowledge_base_dir)
        return result

    @staticmethod
    def _files_identical(a: Path, b: Path) -> bool:
        try:
            return a.read_bytes() == b.read_bytes()
        except Exception:
            return False

    def _run_index_stage(self) -> PipelineResult:
        result = PipelineResult(stage="index", success=True)
        self.knowledge_base.build_index()
        result.items_processed = len(self.knowledge_base.index)
        result.output_path = str(self.config.get("paths.knowledge_base"))
        return result
