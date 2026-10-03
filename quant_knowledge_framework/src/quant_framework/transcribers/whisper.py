"""
Whisper Transcription Engine
=============================
Handles transcription of audio/video content using Whisper and subtitle parsing.
"""

import os
import re
from pathlib import Path
from typing import List, Optional, Callable
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Result of a transcription operation."""
    source_path: str
    text: str
    language: str
    duration: float
    segments: List[dict]
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseTranscriber:
    """Base class for all transcribers."""

    def transcribe(self, input_path: str, **kwargs) -> TranscriptionResult:
        raise NotImplementedError

    def transcribe_directory(self, directory: str, **kwargs) -> List[TranscriptionResult]:
        results: List[TranscriptionResult] = []
        dir_path = Path(directory)
        for ext in ["*.mp3", "*.wav", "*.m4a", "*.mp4", "*.mkv"]:
            for file_path in dir_path.glob(ext):
                try:
                    results.append(self.transcribe(str(file_path), **kwargs))
                except Exception as e:
                    logger.error("Error transcribing %s: %s", file_path, e)
        return results


class WhisperTranscriber(BaseTranscriber):
    """
    Transcription using OpenAI Whisper.
    """

    MODELS = ["tiny", "base", "small", "medium", "large"]

    def __init__(self, config):
        self.config = config
        self.model_size = config.get("transcription.model_size", "base")
        self.language = config.get("transcription.language", "en")
        self.device = config.get("transcription.device", "cpu")
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                import whisper
                self._model = whisper.load_model(self.model_size, device=self.device)
            except ImportError as e:
                raise ImportError("openai-whisper required: pip install openai-whisper") from e
        return self._model

    def transcribe(self, input_path: str, **kwargs) -> TranscriptionResult:
        model = self._load_model()
        language = kwargs.get("language", self.language)
        result = model.transcribe(input_path, language=language, verbose=False)
        return TranscriptionResult(
            source_path=input_path,
            text=result["text"].strip(),
            language=result.get("language", language),
            duration=float(result.get("duration") or 0.0),
            segments=result.get("segments") or [],
            metadata={"model": self.model_size},
        )


class FasterWhisperTranscriber(BaseTranscriber):
    """
    Optimized transcription using faster-whisper (CTranslate2).
    """

    def __init__(self, config):
        self.config = config
        self.model_size = config.get("transcription.model_size", "base")
        self.language = config.get("transcription.language", "en")
        self.device = config.get("transcription.device", "cpu")
        self.compute_type = config.get("transcription.compute_type", "int8")
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            except ImportError as e:
                raise ImportError("faster-whisper required: pip install faster-whisper") from e
        return self._model

    def transcribe(self, input_path: str, **kwargs) -> TranscriptionResult:
        model = self._load_model()
        language = kwargs.get("language", self.language)

        segments, info = model.transcribe(input_path, language=language, beam_size=5)

        text_parts: List[str] = []
        segment_list: List[dict] = []
        duration = 0.0
        for segment in segments:
            text_parts.append(segment.text)
            segment_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            })
            duration = segment.end

        return TranscriptionResult(
            source_path=input_path,
            text=" ".join(text_parts).strip(),
            language=getattr(info, "language", language),
            duration=duration,
            segments=segment_list,
            metadata={"model": f"faster-whisper/{self.model_size}"},
        )


class SubtitleParser:
    """Parses SRT and VTT subtitle files."""

    @staticmethod
    def parse_srt(file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
        text_lines: List[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.isdigit():
                continue
            if "-->" in line:
                continue
            line = re.sub(r"<[^>]+>", "", line)
            text_lines.append(line)
        return " ".join(text_lines)

    @staticmethod
    def parse_vtt(file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
        text_lines: List[str] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
                continue
            if "-->" in line:
                continue
            line = re.sub(r"<[^>]+>", "", line)
            text_lines.append(line)
        return " ".join(text_lines)


def get_transcriber(config) -> BaseTranscriber:
    engine = config.get("transcription.engine", "whisper")
    if engine == "faster-whisper":
        return FasterWhisperTranscriber(config)
    return WhisperTranscriber(config)
