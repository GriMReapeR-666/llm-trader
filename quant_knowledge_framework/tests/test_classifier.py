import pytest
from quant_framework.storage.classifier import KnowledgeClassifier
from quant_framework.config import Config


@pytest.fixture()
def classifier():
    return KnowledgeClassifier(Config("config/sources.yaml"))


def test_classify_trending_regime(classifier):
    topic, confidence, keywords = classifier.classify_text("hidden markov model regime detection trending volatility", title="HMM Regime")
    assert topic == "01_Regime_Detection"
    assert confidence > 0.0


def test_classify_strategy_topic(classifier):
    topic, confidence, keywords = classifier.classify_text("walk forward optimization deflated sharpe ratio overfitting monte carlo permutation", title="Strategy Validation")
    assert topic == "02_Strategy_Development"


def test_classify_portfolio_topic(classifier):
    topic, confidence, keywords = classifier.classify_text("kelly criterion hierarchical risk parity portfolio allocation correlation", title="Portfolio Management")
    assert topic == "03_Portfolio_Management"


def test_classify_execution_topic(classifier):
    topic, confidence, keywords = classifier.classify_text("almgren chriss market impact implementation shortfall transaction cost analysis", title="Execution")
    assert topic == "04_Order_Execution"


def test_classify_utilities_topic(classifier):
    topic, confidence, keywords = classifier.classify_text("python pandas numpy fractional differentiation stationarity adf test", title="Quant Utilities")
    assert topic == "05_Quant_Utilities"


def test_classify_unknown_topic(classifier):
    topic, confidence, keywords = classifier.classify_text("this is completely unrelated finance content about real estate mortgages", title="Random")
    assert topic == "99_Archive_Unsorted"
