"""Common filesystem and database path resolvers.

Provides resilient path resolution for the trading journal database, decoupling
it from OneDrive file synchronization locking and facilitating clean test and
multi-environment isolation.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional


def get_trading_db_path(default_name: str = "trading_state.db") -> str:
    """Resolve the authoritative path for the SQLite trading journal database.

    Resolution order:
      1. Environment variable ``LLM_TRADER_DB_PATH``
      2. Environment variable ``LLM_TRADER_JOURNAL_PATH``
      3. Fallback to ``data/{default_name}`` if present
      4. Default ``{default_name}`` in project root

    If the environment variable points to a custom location (e.g. outside OneDrive,
    such as ``C:\\llm_trader_data\\trading_state.db``) and the file does not yet exist
    there, this resolver automatically creates the parent directory and copies
    any existing root ``trading_state.db`` over so state is seamlessly preserved.
    """
    env_path = os.getenv("LLM_TRADER_DB_PATH") or os.getenv("LLM_TRADER_JOURNAL_PATH")
    if env_path:
        target = Path(env_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        # First-time relocation migration: if destination is empty, copy existing local db
        if not target.is_file():
            local_src = Path(default_name).resolve()
            if local_src.is_file():
                try:
                    shutil.copy2(str(local_src), str(target))
                except Exception:
                    pass
        return str(target)

    # Fallback to data/ subdirectory if present
    data_sub = Path("data") / default_name
    if not os.path.exists(default_name) and data_sub.is_file():
        return str(data_sub.resolve())

    return str(Path(default_name).resolve())


def backup_trading_db(backup_dir: Optional[str] = None) -> Optional[str]:
    """Create an atomic SQLite backup of the active trading database.

    Uses SQLite online backup API to ensure zero write contention and prevent
    OneDrive or reader lock races.
    """
    source_path = get_trading_db_path()
    if not os.path.isfile(source_path):
        return None

    if backup_dir is None:
        backup_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "artifacts", "backups"
        )
    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    dest_filename = f"trading_state_backup_{int(time.time())}.db"
    dest_path = os.path.join(backup_dir, dest_filename)

    try:
        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as src_conn:
            with sqlite3.connect(dest_path) as dst_conn:
                src_conn.backup(dst_conn)
        return dest_path
    except Exception:
        return None
