"""
Transcribers Package
=====================
Whisper and subtitle transcription engines.
"""

from quant_framework.transcribers.whisper import WhisperTranscriber, FasterWhisperTranscriber, SubtitleParser, get_transcriber

__all__ = ["WhisperTranscriber", "FasterWhisperTranscriber", "SubtitleParser", "get_transcriber"]
