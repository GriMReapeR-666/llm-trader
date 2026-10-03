from http_retry import request_with_retry, call_with_retry
from unittest.mock import MagicMock, patch
import requests


def test_request_with_retry_succeeds_after_503():
    session = MagicMock()
    bad = MagicMock(status_code=503)
    good = MagicMock(status_code=200)
    session.request.side_effect = [bad, good]
    with patch("http_retry.time.sleep"):
        result = request_with_retry(session, "GET", "https://example.com", max_retries=3, base_delay=0.01)
    assert result.status_code == 200
    assert session.request.call_count == 2


def test_call_with_retry_raises_after_exhaustion():
    fn = MagicMock(side_effect=RuntimeError("boom"))
    with patch("http_retry.time.sleep"):
        try:
            call_with_retry(fn, max_retries=2, base_delay=0.01)
            assert False, "expected raise"
        except RuntimeError as exc:
            assert "boom" in str(exc)
    assert fn.call_count == 2
