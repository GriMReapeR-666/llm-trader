"""Verified SQLite backup/restore utilities for the authoritative journal."""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict


_ENCRYPTED_MAGIC = b"LLMTRADER_ENCRYPTED_BACKUP_V1\n"
_KDF_ITERATIONS = 390_000
_BACKUP_SUFFIXES = (".backup.db", ".encrypted.backup")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: str) -> Dict[str, Any]:
    con = sqlite3.connect(path)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    finally:
        con.close()
    return {"passed": result == "ok", "result": result, "tables": tables}


def create_backup(source: str = "trading_state.db", destination: str = "") -> Dict[str, Any]:
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    target = Path(destination or Path(source).with_name(f"{Path(source).stem}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.backup.db"))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() == Path(source).resolve():
        raise ValueError("backup destination must differ from the source journal")
    with sqlite3.connect(source) as src:
        src.execute("PRAGMA wal_checkpoint(PASSIVE)")
        with sqlite3.connect(str(target)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
    integrity = _integrity(str(target))
    if not integrity["passed"]:
        raise RuntimeError(f"backup integrity check failed: {integrity['result']}")
    manifest = {"source": os.path.abspath(source), "backup": str(target.resolve()),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sha256": _sha256(str(target)), "integrity": integrity,
                "retention": "operator-managed; never delete the only copy"}
    manifest_path = str(target) + ".manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest | {"manifest_path": manifest_path}


def verify_backup(backup: str, manifest_path: str = "") -> Dict[str, Any]:
    if not os.path.exists(backup):
        return {"passed": False, "reason": "backup_missing", "backup": backup}
    manifest_file = manifest_path or (str(backup) + ".manifest.json")
    expected = None
    if os.path.exists(manifest_file):
        with open(manifest_file, "r", encoding="utf-8") as handle:
            expected = json.load(handle).get("sha256")
    actual = _sha256(backup)
    integrity = _integrity(backup)
    return {"passed": bool(integrity["passed"] and (expected is None or expected == actual)),
            "sha256": actual, "expected_sha256": expected, "integrity": integrity,
            "manifest": manifest_file if os.path.exists(manifest_file) else None}


def restore_backup(backup: str, destination: str, *, overwrite: bool = False) -> Dict[str, Any]:
    check = verify_backup(backup)
    if not check["passed"]:
        raise RuntimeError(f"refusing unverified backup: {check}")
    if os.path.exists(destination) and not overwrite:
        raise FileExistsError(f"restore destination exists; pass overwrite=True explicitly: {destination}")
    if Path(backup).resolve() == Path(destination).resolve():
        raise ValueError("restore destination must differ from backup")
    with sqlite3.connect(backup) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    restored = _integrity(destination)
    if not restored["passed"]:
        raise RuntimeError(f"restored journal integrity failed: {restored['result']}")
    return {"passed": True, "backup": os.path.abspath(backup),
            "destination": os.path.abspath(destination), "integrity": restored}


def _crypto(passphrase: str, salt: bytes, iterations: int = _KDF_ITERATIONS):
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("backup passphrase must contain at least 12 characters")
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("cryptography package is required for encrypted backups") from exc
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=int(iterations)).derive(passphrase.encode("utf-8"))
    return AESGCM(key)


def _integrity_bytes(data: bytes) -> Dict[str, Any]:
    """Run an integrity check on serialized SQLite bytes.

    Python's ``sqlite3.Connection.deserialize`` can load a WAL-backed
    database but, on some Windows SQLite builds, subsequent reads fail with
    ``unable to open database file``.  Use a uniquely-created, short-lived
    file as the verification target instead.  The file is removed in the
    ``finally`` block; the encrypted backup never retains a plaintext copy.
    """
    fd, temp_path = tempfile.mkstemp(prefix="llmtrader-integrity-", suffix=".db")
    os.close(fd)
    try:
        Path(temp_path).write_bytes(data)
        return _integrity(temp_path)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def _decode_encrypted(path: str, passphrase: str) -> tuple[bytes, Dict[str, Any]]:
    raw = Path(path).read_bytes()
    if not raw.startswith(_ENCRYPTED_MAGIC):
        raise ValueError("backup is not an encrypted LLM Trader backup")
    header_start = len(_ENCRYPTED_MAGIC)
    header_end = raw.find(b"\n", header_start)
    if header_end < 0:
        raise ValueError("encrypted backup header is malformed")
    header_line = raw[header_start:header_end]
    header = json.loads(header_line.decode("utf-8"))
    salt = base64.b64decode(header["salt"])
    nonce = base64.b64decode(header["nonce"])
    ciphertext = raw[header_end + 1:]
    aes = _crypto(passphrase, salt, int(header.get("kdf_iterations", _KDF_ITERATIONS)))
    plaintext = aes.decrypt(nonce, ciphertext, _ENCRYPTED_MAGIC + header_line)
    if hashlib.sha256(plaintext).hexdigest() != header.get("plaintext_sha256"):
        raise ValueError("encrypted backup plaintext hash mismatch")
    integrity = _integrity_bytes(plaintext)
    if not integrity["passed"]:
        raise ValueError(f"encrypted backup integrity failed: {integrity['result']}")
    return plaintext, header


