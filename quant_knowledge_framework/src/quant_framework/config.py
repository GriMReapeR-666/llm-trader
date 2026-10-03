"""
Configuration loader for the Quant Knowledge Framework.
"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional


class Config:
    """
    Loads framework configuration from YAML.
    Supports dot-path access like ``config.get("paths.raw_downloads")``.
    """

    def __init__(self, config_path: str = "config/sources.yaml"):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        path = Path(self.config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        with open(path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}

    def get(self, key_path: str, default: Any = None) -> Any:
        keys = key_path.split(".")
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value is not None else default

    def set(self, key_path: str, value: Any) -> None:
        keys = key_path.split(".")
        target = self.config
        for key in keys[:-1]:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value

    def save(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, default_flow_style=False)

    @property
    def channels(self) -> list:
        return list(self.config.get("channels", []))

    @property
    def topics(self) -> dict:
        return dict(self.config.get("topics", {}))

    @property
    def paths(self) -> dict:
        return dict(self.config.get("paths", {}))
