"""Unit tests for kronos_downloader.py."""
import os
import pytest
from kronos_downloader import KronosDownloader, MODELS, sha256_file


def test_kronos_downloader_models_catalog():
    """Verify Kronos low-hardware model variants catalog."""
    assert "kronos-mini" in MODELS
    assert "kronos-small" in MODELS
    assert MODELS["kronos-mini"]["params"] == "4.1M"
    assert MODELS["kronos-small"]["params"] == "24.7M"


def test_kronos_downloader_local_path():
    """Verify local directory check for downloaded Kronos weights."""
    downloader = KronosDownloader(target_dir="./models")
    mini_path = downloader.get_local_path("kronos-mini")
    assert mini_path is not None
    assert os.path.exists(mini_path)


def test_checksum_manifest_roundtrip(tmp_path):
    model_dir = tmp_path / "kronos-mini"
    model_dir.mkdir()
    weight = model_dir / "model.safetensors"
    weight.write_bytes(b"fake-weight-bytes-for-hash")
    downloader = KronosDownloader(target_dir=str(tmp_path))
    written = downloader.write_checksum_manifest("kronos-mini")
    assert written["files"]["model.safetensors"] == sha256_file(str(weight))
    verified = downloader.verify_checksums("kronos-mini", require_manifest=True)
    assert verified["ok"] is True
    # Tamper
    weight.write_bytes(b"tampered")
    failed = downloader.verify_checksums("kronos-mini", require_manifest=True)
    assert failed["ok"] is False
    assert "model.safetensors" in failed["mismatches"]
    assert downloader.get_local_path("kronos-mini") is None
