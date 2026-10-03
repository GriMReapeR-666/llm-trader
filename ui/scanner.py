import logging
from typing import List, Dict, Any, Optional


class MarketScanner:
    """
    Scans multiple symbols to find the best trading opportunities
    based on Volatility, Trend Strength, Spread/Liquidity, and
    multi-provider macro context (FRED / OpenBB / currency strength).
    """

    def __init__(self, client, fetch_func=None, api_manager=None, macro_graph=None):
        self.client = client
        self.fetch_func = fetch_func
        self.api_manager = api_manager
        self.macro_graph = macro_graph
        self.log = logging.getLogger("MarketScanner")

        # User requested specific priority list
        self.primary_watchlist = ["XAUUSD", "EURUSD", "GBPJPY"]

        # Broader pools to pick from
        self.majors = ["GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]
        self.crosses = ["EURGBP", "EURJPY", "AUDJPY"]
        self.indices = ["US500", "DE40", "USTEC"]
        self.crypto = ["BTCUSD", "ETHUSD"]

        # Combined pool for auto-selection
        self.pool = self.majors + self.crosses + self.indices + self.crypto
        self._provider_cache: Optional[Dict[str, Any]] = None

    def scan(self, timeframe: str = "H1", limit_to_watchlist: List[str] = None) -> List[Dict[str, Any]]:
        """
        Scan the primary watchlist plus the first three deterministic wildcards
        from the configured pool.
        Returns sorted list of opportunities.
        """
        self.log.info("Scanning Key Markets & Wildcards...")
        opportunities = []
        self._provider_cache = self._collect_provider_snapshot()

        # Without an authenticated client, fail closed rather than selecting a
        # symbol from delayed or synthetic data.
        if not self.client:
            return self._fallback_scan()

        if limit_to_watchlist:
            self.log.info(f"Scanning restricted watchlist: {limit_to_watchlist}")
            scan_list = limit_to_watchlist
        else:
            # Construction: Primary (Always) + 3 deterministic wildcards.
            # Stable ordering makes retries/restarts reproducible and keeps
            # scanner evidence comparable across cycles.
            scan_list = list(self.primary_watchlist)

            wildcards = [candidate for candidate in self.pool
                         if candidate not in scan_list][:3]
            for w in wildcards:
                if w not in scan_list:
                    scan_list.append(w)

        for symbol in scan_list:
            try:
                score = self._analyze_symbol(symbol, timeframe)
                if score:
                    # Boost score for primary watchlist to prefer them if slightly equal
                    if symbol in self.primary_watchlist:
                        score["score"] += 5

                    if score["score"] > 40:  # Lower threshold to allow more candidates
                        opportunities.append(score)
            except Exception as e:
                self.log.debug(f"Failed to scan {symbol}: {e}")
                continue

        # Sort by score descending
        opportunities.sort(key=lambda x: x["score"], reverse=True)
        return opportunities

    def _collect_provider_snapshot(self) -> Dict[str, Any]:
        """Pull multi-provider context once per scan cycle (fail-soft)."""
        snapshot: Dict[str, Any] = {
            "fred": {},
            "currency_strength": {},
            "market_sentiment": {},
            "openbb": {},
            "providers_used": [],
            "cftc": {"status": "UNAVAILABLE", "reason": "no dedicated CFTC fetcher wired"},
        }
        if not self.api_manager:
            self.log.warning("API manager unavailable; scanner scoring will use local indicators only")
            # Try to get basic VIX from MT5 if available
            try:
                import MetaTrader5 as _mt5
                if _mt5 is not None:
                    _vix_tick = _mt5.symbol_info_tick("VIX")
                    if _vix_tick and _vix_tick.bid > 0:
                        snapshot["vix"] = {"value": float(_vix_tick.bid), "source": "MT5"}
                        snapshot["providers_used"].append("vix_mt5")
            except Exception:
                pass
            return snapshot
        try:
            fred = self.api_manager.get_fred_data(["GDP", "CPIAUCSL", "UNRATE", "DFF", "VIXCLS"])
            if fred:
                snapshot["fred"] = fred
                snapshot["providers_used"].append("FRED")
        except Exception as exc:
            self.log.debug("FRED provider snapshot failed: %s", exc)
        try:
            strength = self.api_manager.get_currency_strength()
            if strength:
                snapshot["currency_strength"] = strength
                snapshot["providers_used"].append("currency_strength")
        except Exception as exc:
            self.log.debug("Currency strength snapshot failed: %s", exc)
        try:
            sentiment = self.api_manager.get_market_sentiment()
            if sentiment:
                snapshot["market_sentiment"] = sentiment
                snapshot["providers_used"].append("market_sentiment")
        except Exception as exc:
            self.log.debug("Market sentiment snapshot failed: %s", exc)
        openbb = getattr(self.api_manager, "openbb", None)
        if openbb is not None:
            try:
                summary = openbb.summary() if hasattr(openbb, "summary") else {}
                snapshot["openbb"] = summary or {}
                if summary:
                    snapshot["providers_used"].append("OpenBB")
                # Honest CFTC labeling: mark present only if a registry provider
                # explicitly covers regulatory / futures positioning data.
                health = (summary or {}).get("provider_health", {}) or {}
                for name in health:
                    lowered = str(name).lower()
                    if "cftc" in lowered or "cot" in lowered:
                        snapshot["cftc"] = {"status": "REGISTERED", "provider": name}
                        snapshot["providers_used"].append(f"CFTC:{name}")
                        break
            except Exception as exc:
                self.log.debug("OpenBB provider snapshot failed: %s", exc)
        return snapshot

    def _provider_score(self, symbol: str) -> Dict[str, Any]:
        """Score adjustment from multi-provider macro / strength context."""
        snapshot = self._provider_cache or {}
        score = 0
        reasons: List[str] = []
        base = symbol[:3].upper() if len(symbol) >= 6 else ""
        quote = symbol[3:6].upper() if len(symbol) >= 6 else ""

        fred = snapshot.get("fred") or {}
        if fred:
            score += 5
            reasons.append(f"FRED({len(fred)})")
            vix = fred.get("VIXCLS", {}).get("value")
            try:
                if vix is not None and float(vix) >= 25:
                    score += 5
                    reasons.append("Elevated VIX")
            except (TypeError, ValueError):
                pass

        strength = snapshot.get("currency_strength") or {}
        if strength and base and quote:
            try:
                base_s = float(strength.get(base, 0) or 0)
                quote_s = float(strength.get(quote, 0) or 0)
                differential = base_s - quote_s
                if abs(differential) >= 0.15:
                    score += 10
                    reasons.append(f"FX strength {base}/{quote}")
                elif abs(differential) >= 0.05:
                    score += 5
                    reasons.append(f"Mild FX strength {base}/{quote}")
            except (TypeError, ValueError):
                pass

        sentiment = snapshot.get("market_sentiment") or {}
        if sentiment:
            bias = str(sentiment.get("bias") or sentiment.get("sentiment") or "").upper()
            if bias in {"BULLISH", "RISK_ON"}:
                score += 5
                reasons.append("Risk-on sentiment")
            elif bias in {"BEARISH", "RISK_OFF"}:
                score += 5
                reasons.append("Risk-off sentiment")

        openbb = snapshot.get("openbb") or {}
        if openbb.get("openbb_installed") and int(openbb.get("active_providers", 0) or 0) > 0:
            score += 5
            reasons.append(f"OpenBB({openbb.get('active_providers')} providers)")

        cftc = snapshot.get("cftc") or {}
        if cftc.get("status") == "REGISTERED":
            score += 3
            reasons.append("CFTC/COT registered")

        if self.macro_graph is not None and (base or quote or symbol):
            try:
                entities = [e for e in (base, quote, symbol, "USD") if e]
                edges = self.macro_graph.approved_edges(entities, limit=5)
                if edges:
                    score += min(10, 2 * len(edges))
                    reasons.append(f"Causal edges({len(edges)})")
            except Exception as exc:
                self.log.debug("Macro graph provider score failed: %s", exc)

        return {
            "score": score,
            "reasons": reasons,
            "providers_used": list(snapshot.get("providers_used") or []),
            "cftc": cftc,
        }

    def _analyze_symbol(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        if not self.fetch_func:
            self.log.error("No fetch_func provided to MarketScanner")
            return None

        try:
            df = self.fetch_func(symbol, timeframe, count=100)
        except Exception as e:
            self.log.debug(f"Fetch failed for {symbol}: {e}")
            return None

        from mt5_trader import compute_indicators
        if df is None or getattr(df, "empty", True) or len(df) < 50:
            return None

        df = compute_indicators(df, ["RSI", "ATR(14)", "EMA(50)"])

        score = 0
        reasons = []

        # 1. Trend (20pts)
        close = df["close"].iloc[-1]
        ema = df["ema_50"].iloc[-1]
        atr = df["atr_14"].iloc[-1]

        if close > ema:
            score += 10
            reasons.append("Above EMA50")

        # 2. Volatility (30pts)
        rel_atr = (atr / close) * 100
        if rel_atr > 0.05:
            score += 20
        elif rel_atr > 0.02:
            score += 10

        # 3. RSI Extremes (20pts)
        rsi = df["rsi_14"].iloc[-1]
        if 30 < rsi < 70:
            score += 10  # Healthy range for trend

        # 4. Volume Trend (10pts)
        recent_vol = df["tick_volume"].tail(5).mean()
        prev_vol = df["tick_volume"].tail(10).head(5).mean()
        if recent_vol > prev_vol:
            score += 10
            reasons.append("Rising Vol")

        # 5. News Sentiment
        news_score = self._scan_news(symbol)
        if news_score != 0:
            score += news_score
            reasons.append(f"News Sentiment ({news_score:+})")

        # 6. Multi-provider macro / OpenBB / FRED / strength (GAP-6)
        provider = self._provider_score(symbol)
        if provider["score"]:
            score += int(provider["score"])
            reasons.extend(provider["reasons"])

        return {
            "symbol": symbol,
            "score": score,
            "reasons": reasons,
            "atr": atr,
            "price": close,
            "provider_context": {
                "providers_used": provider.get("providers_used", []),
                "cftc": provider.get("cftc", {}),
                "provider_score": provider.get("score", 0),
            },
        }

    def _scan_news(self, symbol: str) -> int:
        """
        Scan for Economy, Geopolitics, and Symbol-specific news.
        Returns score adjustment (-20 to +20).
        """
        if not self.api_manager:
            return 0

        try:
            base = symbol[:3]
            quote = symbol[3:]
            keywords = [base, quote, "economy", "central bank", "inflation", "war", "geopolitics"]

            articles = self.api_manager.get_news(keywords=keywords, page_size=3)

            if not articles:
                return 0

            sentiment_score = 0
            for art in articles:
                s = art.get("sentiment", "NEUTRAL")
                if s == "BULLISH":
                    sentiment_score += 5
                elif s == "BEARISH":
                    sentiment_score -= 5

            return max(-20, min(20, sentiment_score))

        except Exception as e:
            self.log.debug(f"News scan failed for {symbol}: {e}")
            return 0

    def _fallback_scan(self) -> List[Dict[str, Any]]:
        """
        Fail-closed scan when MT5 is offline.

        External delayed quotes and random mock candidates are not valid
        execution evidence.  The caller may use ``broker_replay.py`` or an
        explicit research fixture, but AUTO trading must not select a symbol
        from an unauthenticated fallback feed.
        """
        if not getattr(MarketScanner, "_fallback_warned", False):
            self.log.warning(
                "MT5 unavailable; AUTO scanner is returning no opportunities and "
                "will not use delayed or synthetic fallback data"
            )
            MarketScanner._fallback_warned = True
        else:
            self.log.debug("MT5 unavailable; AUTO scanner fallback active")
        return []
