"""Local bounded vector-style retrieval over approved source-linked memory.

Default backend is a deterministic hashed-token representation that works
offline without a second provider.  When ``TRADING_SEMANTIC_EMBEDDINGS`` is
set to ``sentence_transformers`` / ``neural`` and SentenceTransformers is
installed, retrieval optionally uses neural dense embeddings.  Structured
tags/status filters still run first; ranking is only applied to that bounded
approved candidate set.  Failures fall back to the hash backend.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple, Union

log = logging.getLogger("semantic_memory")

_NEURAL_MODEL = None
_NEURAL_LOAD_ATTEMPTED = False

Vector = Union[Dict[int, float], Sequence[float]]


def embedding_backend_name() -> str:
    mode = str(os.getenv("TRADING_SEMANTIC_EMBEDDINGS", "hash") or "hash").strip().lower()
    if mode in {"1", "true", "yes", "neural", "sentence_transformers", "st"}:
        return "sentence_transformers"
    return "hash"


def _tokens(value: Any) -> List[str]:
    text = json.dumps(value, sort_keys=True, default=str).lower()
    # JSON punctuation must not become part of tokens.  The previous split
    # made a query token such as ``eurusd`` differ from stored
    # ``"eurusd",`` and silently returned no semantic memories.
    return [token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 2]


def _vector(value: Any, dimensions: int = 256) -> Dict[int, float]:
    vector: Dict[int, float] = {}
    for token in _tokens(value):
        index = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dimensions
        vector[index] = vector.get(index, 0.0) + 1.0
    return vector


def _cosine_sparse(left: Dict[int, float], right: Dict[int, float]) -> float:
    dot = sum(value * right.get(index, 0.0) for index, value in left.items())
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _cosine_dense(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    norm_left = 0.0
    norm_right = 0.0
    for a, b in zip(left, right):
        dot += float(a) * float(b)
        norm_left += float(a) * float(a)
        norm_right += float(b) * float(b)
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / math.sqrt(norm_left * norm_right)


def _cosine(left: Vector, right: Vector) -> float:
    if isinstance(left, dict) and isinstance(right, dict):
        return _cosine_sparse(left, right)
    if isinstance(left, dict) or isinstance(right, dict):
        return 0.0
    return _cosine_dense(left, right)


def _load_neural_model():
    """Lazy-load SentenceTransformers when explicitly enabled."""
    global _NEURAL_MODEL, _NEURAL_LOAD_ATTEMPTED
    if embedding_backend_name() != "sentence_transformers":
        return None
    if _NEURAL_LOAD_ATTEMPTED:
        return _NEURAL_MODEL
    _NEURAL_LOAD_ATTEMPTED = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model_name = os.getenv(
            "TRADING_SEMANTIC_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        _NEURAL_MODEL = SentenceTransformer(model_name)
        log.info("semantic_memory neural backend ready | model=%s", model_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic_memory neural backend unavailable, using hash: %s", exc)
        _NEURAL_MODEL = None
    return _NEURAL_MODEL


def embed(value: Any) -> Tuple[str, Vector]:
    """Return ``(backend, vector)`` — neural when available, else hash."""
    model = _load_neural_model()
    if model is not None:
        text = json.dumps(value, sort_keys=True, default=str)
        try:
            dense = model.encode([text], normalize_embeddings=True)[0]
            return "sentence_transformers", [float(x) for x in dense]
        except Exception as exc:  # noqa: BLE001
            log.warning("semantic_memory neural encode failed, using hash: %s", exc)
    return "hash", _vector(value)


def status() -> Dict[str, Any]:
    backend = embedding_backend_name()
    neural = _load_neural_model() if backend == "sentence_transformers" else None
    return {
        "requested_backend": backend,
        "active_backend": "sentence_transformers" if neural is not None else "hash",
        "neural_available": neural is not None,
        "execution_authority": False,
    }


_STRUCTURED_FILTER_KEYS = {
    "symbol", "strategy", "side", "setup_id", "regime", "session",
    "timeframe", "event_class", "volatility_regime",
}


def _structured_match(query: Any, encoded_tags: Any) -> bool:
    """Apply exact state filters before semantic similarity ranking.

    A vector/token match is advisory only; it must not cross an explicit
    symbol, strategy, setup, or regime boundary supplied by the caller.
    Missing candidate fields are treated as non-matches rather than silently
    broadening the retrieval universe.
    """
    if not isinstance(query, dict):
        return True
    try:
        tags = json.loads(encoded_tags or "{}") if isinstance(encoded_tags, str) else encoded_tags
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(tags, dict):
        return False
    for key in _STRUCTURED_FILTER_KEYS:
        expected = query.get(key)
        if expected in (None, "", "UNKNOWN"):
            continue
        actual = tags.get(key)
        if actual in (None, "", "UNKNOWN"):
            return False
        if str(actual).strip().upper() != str(expected).strip().upper():
            return False
    return True


def _normalize_as_of(value: Any = None) -> str:
    if value is None or value == "":
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("semantic memory as_of must be a valid timestamp") from None


def _fts_match_query(query: Any, *, max_terms: int = 12) -> str | None:
    """Build a deterministic FTS5 OR query from query tokens."""
    try:
        terms = sorted(set(_tokens(query)))[:max_terms]
    except (TypeError, ValueError):
        return None
    if not terms:
        return None
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _fts_tables_available(con: sqlite3.Connection) -> bool:
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError:
        return False
    return {"narratives_fts", "verified_memories_fts", "scenario_documents_fts"}.issubset(tables)


def search(path: str = "trading_state.db", query: Any = None, limit: int = 5,
           candidate_limit: int = 300, *, as_of: Any = None) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    cutoff = _normalize_as_of(as_of)
    backend, query_vector = embed(query or {})
    match = _fts_match_query(query or {})
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        candidates: List[Dict[str, Any]] = []
        use_fts = bool(match) and _fts_tables_available(con)
        if "narratives" in tables:
            rows = None
            if use_fts:
                try:
                    rows = con.execute("""SELECT n.id,n.tags,n.content,n.source_ids,n.created_at,n.reviewed_at
                        FROM narratives_fts JOIN narratives n ON n.id = narratives_fts.rowid
                        WHERE narratives_fts MATCH ?
                          AND n.status='APPROVED' AND n.created_at<=?
                          AND (n.reviewed_at IS NULL OR n.reviewed_at='' OR n.reviewed_at<=?)
                        ORDER BY bm25(narratives_fts) LIMIT ?""", (match, cutoff, cutoff, candidate_limit)).fetchall()
                except sqlite3.DatabaseError as exc:
                    log.debug("narrative FTS fallback: %s", exc)
                    rows = None
            if not rows:
                rows = con.execute("""SELECT id,tags,content,source_ids,created_at,reviewed_at
                    FROM narratives WHERE status='APPROVED' AND created_at<=?
                      AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY created_at DESC LIMIT ?""", (cutoff, cutoff, candidate_limit)).fetchall()
            for row in rows:
                if _structured_match(query, row["tags"]):
                    candidates.append({"layer": "narrative", "id": row["id"], "tags": row["tags"], "content": row["content"], "source_ids": row["source_ids"], "created_at": row["created_at"], "reviewed_at": row["reviewed_at"]})
        if "scenario_documents" in tables:
            rows = None
            if use_fts:
                try:
                    rows = con.execute("""SELECT s.scenario_key,s.tags,s.content,s.evidence,s.win_count,s.loss_count,s.created_at,s.updated_at,s.reviewed_at
                        FROM scenario_documents_fts JOIN scenario_documents s ON s.rowid = scenario_documents_fts.rowid
                        WHERE scenario_documents_fts MATCH ?
                          AND s.status='APPROVED' AND s.created_at<=? AND s.updated_at<=?
                          AND (s.reviewed_at IS NULL OR s.reviewed_at='' OR s.reviewed_at<=?)
                        ORDER BY bm25(scenario_documents_fts) LIMIT ?""", (match, cutoff, cutoff, cutoff, candidate_limit)).fetchall()
                except sqlite3.DatabaseError as exc:
                    log.debug("scenario FTS fallback: %s", exc)
                    rows = None
            if not rows:
                rows = con.execute("""SELECT scenario_key,tags,content,evidence,win_count,loss_count,created_at,updated_at,reviewed_at
                    FROM scenario_documents WHERE status='APPROVED' AND created_at<=? AND updated_at<=?
                      AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY updated_at DESC LIMIT ?""", (cutoff, cutoff, cutoff, candidate_limit)).fetchall()
            for row in rows:
                if _structured_match(query, row["tags"]):
                    candidates.append({"layer": "scenario", "id": row["scenario_key"], "tags": row["tags"], "content": row["content"], "source_ids": row["evidence"],
                                       "win_count": row["win_count"], "loss_count": row["loss_count"], "created_at": row["created_at"], "updated_at": row["updated_at"], "reviewed_at": row["reviewed_at"]})
        if "verified_memories" in tables:
            rows = None
            if use_fts:
                try:
                    rows = con.execute("""SELECT v.id,v.tags,v.summary,v.lesson,v.evidence,v.created_at,v.reviewed_at
                        FROM verified_memories_fts JOIN verified_memories v ON v.id = verified_memories_fts.rowid
                        WHERE verified_memories_fts MATCH ?
                          AND v.status='APPROVED' AND v.created_at<=?
                          AND (v.reviewed_at IS NULL OR v.reviewed_at='' OR v.reviewed_at<=?)
                        ORDER BY bm25(verified_memories_fts) LIMIT ?""", (match, cutoff, cutoff, candidate_limit)).fetchall()
                except sqlite3.DatabaseError as exc:
                    log.debug("verified FTS fallback: %s", exc)
                    rows = None
            if not rows:
                rows = con.execute("""SELECT id,tags,summary,lesson,evidence,created_at,reviewed_at
                    FROM verified_memories WHERE status='APPROVED' AND created_at<=?
                      AND (reviewed_at IS NULL OR reviewed_at='' OR reviewed_at<=?)
                    ORDER BY created_at DESC LIMIT ?""", (cutoff, cutoff, candidate_limit)).fetchall()
            for row in rows:
                if _structured_match(query, row["tags"]):
                    candidates.append({"layer": "verified", "id": row["id"], "tags": row["tags"], "content": {"summary": row["summary"], "lesson": row["lesson"]}, "source_ids": row["evidence"], "created_at": row["created_at"], "reviewed_at": row["reviewed_at"]})
    ranked = []
    for item in candidates:
        if backend == "sentence_transformers":
            _, cand_vector = embed({"tags": item["tags"], "content": item["content"]})
        else:
            cand_vector = _vector({"tags": item["tags"], "content": item["content"]})
        score = _cosine(query_vector, cand_vector)
        if score > 0:
            item["similarity"] = round(score, 6)
            item["embedding_backend"] = backend
            if item.get("layer") == "scenario":
                balanced = int(item.get("win_count", 0) or 0) > 0 and int(item.get("loss_count", 0) or 0) > 0
                item["retrieval_balance"] = "BALANCED" if balanced else "UNBALANCED"
                if not balanced:
                    item["retrieval_warning"] = "Scenario lacks verified win/loss balance; treat as incomplete evidence."
            try:
                item["tags"] = json.loads(item["tags"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["tags"] = {}
            try:
                item["source_ids"] = json.loads(item["source_ids"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["source_ids"] = []
            item["content"] = item["content"] if isinstance(item["content"], dict) else str(item["content"])[:2000]
            ranked.append(item)
    return sorted(ranked, key=lambda item: (item.get("retrieval_balance") == "BALANCED", item["similarity"]), reverse=True)[:max(0, int(limit))]


class SemanticMemory:
    """Class wrapper for semantic memory operations."""
    @staticmethod
    def embed(value: Any):
        return embed(value)

    @staticmethod
    def status() -> Dict[str, Any]:
        return status()

    @staticmethod
    def rank_memories(query: Any, db_path: str = "trading_state.db", limit: int = 5):
        return rank_memories(query, db_path=db_path, limit=limit)
