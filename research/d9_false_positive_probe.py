"""D9 tripwire false-positive stress test.

Extracts every headline/summary string from the triage-related test corpus via
AST (no imports, no test execution), merges it with an adversarial benign
corpus that probes each tripwire branch's known FP modes, and runs all of it
through the deterministic D9 tripwire. Every trip is attributed to the
responsible pattern branch so root causes are nameable.

Output: artifacts/d9_false_positive_report.json
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "data", "intelligence"):
    sys.path.insert(0, os.path.join(ROOT, p) if p else ROOT)

from data.news_triage import _D9_KILL_PATTERN, _deterministic_d9  # noqa: E402

TEST_FILES = [
    "tests/test_news_triage.py",
    "tests/test_d9_kill_switch_triage.py",
    "tests/test_d9_kill_switch_latch.py",
    "tests/test_d5a_triage.py",
    "tests/test_d12_r1_questions.py",
    "tests/test_realtime_news_ingest.py",
    "tests/test_triage_ledger.py",
    "tests/test_news_evidence_routing.py",
    "tests/test_economic_dynamics.py",
    "tests/test_macro_graph_hardening.py",
]

# Alternation branches of _D9_KILL_PATTERN, nameable for attribution.
D9_BRANCHES = {
    "halt_trading": r"trading halt(?:ed|s)?|halts? (?:trading|all trading)|exchange halt",
    "circuit_breaker": r"circuit breaker(?:s)?(?: triggered| hit)?",
    "markets_closed": r"markets? closed (?:indefinitely|until further notice)|stock exchange closed",
    "peg_break": r"de[- ]pegs?|de[- ]pegged|de[- ]pegging|abandons? (?:the )?peg|breaks? (?:the )?peg|peg (?:breaks|collapses)",
    "emergency_action": r"emergency (?:rate (?:cut|hike|meeting)|ban|measure|action)",
    "crash": r"flash crash|market crash",
    "redemption_freeze": r"freeze(?:s|d)? (?:redemptions|withdrawals)|suspends? withdrawals",
    "bank_run": r"bank runs?",
    "capital_controls": r"capital controls",
}
_BRANCH_RES = {k: re.compile(v, re.IGNORECASE) for k, v in D9_BRANCHES.items()}

# Root-cause feature detectors for false-positive classification.
_FP_FEATURES = {
    "negation": re.compile(
        r"\b(no|not|n't|no longer|never|without|denies|doubt|doubts)\b"
        r"|not on the table|isn't|wasn't", re.IGNORECASE),
    "retrospective": re.compile(
        r"\b(19\d{2}|20\d{2})\b|\b(ago|revisited|anniversary|history|historians|"
        r"retrospective|last quarter|becomes a business school case|case study)\b",
        re.IGNORECASE),
    "forecast_or_debate": re.compile(
        r"\b(warn[s]? of|possible|propose[s]?|would tighten|debate[s]?|"
        r"model[s]?|study|bill would|discuss(?:es)?)\b", re.IGNORECASE),
    "single_name_scope": re.compile(
        r"halts? trading (?:in|of|shares)|shares of|in one stock|on a single stock|"
        r"in three|microcap|token|in \w+ (?:Biotech|Labs|miners|retailer)"
        r"|tripped on a single|in one microcap", re.IGNORECASE),
    "reversal": re.compile(
        r"\b(lifts?|lifted|ends?|removes?|eases?|no longer)\b", re.IGNORECASE),
}


def classify_fp(text: str) -> list:
    return [name for name, rx in _FP_FEATURES.items() if rx.search(text)]


# PROPOSED (not wired into production): suppress a trip when the headline only
# carries commentary/historical/negation/single-instrument cues and no
# present-tense announcement verb. Measured here to prove the recall cost.
_PROPOSED_GUARD = re.compile(
    r"\b(warn\w*|possible|possibility|forecast|expect\w*|anniversary|revisited|"
    r"retrospective|histor\w*|stud(?:y|ies)|debate\w*|propose\w*|bill|would|whether|"
    r"could|might|\bmay\b|not on the table|no\b|never|no longer|lifts?|lifted|eases?|"
    r"removes?|\bends?\b|\bago\b|of (?:19|20)\d{2}|in (?:19|20)\d{2}|last quarter|"
    r"how |\bwhen\b|\bif\b|review|briefly|blamed|fat-finger|\ba single\b|"
    r"halts? trading (?:in|of|shares)|halt(?:ed|s)? trading in )", re.IGNORECASE)
_PRESENT_CUE = re.compile(
    r"\b(announces?\w*|declares?|imposes?|spreads?|underway|hits?|forces?|plunges?|"
    r"slides?|calls?|triggers?|wipes?|drags?)\b", re.IGNORECASE)


def proposed_guard_suppresses(text: str) -> bool:
    return bool(_PROPOSED_GUARD.search(text)) and not _PRESENT_CUE.search(text)


def harvest_strings(path: str) -> list:
    """Pull string literals that plausibly carry news text from a test file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError):
        return []
    found = []

    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if len(s) >= 8 and any(c.isalpha() for c in s):
                found.append(s)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def attribute_branch(text: str) -> str:
    for name, rx in _BRANCH_RES.items():
        if rx.search(text):
            return name
    return "unknown"


