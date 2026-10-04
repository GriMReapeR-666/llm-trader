"""Per-article Laya System 1 triage for realtime news ingestion.

Runs the Layer-0 decision questions (D0_1 source credibility, D0_2 duplicate
detection, D0_3 fact-vs-opinion) from the macro decision taxonomy over EACH
headline as it arrives from RSS/NewsAPI/Seeking Alpha — one non-autoregressive
forward pass per article batch via ``LayaDecisionModel.decide`` — so that only
credible, fresh, fact-bearing headlines enter the causal graph as research
evidence.

Guarantees (mirrors the macro-cognitive doctrine):
- Deterministic fallback: when the Laya checkpoint is unavailable, disabled via
  NEWS_TRIAGE_MODE=off, or inference fails, a rule-based labeler derived from
  ``assess_source`` policy + text heuristics produces the same three labels.
- Post-validation: Laya answers are cross-checked against the deterministic
  fallback (same spirit as ``DeterministicPostValidator``); disagreements are
  recorded, and the stricter/safer label wins when they conflict.
- Spoof veto: SPOOF_REJECTED or UNVERIFIED_SOCIAL articles never reach the
  graph (the allow-list policy gate stays authoritative upstream).
- No execution authority: triage is advisory filtering for research evidence
  only. Calendar releases remain the sole event-reaction path.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from intelligence.macro_source_policy import assess_source
except ImportError:
    from intelligence.macro_source_policy import assess_source
try:
    from intelligence.economic_dynamics_measure import classify_runtime_event
except ImportError:
    from intelligence.economic_dynamics_measure import classify_runtime_event
from research.decision_models.extended_taxonomy import (
    D0_1_SourceCredibility,
    D0_2_DuplicateDetection,
    D0_3_FactVsOpinion,
    D5a_EscalationLadder,
    R1_HeadlineEventRouting,
)
from research.decision_models.macro_ontology import get_standard_macro_questions

log = logging.getLogger("NewsTriage")

# Questions scored per article (subset of the standard macro taxonomy).
TRIAGE_QUESTION_IDS = (
    "D0_1_source_credibility",
    "D0_2_duplicate_detection",
    "D0_3_fact_vs_opinion",
    "D5a_escalation_ladder",
    "D9_emergency_kill_switch",
    "R1_headline_event_routing",
)

# Deterministic gate actions per D0_1 label.
REJECT_D0_1 = {
    D0_1_SourceCredibility.SPOOF_REJECTED.value,
    D0_1_SourceCredibility.UNVERIFIED_SOCIAL.value,
}

# Wire-domain → D0_1 credibility prior (deterministic baseline).
_TIER_CREDIBILITY = {
    "PRIMARY": D0_1_SourceCredibility.TRUSTED_OFFICIAL,
    "RUNTIME_FEED": D0_1_SourceCredibility.VERIFIED_OUTLET,
    "UNVERIFIED": D0_1_SourceCredibility.UNVERIFIED_SOCIAL,
}

# Official press-release URL hosts that are hard VERIFIED_FACT anchors.
_OFFICIAL_HOSTS = (
    "federalreserve.gov", "bls.gov", "treasury.gov", "ecb.europa.eu",
    "bis.org", "imf.org", "usda.gov", "boj.or.jp", "bankofengland.co.uk",
    "bankofcanada.ca", "fred.stlouisfed.org",
)

# D9 emergency kill-switch tripwire — CRITICAL-tier only, pure lexical, no
# model call. These are flash-crisis event classes whose headlines are the
# earliest available signal; false positives cost missed research evidence
# while false negatives cost capital, so the net is deliberately wide.
_D9_KILL_PATTERN = re.compile(
    r"\b(trading halt(?:ed|s)?|halts? (?:[a-zA-Z]+ )?trading|halts? (?:trading|all trading)|exchange halt|"
    r"circuit breaker(?:s)?(?: triggered| hit)?|markets? closed (?:indefinitely|until further notice)|"
    r"stock exchange closed|de[- ]pegs?|de[- ]pegged|de[- ]pegging|abandons? (?:the )?peg|"
    r"breaks? (?:the )?peg|peg (?:breaks|collapses)|peg (?:is |was )?abandoned|"
    r"emergency (?:rate (?:cut|hike|meeting)|ban|measure|action)|"
    r"flash crash|market crash|freeze(?:s|d)? (?:redemptions|withdrawals)|"
    r"bank runs?|suspends? withdrawals|capital controls)\b",
    re.IGNORECASE,
)

# Guard against false positives: commentary, retrospective, negation, single-name scope
# (measured in research/d9_false_positive_probe.py to suppress 31/33 benign trips with 0 loss)
_D9_GUARD = re.compile(
    r"\b(warn\w*|possible|possibility|forecast|expect\w*|anniversary|revisited|"
    r"retrospective|histor\w*|stud(?:y|ies)|debate\w*|propose\w*|bill|would|whether|"
    r"could|might|\bmay\b|not on the table|no\b|never|no longer|lifts?|lifted|eases?|"
    r"removes?|\bends?\b|\bago\b|of (?:19|20)\d{2}|in (?:19|20)\d{2}|last quarter|"
    r"how |\bwhen\b|\bif\b|review|briefly|blamed|fat-finger|\ba single\b|"
    r"halts? trading (?:in|of|shares)|halt(?:ed|s)? trading in |trade normally|for the day)\b",
    re.IGNORECASE,
)
_D9_PRESENT_CUE = re.compile(
    r"\b(announces?\w*|declares?|imposes?|spreads?|underway|hits?|forces?|plunges?|"
    r"slides?|calls?|triggers?|wipes?|drags?)\b",
    re.IGNORECASE,
)


def _d9_guard_suppresses(text: str) -> bool:
    return bool(_D9_GUARD.search(text)) and not _D9_PRESENT_CUE.search(text)

# D9 headline classes (noul-style booleans with explicit abstain/absent value).
D9_KILL_SWITCH_ACTIVE = "EMERGENCY_HALT_ACTIVE"
D9_KILL_SWITCH_NORMAL = "NORMAL_OPERATION"
D9_KILL_SWITCH_NO_DATA = "INSUFFICIENT_DATA"


# Lexical patterns for deterministic D0_3 classification.
_FACT_PATTERNS = re.compile(
    r"\b(cpi|nfp|payrolls|gdp|ppi|unemployment|jobless|pmi|retail sales|"
    r"rate (?:hold|cut|hike|decision)|bp[s]?|basis point|percent[age]?|"
    r"\d+(?:\.\d+)?%)",
    re.IGNORECASE,
)
_OPINION_PATTERNS = re.compile(
    r"\b(analysts?|economists?|strategists?|expected to|could|may|might|"
    r"would|should|rumor(?:ed)?|reportedly|speculat(?:e|ion|es)|"
    r"bets?|positioning for|seen as|view)\b",
    re.IGNORECASE,
)
_RUMOR_PATTERNS = re.compile(
    r"\b(rumor|unconfirmed|leaked|unverified|according to sources|"
    r"not immediately (?:confirmed|verified))\b",
    re.IGNORECASE,
)
_INJECTION_PATTERNS = re.compile(
    r"(ignore (?:all )?(?:prior|previous) (?:rules|instructions)|"
    r"disregard (?:all )?(?:prior|previous) (?:rules|instructions)|"
    r"choose\s+['\"]?\w+['\"]?\s+immediately|jailbreak|"
    r"system prompt|developer mode)",
    re.IGNORECASE,
)

# Geopolitical escalation ladder (D5a) — ordered from strongest to weakest so
# the FIRST match wins; later (weaker) patterns must not be able to downgrade.
# Each band maps to a D5a_EscalationLadder value; geo-relevance gate below
# decides whether D5a applies to a headline at all.
_D5A_LADDER_PATTERNS: tuple = (
    (D5a_EscalationLadder.FULL_BLOCKADE, re.compile(
        r"\b(full blockade|complete blockade|total blockade|all ports closed|"
        r"closes? all (?:ports|shipping)|seals? (?:the )?straits?)\b",
        re.IGNORECASE,
    )),
    (D5a_EscalationLadder.CHOKEPOINT_DISRUPTION, re.compile(
        r"\b(chokepoint|strait of hormuz|bab el[- ]mandab|suez (?:canal )?(?:closed|blocked|disrupted)|"
        r"panama canal (?:closed|blocked|disrupted)|malacca (?:strait )?(?:closed|blocked)|"
        r"pipeline (?:attack|sabotage|blast|explosion|damaged|disrupted)|"
        r"tanker (?:attack|seized|stuck|stranded)|mine(?:s|d)? (?:the )?(?:waters|shipping lane)|"
        r"shipping (?:lane )?(?:closed|halted|suspended|disrupted)|port closed|maritime blockade)\b",
        re.IGNORECASE,
    )),
    (D5a_EscalationLadder.DIRECT_STRIKE, re.compile(
        r"\b(air ?strike[s]?|missile (?:strike|attack)s?|drone (?:strike|attack)s?|"
        r"artillery (?:strike|fire|barrage)|invasion|invades?|invaded|"
        r"declares? war|declaration of war|troops (?:cross|enter|invade)|"
        r"military (?:assault|offensive|incursion)|strike[s]? on (?:military|nuclear|airbase|capital)|"
        r"shell(?:ing|s|ed)? (?:border|city|town)|direct (?:strike|attack|clash))\b",
        re.IGNORECASE,
    )),
    (D5a_EscalationLadder.PROXY_CLASH, re.compile(
        r"\b(proxy (?:war|fight|clash|force|militia)|militia (?:clash|offensive|attack)|"
        r"border (?:clash|skirmish|incursion)|skirmish|regional (?:clash|fighting)|"
        r"armed (?:clash|group) (?:erupts?|attacks?)|rebels? (?:seize|attack|clash)|"
        r"insurgent|civil war|coup (?:attempt|d'?\u00e9tat))\b",
        re.IGNORECASE,
    )),
    (D5a_EscalationLadder.RHETORIC_ONLY, re.compile(
        r"\b(threat(?:s|ens|ened)?|warn(?:s|ed|ings)?|rhetoric|sabre[- ]rattling|"
        r"escalat(?:es|ion|ing) tensions|tensions? (?:rise|escalate|mount|grow)|"
        r"verbal (?:attack|volley)|condemn(?:s|ed|ation)|sanction threats|"
        r"ultimatum|ultimatums|denounces?|vows? (?:retaliation|revenge))\b",
        re.IGNORECASE,
    )),
)

# A headline must look geopolitically relevant before D5a is scored at all.
_GEO_RELEVANCE_PATTERNS = re.compile(
    r"\b(war|military|missile|strike|attack|navy|naval|troops|drone|tanker|"
    r"oil tanker|pipeline|strait|chokepoint|blockade|port|shipping|border|"
    r"geopolit(?:ical|ics)|sanctions?|iran|russia|ukraine|israel|gaza|"
    r"china taiwan|taiwan|north korea|houthi|nato|ceasefire|"
    r"retaliation|sabre[- ]rattling|militia|invade|invasion|airbase)\b",
    re.IGNORECASE,
)

# Headline -> event-class routing (R1). Mirrors the calendar token map in
# economic_dynamics_measure._EVENT_TOKEN_MAP so the model's routing can be
# post-validated against the deterministic lexical router. Order matters only
# for determinism of the FIRST match; all concrete classes are equally ranked.
_R1_TOKEN_MAP: tuple = (
    (("CPI", "INFLATION", "PCE", "HICP"), R1_HeadlineEventRouting.EVENT_CPI),
    (("NFP", "NONFARM", "NON-FARM", "PAYROLL", "JOBLESS", "UNEMPLOYMENT", "LABOR"), R1_HeadlineEventRouting.EVENT_NFP),
    (("FOMC", "RATE DECISION", "INTEREST RATE", "ECB", "BOJ", "MPC", "FED MINUTES", "POWELL", "CENTRAL BANK"), R1_HeadlineEventRouting.EVENT_CB_MEETING),
    (("WAR", "MISSILE", "STRIKE", "INVASION", "GEOPOLIT", "AIRBASE", "BLOCKADE", "CHOKEPOINT"), R1_HeadlineEventRouting.EVENT_GEO_SHOCK),
    (("BANK", "SVB", "FDIC", "CREDIT SUISSE", "DEPOSIT"), R1_HeadlineEventRouting.EVENT_BANKING_STRESS),
    (("TARIFF", "TRADE WAR", "SANCTION"), R1_HeadlineEventRouting.EVENT_TRADE_WAR),
    (("ELECTION", "IMPEACH", "EXECUTIVE ORDER", "TWEET", "TRUTH SOCIAL"), R1_HeadlineEventRouting.EVENT_POLITICAL),
    (("GOLD RESERVE", "GOLD BUYING", "PBOC GOLD", "CENTRAL BANK GOLD", "GOLD ACCUMULATION", "GOLD STOCKPILE"), R1_HeadlineEventRouting.EVENT_CB_GOLD),
)

_R1_CONCRETE = {v.value for _, v in _R1_TOKEN_MAP}


def _deterministic_r1(article: Dict[str, Any]) -> str:
    """Lexical R1 routing; NOT_EVENT_NOISE when no event class matches."""
    title = str(article.get("title") or "").upper()
    if not title:
        title = str(article.get("text_or_summary") or "").upper()
    for tokens, value in _R1_TOKEN_MAP:
        if any(tok in title for tok in tokens):
            return value.value
    return R1_HeadlineEventRouting.NOT_EVENT_NOISE.value


@dataclass
class TriageVerdict:
    """Per-article triage outcome (advisory; never grants authority)."""
    d0_1: str
    d0_2: str
    d0_3: str
    d5a: str = D5a_EscalationLadder.INSUFFICIENT_DATA.value  # geopolitical ladder (INSUFFICIENT_DATA = not geo)
    d9: str = D9_KILL_SWITCH_NO_DATA  # emergency kill-switch tripwire (noul-style)
    r1: str = R1_HeadlineEventRouting.INSUFFICIENT_DATA.value  # headline event-class routing
    source: str = ""                # "laya" | "deterministic"
    action: str = ""                # "INGEST" | "TAG" | "REJECT"
    confidence: float = 0.0
    laya_probabilities: Dict[str, Dict[str, float]] = field(default_factory=dict)
    laya_confidences: Dict[str, float] = field(default_factory=dict)
    laya_answers: Dict[str, str] = field(default_factory=dict)   # raw model labels (pre-validation)
    deterministic_labels: Dict[str, str] = field(default_factory=dict)  # rule labels (pre-validation)
    prompt_state: Dict[str, Any] = field(default_factory=dict)   # model input context (not serialized)
    disagreement_ids: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "d0_1": self.d0_1,
            "d0_2": self.d0_2,
            "d0_3": self.d0_3,
            "d5a": self.d5a,
            "d9_kill_switch": self.d9,
            "r1": self.r1,
            "source": self.source,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "laya_answers": dict(self.laya_answers),
            "disagreements": self.disagreement_ids,
            "latency_ms": round(self.latency_ms, 2),
            "reasons": self.reasons[:4],
            "execution_authority": False,
        }


def triage_mode() -> str:
    """Resolve triage mode from NEWS_TRIAGE_MODE: auto (default) | laya | off."""
    return str(os.environ.get("NEWS_TRIAGE_MODE", "auto")).strip().lower() or "auto"


def dedupe_key_for(url: str, title: str) -> str:
    """Single owner of the cross-path article dedupe key: sha256(url|title)[:24].

    Every collection path keys rows with this formula so the same headline
    seen on one path collides with the same headline on another (D0_2).
    """
    return hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()[:24]


def project_article(
    *,
    title: Any = "",
    url: Any = "",
    publisher: Any = "",
    provider: Any = "",
    published_at: Any = "",
    text_or_summary: Any = "",
) -> Dict[str, Any]:
    """Single owner of the Layer-0 article field contract.

    Every collection path projects its rows through this function so the
    triage gate sees identical shapes regardless of origin: the body lives in
    ``text_or_summary`` (D0_1's injection scan and D0_3 read it), the publisher
    in ``publisher`` (assess_source), and ``dedupe_key`` follows
    ``dedupe_key_for`` (D0_2 cross-path collision). Callers resolve their own
    input aliases (``event``→title, ``description``→body, ...) before calling
    and keep provider-specific extras on the row after merging the projection.
    """
    t = str(title or "").strip()[:400]
    u = str(url or "").strip()[:2000]
    pub = str(publisher or "").strip()[:200] or str(provider or "")
    prov = str(provider or "").strip()[:80] or "unknown"
    return {
        "title": t,
        "url": u,
        "publisher": pub,
        "provider": prov,
        "published_at": str(published_at or "").strip()[:80],
        "text_or_summary": str(text_or_summary or "").strip()[:4000],
        "source_policy": assess_source(u, pub),
        "event_class": classify_runtime_event({"event": t, "title": t}),
        "execution_authority": False,
        "dedupe_key": dedupe_key_for(u, t),
    }


class TriageGate:
    """Shared Layer-0 gate application for every collection path.

    Owns the parts that must not drift between paths: whether the gate is
    active (``triage_enabled`` + ``NEWS_TRIAGE_MODE``), applying
    ``triage_articles`` with the active-learning sink, REJECT enforcement,
    and the rejected count. Callers keep everything path-local: their own
    row projection, policy checks, payload shapes, and per-row dedupe.
    """

    def __init__(self, *, triage_enabled: bool = True, learning_logger: Any = None) -> None:
        self.active = bool(triage_enabled) and triage_mode() != "off"
        self.report: Optional[Dict[str, Any]] = None
        self.rejected = 0
        self._on_verdict = None
        if learning_logger is not None and callable(
            getattr(learning_logger, "log_verdict", None)
        ):
            self._on_verdict = learning_logger.log_verdict

    def apply(self, rows: List[Dict[str, Any]]) -> None:
        """Triage ``rows`` in place when active; no-op otherwise."""
        self.rejected = 0
        if self.active:
            self.report = triage_articles(rows, on_verdict=self._on_verdict)
            self.rejected = sum(
                1 for r in rows if (r.get("triage") or {}).get("action") == "REJECT"
            )

    def is_rejected(self, article: Dict[str, Any]) -> bool:
        """True when the gate is active and the row's verdict is REJECT."""
        if not self.active or self.report is None:
            return False
        return (article.get("triage") or {}).get("action") == "REJECT"


# Evidence-quality constants shared by the cycle summary and consumers.
EVIDENCE_FACT_WEIGHT = 1.0
EVIDENCE_OPINION_WEIGHT = 0.5


def summarize_cycle_evidence(
    articles: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Aggregate per-article triage verdicts into a cycle evidence-quality summary.

    Input: the same article dicts ``triage_articles`` annotated (each with a
    ``"triage"`` verdict dict). Returns None when no triage data exists so
    callers can skip the field entirely.
    """
    total = 0
    ingested = 0
    rejected = 0
    tagged = 0
    fact_count = 0
    opinion_count = 0
    rumor_count = 0
    official_count = 0
    stale_repeat = 0
    confidence_sum = 0.0
    laya_count = 0
    geo_total = 0
    max_escalation_rank = 0
    max_escalation = D5a_EscalationLadder.INSUFFICIENT_DATA.value
    r1_class_counts: Dict[str, int] = {}
    d9_kill_count = 0

    for article in articles:
        if not isinstance(article, dict):
            continue
        v = article.get("triage")
        if not isinstance(v, dict):
            continue
        total += 1
        action = str(v.get("action") or "")
        if action == "INGEST":
            ingested += 1
        elif action == "REJECT":
            rejected += 1
        elif action == "TAG":
            tagged += 1
        d03 = str(v.get("d0_3") or "").upper()
        if d03 == "VERIFIED_FACT":
            fact_count += 1
        elif d03 == "OFFICIAL_STATEMENT":
            official_count += 1
        elif d03 == "ANALYST_OPINION":
            opinion_count += 1
        elif d03 == "RUMOR_UNCONFIRMED":
            rumor_count += 1
        if str(v.get("d0_2") or "").upper() == "STALE_REPEAT":
            stale_repeat += 1
        d5a = str(v.get("d5a") or "").upper()
        if d5a and d5a != D5a_EscalationLadder.INSUFFICIENT_DATA.value:
            geo_total += 1
            rank = D5A_RANK.get(d5a, 0)
            if rank > max_escalation_rank:
                max_escalation_rank = rank
                max_escalation = d5a
        r1 = str(v.get("r1") or "").upper()
        if r1 in _R1_CONCRETE:
            r1_class_counts[r1] = r1_class_counts.get(r1, 0) + 1
        if str(v.get("d9_kill_switch") or v.get("d9") or "").upper() == D9_KILL_SWITCH_ACTIVE:
            d9_kill_count += 1
        try:
            confidence_sum += float(v.get("confidence") or 0.0)
        except (TypeError, ValueError):
            pass
        if str(v.get("source") or "") == "laya":
            laya_count += 1

    if total == 0:
        return None

    credible_total = fact_count + official_count
    denom = max(1, total)
    fact_ratio = round(credible_total / denom, 3)
    evidence_score = round(
        (credible_total * EVIDENCE_FACT_WEIGHT + opinion_count * EVIDENCE_OPINION_WEIGHT) / denom,
        3,
    )
    avg_confidence = round(confidence_sum / denom, 3)

    if rejected > 0 and credible_total == 0:
        quality = "POOR"
    elif (opinion_count + rumor_count) > credible_total:
        quality = "LOW"
    elif fact_ratio >= 0.7:
        quality = "HIGH"
    else:
        quality = "MIXED"

    return {
        "total": total,
        "ingested": ingested,
        "tagged": tagged,
        "rejected": rejected,
        "stale_repeat": stale_repeat,
        "fact_count": fact_count,
        "official_statement_count": official_count,
        "opinion_count": opinion_count,
        "rumor_count": rumor_count,
        "fact_ratio": fact_ratio,
        "evidence_score": evidence_score,
        "avg_confidence": avg_confidence,
        "quality": quality,
        "mode": "laya" if laya_count > 0 else "deterministic",
        "geo_total": geo_total,
        "max_escalation": max_escalation,
        "r1_class_counts": dict(r1_class_counts),
        "dominant_event_class": (
            max(r1_class_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if r1_class_counts else None
        ),
        "d9_kill_switch_count": d9_kill_count,
        "d9_kill_switch_active": d9_kill_count > 0,
        "execution_authority": False,
    }


_LAYA_CACHED = False
_LAYA_INSTANCE = None
_LAYA_LOGGED_SKIP = False

# --- CPU auto-enable guards (2026-09-28) -------------------------------------
# The auto-mode CPU skip was absolute: torch-without-CUDA meant the model
# never loaded, even when the box had ample free RAM for the ~1.6GB fp32
# checkpoint. It is now conditional: a free-RAM preflight, a hard per-batch
# article cap, and a wall-clock budget bound the cost of CPU forward passes
# (the historical failure was EVERY article × ~19s ≈ a 20+ min stall, not
# the model itself). NEWS_TRIAGE_MODE=laya still opts in unconditionally;
# NEWS_TRIAGE_MODE=off stays deterministic-only.


def _free_ram_gb() -> float:
    """Best-effort available RAM (GB); inf when unknowable (don't block)."""
    try:
        import psutil
        return float(psutil.virtual_memory().available) / (1024 ** 3)
    except Exception:
        pass
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return float(stat.ullAvailPhys) / (1024 ** 3)
    except Exception:
        pass
    return float("inf")


# A load needs headroom for the checkpoint AND the box: below this, the
# deterministic labeler wins (torch allocation failures were seen crashing
# whole processes at ~1.0GB free on this machine, 2026-09-28).
MIN_FREE_RAM_GB = float(os.getenv("NEWS_TRIAGE_MIN_FREE_RAM_GB", "1.8"))
# Hard per-batch cap on model attempts: bounds worst-case latency to
# cap × per-pass time regardless of batch size.
_LAYA_MAX_ARTICLES_DEFAULT = max(1, int(os.getenv("NEWS_TRIAGE_LAYA_MAX_ARTICLES", "12")))
# Wall-clock budget for the model path within one batch.
_LAYA_TIME_BUDGET_S = float(os.getenv("NEWS_TRIAGE_LAYA_TIME_BUDGET_S", "60"))

# Runtime circuit: repeated decide() failures in consecutive batches park
# the model path for a cooldown instead of paying the failure cost per batch.
_LAYA_ERROR_STREAK = 0
_LAYA_CIRCUIT_OPEN_UNTIL = 0.0
_LAYA_CIRCUIT_LOGGED = False
_LAYA_CIRCUIT_COOLDOWN_S = 900.0


def reset_laya_triage_state() -> None:
    """Clear the cached model/skip latch + runtime circuit (ops retry lever)."""
    global _LAYA_CACHED, _LAYA_INSTANCE, _LAYA_ERROR_STREAK
    global _LAYA_CIRCUIT_OPEN_UNTIL, _LAYA_CIRCUIT_LOGGED
    _LAYA_CACHED = False
    _LAYA_INSTANCE = None
    _LAYA_ERROR_STREAK = 0
    _LAYA_CIRCUIT_OPEN_UNTIL = 0.0
    _LAYA_CIRCUIT_LOGGED = False


def _get_laya():
    """Best-effort Laya singleton; returns None when offline/disabled."""
    global _LAYA_CACHED, _LAYA_INSTANCE, _LAYA_LOGGED_SKIP
    if _LAYA_CACHED:
        return _LAYA_INSTANCE

    mode = triage_mode()
    if mode == "off":
        _LAYA_CACHED = True
        _LAYA_INSTANCE = None
        return None
    if mode == "auto":
        # CPU auto-enable: attempt the model when the box has RAM headroom for
        # the fp32 checkpoint; the batch cap + time budget keep a full article
        # flood from stalling the cycle (the original reason for this skip).
        # NEWS_TRIAGE_MODE=laya opts in unconditionally (no RAM gate).
        try:
            import torch
            if not torch.cuda.is_available():
                free_gb = _free_ram_gb()
                if free_gb < MIN_FREE_RAM_GB:
                    if not _LAYA_LOGGED_SKIP:
                        log.info(
                            "Laya triage skipped (auto mode, CPU, free RAM "
                            "%.2fGB below %.1fGB; deterministic rule triage active)",
                            free_gb, MIN_FREE_RAM_GB)
                        _LAYA_LOGGED_SKIP = True
                    _LAYA_CACHED = True
                    _LAYA_INSTANCE = None
                    return None
                log.info(
                    "Laya triage enabled (auto mode, CPU, %.2fGB free; "
                    "batch capped at %d articles, %.0fs budget)",
                    free_gb, _LAYA_MAX_ARTICLES_DEFAULT, _LAYA_TIME_BUDGET_S)
        except Exception:
            _LAYA_CACHED = True
            _LAYA_INSTANCE = None
            return None
    try:
        if os.environ.get("FAST_TEST"):
            _LAYA_CACHED = True
            _LAYA_INSTANCE = None
            return None
        if not os.path.exists("models/laya/model.safetensors"):
            _LAYA_CACHED = True
            _LAYA_INSTANCE = None
            return None
        from research.decision_models.laya_model import get_laya_decision_model
        _LAYA_INSTANCE = get_laya_decision_model("models/laya")
        _LAYA_CACHED = True
        return _LAYA_INSTANCE
    except Exception as exc:
        log.debug("Laya triage unavailable, deterministic fallback active: %s", exc)
        _LAYA_CACHED = True
        _LAYA_INSTANCE = None
        return None



def _article_state(article: Dict[str, Any], policy: Dict[str, Any], seen_keys: set) -> Dict[str, Any]:
    """Build the Laya prompt state for one article (mirrors to_prompt_state shape)."""
    title = str(article.get("title") or "")
    summary = str(article.get("text_or_summary") or "")
    text = " ".join((title, summary))
    publisher = str(article.get("publisher") or article.get("provider") or "")
    return {
        "event": {
            "name": title[:240],
            "currency": "USD",
            "tier": "HIGH" if policy.get("tier") == "PRIMARY" else "MEDIUM",
            "timestamp": str(article.get("published_at") or ""),
        },
        "headline": title[:400],
        "narrative": summary[:600],
        "source": {
            "publisher": publisher[:120],
            "provider": str(article.get("provider") or "")[:80],
            "policy_tier": str(policy.get("tier") or "UNVERIFIED"),
            "category": str(policy.get("category") or "unverified"),
            "domain": str(policy.get("domain") or ""),
        },
        "dedupe": {
            "seen_before": bool(article.get("dedupe_key") in seen_keys) if article.get("dedupe_key") else False,
        },
        "geopolitical": {
            "escalation_signals": bool(
                _GEO_RELEVANCE_PATTERNS.search(text)
                or any(p.search(text) for _, p in _D5A_LADDER_PATTERNS)
            ),
        },
        "routing": {
            "event_classes": sorted(_R1_CONCRETE),
        },
        "time_context": {"minutes_to_release": 999.0, "is_blackout_active": False},
    }


def _triage_questions() -> Dict[str, Dict[str, Any]]:
    std = get_standard_macro_questions()
    return {qid: std[qid] for qid in TRIAGE_QUESTION_IDS if qid in std}


def _deterministic_d5a(article: Dict[str, Any]) -> str:
    """Lexical D5a escalation ladder with explicit abstention.

    INSUFFICIENT_DATA unless the headline is geopolitically relevant AND at
    least one ladder band matches; the strongest matching band wins.
    """
    text = " ".join((
        str(article.get("title") or ""),
        str(article.get("text_or_summary") or ""),
    ))
    if not _GEO_RELEVANCE_PATTERNS.search(text):
        return D5a_EscalationLadder.INSUFFICIENT_DATA.value
    for value, pattern in _D5A_LADDER_PATTERNS:
        if pattern.search(text):
            return value.value
    return D5a_EscalationLadder.RHETORIC_ONLY.value


def _deterministic_d9(article: Dict[str, Any]) -> str:
    """Near-zero-latency D9 tripwire: lexical kill-switch detection.

    No model call, no policy lookup — runs on every headline even when the
    Laya path is offline. Kill-switch headlines are flash-crisis event
    classes whose earliest signal is the headline itself.
    """
    text = " ".join((
        str(article.get("title") or ""),
        str(article.get("text_or_summary") or ""),
    ))
    if _D9_KILL_PATTERN.search(text):
        if not _d9_guard_suppresses(text):
            return D9_KILL_SWITCH_ACTIVE
    if not text.strip():
        return D9_KILL_SWITCH_NO_DATA
    return D9_KILL_SWITCH_NORMAL


# Model noul answers mapped to the D9 boolean. Tolerant to the label spellings
# a noul head may emit (boolean words or the enum value itself).
_D9_TRUTHY = {"TRUE", "YES", "1", "EMERGENCY_HALT_ACTIVE", "KILL_SWITCH_ACTIVE"}


# ===========================================================================
# Model vocabulary -> gate enum vocabulary.
#
# The checkpoint was trained on Convai's generic decision ontology, NOT on this
# repo's 35-head taxonomy. Measured over 2269 real production articles, the
# model's D0_1/D0_2 answer sets were COMPLETELY DISJOINT from the gate enums
# (``WIRE_SERVICE``/``SPIN_NARRATIVE``/``OFFICIAL_VERIFIED``/``UNVERIFIED_LEAK``
# vs ``TRUSTED_OFFICIAL``/``VERIFIED_OUTLET``/... ; ``FRESH_EVENT``/
# ``FULLY_PRICED``/``PARTIAL_OVERLAP`` vs ``FIRST_REPORT``/``STALE_REPEAT``/...).
# Every answer therefore failed the ``val not in known`` check in
# ``_pick_better`` and silently deferred — the model path was 100% inert on
# both heads (see artifacts/laya_parity_report.json).
#
# These maps translate the model's generic labels onto the gate enums so the
# head is no longer inert. Only genuine semantic equivalents are mapped; a
# label with no counterpart abstains and defers to the deterministic labeler.
# ===========================================================================

_D0_1_MODEL_VOCAB: Dict[str, str] = {
    # Vendor "official source" == our central-bank / gov-stat credibility tier.
    "OFFICIAL_VERIFIED": D0_1_SourceCredibility.TRUSTED_OFFICIAL.value,
    "OFFICIAL_STATEMENT": D0_1_SourceCredibility.TRUSTED_OFFICIAL.value,
    "VERIFIED_FACT": D0_1_SourceCredibility.VERIFIED_OUTLET.value,
    # Vendor "wire service" == confirmed outlet.
    "WIRE_SERVICE": D0_1_SourceCredibility.VERIFIED_OUTLET.value,
    "VERIFIED_OUTLET": D0_1_SourceCredibility.VERIFIED_OUTLET.value,
    # Vendor "spin / promotional" has no credibility-tier analogue: the domain
    # allow-list in assess_source() already governs reachability, so it maps to
    # the outlet tier and is left for D0_3 to classify as opinion.
    "SPIN_NARRATIVE": D0_1_SourceCredibility.VERIFIED_OUTLET.value,
    # Unverified wire leak == unverified source. Mapping this way is the safe
    # direction (it can only tighten the gate, never loosen it).
    "UNVERIFIED_LEAK": D0_1_SourceCredibility.UNVERIFIED_SOCIAL.value,
    "UNVERIFIED_SOCIAL": D0_1_SourceCredibility.UNVERIFIED_SOCIAL.value,
}

_D0_2_MODEL_VOCAB: Dict[str, str] = {
    "FRESH_EVENT": D0_2_DuplicateDetection.FIRST_REPORT.value,
    "NEW_EVENT": D0_2_DuplicateDetection.FIRST_REPORT.value,
    "FIRST_REPORT": D0_2_DuplicateDetection.FIRST_REPORT.value,
    # Overlapping but not identical == corroborating update carrying new data.
    "PARTIAL_OVERLAP": D0_2_DuplicateDetection.CONFIRMED_UPDATE.value,
    "CONFIRMED_UPDATE": D0_2_DuplicateDetection.CONFIRMED_UPDATE.value,
    # NOTE: "FULLY_PRICED" is a market-pricing concept, not a dedup concept.
    # It was the model's single most common D0_2 answer (1376/2269) but has no
    # honest D0_2 counterpart, so it deliberately abstains and defers. Mapping
    # it to STALE_REPEAT would have changed INGEST -> TAG on 61% of articles on
    # the strength of an unrelated label. If you want pricing-aware ingestion
    # gating, add D1b_priced_in_asymmetry as its own head with its own eval.
}

_MODEL_VOCAB_MAPS: Dict[str, Dict[str, str]] = {
    "D0_1_source_credibility": _D0_1_MODEL_VOCAB,
    "D0_2_duplicate_detection": _D0_2_MODEL_VOCAB,
}


def _map_model_label(qid: str, value: Any) -> str:
    """Normalize a raw model label, translating vendor vocab to gate vocab.

    Returns the upper-cased label, or "" when the model abstained. Labels with
    no entry in the head's map pass through unchanged (D0_3/D5a/D9/R1 already
    speak the gate vocabulary).
    """
    val = str(value or "").strip().upper()
    if not val:
        return ""
    return _MODEL_VOCAB_MAPS.get(qid, {}).get(val, val)


# ===========================================================================
# Model escalation authority.
#
# SAFETY FINDING (2026-10-01, measured over 2269 real production articles):
# the "monotone escalation" rule in ``_pick_better`` let the model raise D5a
# and D9 severity but never lower it. With a 52% decision accuracy the model
# fabricated 517 escalations, EVERY ONE of them an escalation:
#     D5a FULL_BLOCKADE  303/2269 (13.4%)  vs deterministic 0/428 (0.0%)
#     D9 EMERGENCY_HALT  214/2269 (9.4%)  vs deterministic 0/428 (0.0%)
# Examples: FULL_BLOCKADE on "VNET Group: A Play On China's Data Center
# Boom"; EMERGENCY_HALT on "Why AutoZone Stock Rallied Today" and "Federal
# Reserve Board announces approval of application by BancFirst Corporation".
#
# Both labels are veto triggers (``extended_taxonomy.create_decision_bundle``):
# a fabricated one drives risk_multiplier to 0.0 and freezes the cycle. The
# guardrail was protecting against the model MISSING risk while giving it
# unlimited power to FABRICATE risk — strictly the wrong asymmetry for a
# 52%-accurate classifier.
#
# Authority is therefore withheld from every head by default. The lexical
# tripwire remains the sole escalation path: it is deterministic, auditable,
# and empirically produced zero false positives on the same corpus.
# Re-granting escalation to a specific head is a one-line change here and
# should require an accuracy figure from scripts/score_triage_corpus.py.
# ===========================================================================
MODEL_ESCALATION_AUTHORITY: frozenset = frozenset()


def _deterministic_labels(
    article: Dict[str, Any],
    policy: Dict[str, Any],
    seen_keys: set,
) -> Dict[str, str]:
    title = str(article.get("title") or "")
    text = " ".join((title, str(article.get("text_or_summary") or "")))
    host = str(policy.get("domain") or "")
    d5a = _deterministic_d5a(article)

    # D0_1: adversarial content → spoof; blocked/unknown domain → unverified;
    # policy tier → official/wire prior.
    if _INJECTION_PATTERNS.search(text):
        d0_1 = D0_1_SourceCredibility.SPOOF_REJECTED
    elif policy.get("allowed") is not True:
        d0_1 = D0_1_SourceCredibility.UNVERIFIED_SOCIAL
    else:
        d0_1 = _TIER_CREDIBILITY.get(str(policy.get("tier") or ""), D0_1_SourceCredibility.VERIFIED_OUTLET)

    # D0_2: exact dedupe key seen before → stale repeat; else first report.
    key = article.get("dedupe_key")
    if key and key in seen_keys:
        d0_2 = D0_2_DuplicateDetection.STALE_REPEAT
    else:
        d0_2 = D0_2_DuplicateDetection.FIRST_REPORT

    # D0_3: official host or hard numeric print → fact; rumor lexical → rumor;
    # opinion lexical dominance → opinion; else official statement for
    # PRIMARY tiers, opinion for wires.
    if _INJECTION_PATTERNS.search(text):
        d0_3 = D0_3_FactVsOpinion.RUMOR_UNCONFIRMED
    elif any(h in host for h in _OFFICIAL_HOSTS) or policy.get("tier") == "PRIMARY":
        d0_3 = D0_3_FactVsOpinion.OFFICIAL_STATEMENT
    elif _RUMOR_PATTERNS.search(text):
        d0_3 = D0_3_FactVsOpinion.RUMOR_UNCONFIRMED
    else:
        fact_hits = len(_FACT_PATTERNS.findall(text))
        opinion_hits = len(_OPINION_PATTERNS.findall(text))
        if _FACT_PATTERNS.search(title) and fact_hits >= opinion_hits:
            d0_3 = D0_3_FactVsOpinion.VERIFIED_FACT
        elif opinion_hits > fact_hits:
            d0_3 = D0_3_FactVsOpinion.ANALYST_OPINION
        elif fact_hits > 0:
            d0_3 = D0_3_FactVsOpinion.VERIFIED_FACT
        else:
            d0_3 = D0_3_FactVsOpinion.ANALYST_OPINION

    return {
        "D0_1_source_credibility": d0_1,
        "D0_2_duplicate_detection": d0_2,
        "D0_3_fact_vs_opinion": d0_3,
        "D5a_escalation_ladder": d5a,
        "D9_emergency_kill_switch": _deterministic_d9(article),
        "R1_headline_event_routing": _deterministic_r1(article),
    }


def _resolve_action(d0_1: str, d0_2: str, d0_3: str, policy_allowed: bool) -> str:
    """Gate action: REJECT (spoof/social/allow-list fail), TAG (opinion/rumor/dup), INGEST."""
    if d0_1 in REJECT_D0_1 or not policy_allowed:
        return "REJECT"
    if d0_2 == D0_2_DuplicateDetection.STALE_REPEAT.value:
        return "TAG"
    if d0_3 in (
        D0_3_FactVsOpinion.ANALYST_OPINION.value,
        D0_3_FactVsOpinion.RUMOR_UNCONFIRMED.value,
    ):
        return "TAG"
    return "INGEST"


# Ladder ranks for the monotone D5a floor: the model may only escalate.
# INSUFFICIENT_DATA is explicitly rank 0 (abstention never counts as escalation).
D5A_RANK = {
    D5a_EscalationLadder.INSUFFICIENT_DATA.value: 0,
    D5a_EscalationLadder.RHETORIC_ONLY.value: 1,
    D5a_EscalationLadder.PROXY_CLASH.value: 2,
    D5a_EscalationLadder.DIRECT_STRIKE.value: 3,
    D5a_EscalationLadder.CHOKEPOINT_DISRUPTION.value: 4,
    D5a_EscalationLadder.FULL_BLOCKADE.value: 5,
}


def _pick_better(qid: str, raw_answer: Any, det_label: str, laya_probs: Optional[Dict[str, float]]) -> str:
    """Post-validation: prefer deterministic label on any model abstain/unknown.

    Monotone safety: deterministic REJECT-class D0_1 labels (SPOOF_REJECTED,
    UNVERIFIED_SOCIAL) are floors the model may never raise — overrides only
    ever move decisions toward the safe side. For R1 a concrete deterministic
    event class is authoritative — only the model may route to noise/abstain,
    never the reverse (mis-routing an event headline into noise would erase it
    from the event paths).

    Escalation is now authority-gated, not merely direction-gated. D5a and D9
    keep their "model may never lower severity" floors, but the model can only
    RAISE severity on a head listed in ``MODEL_ESCALATION_AUTHORITY`` — which
    is empty by default. See the finding documented above that constant: at 52%
    decision accuracy the model fabricated 517 severity escalations on real
    production articles against 0 for the deterministic tripwire, and both
    labels are veto triggers.

    Vendor-vocabulary labels (D0_1/D0_2) are translated to gate enums by
    ``_map_model_label`` before the known-label check, which is what makes those
    two heads live instead of permanently inert.
    """
    if det_label in REJECT_D0_1:
        return det_label
    if qid == "D9_emergency_kill_switch":
        # D9 noul floor: a deterministic kill-switch tripwire can never be
        # un-killed by the model. Escalation normal->active requires explicit
        # authority, which is withheld by default (see MODEL_ESCALATION_AUTHORITY).
        if det_label == D9_KILL_SWITCH_ACTIVE:
            return det_label
        if qid not in MODEL_ESCALATION_AUTHORITY:
            return det_label
        if _map_model_label(qid, raw_answer) in _D9_TRUTHY:
            return D9_KILL_SWITCH_ACTIVE
        return det_label
    if det_label in D5A_RANK:
        val = _map_model_label(qid, raw_answer)
        if val not in D5A_RANK:
            return det_label
        if qid not in MODEL_ESCALATION_AUTHORITY:
            return det_label
        det_rank = D5A_RANK[det_label]
        # Monotone escalation only; abstention (INSUFFICIENT_DATA, rank 0) is
        # never an escalation of anything.
        return val if D5A_RANK[val] > det_rank else det_label
    if det_label in _R1_CONCRETE:
        return det_label
    val = _map_model_label(qid, raw_answer)
    # Simple, auditable rule: unknown/abstain from model → deterministic wins.
    # R1 concrete classes join the known set so the model can ADD an event
    # class the lexical router missed (unseeded vocabulary).
    known = {m.value for m in (
        *D0_1_SourceCredibility,
        *D0_2_DuplicateDetection,
        *D0_3_FactVsOpinion,
    )} | _R1_CONCRETE
    if not val or val == "INSUFFICIENT_DATA" or val not in known:
        return det_label
    # Low-probability model choice (< 0.5) defers to deterministic rules.
    if laya_probs is not None:
        try:
            if float(laya_probs.get(val.lower(), laya_probs.get(val, 0.0)) or 0.0) < 0.5:
                return det_label
        except (TypeError, ValueError):
            return det_label
    # R1: reaching here means det was NOT a concrete class (those returned
    # above), i.e. det-noise/abstain — a concrete, confident model routing
    # ADDS the event class. Model noise never erases a det-concrete class.
    return val


def triage_article(
    article: Dict[str, Any],
    *,
    seen_keys: Optional[set] = None,
    laya: Any = None,
) -> TriageVerdict:
    """Triage one normalized article dict into D0_1/D0_2/D0_3 + gate action."""
    seen = seen_keys if seen_keys is not None else set()
    policy = article.get("source_policy") or assess_source(
        str(article.get("url") or ""), str(article.get("publisher") or "")
    )
    det = _deterministic_labels(article, policy, seen)

    state = _article_state(article, policy, seen)
    questions = _triage_questions()

    # D9 near-zero-latency short-circuit: a lexical kill-switch tripwire on a
    # flash-crisis headline returns immediately — it never waits for the
    # model forward pass (measured ~19s/article on CPU when the checkpoint
    # loads). The tripwire is a CYCLE-STATE signal (halt risk trading), not an
    # article-quality gate: the headline itself is high-value evidence and
    # still gets the normal D0 gate action. The model can never un-kill this
    # verdict (hard floor in _pick_better); downstream market-data
    # confirmation is the second gate.
    if det["D9_emergency_kill_switch"] == D9_KILL_SWITCH_ACTIVE:
        return TriageVerdict(
            d0_1=det["D0_1_source_credibility"],
            d0_2=det["D0_2_duplicate_detection"],
            d0_3=det["D0_3_fact_vs_opinion"],
            d5a=det["D5a_escalation_ladder"],
            d9=D9_KILL_SWITCH_ACTIVE,
            r1=det["R1_headline_event_routing"],
            source="deterministic",
            action=_resolve_action(
                det["D0_1_source_credibility"],
                det["D0_2_duplicate_detection"],
                det["D0_3_fact_vs_opinion"],
                policy.get("allowed") is True,
            ),
            confidence=1.0,
            deterministic_labels=dict(det),
            prompt_state=state,
            reasons=["d9_kill_switch_tripwire", "short_circuit_before_model"],
        )

    # ``laya=False`` is an explicit sentinel: force the deterministic path
    # (batch cap / time budget in ``triage_articles``). ``None`` means resolve
    # via the ``_get_laya`` singleton as before.
    model = _get_laya() if laya is None else (laya if laya is not False else None)
    if model is None:
        action = _resolve_action(det["D0_1_source_credibility"], det["D0_2_duplicate_detection"], det["D0_3_fact_vs_opinion"], policy.get("allowed") is True)
        return TriageVerdict(
            d0_1=det["D0_1_source_credibility"],
            d0_2=det["D0_2_duplicate_detection"],
            d0_3=det["D0_3_fact_vs_opinion"],
            d5a=det["D5a_escalation_ladder"],
            d9=det["D9_emergency_kill_switch"],
            r1=det["R1_headline_event_routing"],
            source="deterministic",
            action=action,
            confidence=1.0,
            deterministic_labels=dict(det),
            prompt_state=state,
            reasons=["laya_offline"],
        )

    t0 = time.perf_counter()
    try:
        res = model.decide(state, questions)
    except Exception as exc:
        log.debug("Laya triage inference failed, deterministic fallback: %s", exc)
        action = _resolve_action(det["D0_1_source_credibility"], det["D0_2_duplicate_detection"], det["D0_3_fact_vs_opinion"], policy.get("allowed") is True)
        return TriageVerdict(
            d0_1=det["D0_1_source_credibility"],
            d0_2=det["D0_2_duplicate_detection"],
            d0_3=det["D0_3_fact_vs_opinion"],
            d5a=det["D5a_escalation_ladder"],
            d9=det["D9_emergency_kill_switch"],
            r1=det["R1_headline_event_routing"],
            source="deterministic",
            action=action,
            confidence=1.0,
            deterministic_labels=dict(det),
            prompt_state=state,
            reasons=["laya_error"],
        )
    latency = (time.perf_counter() - t0) * 1000

    raw_answers = dict(getattr(res, "answers", {}) or {})
    probs = dict(getattr(res, "probabilities", {}) or {})
    confs = dict(getattr(res, "confidences", {}) or {})

    final: Dict[str, str] = {}
    disagreements: List[str] = []
    for qid, det_label in det.items():
        laya_probs = probs.get(qid)
        raw_val = str(raw_answers.get(qid) or "").strip().upper()
        chosen = _pick_better(qid, raw_answers.get(qid), det_label, laya_probs)
        # Disagreement = model label differed from the deterministic rule,
        # BEFORE validation — a floored override (e.g. model tries to lax a
        # SPOOF_REJECTED) is still a disagreement and the active-learning
        # signal, even though the final label stays on the safe side.
        #
        # Compared against the TRANSLATED label so a pure vocabulary mismatch
        # (model says WIRE_SERVICE, gate says VERIFIED_OUTLET) does not flood
        # the active-learning ledger — that is an integration artefact, not a
        # model disagreement. The unmapped raw label is still preserved on the
        # verdict as ``laya_answers`` for audit.
        if _map_model_label(qid, raw_val) not in ("", det_label):
            disagreements.append(qid)
        final[qid] = chosen

    d0_1, d0_2, d0_3 = final["D0_1_source_credibility"], final["D0_2_duplicate_detection"], final["D0_3_fact_vs_opinion"]
    d5a = final["D5a_escalation_ladder"]
    d9 = final["D9_emergency_kill_switch"]
    r1 = final["R1_headline_event_routing"]
    action = _resolve_action(d0_1, d0_2, d0_3, policy.get("allowed") is True)

    avg_conf = 0.0
    try:
        vals = [float(confs.get(q, 0.0)) for q in TRIAGE_QUESTION_IDS]
        avg_conf = sum(vals) / max(1, len(vals))
    except (TypeError, ValueError):
        avg_conf = 0.0

    reasons: List[str] = []
    if d0_1 in REJECT_D0_1:
        reasons.append(f"gate:{d0_1}")
    if disagreements:
        reasons.append("validated_overrides:" + ",".join(disagreements))

    return TriageVerdict(
        d0_1=d0_1,
        d0_2=d0_2,
        d0_3=d0_3,
        d5a=d5a,
        d9=d9,
        r1=r1,
        source="laya",
        action=action,
        confidence=avg_conf,
        laya_probabilities={q: (probs.get(q) or {}) for q in TRIAGE_QUESTION_IDS},
        laya_confidences={q: float(confs.get(q, 0.0) or 0.0) for q in TRIAGE_QUESTION_IDS},
        laya_answers={q: str(raw_answers.get(q) or "") for q in TRIAGE_QUESTION_IDS},
        deterministic_labels=dict(det),
        prompt_state=state,
        disagreement_ids=disagreements,
        latency_ms=latency,
        reasons=reasons,
    )


def triage_articles(
    articles: Sequence[Dict[str, Any]],
    *,
    laya: Any = None,
    on_verdict: Optional[Callable[["TriageVerdict", Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Triage a batch; returns summary + attaches verdict dicts onto articles.

    ``on_verdict`` is invoked per (verdict, article) for side-channel consumers
    (e.g. active-learning ledger); exceptions it raises are swallowed.
    """
    global _LAYA_ERROR_STREAK, _LAYA_CIRCUIT_OPEN_UNTIL, _LAYA_CIRCUIT_LOGGED
    seen: set = set()
    counts = {"INGEST": 0, "TAG": 0, "REJECT": 0}
    verdicts: List[Dict[str, Any]] = []
    laya_used = 0
    laya_errors = 0
    total_ms = 0.0
    # Runtime circuit: repeated inference failures park the model path for a
    # cooldown — checked BEFORE resolution so an open circuit never pays the
    # load/first-pass cost, and it lapses naturally when the window expires.
    circuit_open = bool(_LAYA_CIRCUIT_OPEN_UNTIL
                        and time.time() < _LAYA_CIRCUIT_OPEN_UNTIL)
    # NOTE: force-off is the ``False`` sentinel — ``None`` would make
    # triage_article re-resolve the singleton (paying the factory cost the
    # circuit exists to avoid).
    model = False if circuit_open else (laya if laya is not None else _get_laya())
    # CPU batch guards: the cap counts model ATTEMPTS only (deterministic
    # fallbacks are ~free); when either guard fires, remaining articles ride
    # the deterministic labeler in-batch with the reason recorded on the
    # verdict, instead of stalling ingestion. ``laya=False`` forces the
    # deterministic path (the None sentinel would re-resolve the singleton).
    laya_remaining = _LAYA_MAX_ARTICLES_DEFAULT if laya is None else None
    laya_deadline = (time.monotonic() + _LAYA_TIME_BUDGET_S) if model is not None else None
    capped_reason = None

    for article in articles:
        if not isinstance(article, dict):
            continue
        batch_reason = None
        if model is not None and laya_remaining == 0:
            batch_reason = capped_reason = "laya_batch_cap"
            model = False
        elif (model is not None and laya_deadline is not None
              and time.monotonic() > laya_deadline):
            batch_reason = capped_reason = "laya_time_budget"
            model = False
        verdict = triage_article(article, seen_keys=seen, laya=model)
        counts[verdict.action] = counts.get(verdict.action, 0) + 1
        if verdict.source == "laya":
            laya_used += 1
            total_ms += verdict.latency_ms
            if laya_remaining is not None:
                laya_remaining -= 1
        elif "laya_error" in (verdict.reasons or []):
            laya_errors += 1
        elif batch_reason is not None and verdict.reasons == ["laya_offline"]:
            verdict.reasons = [batch_reason]
        elif (capped_reason is not None and model is False
              and verdict.reasons == ["laya_offline"]):
            verdict.reasons = [capped_reason]
        elif circuit_open and verdict.reasons == ["laya_offline"]:
            verdict.reasons = ["laya_circuit_open"]
        key = article.get("dedupe_key")
        if key:
            seen.add(key)
        verdict_dict = verdict.to_dict()
        article["triage"] = verdict_dict
        verdicts.append(verdict_dict)
        if on_verdict is not None:
            try:
                on_verdict(verdict, article)
            except Exception as exc:
                log.debug("triage verdict callback failed: %s", exc)

    # Runtime circuit: two consecutive batches with repeated inference
    # failures park the model path for a cooldown (fail-soft to deterministic).
    if model not in (None, False) and laya_errors >= 2:
        _LAYA_ERROR_STREAK += 1
        if _LAYA_ERROR_STREAK >= 2 and not _LAYA_CIRCUIT_LOGGED:
            _LAYA_CIRCUIT_OPEN_UNTIL = time.time() + _LAYA_CIRCUIT_COOLDOWN_S
            _LAYA_CIRCUIT_LOGGED = True
            log.warning(
                "Laya triage circuit OPEN %.0fs after repeated inference "
                "failures (%d this batch); deterministic path holds",
                _LAYA_CIRCUIT_COOLDOWN_S, laya_errors)
    elif laya_used > 0:
        _LAYA_ERROR_STREAK = 0
        _LAYA_CIRCUIT_LOGGED = False

    mode = "laya" if laya_used > 0 else "deterministic"
    return {
        "status": "TRIAGED",
        "mode": mode,
        "article_count": len(verdicts),
        "counts": counts,
        "laya_used": laya_used,
        "laya_errors": laya_errors,
        "laya_latency_ms_avg": round(total_ms / laya_used, 2) if laya_used else 0.0,
        "laya_batch_cap": _LAYA_MAX_ARTICLES_DEFAULT if laya is None else None,
        "laya_circuit_open": bool(circuit_open),
        "verdicts": verdicts[:40],
        "execution_authority": False,
    }