def create_encrypted_backup(source: str = "trading_state.db", destination: str = "",
                            *, passphrase: str) -> Dict[str, Any]:
    """Create an AES-GCM encrypted backup without retaining plaintext on disk.

    The source journal is already plaintext by definition.  Verification uses
    a short-lived temporary SQLite file only because the Windows sqlite3
    build cannot reliably query a deserialized WAL image; it is deleted before
    this function returns (or raises).
    """
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    target = Path(destination or Path(source).with_name(
        f"{Path(source).stem}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.encrypted.backup"
    ))
    if target.resolve() == Path(source).resolve():
        raise ValueError("backup destination must differ from the source journal")
    with sqlite3.connect(source) as src:
        src.execute("PRAGMA wal_checkpoint(PASSIVE)")
        plaintext = src.serialize()
    integrity = _integrity_bytes(plaintext)
    if not integrity["passed"]:
        raise RuntimeError(f"source integrity check failed: {integrity['result']}")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = {
        "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_name": Path(source).name, "kdf": "PBKDF2-HMAC-SHA256",
        "kdf_iterations": _KDF_ITERATIONS, "cipher": "AES-256-GCM",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        "integrity": integrity,
    }
    header_line = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ciphertext = _crypto(passphrase, salt).encrypt(nonce, plaintext, _ENCRYPTED_MAGIC + header_line)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_ENCRYPTED_MAGIC + header_line + b"\n" + ciphertext)
    manifest = {
        "source": os.path.abspath(source), "backup": str(target.resolve()),
        "created_at": header["created_at"], "encrypted": True,
        "cipher": header["cipher"], "sha256": _sha256(str(target)),
        "plaintext_sha256": header["plaintext_sha256"], "integrity": integrity,
        "retention": "operator-managed; never delete the only encrypted copy",
    }
    manifest_path = str(target) + ".manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest | {"manifest_path": manifest_path}


def verify_encrypted_backup(backup: str, *, passphrase: str) -> Dict[str, Any]:
    if not os.path.exists(backup):
        return {"passed": False, "reason": "backup_missing", "backup": backup}
    try:
        plaintext, header = _decode_encrypted(backup, passphrase)
        return {"passed": True, "encrypted": True, "cipher": header.get("cipher"),
                "sha256": _sha256(backup), "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
                "integrity": header.get("integrity", _integrity_bytes(plaintext))}
    except Exception as exc:
        return {"passed": False, "encrypted": True, "reason": "verification_failed",
                "error": str(exc)[:500], "backup": os.path.abspath(backup)}


def restore_encrypted_backup(backup: str, destination: str, *, passphrase: str,
                             overwrite: bool = False) -> Dict[str, Any]:
    if os.path.exists(destination) and not overwrite:
        raise FileExistsError(f"restore destination exists; pass overwrite=True explicitly: {destination}")
    if Path(backup).resolve() == Path(destination).resolve():
        raise ValueError("restore destination must differ from backup")
    plaintext, _ = _decode_encrypted(backup, passphrase)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plaintext)
    restored = _integrity(str(target))
    if not restored["passed"]:
        raise RuntimeError(f"restored journal integrity failed: {restored['result']}")
    return {"passed": True, "encrypted": True, "backup": os.path.abspath(backup),
            "destination": os.path.abspath(destination), "integrity": restored}


def prune_backups(directory: str, *, older_than_days: int = 30,
                  keep_minimum: int = 2, apply: bool = False) -> Dict[str, Any]:
    """Plan (or explicitly apply) deletion of old recognized backup artifacts.

    Only files produced by this module (``*.backup.db`` and
    ``*.encrypted.backup``) are candidates.  The newest ``keep_minimum``
    backups are always retained, and manifests are removed only together with
    their backup.  The default is a dry run so an operator can review the
    exact paths before making a destructive change.
    """
    root = Path(directory).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(str(root))
    if root == Path(root.anchor):
        raise ValueError("refusing to prune a filesystem root")
    if older_than_days < 0:
        raise ValueError("older_than_days must be non-negative")
    if keep_minimum < 0:
        raise ValueError("keep_minimum must be non-negative")

    candidates = sorted(
        (path for path in root.iterdir()
         if path.is_file() and path.name.endswith(_BACKUP_SUFFIXES)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    planned = []
    retained = []
    for index, path in enumerate(candidates):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        eligible = index >= keep_minimum and modified < cutoff
        item = {"path": str(path), "modified_at": modified.isoformat(),
                "age_days": max(0.0, (datetime.now(timezone.utc) - modified).total_seconds() / 86400.0),
                "reason": "older_than_retention_and_beyond_minimum" if eligible else "retained"}
        if eligible:
            planned.append(item)
        else:
            retained.append(item)

    deleted = []
    if apply:
        for item in planned:
            path = Path(item["path"])
            path.unlink()
            manifest = Path(str(path) + ".manifest.json")
            if manifest.exists():
                manifest.unlink()
            deleted.append(item["path"])
    return {
        "passed": True,
        "directory": str(root),
        "dry_run": not apply,
        "older_than_days": older_than_days,
        "keep_minimum": keep_minimum,
        "candidate_count": len(candidates),
        "planned": planned,
        "retained": retained,
        "deleted": deleted,
        "limitation": "Retention is operator-controlled; never delete the only recoverable journal copy.",
    }
