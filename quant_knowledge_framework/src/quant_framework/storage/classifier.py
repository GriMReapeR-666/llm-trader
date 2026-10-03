"""
Knowledge Classifier
=====================
Classifies transcriptions and documents into quant finance topics.
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ClassifiedItem:
    """A classified transcription or document."""
    source_path: str
    title: str
    topic: str
    subtopic: Optional[str]
    confidence: float
    keywords: List[str]
    metadata: dict = field(default_factory=dict)


class KnowledgeClassifier:
    """
    Classifies transcriptions and documents into quant finance topics.
    """

    def __init__(self, config):
        self.config = config
        self.classification_rules = self._load_classification_rules()

    def _load_classification_rules(self) -> Dict[str, Dict]:
        return {
            "01_Regime_Detection": {
                "keywords": [
                    "hurst", "hmm", "hidden markov", "regime", "adx", "atr",
                    "volatility", "bocpd", "changepoint", "market state",
                    "trending", "range bound", "persistence", "anti-persistent",
                    "garman-klass", "parkinson", "yang-zhang", "realized volatility",
                    "vix", "volatility index", "implied volatility", "realized variance"
                ],
                "patterns": [
                    r"hurst\s+exponent",
                    r"hidden\s+markov\s+model",
                    r"regime\s+(detection|switching|classification)",
                    r"bayesian\s+online\s+changepoint",
                    r"adx|average\s+directional\s+index",
                    r"average\s+true\s+range",
                    r"volatility\s+(targeting|scaling|regime)",
                    r"garman.klass|parkinson|yang.zhang",
                    r"realized\s+(variance|volatility|kernel)",
                ],
                "weight": 1.0,
            },
            "02_Strategy_Development": {
                "keywords": [
                    "strategy", "backtest", "walk-forward", "wfo", "dsr",
                    "deflated sharpe", "cpcv", "purged cross-validation",
                    "expected value", "ev", "win rate", "payoff", "r-multiple",
                    "system quality", "sqn", "monte carlo", "permutation",
                    "triple barrier", "meta-labeling", "author-c",
                    "overfitting", "curve fitting", "in-sample", "out-of-sample",
                    "incubation", "paper trading", "slippage decay", "alpha decay"
                ],
                "patterns": [
                    r"walk.forward\s+(optimization|analysis)",
                    r"deflated\s+sharpe\s+ratio",
                    r"combinatorial\s+purged\s+cross",
                    r"expected\s+value.*ev",
                    r"system\s+quality\s+number",
                    r"triple\s+barrier",
                    r"meta.labeling",
                    r"monte\s+carlo\s+permutation",
                    r"probability\s+of\s+backtest\s+overfitting",
                ],
                "weight": 1.0,
            },
            "03_Portfolio_Management": {
                "keywords": [
                    "portfolio", "kelly", "risk parity", "hrp", "hierarchical risk parity",
                    "correlation", "allocation", "heat cap", "position sizing",
                    "volatility parity", "inverse volatility", "capital allocation",
                    "drawdown", "maxdd", "calmar", "mar ratio", "cagr",
                    "compounding", "geometric", "variance drag", "quarter kelly",
                    "fractional kelly", "risk of ruin", "ror", "rebalancing"
                ],
                "patterns": [
                    r"kelly\s+criterion",
                    r"hierarchical\s+risk\s+parity",
                    r"risk\s+parity",
                    r"volatility\s+parity",
                    r"inverse\s+volatility",
                    r"portfolio\s+heat",
                    r"correlation\s+(gate|matrix|collapse)",
                    r"calmar\s+ratio",
                    r"quarter.kelly|fractional.kelly",
                    r"risk\s+of\s+ruin",
                ],
                "weight": 1.0,
            },
            "04_Order_Execution": {
                "keywords": [
                    "execution", "slippage", "spread", "market impact",
                    "almgren-chriss", "tca", "transaction cost", "implementation shortfall",
                    "order routing", "smart order router", "sor",
                    "maker", "taker", "passive limit", "aggressive taker",
                    "vpin", "order flow", "toxicity", "queue priority",
                    "micro-price", "effective spread", "realized spread",
                    "permanent impact", "temporary impact", "square-root law"
                ],
                "patterns": [
                    r"almgren.chriss",
                    r"implementation\s+shortfall",
                    r"transaction\s+cost\s+analysis",
                    r"smart\s+order\s+routing",
                    r"order\s+flow\s+imbalance",
                    r"vpin|volume.synchronized\s+probability",
                    r"micro.price",
                    r"permanent\s+impact|temporary\s+impact",
                    r"square.root\s+law",
                    r"bid.ask\s+spread",
                ],
                "weight": 1.0,
            },
            "05_Quant_Utilities": {
                "keywords": [
                    "python", "pandas", "numpy", "data pipeline", "feature engineering",
                    "fractional differentiation", "stationarity", "adf test",
                    "augmented dickey-fuller", "johansen", "cointegration",
                    "z-score", "normalize", "standardize", "rolling window",
                    "data cleaning", "survivorship bias", "lookahead bias",
                    "point-in-time", "corporate actions", "split adjustment"
                ],
                "patterns": [
                    r"fractional\s+differentiation",
                    r"augmented\s+dickey.fuller",
                    r"johansen\s+test",
                    r"cointegration",
                    r"survivorship\s+bias",
                    r"lookahead\s+bias",
                    r"point.in.time",
                    r"data\s+pipeline",
                ],
                "weight": 1.0,
            },
        }

    def classify_text(self, text: str, title: str = "") -> Tuple[str, float, List[str]]:
        text_lower = text.lower()
        title_lower = title.lower()
        scores: Dict[str, float] = {}
        matched_keywords: Dict[str, List[str]] = {}

        for topic_id, rules in self.classification_rules.items():
            score = 0.0
            keywords_found: List[str] = []

            for keyword in rules["keywords"]:
                if keyword in text_lower or keyword in title_lower:
                    score += 1.0
                    keywords_found.append(keyword)

            for pattern in rules["patterns"]:
                matches = re.findall(pattern, text_lower)
                if matches:
                    score += 2.0 * len(matches)
                    keywords_found.extend(matches)

            score *= rules.get("weight", 1.0)
            scores[topic_id] = score
            matched_keywords[topic_id] = list(set(keywords_found))

        best_topic = max(scores, key=scores.get)
        best_score = scores[best_topic]

        total_score = sum(scores.values())
        confidence = best_score / total_score if total_score > 0 else 0.0

        if best_score < 2.0:
            return "99_Archive_Unsorted", 0.0, []

        return best_topic, min(confidence, 1.0), matched_keywords[best_topic]

    def classify_file(self, file_path: str) -> ClassifiedItem:
        path = Path(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            logger.error("Error reading %s: %s", file_path, e)
            text = ""

        topic, confidence, keywords = self.classify_text(text, path.stem)
        return ClassifiedItem(
            source_path=file_path,
            title=path.stem,
            topic=topic,
            subtopic=None,
            confidence=confidence,
            keywords=keywords,
            metadata={"file_size": path.stat().st_size if path.exists() else 0},
        )

    def classify_directory(self, directory: str) -> List[ClassifiedItem]:
        results: List[ClassifiedItem] = []
        dir_path = Path(directory)
        for file_path in dir_path.rglob("*.txt"):
            try:
                results.append(self.classify_file(str(file_path)))
            except Exception as e:
                logger.error("Error classifying %s: %s", file_path, e)
        return results
