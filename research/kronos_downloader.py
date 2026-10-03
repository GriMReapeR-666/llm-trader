"""Kronos LLM Downloader & Local HuggingFace Loader.

Downloads and initializes lightweight, CPU-compatible Kronos foundation models:
- NeoQuasar/Kronos-mini (4.1M parameters — ultra-low memory < 50MB RAM)
- NeoQuasar/Kronos-small (24.7M parameters — fast CPU/GPU inference)

GAP-20: downloaded ``.safetensors`` weights are hashed (SHA-256) and verified
against a local checksum manifest before the path is trusted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger("KronosDownloader")

try:
    from huggingface_hub import snapshot_download  # type: ignore[import-untyped]
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False


MODELS = {
    "kronos-mini": {
        "repo_id": "NeoQuasar/Kronos-mini",
        "params": "4.1M",
        "context_len": 2048,
        "ram_mb": "~50MB",
        "description": "Ultra-lightweight financial K-line model, runs blazingly fast on CPU",
    },
    "kronos-small": {
        "repo_id": "NeoQuasar/Kronos-small",
        "params": "24.7M",
        "context_len": 512,
        "ram_mb": "~200MB",
        "description": "Balanced high-performance K-line model for CPU or GPU inference",
    },
    "kronos-base": {
        "repo_id": "NeoQuasar/Kronos-base",
        "params": "102.3M",
        "context_len": 512,
        "ram_mb": "~800MB",
        "description": "Full-size financial foundation model for multi-asset forecasting",
    },
}


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class KronosDownloader:
    """Downloader and manager for Kronos foundation model weights."""

    def __init__(self, target_dir: str = "./models"):
        self.target_dir = os.path.abspath(target_dir)
        os.makedirs(self.target_dir, exist_ok=True)

    def _manifest_path(self, model_key: str) -> str:
        return os.path.join(self.target_dir, model_key, "sha256_manifest.json")

    def compute_weight_checksums(self, model_dir: str) -> Dict[str, str]:
        """SHA-256 all ``.safetensors`` files under a model directory."""
        checksums: Dict[str, str] = {}
        for root, _dirs, files in os.walk(model_dir):
            for name in files:
                if not name.endswith(".safetensors"):
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, model_dir).replace("\\", "/")
                checksums[rel] = sha256_file(full)
        return checksums

    def write_checksum_manifest(self, model_key: str) -> Dict[str, Any]:
        model_dir = os.path.join(self.target_dir, model_key)
        checksums = self.compute_weight_checksums(model_dir)
        payload = {
            "model_key": model_key,
            "algo": "sha256",
            "files": checksums,
        }
        manifest = self._manifest_path(model_key)
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        return payload

    def verify_checksums(self, model_key: str, *, require_manifest: bool = False) -> Dict[str, Any]:
        """Verify local weights against the SHA-256 manifest.

        If no manifest exists and ``require_manifest`` is False, a new manifest
        is written from current files (first trusted snapshot). When a manifest
        exists, every listed ``.safetensors`` hash must match.
        """
        model_dir = os.path.join(self.target_dir, model_key)
        manifest_path = self._manifest_path(model_key)
        if not os.path.isdir(model_dir):
            return {"ok": False, "reason": "model directory missing", "model_key": model_key}
        current = self.compute_weight_checksums(model_dir)
        if not current:
            return {"ok": False, "reason": "no .safetensors weights found", "model_key": model_key}
        if not os.path.exists(manifest_path):
            if require_manifest:
                return {"ok": False, "reason": "checksum manifest missing", "model_key": model_key}
            written = self.write_checksum_manifest(model_key)
            return {"ok": True, "reason": "manifest created", "model_key": model_key, "files": written["files"]}
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                expected = json.load(handle)
        except Exception as exc:
            return {"ok": False, "reason": f"manifest unreadable: {exc}", "model_key": model_key}
        expected_files = expected.get("files") or {}
        mismatches: List[str] = []
        missing: List[str] = []
        for rel, digest in expected_files.items():
            if rel not in current:
                missing.append(rel)
            elif current[rel] != digest:
                mismatches.append(rel)
        ok = not missing and not mismatches
        return {
            "ok": ok,
            "reason": "verified" if ok else "checksum mismatch",
            "model_key": model_key,
            "missing": missing,
            "mismatches": mismatches,
            "file_count": len(current),
        }

    def download_model(self, model_key: str = "kronos-mini") -> Dict[str, Any]:
        """Download specified Kronos model weights from HuggingFace Hub."""
        if model_key not in MODELS:
            raise ValueError(f"Unknown model_key '{model_key}'. Available: {list(MODELS.keys())}")

        cfg = MODELS[model_key]
        repo_id = cfg["repo_id"]
        local_folder = os.path.join(self.target_dir, model_key)

        log.info("Starting download for %s (%s params, RAM: %s) to %s", repo_id, cfg["params"], cfg["ram_mb"], local_folder)

        if not HF_AVAILABLE:
            log.warning("huggingface_hub python package not installed. Installing or using snapshot API...")
            return {
                "status": "MISSING_DEPENDENCY",
                "message": "Please install huggingface_hub: pip install huggingface-hub",
                "repo_id": repo_id,
                "local_dir": local_folder,
            }

        try:
            downloaded_path = snapshot_download(
                repo_id=repo_id,
                local_dir=local_folder,
                local_dir_use_symlinks=False,
                resume_download=True,
            )
            manifest = self.write_checksum_manifest(model_key)
            log.info("✅ Download complete for %s -> %s (%s weight files hashed)",
                     repo_id, downloaded_path, len(manifest.get("files") or {}))
            return {
                "status": "SUCCESS",
                "repo_id": repo_id,
                "local_dir": downloaded_path,
                "params": cfg["params"],
                "context_len": cfg["context_len"],
                "ram_mb": cfg["ram_mb"],
                "sha256_manifest": self._manifest_path(model_key),
                "sha256_files": len(manifest.get("files") or {}),
            }
        except Exception as exc:
            log.error("Failed to download %s: %s", repo_id, exc)
            return {
                "status": "ERROR",
                "error": str(exc),
                "repo_id": repo_id,
                "local_dir": local_folder,
            }

    def get_local_path(self, model_key: str = "kronos-mini", *, verify: bool = True) -> Optional[str]:
        """Return local model path only when weights exist and checksums verify."""
        path = os.path.join(self.target_dir, model_key)
        if not (os.path.exists(path) and len(os.listdir(path)) > 0):
            return None
        if not verify:
            return path
        result = self.verify_checksums(model_key, require_manifest=False)
        if not result.get("ok"):
            log.error("Kronos weight integrity check failed for %s: %s", model_key, result)
            return None
        return path