def main() -> None:
    corpus = []  # (source, expected_kill, text)

    # 1) Harvested test-corpus strings. Human-marked expectations only.
    marked = {
        # test_d9_kill_switch_triage expected-trip headlines
        "Exchange halts trading as circuit breaker hits": "trip",
        "Central bank announces emergency rate cut": "trip",
        "Depegged stablecoin drags exchange token lower": "trip",
        "Bank runs spread across regional lenders": "trip",
        "Bank run forces rescue of regional lender": "trip",
        "Regulator freezes redemptions at money market fund": "trip",
        "Country imposes capital controls as currency slides": "trip",
        "Flash crash wipes billions from futures market": "trip",
        "banks suspend withdrawals": "trip",
        "exchange halts all trading": "trip",
        "markets closed until further notice": "trip",
        "several stocks circuit breaker": "trip",
    }
    for tf in TEST_FILES:
        full = os.path.join(ROOT, tf)
        for s in harvest_strings(full):
            exp = marked.get(s, "unknown")
            corpus.append({"source": tf, "expect": exp, "text": s})

    # 2) Adversarial benign corpus — plausible real headlines that must NOT trip.
    benign = [
        # halt family: single-name / single-stock / sector, not market-wide
        "Nasdaq halts trading in Zenith Biotech amid merger talks",
        "NYSE halts trading shares of small-cap retailer pending news",
        "Trading halted in three lithium miners on unusual volume",
        "Nasdaq halts trading of Terra Labs stock after review",
        "LME halts nickel trading for the day; other metals trade normally",
        "Euronext halts trading in one stock after order-entry glitch",
        # circuit-breaker family: retrospective, individual-stock, proposed rules
        "Circuit breakers triggered briefly in March, SEC review finds",
        "How circuit breakers work and when they last triggered",
        "Senate bill would tighten circuit breaker rules for exchanges",
        "Circuit breaker tripped on a single stock after earnings leak",
        "Exchanges propose wider circuit breaker bands after volatility",
        # closed family: holidays, single venues, non-market
        "Bond market closed for Veterans Day; equities trade normally",
        "London Metal Exchange closed for a public holiday Thursday",
        "Schools and government offices closed until further notice after storm",
        "Gold market closed until further notice? No — regular session ahead",
        # peg family: currencies that ARE the peg, not breaking it
        "Hong Kong dollar peg holds through turbulent week",
        "Swiss franc peg abandoned in 2015, a decade of lessons since",
        "Danish central bank defends its euro peg with rate hike",
        "Saudi riyal peg intact despite oil price swings",
        "Officials discuss whether the currency board peg breaks the taboo on flexibility",
        # emergency family: scheduled meetings, historical, non-rate
        "Fed emergency rate cut of 2008 revisited by historians",
        "ECB holds rates, emergency meeting not expected this week",
        "Pakistan seeks emergency loan measure from IMF",
        "Parliament debates emergency ban on single-use plastics",
        "Turkey held an emergency rate meeting last quarter, minutes show",
        # crash family: single stocks, crypto tokens, forecasts, past events
        "Flash crash in one microcap stock draws SEC inquiry",
        "Wheat futures flash crash blamed on fat-finger order",
        "Analysts warn of a possible market crash next year",
        "1987 market crash anniversary: what changed since",
        "Crypto token flash crash revives debate on thin liquidity",
        # freeze family: wages, hiring, assets, credit, not redemptions/withdrawals
        "Company freezes hiring as growth slows",
        "Fed freezes assets of sanctioned oligarchs",
        "Pension fund freezes withdrawals? No — it only delayed benefit adjustments",
        "Bank of England lifts cap; lenders no longer suspend withdrawals in old plan",
        "City freezes parking fees during the festival",
        # bank-run family: named past events, studies, risk-index chatter
        "Silicon Valley Bank bank run of 2023 becomes a business school case",
        "Economists model contagion risk after bank runs in history",
        "Study: deposit insurance reduced the odds of a bank run",
        "First Republic customers had queued online, but no bank run materialised",
        # capital controls: historical, prospective-debate, other countries
        "Greece capital controls of 2015: a retrospective",
        "Economists debate whether capital controls ever work",
        "Malaysia lifts remaining capital controls imposed decades ago",
        # negation / hypothetical / question forms
        "No trading halt expected as volatility stays contained",
        "If a trading halt were triggered, dealers would face settlement gaps",
        "Would a de-peg of the euro cross-rate threaten stability? Economists doubt it",
        "Officials said an emergency rate cut is not on the table",
        "Markets were closed until further notice, a headline from 1914",
    ]
    for s in benign:
        corpus.append({"source": "adversarial_benign", "expect": "benign", "text": s})

    results = []
    for row in corpus:
        text = row["text"]
        verdict = _deterministic_d9({"title": text, "text_or_summary": ""})
        tripped = verdict == "EMERGENCY_HALT_ACTIVE"
        rx_match = bool(_D9_KILL_PATTERN.search(text))
        results.append({
            **row,
            "d9": verdict,
            "tripped": tripped,
            "regex_direct": rx_match,
            "branch": attribute_branch(text) if (tripped or rx_match) else None,
            "proposed_guard_would_suppress": (
                tripped and proposed_guard_suppresses(text)
            ),
        })

    harvested = [r for r in results if r["source"] != "adversarial_benign"]
    benign_rows = [r for r in results if r["source"] == "adversarial_benign"]

    harvested_trips = [r for r in harvested if r["tripped"]]
    benign_trips = [r for r in benign_rows if r["tripped"]]

    by_branch = {}
    for r in results:
        if r["tripped"]:
            by_branch.setdefault(r["branch"], []).append(r["text"])

    report = {
        "pattern_under_test": "data/news_triage.py::_D9_KILL_PATTERN via _deterministic_d9",
        "corpus_sizes": {
            "harvested_test_strings": len(harvested),
            "adversarial_benign": len(benign_rows),
        },
        "summary": {
            "harvested_trips": len(harvested_trips),
            "benign_false_positives": len(benign_trips),
            "trips_by_branch": {k: len(v) for k, v in sorted(by_branch.items())},
        },
        "harvested_trip_detail": [
            {k: r[k] for k in ("source", "text", "branch", "expect")} for r in harvested_trips
        ],
        "benign_false_positive_detail": [
            {**{k: r[k] for k in ("text", "branch")},
             "fp_classes": classify_fp(r["text"]),
             "guard_would_suppress": r["proposed_guard_would_suppress"]}
            for r in benign_trips
        ],
        "recall_holes": [
            "Passive voice escapes: 'Swiss franc peg abandoned in 2015' does NOT trip (pattern only has active 'abandons the peg')",
            "Instrument-intervening halts escape: 'LME halts nickel trading for the day' does NOT trip ('halts? trading' requires adjacency)",
        ],
        "benign_passes": [r["text"] for r in benign_rows if not r["tripped"]],
        "proposed_guard_measurement": {
            "benign_fps_suppressed": sum(
                1 for r in benign_trips if r["proposed_guard_would_suppress"]),
            "real_trips_lost": sum(
                1 for r in harvested_trips if r["proposed_guard_would_suppress"]),
            "real_trips_lost_detail": [
                r["text"] for r in harvested_trips if r["proposed_guard_would_suppress"]
            ],
        },
    }
    out = os.path.join(ROOT, "artifacts", "d9_false_positive_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)

    print(f"harvested={len(harvested)} trips={len(harvested_trips)}")
    print(f"benign={len(benign_rows)} false_positives={len(benign_trips)}")
    for k, v in sorted(by_branch.items()):
        print(f"  branch {k}: {len(v)} trip(s)")
    print("report ->", out)


if __name__ == "__main__":
    main()
