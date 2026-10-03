"""Secrets hygiene gate (master-plan P6.3).

import logging

logger = logging.getLogger(__name__)
Detects plaintext credential-looking values in known config files so the
supervisor can surface them as a failing boot check. Detection only —
remediation stays an explicit operator action.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "api_key", "apikey"}


def scan_file(path: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return findings

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).lower()
                here = f"{trail}.{k}" if trail else str(k)
                if key in SENSITIVE_KEYS and isinstance(v, str) and v.strip():
                    findings.append({"file": path, "path": here,
                                     "hint": f"{v[:2]}*** ({len(v)} chars)"})
                else:
                    walk(v, here)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}]")

    walk(data, "")
    return findings


def scan_standard_files() -> List[Dict[str, Any]]:
    targets = [
        os.path.join(ROOT, "accounts.json"),
        os.path.join(ROOT, "config.ini"),
    ]
    out: List[Dict[str, Any]] = []
    for t in targets:
        if t.endswith(".ini"):
            out.extend(_scan_ini(t))
        else:
            out.extend(scan_file(t))
    return out


def _scan_ini(path: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    try:
        import configparser
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        for section in parser.sections():
            for key, value in parser.items(section):
                if key.lower() in SENSITIVE_KEYS and value.strip():
                    findings.append({"file": path,
                                     "path": f"[{section}] {key}",
                                     "hint": f"{value[:2]}***"})
    except Exception as exc:
        logger.debug("response parsing failed: %s", exc)
    return findings


def is_clean() -> tuple[bool, int]:
    findings = scan_standard_files()
    return (len(findings) == 0, len(findings))
