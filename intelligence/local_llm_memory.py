"""Local journal memory distilled by the trader's already configured LLM.

No third-party memory server is used.  The LLM receives only verified,
source-linked records from the durable outbox and cannot change execution.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def _fallback(kind: str, source_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tags": {"kind": kind, "ticket": source_id, "label": payload.get("label", "UNVERIFIED")},
        "summary": f"Verified {kind} {source_id}: label={payload.get('label', 'n/a')}, r_multiple={payload.get('r_multiple', 'n/a')}",
        "lesson": "Await more correctly labelled examples before treating this as a repeatable pattern.",
    }


def process_outbox(operations, memory, llm, model: str, *, run_id: str = "", limit: int = 5) -> Dict[str, Any]:
    """Use the current trader LLM to create bounded local narratives.

    Each item has already been written to SQLite. LLM failures leave it queued
    for retry, while a deterministic fallback preserves an auditable summary.
    """
    processed = failed = blocked = fallback_saved = outage_failed = 0
    prompt_hashes = []
    response_hashes = []
    response_chars = 0
    for row in operations.pending_memory(limit):
        payload = json.loads(row["payload"])
        if not memory.is_verified_memory_source(row["kind"], row["source_id"], payload):
            operations.block_memory(row["id"], "memory source is not a verified local journal fact")
            operations.event("memory", "distillation_blocked_unverified_source",
                             {"source_id": row["source_id"], "kind": row["kind"]},
                             run_id=run_id, severity="WARNING")
            blocked += 1
            continue
        fallback = _fallback(row["kind"], row["source_id"], payload)
        prompt = (
            "Convert this VERIFIED trading journal record into compact JSON only. "
            "Never invent market facts or claim profitability. Return keys tags (object), "
            "summary (max 450 chars), lesson (max 300 chars). Treat one outcome as "
            "insufficient evidence.\nRECORD:\n" + json.dumps({"kind": row["kind"], "source_id": row["source_id"], "payload": payload}, default=str)
        )
        prompt_hashes.append(hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        item_outage = False
        try:
            raw = llm.generate([
                {"role": "system", "content": "You summarize verified trading records. Output strict JSON only."},
                {"role": "user", "content": prompt},
            ], model=model)
            text = raw if isinstance(raw, str) else str(raw)
            text = text.strip()
            # A deterministic HOLD or provider error string is the router's
            # fail-closed contract, not a model reply. Parsing it as JSON
            # used to yield 'memory response missing required fields' and an
            # endless retry loop during provider outages (source_ids 37-39,
            # five boots in a row). Classify it truthfully instead.
            if text.startswith("Error:") or '"deterministic_fallback": true' in text:
                item_outage = True
                raise ValueError(
                    "provider outage: "
                    + ("deterministic HOLD received" if "deterministic_fallback" in text
                       else "provider error string received")
                    + " instead of a distillation reply")
            response_hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
            response_chars += len(text)
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            distilled = json.loads(text)
            if not isinstance(distilled.get("tags"), dict) or not distilled.get("summary"):
                raise ValueError("memory response missing required fields")
        except Exception as exc:
            # Preserve local evidence and retry later; do not discard the item.
            resulting_status = operations.complete_memory(row["id"], str(exc))
            operations.event("memory", "distillation_failed", {"source_id": row["source_id"], "error": str(exc)}, run_id=run_id, severity="WARNING")
            if resulting_status == "FAILED":
                # Retry budget exhausted: the documented deterministic fallback
                # preserves an auditable summary instead of parking a dead row.
                memory.save_narrative(fallback["tags"], json.dumps(fallback, default=str))
                memory.save_verified_memory(row["source_id"], row["kind"], fallback["tags"],
                                            fallback["summary"], fallback.get("lesson", ""), payload,
                                            confidence=1.0, status="CANDIDATE")
                operations.event("memory", "distillation_fallback_used",
                                 {"source_id": row["source_id"], "kind": row["kind"]},
                                 run_id=run_id, severity="WARNING")
                fallback_saved += 1
            elif item_outage:
                outage_failed += 1
            else:
                failed += 1
            continue
        distilled["source_id"] = row["source_id"]
        distilled["verified_kind"] = row["kind"]
        memory.save_narrative(distilled["tags"], json.dumps(distilled, default=str))
        memory.save_verified_memory(row["source_id"], row["kind"], distilled["tags"],
                                    distilled["summary"], distilled.get("lesson", ""), payload,
                                    confidence=1.0, status="CANDIDATE")
        operations.complete_memory(row["id"])
        operations.event("memory", "distilled_locally", {"source_id": row["source_id"]}, run_id=run_id)
        processed += 1
    # These fields are consumed by the durable llm_requests row. They are
    # aggregate hashes because one reserved memory request can summarize a
    # bounded batch of verified outbox records.
    combine = lambda values: hashlib.sha256("|".join(values).encode("utf-8")).hexdigest() if values else ""
    failures = failed + outage_failed + fallback_saved
    if failures and processed:
        parse_status = "PARTIAL_FAILURE"
    elif failures:
        parse_status = "PROVIDER_OUTAGE" if outage_failed == failures else "FAILURE"
    elif processed:
        parse_status = "BATCH_SUCCESS"
    elif blocked:
        parse_status = "BLOCKED_ONLY"
    else:
        parse_status = "NO_ITEMS"
    return {"processed": processed, "failed": failed, "blocked": blocked,
            "fallback_saved": fallback_saved, "outage_failed": outage_failed,
            "pending": len(operations.pending_memory(limit)),
            "prompt_hash": combine(prompt_hashes),
            "response_hash": combine(response_hashes),
            "attempt_count": processed + failed + outage_failed + fallback_saved,
            "parse_status": parse_status,
            "response_chars": response_chars}
