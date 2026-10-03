"""Unstructured Alternative Data & NLP Alpha Pipeline.

Implements the institutional quantitative mechanics from 'docs/how to find edge.txt':
1. Form 10-K Modification Index (Cohen, Malloy, Nguyen 2020 - "Lazy Prices"):
   Measures textual cosine similarity S_C and modification index M = 1 - S_C
   between consecutive 10-K "Item 1A: Risk Factors" sections. Significant
   unannounced changes forecast systematic equity underperformance.
2. Earnings Call Linguistic Hedging (Unscripted Q&A vs Prepared Remarks):
   Measures the ratio of affirmative/confident vs evasive/hedging language
   used by executives during unscripted Q&A sessions.
3. Alternative Activity Proxies:
   Quantifies operational momentum via corporate job postings delta (hiring freezes)
   and developer commit velocity (tech/crypto protocols).

Design & Safety Guarantees:
- execution_authority = False: Pure advisory / alpha feature generator.
- Fail-closed & offline-ready: Operates deterministically without network calls.
- Stdlib + NumPy / Scikit-Learn vectorization with pure-Python fallback.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

# -----------------------------------------------------------------------------
# Financial Lexicons (Loughran-McDonald & Behavioral Finance Inspired)
# -----------------------------------------------------------------------------

AFFIRMATIVE_WORDS: Set[str] = {
    "definitely", "confident", "confidence", "certain", "certainly", "exceeding",
    "exceeded", "robust", "strong", "strongly", "accelerating", "accelerated",
    "unmatched", "outperform", "outperforming", "momentum", "solid", "exceptional",
    "unprecedented", "discipline", "conviction", "thriving", "tailwind",
    "expand", "expanding", "growth", "profitable", "resilient", "leadership",
    "advantage", "compounding", "superior", "optimized", "scalable"
}

EVASIVE_WORDS: Set[str] = {
    "somewhat", "possible", "possibly", "challenging", "challenge", "headwind",
    "headwinds", "uncertain", "uncertainty", "tentative", "tentatively", "cautious",
    "cautiously", "roughly", "unclear", "unforeseen", "turbulent", "volatility",
    "delay", "delayed", "hesitant", "impairment", "softening", "subdued",
    "depends", "contingent", "approximate", "alleged", "supposedly", "vulnerable",
    "pressure", "pressured", "unresolved", "risk", "risks"
}

STOPWORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves"
}


# -----------------------------------------------------------------------------
# Utility: Clean Tokenization & Cosine Vectorizer
# -----------------------------------------------------------------------------

def tokenize_text(text: str, remove_stopwords: bool = True) -> List[str]:
    """Tokenize alphanumeric words, convert to lowercase, and filter stopwords."""
    if not text or not isinstance(text, str):
        return []
    words = re.findall(r"\b[a-zA-Z]{2,}\b", text.lower())
    if remove_stopwords:
        return [w for w in words if w not in STOPWORDS]
    return words


def compute_bow_tf_cosine_similarity(tokens_a: List[str], tokens_b: List[str]) -> float:
    """Computes Term Frequency Bag-of-Words Cosine Similarity between two token lists."""
    if not tokens_a or not tokens_b:
        return 0.0

    counts_a: Dict[str, int] = {}
    for t in tokens_a:
        counts_a[t] = counts_a.get(t, 0) + 1

    counts_b: Dict[str, int] = {}
    for t in tokens_b:
        counts_b[t] = counts_b.get(t, 0) + 1

    all_vocab = set(counts_a.keys()).union(set(counts_b.keys()))
    if not all_vocab:
        return 0.0

    dot_product = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for word in all_vocab:
        val_a = float(counts_a.get(word, 0))
        val_b = float(counts_b.get(word, 0))
        dot_product += val_a * val_b
        norm_a += val_a * val_a
        norm_b += val_b * val_b

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    sim = dot_product / denom if denom > 1e-12 else 0.0
    return float(np.clip(sim, 0.0, 1.0))


# -----------------------------------------------------------------------------
# 1. Form 10-K Modification Index (Cohen, Malloy, Nguyen 2020)
# -----------------------------------------------------------------------------

class Form10KModificationAnalyzer:
    """Analyzes year-over-year textual modification in Form 10-K filings.

    Empirical finding: Companies that significantly revise their 10-K 'Item 1A:
    Risk Factors' or 'MD&A' exhibit negative abnormal returns over the following
    quarters, as market participants initially underreact to disclosed risk shifts.
    """

    @staticmethod
    def extract_risk_factors(filing_text: str) -> str:
        """Extracts Item 1A: Risk Factors from raw text or returns cleaned text."""
        if not filing_text:
            return ""

        pattern = re.compile(
            r"(?:item\s+1a[\.\:\s\-]+risk\s+factors)(.*?)(?:item\s+1b|item\s+2|item\s+3)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(filing_text)
        if match:
            extracted = match.group(1).strip()
            if len(extracted) > 100:
                return extracted

        return filing_text.strip()

    @classmethod
    def compute_modification_index(
        cls,
        text_prior: str,
        text_current: str,
        is_already_extracted: bool = False,
    ) -> Dict[str, Any]:
        """Calculates Cosine Similarity and Modification Index between consecutive 10-Ks.

        Returns:
            Dict with cosine_similarity, modification_index (1 - sim),
            revision_severity, underperformance_warning, and execution_authority=False.
        """
        if not text_prior or not text_current:
            return {
                "cosine_similarity": 0.0,
                "modification_index": 1.0,
                "revision_severity": "INSUFFICIENT_DATA",
                "underperformance_warning": False,
                "token_count_prior": 0,
                "token_count_current": 0,
                "alpha_penalty": 0.0,
                "execution_authority": False,
                "advisory_only": True,
            }

        sec_prior = text_prior if is_already_extracted else cls.extract_risk_factors(text_prior)
        sec_current = text_current if is_already_extracted else cls.extract_risk_factors(text_current)

        tokens_prior = tokenize_text(sec_prior)
        tokens_current = tokenize_text(sec_current)

        if not tokens_prior or not tokens_current:
            return {
                "cosine_similarity": 0.0,
                "modification_index": 1.0,
                "revision_severity": "EMPTY_TOKENS",
                "underperformance_warning": False,
                "token_count_prior": len(tokens_prior),
                "token_count_current": len(tokens_current),
                "alpha_penalty": 0.0,
                "execution_authority": False,
                "advisory_only": True,
            }

        sim = 0.0
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", max_features=10000)
            tfidf_mat = vectorizer.fit_transform([sec_prior, sec_current])
            vec_a = tfidf_mat[0].toarray().flatten()
            vec_b = tfidf_mat[1].toarray().flatten()
            norm_a = np.linalg.norm(vec_a)
            norm_b = np.linalg.norm(vec_b)
            if norm_a > 1e-12 and norm_b > 1e-12:
                sim = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
                sim = float(np.clip(sim, 0.0, 1.0))
            else:
                sim = 0.0
        except Exception:
            sim = compute_bow_tf_cosine_similarity(tokens_prior, tokens_current)

        mod_index = float(np.clip(1.0 - sim, 0.0, 1.0))

        if mod_index < 0.04:
            severity = "UNCHANGED"
            warning = False
            alpha_penalty = 0.0
        elif mod_index < 0.15:
            severity = "ROUTINE_UPDATE"
            warning = False
            alpha_penalty = -0.05
        elif mod_index < 0.25:
            severity = "MATERIAL_REVISION"
            warning = True
            alpha_penalty = -0.25
        else:
            severity = "SEVERE_REWRITE"
            warning = True
            alpha_penalty = -0.50

        return {
            "cosine_similarity": round(sim, 4),
            "modification_index": round(mod_index, 4),
            "revision_severity": severity,
            "underperformance_warning": warning,
            "token_count_prior": len(tokens_prior),
            "token_count_current": len(tokens_current),
            "alpha_penalty": alpha_penalty,
            "execution_authority": False,
            "advisory_only": True,
        }


# -----------------------------------------------------------------------------
# 2. Earnings Call Linguistic Hedging Analyzer
# -----------------------------------------------------------------------------

class EarningsCallHedgingAnalyzer:
    """Analyzes linguistic tone, confidence, and evasiveness in corporate earnings calls."""

    @staticmethod
    def split_call_sections(transcript_text: str) -> Tuple[str, str]:
        """Splits transcript into (prepared_presentation, analyst_qa)."""
        if not transcript_text:
            return "", ""

        qa_delimiters = [
            r"questions?\s+(?:and|&)\s+answers?",
            r"q\s*&\s*a(?:\s+session)?",
            r"question-and-answer\s+session",
            r"we\s+will\s+now\s+begin\s+the\s+question-and-answer",
            r"operator:\s+ladies\s+and\s+gentlemen,\s+we\s+will\s+now\s+begin",
        ]
        pattern = re.compile("|".join(qa_delimiters), re.IGNORECASE)
        match = pattern.search(transcript_text)
        if match:
            idx = match.start()
            presentation = transcript_text[:idx].strip()
            qa = transcript_text[idx:].strip()
            return presentation, qa

        return transcript_text.strip(), ""

    @classmethod
    def score_hedging_ratio(cls, text: str) -> Dict[str, Any]:
        """Scores affirmative vs evasive tokens in a text block."""
        tokens = tokenize_text(text, remove_stopwords=False)
        total_tokens = len(tokens)
        if total_tokens == 0:
            return {
                "affirmative_count": 0,
                "evasive_count": 0,
                "total_tokens": 0,
                "hedging_ratio": 0.5,
                "tone_polarity": 0.0,
                "conviction_state": "NEUTRAL",
                "execution_authority": False,
            }

        aff_count = sum(1 for w in tokens if w in AFFIRMATIVE_WORDS)
        eva_count = sum(1 for w in tokens if w in EVASIVE_WORDS)
        salient_total = aff_count + eva_count

        if salient_total == 0:
            hedging_ratio = 0.5
            tone_polarity = 0.0
            conviction_state = "NEUTRAL"
        else:
            hedging_ratio = float(eva_count / (salient_total + 1e-9))
            tone_polarity = float((aff_count - eva_count) / (salient_total + 1e-9))

            if hedging_ratio > 0.65:
                conviction_state = "EVASIVE_DIVERGENCE"
            elif hedging_ratio > 0.50:
                conviction_state = "DEFENSIVE_HEDGING"
            elif hedging_ratio < 0.35 and aff_count >= 3:
                conviction_state = "HIGH_CONVICTION"
            else:
                conviction_state = "BALANCED"

        return {
            "affirmative_count": aff_count,
            "evasive_count": eva_count,
            "total_tokens": total_tokens,
            "hedging_ratio": round(hedging_ratio, 4),
            "tone_polarity": round(tone_polarity, 4),
            "conviction_state": conviction_state,
            "execution_authority": False,
        }

    @classmethod
    def analyze_transcript(cls, transcript_text: str) -> Dict[str, Any]:
        """Full end-to-end analysis comparing prepared remarks to unscripted Q&A."""
        pres_text, qa_text = cls.split_call_sections(transcript_text)
        pres_score = cls.score_hedging_ratio(pres_text)
        has_qa = len(qa_text) > 50
        qa_score = cls.score_hedging_ratio(qa_text) if has_qa else pres_score

        pres_hedging = pres_score["hedging_ratio"]
        qa_hedging = qa_score["hedging_ratio"]
        qa_divergence = round(qa_hedging - pres_hedging, 4) if has_qa else 0.0

        if has_qa and qa_divergence >= 0.25 and qa_score["hedging_ratio"] >= 0.55:
            overall_verdict = "EVASIVE_DIVERGENCE_WARNING"
            credibility_score = 0.20
        elif qa_score["hedging_ratio"] <= 0.35 and pres_score["hedging_ratio"] <= 0.40:
            overall_verdict = "HIGH_CONVICTION_MANAGEMENT"
            credibility_score = 0.90
        elif qa_score["hedging_ratio"] >= 0.60:
            overall_verdict = "DEFENSIVE_MANAGEMENT"
            credibility_score = 0.35
        else:
            overall_verdict = "BALANCED_MANAGEMENT"
            credibility_score = 0.55

        return {
            "presentation_score": pres_score,
            "qa_score": qa_score,
            "qa_available": has_qa,
            "qa_divergence_delta": qa_divergence,
            "overall_verdict": overall_verdict,
            "credibility_score": round(credibility_score, 4),
            "execution_authority": False,
            "advisory_only": True,
        }


# -----------------------------------------------------------------------------
# 3. Alternative Activity Proxy Scorer
# -----------------------------------------------------------------------------

class AlternativeActivityProxyScorer:
    """Scores operational real-economy activity proxies as leading signals."""

    @staticmethod
    def score_job_postings_momentum(
        current_openings: int,
        prior_openings: int,
        keyword_counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """Evaluates corporate job listings change to spot hiring freezes or expansion surges."""
        if prior_openings <= 0:
            pct_change = 0.0
        else:
            pct_change = (current_openings - prior_openings) / float(prior_openings)

        pct_change = float(np.clip(pct_change, -1.0, 5.0))

        if pct_change <= -0.30:
            status = "HIRING_FREEZE"
            signal_bias = -0.5
        elif pct_change <= -0.10:
            status = "SLOWDOWN"
            signal_bias = -0.2
        elif pct_change >= 0.35:
            status = "AGGRESSIVE_EXPANSION"
            signal_bias = 0.4
        elif pct_change >= 0.10:
            status = "MODERATE_EXPANSION"
            signal_bias = 0.2
        else:
            status = "STABLE"
            signal_bias = 0.0

        return {
            "current_openings": current_openings,
            "prior_openings": prior_openings,
            "postings_pct_change": round(pct_change, 4),
            "operational_status": status,
            "signal_bias": signal_bias,
            "execution_authority": False,
        }

    @staticmethod
    def score_developer_velocity(
        current_commits: int,
        prior_commits: int,
        active_contributors: int = 1,
    ) -> Dict[str, Any]:
        """Evaluates GitHub/repository commit velocity for crypto and tech protocols."""
        if prior_commits <= 0:
            velocity_delta = 0.0
        else:
            velocity_delta = (current_commits - prior_commits) / float(prior_commits)

        velocity_delta = float(np.clip(velocity_delta, -1.0, 5.0))

        if velocity_delta >= 0.50 and active_contributors >= 3:
            status = "DEVELOPER_ACCELERATION"
            signal_bias = 0.4
        elif velocity_delta <= -0.50 or active_contributors == 0:
            status = "DEVELOPER_DESERTION"
            signal_bias = -0.5
        else:
            status = "NORMAL_ACTIVITY"
            signal_bias = 0.0

        return {
            "current_commits": current_commits,
            "prior_commits": prior_commits,
            "velocity_delta": round(velocity_delta, 4),
            "active_contributors": active_contributors,
            "status": status,
            "signal_bias": signal_bias,
            "execution_authority": False,
        }

    @classmethod
    def synthesize_alternative_alpha_signal(
        cls,
        modification_index_result: Optional[Dict[str, Any]] = None,
        transcript_hedging_result: Optional[Dict[str, Any]] = None,
        job_postings_result: Optional[Dict[str, Any]] = None,
        developer_velocity_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synthesizes an aggregate composite alternative alpha signal in [-1.0, +1.0]."""
        weights: List[float] = []
        signals: List[float] = []

        if modification_index_result:
            penalty = float(modification_index_result.get("alpha_penalty", 0.0))
            signals.append(penalty)
            weights.append(1.5)

        if transcript_hedging_result:
            polarity = float(transcript_hedging_result.get("qa_score", {}).get("tone_polarity", 0.0))
            signals.append(polarity)
            weights.append(1.2)

        if job_postings_result:
            postings_bias = float(job_postings_result.get("signal_bias", 0.0))
            signals.append(postings_bias)
            weights.append(1.0)

        if developer_velocity_result:
            dev_bias = float(developer_velocity_result.get("signal_bias", 0.0))
            signals.append(dev_bias)
            weights.append(1.0)

        if not signals:
            composite_score = 0.0
            confidence = 0.0
        else:
            total_weight = sum(weights)
            composite_score = sum(s * w for s, w in zip(signals, weights)) / total_weight
            composite_score = float(np.clip(composite_score, -1.0, 1.0))
            confidence = min(1.0, len(signals) * 0.25)

        return {
            "composite_alternative_alpha": round(composite_score, 4),
            "confidence": round(confidence, 2),
            "signals_evaluated": len(signals),
            "favorable_bias": composite_score > 0.15,
            "unfavorable_bias": composite_score < -0.15,
            "execution_authority": False,
            "advisory_only": True,
        }
