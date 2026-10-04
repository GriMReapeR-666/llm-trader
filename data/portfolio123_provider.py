"""Portfolio123 Provider — Real REST API client & Data Provider.

Real Data Integration:
----------------------
Portfolio123 requires a Data Miner API Key (API ID + API Key from portfolio123.com account settings).
When credentials (`P123_API_ID` & `P123_API_KEY`) are provided in `.env`, this provider executes real
HTTP REST requests to `https://api.portfolio123.com:8443` via token authentication (`/auth`).

If credentials are NOT provided, the provider explicitly reports `UNCONFIGURED (API key required)`
so the system never confuses missing credentials with real live market data.
"""
from __future__ import annotations

import logging
import os
import requests
from typing import Any, Dict, List, Optional

log = logging.getLogger("Portfolio123Provider")

try:
    import p123api  # type: ignore[import-untyped]
    P123API_AVAILABLE = True
except ImportError:
    P123API_AVAILABLE = False


class Portfolio123Provider:
    """Portfolio123 Production REST API Provider.

    Endpoints:
    - POST /auth             -> Get Bearer JWT token
    - POST /screen/run       -> Run stock screens
    - POST /screen/backtest  -> Screen backtest performance
    - POST /rank/ranks       -> Multi-factor stock rankings
    - POST /rank/perf        -> Decile bucket performance
    - POST /data/universe    -> Point-in-time factor matrix
    """

    BASE_URL = "https://api.portfolio123.com:8443"

    def __init__(self, api_id: Optional[str] = None, api_key: Optional[str] = None):
        self.api_id = api_id or os.getenv("P123_API_ID") or os.getenv("PORTFOLIO123_API_ID")
        self.api_key = api_key or os.getenv("P123_API_KEY") or os.getenv("PORTFOLIO123_API_KEY")
        self._auth_token: Optional[str] = None
        self.client = None

        if P123API_AVAILABLE and self.api_id and self.api_key:
            try:
                self.client = p123api.Client(api_id=self.api_id, api_key=self.api_key)
                log.info("✅ Portfolio123 p123api client initialized")
            except Exception as exc:
                log.warning("p123api init error: %s", exc)

    def is_configured(self) -> bool:
        """True only if valid API credentials are set in environment."""
        return bool(self.api_id and self.api_key)

    def _get_auth_token(self) -> Optional[str]:
        """Obtain a session bearer token from Portfolio123 REST /auth endpoint."""
        if self._auth_token:
            return self._auth_token
        if not self.is_configured():
            return None
        try:
            from core.http_retry import request_with_retry
            resp = request_with_retry(
                None, "POST",
                f"{self.BASE_URL}/auth",
                max_retries=3,
                json={"apiId": self.api_id, "apiKey": self.api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._auth_token = data.get("token") or data.get("apiKey")
                return self._auth_token
            else:
                log.warning("Portfolio123 /auth HTTP %d: %s", resp.status_code, resp.text)
        except Exception as exc:
            log.error("Portfolio123 /auth connection error: %s", exc)
        return None

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make an authenticated POST request to Portfolio123 REST API."""
        if not self.is_configured():
            return {
                "status": "UNCONFIGURED",
                "error": "P123_API_ID and P123_API_KEY not set in .env. Real data requires a Portfolio123 Data Miner API key.",
                "configured": False,
            }

        token = self._get_auth_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                return {"status": "SUCCESS", "source": "REST_API", "data": resp.json()}
            return {
                "status": "HTTP_ERROR",
                "code": resp.status_code,
                "error": resp.text,
            }
        except Exception as exc:
            return {"status": "CONNECTION_ERROR", "error": str(exc)}

    # ── Real Data Methods ────────────────────────────────────────────────────────

    def screen_run(
        self,
        screen_def: Optional[Dict[str, Any]] = None,
        as_of_date: Optional[str] = None,
        universe: str = "sp500",
    ) -> Dict[str, Any]:
        """Execute real multi-factor stock screen on Portfolio123."""
        payload = {
            "screen": screen_def or {"type": "stock", "universe": universe},
            "asOfDt": as_of_date,
        }

        # 1. Use p123api wrapper if available
        if self.client:
            try:
                res = self.client.screen_run(payload)
                return {"status": "SUCCESS", "source": "p123api", "data": res}
            except Exception as exc:
                log.warning("p123api screen_run failed: %s", exc)

        # 2. Direct REST HTTP API call
        if self.is_configured():
            return self._post("/screen/run", payload)

        # 3. Unconfigured explicit status
        return {
            "status": "UNCONFIGURED",
            "message": "P123_API_KEY is missing. Real stock screen data requires a Portfolio123 Data Miner API key.",
            "data": [],
        }

    def screen_backtest(
        self,
        screen_def: Optional[Dict[str, Any]] = None,
        start_date: str = "2020-01-01",
        end_date: str = "2025-01-01",
        rebalance_days: int = 28,
    ) -> Dict[str, Any]:
        """Execute real screen backtest on Portfolio123."""
        payload = {
            "screen": screen_def or {"type": "stock", "universe": "sp500"},
            "startDt": start_date,
            "endDt": end_date,
            "rebalanceFrequency": f"{rebalance_days}D",
        }

        if self.client:
            try:
                res = self.client.screen_backtest(payload)
                return {"status": "SUCCESS", "source": "p123api", "data": res}
            except Exception as exc:
                log.warning("p123api screen_backtest failed: %s", exc)

        if self.is_configured():
            return self._post("/screen/backtest", payload)

        return {
            "status": "UNCONFIGURED",
            "message": "P123_API_KEY is missing. Real backtest metrics require Portfolio123 API credentials.",
            "data": {},
        }

    def rank_ranks(
        self,
        ranking_system: str = "Balanced Value & Growth",
        as_of_date: Optional[str] = None,
        tickers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Fetch real multi-factor rank scores for symbols."""
        payload = {
            "rankingSystem": ranking_system,
            "asOfDt": as_of_date,
            "tickers": tickers or ["AAPL", "MSFT", "GOOGL", "AMZN"],
        }

        if self.client:
            try:
                res = self.client.rank_ranks(payload)
                return {"status": "SUCCESS", "source": "p123api", "data": res}
            except Exception as exc:
                log.warning("p123api rank_ranks failed: %s", exc)

        if self.is_configured():
            return self._post("/rank/ranks", payload)

        return {
            "status": "UNCONFIGURED",
            "message": "P123_API_KEY is missing. Real factor ranks require Portfolio123 API credentials.",
            "data": [],
        }

    def rank_perf(self, ranking_system: str = "Balanced Value & Growth") -> Dict[str, Any]:
        """Fetch real ranking system decile bucket performance."""
        payload = {"rankingSystem": ranking_system}

        if self.client:
            try:
                res = self.client.rank_perf(payload)
                return {"status": "SUCCESS", "source": "p123api", "data": res}
            except Exception as exc:
                log.warning("p123api rank_perf failed: %s", exc)

        if self.is_configured():
            return self._post("/rank/perf", payload)

        return {
            "status": "UNCONFIGURED",
            "message": "P123_API_KEY is missing. Real decile performance requires Portfolio123 API credentials.",
            "data": {},
        }

    def data_universe(
        self,
        universe: str = "sp500",
        formulas: Optional[List[str]] = None,
        as_of_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch real point-in-time factor data matrix."""
        payload = {
            "universe": universe,
            "formulas": formulas or ["PEExclXor", "Price/Sales", "ROE%", "Debt/Equity", "52WkChg%"],
            "asOfDt": as_of_date,
        }

        if self.client:
            try:
                res = self.client.data_universe(payload)
                return {"status": "SUCCESS", "source": "p123api", "data": res}
            except Exception as exc:
                log.warning("p123api data_universe failed: %s", exc)

        if self.is_configured():
            return self._post("/data/universe", payload)

        return {
            "status": "UNCONFIGURED",
            "message": "P123_API_KEY is missing. Real factor matrices require Portfolio123 API credentials.",
            "data": [],
        }
