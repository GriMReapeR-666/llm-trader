from research.adaptive_research_service import AdaptiveResearchService
from core.trading_safety import TradingStore


def test_adaptive_research_skips_malformed_nonfinite_closes(tmp_path):
    path = str(tmp_path / "adaptive.db")
    store = TradingStore(path)
    service = AdaptiveResearchService(store)
    result = service.update_from_closes(
        "EURUSD", "H1", ["bad", None, float("nan"), 1.1000, 1.1010],
        completed_bar_time="2026-01-07T13:00:00+00:00",
    )
    assert result["available"] is True
    assert result["advisory_only"] is True
    assert result["observations"] == 1
    assert result["completed_bar_time"] == "2026-01-07T13:00:00+00:00"


def test_adaptive_research_returns_unavailable_without_two_valid_closes(tmp_path):
    path = str(tmp_path / "adaptive-empty.db")
    service = AdaptiveResearchService(TradingStore(path))
    result = service.update_from_closes("EURUSD", "H1", ["bad", None, float("inf")])
    assert result == {"available": False, "reason": "insufficient completed closes"}


def test_adaptive_research_recovers_from_malformed_persisted_state(tmp_path):
    path = str(tmp_path / "adaptive-malformed.db")
    store = TradingStore(path)
    store.set_state("adaptive_research:EURUSD:H1", {"garch": {"variance": float("nan")}, "hmm": {}})
    result = AdaptiveResearchService(store).update_from_closes("EURUSD", "H1", [1.1, 1.101])
    assert result["available"] is False
    assert result["advisory_only"] is True
    assert "malformed" in result["reason"]
