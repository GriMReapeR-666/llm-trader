"""
Quant Knowledge Framework
=========================
Isolated framework for downloading, transcribing, and organizing
quantitative finance knowledge from YouTube channels, books, and sources.
"""

__version__ = "1.0.0"

from quant_framework.config import Config
from quant_framework.agent import QuantAgent
from quant_framework.pipeline import KnowledgePipeline

__all__ = ["Config", "QuantAgent", "KnowledgePipeline"]
