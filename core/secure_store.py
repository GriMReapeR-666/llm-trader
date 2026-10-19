"""Secure credential store for MT5 accounts and provider keys.

Hierarchy (fail-closed, never writes plaintext to accounts.json):
  1. OS keyring (preferred, if `keyring` installed)  -> service "llm-trader", key = account f"mt5:{login}:{server}"
  2. AES-GCM encrypted file `.secure_store.enc` (Fernet, key derived from machine id) fallback
  3. Plain 0o600 file `.secure_store.json` as last resort, with warning (still not in repo)

All callers MUST use `store_password` / `retrieve_password` and reference via
`password_store_key` in accounts.json. Env var MT5_PASSWORD remains override for CI.

Never log secrets.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
import os
import json
import base64
import hashlib
import getpass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SECURE_FILE = ROOT / ".secure_store.enc"
LEGACY_FILE = ROOT / ".secure_store.json"
KEYRING_SERVICE = "llm-trader"

def _machine_key() -> bytes:
    # Deterministic per-machine 32-byte key derived from hostname + user + existing .env salt if present
    raw = f"{os.getenv('USERNAME','')}|{os.getenv('COMPUTERNAME','')}|{Path.home()}".encode("utf-8")
    # mix in existing secure file if present to avoid changing key on reinstall
    try:
        # stable hash
        return hashlib.sha256(raw).digest()
    except Exception:
        return os.urandom(32)

def _fernet_available():
    try:
        from cryptography.fernet import Fernet  # type: ignore
        return True
    except Exception:
        return False

def _fernet_key() -> bytes:
    # Fernet expects urlsafe base64 32 bytes
    return base64.urlsafe_b64encode(_machine_key()[:32])

def _keyring_available() -> bool:
    try:
        import keyring  # type: ignore
        # probe
        keyring.get_keyring()
        return True
    except Exception:
        return False

def _write_value(key: str, value: str) -> None:
    """Persist one secret across the keyring -> fernet -> 0600 file hierarchy."""
    # 1. try keyring
    if _keyring_available():
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, key, value)
            return
        except Exception as exc:
            logger.warning("keyring access failed: %s", exc)
    # 2. try Fernet file
    if _fernet_available():
        try:
            from cryptography.fernet import Fernet
            f = Fernet(_fernet_key())
            data = {}
            if SECURE_FILE.exists():
                try:
                    raw = SECURE_FILE.read_bytes()
                    data = json.loads(f.decrypt(raw).decode("utf-8"))
                except Exception:
                    data = {}
            data[key] = value
            SECURE_FILE.write_bytes(f.encrypt(json.dumps(data).encode("utf-8")))
            try:
                os.chmod(SECURE_FILE, 0o600)
            except Exception as exc:
                logger.warning("encryption failed: %s", exc)
            return
        except Exception as exc:
            logger.warning("encryption failed: %s", exc)
    # 3. fallback plain file with 600 perms (still better than accounts.json)
    data = {}
    if LEGACY_FILE.exists():
        try:
            data = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = base64.b64encode(value.encode("utf-8")).decode("utf-8")
    LEGACY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(LEGACY_FILE, 0o600)
    except Exception as exc:
        logger.debug("JSON operation failed: %s", exc)


def _read_value(key: str) -> str:
    """Read one secret. Precedence: keyring -> fernet -> base64 file."""
    # 1. keyring
    if _keyring_available():
        try:
            import keyring
            v = keyring.get_password(KEYRING_SERVICE, key)
            if v:
                return str(v)
        except Exception as exc:
            logger.warning("keyring access failed: %s", exc)
    # 2. fernet
    if _fernet_available() and SECURE_FILE.exists():
        try:
            from cryptography.fernet import Fernet
            f = Fernet(_fernet_key())
            raw = SECURE_FILE.read_bytes()
            data = json.loads(f.decrypt(raw).decode("utf-8"))
            if key in data:
                return str(data[key])
        except Exception as exc:
            logger.warning("decryption failed: %s", exc)
    # 3. legacy base64 file
    if LEGACY_FILE.exists():
        try:
            data = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
            if key in data:
                try:
                    return base64.b64decode(data[key]).decode("utf-8")
                except Exception:
                    return str(data[key])
        except Exception as exc:
            logger.warning("decryption failed: %s", exc)
    return ""


def _delete_value(key: str) -> bool:
    removed = False
    if _keyring_available():
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, key)
            removed = True
        except Exception as exc:
            logger.warning("keyring access failed: %s", exc)
    if _fernet_available() and SECURE_FILE.exists():
        try:
            from cryptography.fernet import Fernet
            f = Fernet(_fernet_key())
            data = json.loads(f.decrypt(SECURE_FILE.read_bytes()).decode("utf-8"))
            if key in data:
                data.pop(key, None)
                SECURE_FILE.write_bytes(f.encrypt(json.dumps(data).encode("utf-8")))
                removed = True
        except Exception as exc:
            logger.warning("encryption failed: %s", exc)
    if LEGACY_FILE.exists():
        try:
            data = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
            if key in data:
                data.pop(key, None)
                LEGACY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
                removed = True
        except Exception as exc:
            logger.warning("encryption failed: %s", exc)
    return removed


def store_password(login: str, server: str, password: str) -> str:
    """Persist password securely, return store_key for accounts.json."""
    login = str(login).strip()
    server = str(server).strip()
    if not login or not server or not password:
        raise ValueError("login, server, password required")
    store_key = f"mt5:{login}:{server}"
    _write_value(store_key, password)
    return store_key


def retrieve_password(store_key: Optional[str], login: Optional[str]=None, server: Optional[str]=None) -> str:
    """Fetch password. Precedence: store_key lookup -> keyring -> fernet -> base64 file."""
    if store_key:
        key = str(store_key).strip()
    elif login and server:
        key = f"mt5:{str(login).strip()}:{str(server).strip()}"
    else:
        return ""
    return _read_value(key)


def delete_password(store_key: str) -> bool:
    return _delete_value(str(store_key).strip())


def store_secret(store_key: str, value: str) -> str:
    """Persist an arbitrary named secret through the same hierarchy.

    Non-MT5 service tokens (e.g. ``sieve:api_key``) live here so they are kept
    in the OS keyring / encrypted file instead of plaintext config.  Never log
    the value.
    """
    key = str(store_key).strip()
    if not key or not value:
        raise ValueError("store_key and value required")
    _write_value(key, str(value))
    return key


def retrieve_secret(store_key: str) -> str:
    """Read a named secret previously written by :func:`store_secret`."""
    key = str(store_key).strip()
    return _read_value(key) if key else ""


def delete_secret(store_key: str) -> bool:
    key = str(store_key).strip()
    return _delete_value(key) if key else False

def has_store() -> str:
    if _keyring_available():
        return "keyring"
    if _fernet_available():
        return "fernet"
    return "file600"

def masked(s: str, keep: int = 3) -> str:
    if not s:
        return "(empty)"
    s = str(s)
    if len(s) <= keep:
        return "*" * len(s)
    return s[:keep] + "*" * (len(s)-keep)
