"""Unit tests for production portfolio123_provider.py."""
import pytest
from unittest.mock import MagicMock, patch
from data.portfolio123_provider import Portfolio123Provider


def test_portfolio123_unconfigured_by_default():
    """Verify provider correctly identifies when API keys are not set."""
    p123 = Portfolio123Provider(api_id=None, api_key=None)
    assert p123.is_configured() is False

    res = p123.screen_run(universe="sp500")
    assert res["status"] == "UNCONFIGURED"
    assert "P123_API_KEY" in res["message"]


def test_portfolio123_configured():
    """Verify is_configured returns True when credentials exist."""
    p123 = Portfolio123Provider(api_id="test_id_123", api_key="test_key_abc")
    assert p123.is_configured() is True


def test_real_http_call_simulation():
    """Verify that when configured, real POST requests are sent to Portfolio123 REST API."""
    p123 = Portfolio123Provider(api_id="my_id", api_key="my_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "jwt_token_xyz", "rows": [{"ticker": "AAPL", "rank": 99.2}]}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        res = p123.screen_run(universe="sp500")
        assert mock_post.called
        assert res["status"] == "SUCCESS"
        assert res["source"] == "REST_API"
        assert "data" in res


def test_real_rank_ranks_http_call():
    """Verify rank_ranks executes POST request to /rank/ranks."""
    p123 = Portfolio123Provider(api_id="my_id", api_key="my_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "jwt_token_xyz", "ranks": [{"ticker": "MSFT", "overall_rank": 95.5}]}

    with patch("requests.post", return_value=mock_resp):
        res = p123.rank_ranks(tickers=["MSFT"])
        assert res["status"] == "SUCCESS"


def test_real_data_universe_http_call():
    """Verify data_universe executes POST request to /data/universe."""
    p123 = Portfolio123Provider(api_id="my_id", api_key="my_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "jwt_token_xyz", "matrix": [{"ticker": "AAPL", "PEExclXor": 28.4}]}

    with patch("requests.post", return_value=mock_resp):
        res = p123.data_universe(universe="sp500")
        assert res["status"] == "SUCCESS"
