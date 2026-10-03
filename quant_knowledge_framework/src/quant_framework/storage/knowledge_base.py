"""
Knowledge Base Storage
======================
Manages the organized knowledge base storage and indexing.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from quant_framework.storage.classifier import ClassifiedItem
from quant_framework.storage.search_index import build_or_sync_index

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    Manages the organized knowledge base storage.
    """

    def __init__(self, config):
        self.config = config
        self.base_path = Path(config.get("paths.knowledge_base", "data/knowledge_base"))
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.index: Dict[str, List[str]] = {}
        self._search_index = None
        self._load_index()

    def _load_index(self) -> None:
        index_path = self.base_path / "index.json"
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    self.index = json.load(f)
            except Exception:
                self.index = {}
        else:
            self.build_index()

    def store(self, item: ClassifiedItem) -> Path:
        topic_dir = self.base_path / item.topic
        topic_dir.mkdir(parents=True, exist_ok=True)

        dest_file = topic_dir / f"{item.title}.txt"

        try:
            with open(item.source_path, "r", encoding="utf-8") as src:
                content = src.read()

            with open(dest_file, "w", encoding="utf-8") as dst:
                dst.write(f"# {item.title}\n\n")
                dst.write(f"Topic: {item.topic}\n")
                dst.write(f"Confidence: {item.confidence:.2f}\n")
                dst.write(f"Keywords: {', '.join(item.keywords)}\n\n")
                dst.write("---\n\n")
                dst.write(content)

            if item.topic not in self.index:
                self.index[item.topic] = []
            self.index[item.topic].append(str(dest_file))

            return dest_file
        except Exception as e:
            logger.error("Error storing %s: %s", item.source_path, e)
            raise

    def build_index(self) -> Dict[str, List[str]]:
        self.index = {}
        for topic_dir in self.base_path.iterdir():
            if topic_dir.is_dir():
                topic_name = topic_dir.name
                files = list(topic_dir.glob("*.txt")) + list(topic_dir.glob("*.md"))
                self.index[topic_name] = [str(f) for f in files]

        index_path = self.base_path / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, indent=2)

        # Keep the tokenized search index in sync with the rebuilt file list
        # (incremental: unchanged files are detected by mtime+size and skipped).
        self._search_index = build_or_sync_index(self.base_path)
        return self.index

    def get_by_topic(self, topic: str) -> List[str]:
        return list(self.index.get(topic, []))

    def get_content(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error("Error reading %s: %s", path, e)
            return f"Error: {e}"

    def search(self, query: str) -> List[str]:
        """Tokenized search over the persisted inverted index.

        Replaces the historical O(files x size) substring scan. Tokens are
        prefix-matched, so "volat" finds "volatility"; multi-token queries
        AND their terms together. Before every query the index syncs against
        the KB directory via mtime+size fingerprinting, so files added,
        changed, or removed at any time are picked up (an unchanged KB costs
        one directory stat pass, not a re-read of every file).
        """
        if self._search_index is None:
            self._search_index = build_or_sync_index(self.base_path)
        else:
            # Long-lived instances (e.g. the agent singleton) must see KB
            # changes without a rebuild; sync() is a no-op when nothing moved.
            stats = self._search_index.sync()
            if stats["added"] or stats["updated"] or stats["removed"]:
                self._search_index.save()
        return self._search_index.search(query)
