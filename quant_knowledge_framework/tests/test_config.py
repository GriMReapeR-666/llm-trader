import pytest
from pathlib import Path
from quant_framework.config import Config


def test_config_loads_sources_file():
    config = Config("config/sources.yaml")
    assert "channels" in config.config
    assert "topics" in config.config
    assert "paths" in config.config


def test_config_get_uses_dot_notation():
    config = Config("config/sources.yaml")
    assert isinstance(config.get("channels"), list)
    assert isinstance(config.get("topics"), dict)
    assert config.get("paths.raw_downloads") == "data/raw_downloads"
    assert config.get("missing.key", "default") == "default"


def test_config_channels_property():
    config = Config("config/sources.yaml")
    channels = config.channels
    assert len(channels) >= 1
    assert "url" in channels[0]


def test_config_topics_property():
    config = Config("config/sources.yaml")
    topics = config.topics
    assert "01_Regime_Detection" in topics
    assert "02_Strategy_Development" in topics


def test_config_paths_property():
    config = Config("config/sources.yaml")
    paths = config.paths
    assert "raw_downloads" in paths
    assert "transcriptions" in paths
    assert "knowledge_base" in paths
