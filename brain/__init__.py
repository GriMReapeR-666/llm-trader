"""Brain — Agent cognitive system.

Provides conversational context continuity (ContextBuffer), episodic trace
management, and distilled lesson storage.  Complements the SQLite-backed
L0-L3 memory hierarchy in ``intelligence/trading_memory.py`` with on-disk
artifact representations.

The LLM orchestrator imports ``ContextBuffer`` from here::

    from brain.memory import ContextBuffer

When the import fails, the orchestrator falls back to stateless mode.
"""
from brain.memory import ContextBuffer
from brain.episodic import EpisodicStore, EpisodicTrace
from brain.distiller import LessonDistiller, DistilledLesson

__all__ = [
    "ContextBuffer",
    "EpisodicStore",
    "EpisodicTrace",
    "LessonDistiller",
    "DistilledLesson",
]
