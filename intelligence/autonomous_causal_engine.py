"""Fully Autonomous Causal Edge Validation, Setup Analysis & Strategy Backtest Engine.

This module automates the validation of macro causal edges by combining:
1. Historical event reaction statistics (event_reactions)
2. Macro source policy tiering (PRIMARY / RUNTIME_FEED)
3. Causal chain graph traversal & emerging setup analysis
4. Broker-realistic historical replay (broker_replay.py) strategy optimization

While causal edge validation and setup discovery run fully autonomously, order intent
creation remains strictly fail-closed (execution_authority = False lock).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from macro_graph import MacroGraph, utcnow
    from macro_source_policy import assess_source
    from broker_replay import ReplaySpec, ReplayIntent, replay
    from causal_setup_map import resolve_causal_setup
    from edge_lifecycle import advance_lifecycle, current_lifecycle
    from research_validation import MIN_PROMOTION_EXPECTANCY_R
except ImportError:
    from intelligence.macro_graph import MacroGraph, utcnow
    from intelligence.macro_source_policy import assess_source
    from research.broker_replay import ReplaySpec, ReplayIntent, replay
    from intelligence.causal_setup_map import resolve_causal_setup
    from research.edge_lifecycle import advance_lifecycle, current_lifecycle
    from research.research_validation import MIN_PROMOTION_EXPECTANCY_R


# Fail closed: never invent a high hit-rate when the sample is empty/small.
MIN_REACTION_SAMPLES = 30
MIN_CONFIDENCE = 0.70
MIN_EMPIRICAL_WIN_RATE = 0.60

# Approved-edge reinforcement: bounded confidence nudges from measured event
# reactions (see AutonomousCausalValidator.reinforce_approved_edges_from_reactions).
# Confirmations and misses net out per pass; the pass delta is capped so a
# burst of reactions cannot swing an approved edge in one pulse.
REINFORCEMENT_STEP = 0.01
REINFORCEMENT_MAX_DELTA_PER_PASS = 0.05
# The band matches the validator's own output range: candidate validation
# clamps to 1.0 via min(1.0, ...), so reinforcement must be monotonic within
# [0.05, 1.0] — confirms never lower a capped edge, misses never raise a
# floored one, and a zero-delta batch never churns the row.
REINFORCEMENT_MIN_CONFIDENCE = 0.05
REINFORCEMENT_MAX_CONFIDENCE = 1.0
REINFORCEMENT_MAX_EDGES_PER_PASS = 25
REINFORCEMENT_SAMPLE_WINDOW = 200
# Reinforcement only applies to edges that already cleared statistical
# validation (or later); CANDIDATE/STAT_FAIL/RETIRED edges stay untouched.
REINFORCEMENT_LIFECYCLE_SET = frozenset(
    {"STAT_PASS", "REPLAY_PASS", "DEMO_MATCH", "TINY_LIVE", "FULL"}
)


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _bar_timestamp(bar: Dict[str, Any]) -> Optional[datetime]:
    if not isinstance(bar, dict):
        return None
    for key in ("timestamp", "time", "date", "observed_at"):
        stamp = _parse_time(bar.get(key))
        if stamp is not None:
            return stamp
    return None


def _bar_index_at_or_after(bars: Sequence[Dict[str, Any]], when: datetime) -> Optional[int]:
    """First completed bar at/after the reaction observation (no look-ahead invent)."""
    for index, bar in enumerate(bars):
        stamp = _bar_timestamp(bar)
        if stamp is not None and stamp >= when:
            return index
    return None


def _directional_hit(expected_sign: str, surprise: float, ret_15m: float) -> Optional[bool]:
    """Directional agreement test, or None when the sign carries no prediction.

    CONDITIONAL / UNKNOWN (or anything else) makes no directional claim, so a
    reaction can neither confirm nor refute it. Callers must SKIP the sample -
    the same treatment this module already gives an unmeasurable return
    (ret_15m is None). Counting it as a hit forces every conditional edge to
    a 100% win rate: auto-approval at `MIN_EMPIRICAL_WIN_RATE`, and a confidence
    that can only ever rise in the reinforcement pass.
    """
    if expected_sign == "POSITIVE":
        return (surprise > 0 and ret_15m >= 0) or (surprise < 0 and ret_15m <= 0)
    if expected_sign == "NEGATIVE":
        return (surprise > 0 and ret_15m <= 0) or (surprise < 0 and ret_15m >= 0)
    return None


def _reaction_directional_return(windows: Any) -> Optional[float]:
    """Parse the directional return from a reaction windows payload.

    Prefers the 15m window (the live capture cadence). Falls back to the 1d
    window ONLY when the payload self-labels daily resolution (backfilled
    reactions measured from a daily tick ledger) — live reactions always
    carry 15m, so their semantics are unchanged. Returns None when neither
    is measurable: callers must treat None as *non-informative* and skip the
    sample rather than default the return to 0.0, which would silently count
    every such reaction as directional agreement and inflate win rates.
    """
    if not isinstance(windows, dict):
        return None
    for name in ("15m", "1d"):
        window = windows.get(name)
        if not isinstance(window, dict):
            continue
        if name == "1d" and window.get("resolution") != "daily":
            continue
        try:
            value = float(window.get("return_fraction"))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value):
            return value
    return None


class AutonomousCausalValidator:
    """Statistical validator that automatically verifies candidate macro causal edges."""

    def __init__(self, graph: MacroGraph, *, min_reaction_samples: int = MIN_REACTION_SAMPLES):
        if not isinstance(min_reaction_samples, int) or isinstance(min_reaction_samples, bool) or min_reaction_samples < 1:
            raise ValueError("min_reaction_samples must be an integer >= 1")
        self.graph = graph
        self.min_reaction_samples = min_reaction_samples

    def validate_candidate_edge(self, edge_id: int) -> Dict[str, Any]:
        """Validate a candidate edge using historical event reaction statistics."""
        with self.graph._con() as con:
            row = con.execute(
                """SELECT e.*, a.name AS source_name, b.name AS target_name, s.source_url, s.publisher, s.payload
                   FROM macro_edges e
                   JOIN macro_entities a ON a.entity_id = e.source_entity
                   JOIN macro_entities b ON b.entity_id = e.target_entity
                   JOIN macro_sources s ON s.source_id = e.source_id
                   WHERE e.edge_id = ? AND e.status = 'CANDIDATE'""",
                (int(edge_id),)
            ).fetchone()

        if not row:
            return {
                "edge_id": edge_id,
                "approved": False,
                "reason": "candidate edge not found or already reviewed",
                "execution_authority": False,
            }

        edge_data = dict(row)
        source_name = edge_data["source_name"]
        target_name = edge_data["target_name"]
        expected_sign = edge_data["sign"]
        initial_confidence = float(edge_data["confidence"])
        source_id = edge_data["source_id"]

        # 1. Source Policy Tier Guard
        policy = assess_source(url=edge_data.get("source_url", ""), publisher=edge_data.get("publisher", ""))
        if not policy["allowed"]:
            return {
                "edge_id": edge_id,
                "approved": False,
                "status": "REJECTED_SOURCE_POLICY",
                "reason": f"unapproved source policy tier: {policy['tier']}",
                "execution_authority": False,
            }

        # 2. Historical Event Reaction Empirical Evidence Check
        with self.graph._con() as con:
            reactions = con.execute(
                "SELECT * FROM event_reactions WHERE event_source_id = ? ORDER BY observed_at DESC",
                (source_id,)
            ).fetchall()

        reaction_list = [dict(r) for r in reactions]
        sample_count = len(reaction_list)

        matching_directions = 0
        valid_samples = 0
        consumed_ids: List[int] = []

        for reaction in reaction_list:
            surprise = reaction.get("surprise")
            if surprise is None:
                continue
            try:
                surprise_value = float(surprise)
            except (TypeError, ValueError, OverflowError):
                continue

            windows: Dict[str, Any] = {}
            raw_windows = reaction.get("windows")
            if isinstance(raw_windows, dict):
                windows = raw_windows
            else:
                try:
                    windows = json.loads(raw_windows or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    windows = {}

            ret_15m = _reaction_directional_return(windows)
            if ret_15m is None:
                # No measurable 15m return: non-informative for directional
                # statistics (defaulting to 0.0 would inflate the win rate).
                continue

            try:
                consumed_ids.append(int(reaction["id"]))
            except (TypeError, ValueError, OverflowError):
                pass

            hit = _directional_hit(expected_sign, surprise_value, ret_15m)
            if hit is None:
                # CONDITIONAL/UNKNOWN sign: not a directional sample at all, so
                # it belongs in neither the numerator nor the denominator.
                continue
            valid_samples += 1
            if hit:
                matching_directions += 1

        # R1: never default a high hit-rate; require a minimum measured sample.
        if valid_samples < self.min_reaction_samples:
            empirical_win_rate = None
            now = utcnow()
            with self.graph._con() as con:
                con.execute(
                    """INSERT INTO macro_edge_history(edge_id, changed_at, action, status, reviewer, payload)
                       VALUES(?, ?, 'INSUFFICIENT_SAMPLE', 'CANDIDATE', 'AUTONOMOUS_VALIDATOR', ?)""",
                    (int(edge_id), now, self.graph._json({
                        "sample_count": sample_count,
                        "valid_samples": valid_samples,
                        "min_reaction_samples": self.min_reaction_samples,
                        "empirical_win_rate": None,
                        "initial_confidence": initial_confidence,
                        "policy_tier": policy["tier"],
                        "reason": "insufficient event reactions for autonomous approval",
                    }))
                )
            return {
                "edge_id": edge_id,
                "approved": False,
                "status": "INSUFFICIENT_SAMPLE",
                "source_name": source_name,
                "target_name": target_name,
                "sample_count": sample_count,
                "valid_samples": valid_samples,
                "min_reaction_samples": self.min_reaction_samples,
                "empirical_win_rate": empirical_win_rate,
                "adjusted_confidence": initial_confidence,
                "reason": (
                    f"need >= {self.min_reaction_samples} reactions with surprise; "
                    f"have {valid_samples}"
                ),
                "lifecycle": advance_lifecycle(
                    self.graph, int(edge_id), "MEASURED",
                    evidence={"valid_samples": valid_samples,
                              "min_reaction_samples": self.min_reaction_samples},
                ),
                "execution_authority": False,
            }

        empirical_win_rate = matching_directions / valid_samples

        # 3. Autonomous Approval Criteria (only after sample floor is met)
        approved = (
            policy["allowed"]
            and initial_confidence >= MIN_CONFIDENCE
            and empirical_win_rate >= MIN_EMPIRICAL_WIN_RATE
        )
        adjusted_confidence = min(
            1.0,
            round(initial_confidence * (0.8 + 0.4 * empirical_win_rate), 4),
        )

        new_status = "AUTO_APPROVED" if approved else "REJECTED_STATISTICAL"
        now = utcnow()

        with self.graph._con() as con:
            con.execute(
                """UPDATE macro_edges SET status = ?, confidence = ?, updated_at = ?,
                   review_by = 'AUTONOMOUS_VALIDATOR', reviewed_at = ?
                   WHERE edge_id = ? AND status = 'CANDIDATE'""",
                (new_status, adjusted_confidence, now, now, int(edge_id))
            )
            con.execute(
                """INSERT INTO macro_edge_history(edge_id, changed_at, action, status, reviewer, payload)
                   VALUES(?, ?, 'AUTO_VALIDATED', ?, 'AUTONOMOUS_VALIDATOR', ?)""",
                (int(edge_id), now, new_status, self.graph._json({
                    "sample_count": sample_count,
                    "valid_samples": valid_samples,
                    "min_reaction_samples": self.min_reaction_samples,
                    "empirical_win_rate": empirical_win_rate,
                    "initial_confidence": initial_confidence,
                    "adjusted_confidence": adjusted_confidence,
                    "reaction_ids": consumed_ids,
                    "policy_tier": policy["tier"],
                }))
            )

        return {
            "edge_id": edge_id,
            "approved": approved,
            "status": new_status,
            "source_name": source_name,
            "target_name": target_name,
            "sample_count": sample_count,
            "valid_samples": valid_samples,
            "min_reaction_samples": self.min_reaction_samples,
            "empirical_win_rate": empirical_win_rate,
            "adjusted_confidence": adjusted_confidence,
            "lifecycle": advance_lifecycle(
                self.graph, int(edge_id),
                "STAT_PASS" if approved else "STAT_FAIL",
                evidence={
                    "empirical_win_rate": empirical_win_rate,
                    "valid_samples": valid_samples,
                    "status": new_status,
                },
            ),
            "execution_authority": False,
        }

    def auto_validate_all_candidates(self) -> List[Dict[str, Any]]:
        """Scan all CANDIDATE edges and run autonomous statistical validation."""
        with self.graph._con() as con:
            rows = con.execute("SELECT edge_id FROM macro_edges WHERE status = 'CANDIDATE'").fetchall()

        return [self.validate_candidate_edge(r["edge_id"]) for r in rows]

    def reinforce_approved_edges_from_reactions(self) -> Dict[str, Any]:
        """Fold measured event-reaction evidence into already-approved edges.

        ``validate_candidate_edge`` only ever transitions CANDIDATE edges, so
        an APPROVED/AUTO_APPROVED edge never absorbed later measurement
        evidence. This pass closes that loop on the maintenance cadence:

        - Every measured reaction (numeric surprise) whose directional outcome
          agrees with the edge sign nudges confidence up; every disagreement
          nudges down. The step is bounded and the per-pass delta is capped,
          clamped to [REINFORCEMENT_MIN_CONFIDENCE, REINFORCEMENT_MAX_CONFIDENCE].
        - Each reaction reinforces a given edge at most once: consumed
          reaction ids are replayed from the edge's REACTION_REINFORCED
          history markers plus the AUTO_VALIDATED pass that approved it.
        - A single pass examines a bounded number of edges (oldest-updated
          first) so the schedule budget cannot be blown by a large graph.
        - Governance-only: never demotes, never retires, never grants
          execution authority, and leaves lifecycle stages untouched.
        """
        with self.graph._con() as con:
            rows = con.execute(
                """SELECT e.edge_id, e.sign, e.confidence, e.source_id, e.status,
                          a.name AS source_name, b.name AS target_name
                   FROM macro_edges e
                   JOIN macro_entities a ON a.entity_id = e.source_entity
                   JOIN macro_entities b ON b.entity_id = e.target_entity
                   WHERE e.status IN ('APPROVED','AUTO_APPROVED')
                   ORDER BY e.updated_at ASC LIMIT ?""",
                (REINFORCEMENT_MAX_EDGES_PER_PASS,),
            ).fetchall()

        reinforced: List[Dict[str, Any]] = []
        errors: List[str] = []
        for row in rows:
            edge_id = int(row["edge_id"])
            try:
                # Status is the authoritative gate; the lifecycle check is
                # belt-and-braces against odd status/lifecycle combinations.
                lifecycle = current_lifecycle(self.graph, edge_id).get("lifecycle")
                if lifecycle not in REINFORCEMENT_LIFECYCLE_SET:
                    continue
                outcome = self._reinforce_single_edge(
                    edge_id, str(row["source_id"]), str(row["sign"]),
                    float(row["confidence"]), str(row["status"]),
                )
            except Exception as exc:  # noqa: BLE001 - one bad edge must not stop the pass
                errors.append(f"edge {edge_id}: {str(exc)[:200]}")
                continue
            if outcome:
                outcome["source_name"] = row["source_name"]
                outcome["target_name"] = row["target_name"]
                reinforced.append(outcome)

        return {
            "edges_examined": len(rows),
            "reinforced_count": len(reinforced),
            "confirmations": sum(int(r.get("confirms", 0)) for r in reinforced),
            "misses": sum(int(r.get("misses", 0)) for r in reinforced),
            "reinforced": reinforced,
            "errors": errors,
            "execution_authority": False,
        }

    def _reinforce_single_edge(self, edge_id: int, source_id: str, expected_sign: str,
                               confidence: float, edge_status: str) -> Optional[Dict[str, Any]]:
        """Apply the exactly-once reaction batch for one approved edge."""
        with self.graph._con() as con:
            # Unbounded on purpose: the exactly-once guarantee is only as good
            # as the completeness of this set. A LIMIT here would silently evict
            # the consumed reaction_ids of older passes, letting them be
            # re-consumed and re-rewarded once they re-enter the sample window.
            marker_rows = con.execute(
                """SELECT action, payload FROM macro_edge_history
                   WHERE edge_id=? AND action IN ('REACTION_REINFORCED','AUTO_VALIDATED')
                   ORDER BY history_id DESC""",
                (edge_id,),
            ).fetchall()
            reaction_rows = [dict(r) for r in con.execute(
                """SELECT id, surprise, windows FROM event_reactions
                   WHERE event_source_id=? ORDER BY observed_at DESC LIMIT ?""",
                (source_id, REINFORCEMENT_SAMPLE_WINDOW),
            ).fetchall()]

        # Exactly-once: union reaction ids across every prior reinforcement
        # marker for this edge, plus the ids the candidate validator itself
        # consumed in its latest AUTO_VALIDATED pass (so the statistical
        # basis of the original approval is not rewarded a second time).
        # Legacy AUTO_VALIDATED markers predate reaction_ids and simply
        # contribute nothing; their reactions fold in once, capped, and are
        # then permanently marked.
        seen: set = set()
        for marker_row in marker_rows:
            try:
                data = json.loads(marker_row["payload"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for value in (data.get("reaction_ids") or []):
                try:
                    seen.add(int(value))
                except (TypeError, ValueError, OverflowError):
                    continue

        confirms = misses = 0
        consumed: List[int] = []
        for reaction in reaction_rows:
            reaction_id = int(reaction["id"])
            if reaction_id in seen:
                continue
            surprise = reaction.get("surprise")
            if surprise is None:
                continue
            try:
                surprise_value = float(surprise)
            except (TypeError, ValueError, OverflowError):
                continue
            consumed.append(reaction_id)  # seen once; never rescanned
            windows: Dict[str, Any] = {}
            raw_windows = reaction.get("windows")
            if isinstance(raw_windows, dict):
                windows = raw_windows
            else:
                try:
                    windows = json.loads(raw_windows or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    windows = {}
            ret = _reaction_directional_return(windows)
            if ret is None:
                # Non-informative reaction (no measurable 15m/daily return):
                # consume-and-mark as neutral so it is not rescanned forever,
                # but it can never directionally confirm or miss.
                continue
            hit = _directional_hit(expected_sign, surprise_value, ret)
            if hit is None:
                # Non-directional sign: the reaction is already consumed above
                # (never rescanned), but it can never confirm or miss -- same
                # rule as an unmeasurable return.
                continue
            if hit:
                confirms += 1
            else:
                misses += 1
        if not consumed:
            return None

        requested_delta = max(-REINFORCEMENT_MAX_DELTA_PER_PASS,
                              min(REINFORCEMENT_MAX_DELTA_PER_PASS,
                                  (confirms - misses) * REINFORCEMENT_STEP))
        new_confidence = round(
            max(REINFORCEMENT_MIN_CONFIDENCE,
                min(REINFORCEMENT_MAX_CONFIDENCE, confidence + requested_delta)), 4)
        # Report the applied delta, not the request: at the band edges the
        # ceiling/floor absorbs part of the request.
        delta = round(new_confidence - confidence, 4)
        now = utcnow()
        with self.graph._con() as con:
            if new_confidence != confidence:
                # A perfectly neutral batch only records its marker so the
                # reactions stay consumed without churning the edge row.
                con.execute(
                    "UPDATE macro_edges SET confidence=?, updated_at=? WHERE edge_id=?",
                    (new_confidence, now, edge_id),
                )
            con.execute(
                """INSERT INTO macro_edge_history(edge_id, changed_at, action, status, reviewer, payload)
                   VALUES(?, ?, 'REACTION_REINFORCED', ?, 'AUTONOMOUS_VALIDATOR', ?)""",
                (edge_id, now, edge_status, self.graph._json({
                    "reaction_ids": consumed,
                    "confirms": confirms,
                    "misses": misses,
                    "confidence_delta": delta,
                    "requested_delta": requested_delta,
                    "previous_confidence": confidence,
                    "new_confidence": new_confidence,
                    "sign": expected_sign,
                    "sample_window": REINFORCEMENT_SAMPLE_WINDOW,
                })),
            )
        return {
            "edge_id": edge_id,
            "direction": "CONFIRM" if confirms > misses else ("MISS" if misses > confirms else "NEUTRAL"),
            "confirms": confirms,
            "misses": misses,
            "confidence_delta": delta,
            "previous_confidence": confidence,
            "new_confidence": new_confidence,
            "reaction_ids": consumed,
        }


class CausalChainSetupAnalyzer:
    """Traverses active causal chains to discover emerging setups and auto-generate proposals."""

    def __init__(self, graph: MacroGraph):
        self.graph = graph

    def discover_emerging_setups(self, symbol: str, active_entities: list[str]) -> List[Dict[str, Any]]:
        """Traverse causal graph nodes to form setup analysis emerging from causal chains."""
        if not active_entities:
            return []

        placeholders = ",".join("?" for _ in active_entities)
        sql = f"""SELECT e.*, a.name AS source_name, a.entity_type AS source_type,
                         b.name AS target_name, b.entity_type AS target_type
                  FROM macro_edges e
                  JOIN macro_entities a ON a.entity_id = e.source_entity
                  JOIN macro_entities b ON b.entity_id = e.target_entity
                  WHERE e.status IN ('APPROVED', 'AUTO_APPROVED')
                    AND (a.name IN ({placeholders}) OR b.name IN ({placeholders}))
                  ORDER BY e.confidence DESC"""

        with self.graph._con() as con:
            rows = con.execute(sql, (*active_entities, *active_entities)).fetchall()

        chain_proposals: List[Dict[str, Any]] = []
        for row in rows:
            r = dict(row)
            source_name = r["source_name"]
            target_name = r["target_name"]
            confidence = float(r["confidence"])
            sign = r["sign"]

            mapped = resolve_causal_setup(
                relation=str(r.get("relation") or ""),
                source_type=str(r.get("source_type") or ""),
                target_type=str(r.get("target_type") or ""),
                source_name=source_name,
                target_name=target_name,
            )
            if not mapped.get("eligible"):
                continue

            proposal_side = "BUY" if sign == "POSITIVE" else "SELL"

            chain_proposals.append({
                "symbol": symbol,
                "causal_chain": f"{source_name} --({r['relation']})--> {target_name}",
                "setup_class": mapped["setup_id"],
                "setup_mapping": mapped,
                "suggested_side": proposal_side,
                "chain_confidence": confidence,
                "edge_id": r["edge_id"],
                "status": "CAUSAL_PROPOSAL_GENERATED",
                "execution_authority": False,
            })

        return chain_proposals


class CausalEdgeBacktestOptimizer:
    """Backtests strategy performance of discovered causal edges against historical replay data."""

    def __init__(self, graph: MacroGraph):
        self.graph = graph

    def _load_edge(self, edge_id: int) -> Optional[Dict[str, Any]]:
        with self.graph._con() as con:
            row = con.execute("SELECT * FROM macro_edges WHERE edge_id = ?", (int(edge_id),)).fetchone()
        return dict(row) if row else None

    def _event_reactions_for_source(self, source_id: str) -> List[Dict[str, Any]]:
        with self.graph._con() as con:
            rows = con.execute(
                """SELECT * FROM event_reactions
                   WHERE event_source_id = ?
                   ORDER BY observed_at ASC""",
                (source_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _build_event_aligned_intents(
        self,
        *,
        edge_id: int,
        side: str,
        bars: Sequence[Dict[str, Any]],
        reactions: Sequence[Dict[str, Any]],
        stop_distance: float = 0.0030,
        reward_multiple: float = 2.0,
        volume: float = 0.1,
    ) -> Tuple[List[ReplayIntent], List[int], Dict[str, int]]:
        """Build replay intents only at measured event-reaction anchors."""
        intents: List[ReplayIntent] = []
        event_indices: List[int] = []
        counters = {
            "reactions_seen": 0,
            "missing_timestamp": 0,
            "unmatched_bar": 0,
            "too_near_end": 0,
            "aligned": 0,
        }

        direction = 1 if side == "BUY" else -1
        for reaction in reactions:
            counters["reactions_seen"] += 1
            when = _parse_time(reaction.get("observed_at"))
            if when is None:
                counters["missing_timestamp"] += 1
                continue
            index = _bar_index_at_or_after(bars, when)
            if index is None:
                counters["unmatched_bar"] += 1
                continue
            # Need room for stop/target evaluation after the event bar.
            if index >= len(bars) - 2:
                counters["too_near_end"] += 1
                continue

            entry_price = float(bars[index].get("close", 0.0) or 0.0)
            if entry_price <= 0:
                counters["unmatched_bar"] += 1
                continue

            stop_loss = entry_price - direction * stop_distance
            take_profit = entry_price + direction * reward_multiple * stop_distance
            intents.append(ReplayIntent(
                ticket=f"edge_{edge_id}_evt_{reaction.get('id', counters['aligned'])}_bar_{index}",
                side=side,
                entry=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                volume=volume,
                submit_index=index,
            ))
            event_indices.append(index)
            counters["aligned"] += 1

        return intents, event_indices, counters

    def backtest_causal_edge_strategy(
        self,
        edge_id: int,
        bars: list[dict[str, Any]],
        spec: ReplaySpec | None = None,
    ) -> Dict[str, Any]:
        """Backtest edge proposals only on event-reaction windows (not uniform bar sampling)."""
        replay_spec = spec or ReplaySpec(
            point=0.00001,
            contract_size=100000.0,
            commission_per_volume=7.0,
            swap_per_volume_day=2.0,
            expected_slippage_price=0.00002,
            spread_price=0.00010,
        )

        edge_data = self._load_edge(edge_id)
        if not edge_data:
            return {
                "edge_id": edge_id,
                "status": "ERROR",
                "reason": "edge not found",
                "execution_authority": False,
            }

        if not bars:
            return {
                "edge_id": edge_id,
                "status": "INSUFFICIENT_BARS",
                "trades_simulated": 0,
                "execution_authority": False,
            }

        side = "BUY" if edge_data["sign"] == "POSITIVE" else "SELL"
        reactions = self._event_reactions_for_source(str(edge_data["source_id"]))
        intents, event_indices, alignment = self._build_event_aligned_intents(
            edge_id=int(edge_id),
            side=side,
            bars=bars,
            reactions=reactions,
        )

        if not intents:
            return {
                "edge_id": edge_id,
                "status": "INSUFFICIENT_EVENT_ANCHORS",
                "trades_simulated": 0,
                "simulated_intents": 0,
                "alignment": alignment,
                "execution_authority": False,
                "reason": (
                    "no event reactions could be aligned to bar timestamps; "
                    "refusing uniform bar sampling"
                ),
                "limitation": (
                    "Backtest requires measured event_reaction anchors; "
                    "execution_authority remains False."
                ),
            }

        replay_result = replay(intents, list(bars), replay_spec, event_indices=event_indices)

        closed_trades = int(replay_result.get("closed_trades", 0) or 0)
        net_pnl = float(replay_result.get("net_pnl", 0.0) or 0.0)
        net_r = float(replay_result.get("net_r", 0.0) or 0.0)
        expectancy_r = (net_r / closed_trades) if closed_trades > 0 else 0.0

        return {
            "edge_id": edge_id,
            "status": "BACKTEST_COMPLETED",
            "simulated_intents": len(intents),
            "event_indices": list(event_indices),
            "alignment": alignment,
            "closed_trades": closed_trades,
            "net_pnl": net_pnl,
            "net_r": net_r,
            "expectancy_r": expectancy_r,
            "execution_authority": False,
            "limitation": (
                "Event-aligned backtest validates feasibility only; "
                "execution_authority remains False."
            ),
        }

    def apply_replay_floors(
        self,
        bars: Sequence[Dict[str, Any]],
        *,
        min_expectancy_r: Optional[float] = None,
        min_closed_trades: int = 1,
    ) -> List[Dict[str, Any]]:
        """Advance STAT_PASS → REPLAY_PASS only when event-aligned backtest clears floors.

        Never grants execution_authority. Missing/insufficient bars skip without
        inventing a pass. Failed floors mark REPLAY_FAIL.
        """
        floor = (
            MIN_PROMOTION_EXPECTANCY_R
            if min_expectancy_r is None
            else float(min_expectancy_r)
        )
        if isinstance(min_closed_trades, bool) or not isinstance(min_closed_trades, int) or min_closed_trades < 1:
            raise ValueError("min_closed_trades must be an integer >= 1")
        if not bars:
            return [{
                "status": "SKIPPED_NO_BARS",
                "advanced": False,
                "execution_authority": False,
                "limitation": "Store causal_replay_bars before replay-floor promotion.",
            }]

        with self.graph._con() as con:
            rows = con.execute(
                """SELECT edge_id FROM macro_edges
                   WHERE status IN ('AUTO_APPROVED', 'APPROVED')
                   ORDER BY edge_id ASC"""
            ).fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            edge_id = int(row["edge_id"])
            life = current_lifecycle(self.graph, edge_id)
            if str(life.get("lifecycle") or "") != "STAT_PASS":
                continue
            backtest = self.backtest_causal_edge_strategy(edge_id, list(bars))
            expectancy = float(backtest.get("expectancy_r") or 0.0)
            closed = int(backtest.get("closed_trades") or 0)
            passed = (
                str(backtest.get("status") or "") == "BACKTEST_COMPLETED"
                and closed >= min_closed_trades
                and expectancy > floor
            )
            target = "REPLAY_PASS" if passed else "REPLAY_FAIL"
            lifecycle = advance_lifecycle(
                self.graph,
                edge_id,
                target,
                reviewer="CAUSAL_REPLAY_FLOOR",
                evidence={
                    "backtest_status": backtest.get("status"),
                    "expectancy_r": expectancy,
                    "closed_trades": closed,
                    "min_expectancy_r": floor,
                    "min_closed_trades": min_closed_trades,
                    "passed": passed,
                },
            )
            results.append({
                "edge_id": edge_id,
                "advanced": bool(lifecycle.get("advanced")),
                "from": lifecycle.get("from"),
                "to": lifecycle.get("to") if lifecycle.get("advanced") else None,
                "passed": passed,
                "expectancy_r": expectancy,
                "closed_trades": closed,
                "backtest_status": backtest.get("status"),
                "lifecycle": lifecycle,
                "execution_authority": False,
            })
        return results
