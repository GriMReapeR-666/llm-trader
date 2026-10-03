"""OpenBB no-key provider registry — type-classified, dedup-aware.

Every provider is annotated with a *data_type* (what kind of data it produces)
rather than a policy tier.  The tier decision is deferred to
``macro_source_policy.assess_source()`` at ingestion time so that the policy
module remains the single source of truth for allowed/disallowed sources.

Dedup rules
-----------
* FRED series          → already served by ``fredapi`` in api_manager.py
* YFinance OHLCV/quote → already served by ``yahoo_finance_provider.py``
* SEC filing search    → already served by direct HTTP in api_manager.py
* NewsAPI headlines    → already served by ``newsapi-python``
* MT5 calendar/quotes  → already served by MT5 terminal

Only *unique* fetcher endpoints that are **not** covered above are registered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── data-type taxonomy ──────────────────────────────────────────────────────

DATA_TYPES = {
    "central-bank-release":   "Official monetary-policy decisions, balance sheets, FOMC documents",
    "official-statistics":    "Government statistical agencies — GDP, CPI, trade, employment",
    "exchange-data":          "Exchange-originated data — VIX, options, indices, futures curves",
    "regulatory-filing":      "Regulatory/compliance filings — 13F, FTD, XBRL, insider",
    "market-microstructure":  "Dark pool, short interest, OTC aggregate volume",
    "equity-screening":       "Screener, sector comparison, performance ranking",
    "macro-indicators":       "Cross-country macro indicators, trade flows, port volume",
    "factor-model":           "Academic factor model datasets & multi-factor ranking scores",
    "factor-screening":       "Multi-factor equity screener execution and screen backtests",
    "valuation-multiples":    "Broad market valuation ratios (PE, CAPE, earnings yield)",
    "calendar-events":        "IPO calendar, earnings releases, economic calendar",
    "estimates-consensus":    "Forward EPS and sales consensus estimates and analyst targets",
    "transcripts":            "Earnings call transcripts, management prepared remarks and Q&A",
    "quant-ratings":          "Quant score (1.0-5.0), SA author rating, Wall St rating, Factor Grades",
    "fixed-income":           "Yield curves, treasury auctions, bond prices",
    "news-sentiment":         "News headlines, analyst ratings, market movers",
    "regional-exchange":      "Non-US exchange data (TMX / Canada)",
}


@dataclass(frozen=True)
class FetcherEndpoint:
    """One unique data endpoint within a provider."""
    name: str                   # OpenBB fetcher/model name
    router: str                 # e.g. "economy", "equity", "fixedincome"
    endpoint: str               # e.g. "cpi", "price.historical"
    description: str = ""
    skip: bool = False          # True = duplicate of existing source
    skip_reason: str = ""       # Why it was skipped
    default_params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderConfig:
    """Registration entry for one OpenBB no-key provider."""
    name: str                         # openbb provider name
    website: str                      # canonical URL for source attribution
    data_type: str                    # key into DATA_TYPES
    description: str = ""
    source_domain: str = ""           # domain for macro_source_policy matching
    publisher_tag: str = ""           # publisher string for RUNTIME_FEED matching
    rate_limit_rpm: int = 30          # requests per minute (conservative default)
    fetchers: List[FetcherEndpoint] = field(default_factory=list)

    @property
    def active_fetchers(self) -> List[FetcherEndpoint]:
        """Return only non-skipped (unique) fetchers."""
        return [f for f in self.fetchers if not f.skip]


# ── provider definitions ────────────────────────────────────────────────────

_CBOE = ProviderConfig(
    name="cboe",
    website="https://www.cboe.com",
    data_type="exchange-data",
    description="CBOE exchange — VIX, volatility indices, options snapshots, futures curves",
    source_domain="cboe.com",
    publisher_tag="cboe",
    rate_limit_rpm=20,
    fetchers=[
        FetcherEndpoint("CboeAvailableIndices", "index", "available", "List all CBOE indices"),
        FetcherEndpoint("CboeEquityHistorical", "equity", "price.historical", skip=True, skip_reason="YFinance covers equity OHLCV"),
        FetcherEndpoint("CboeEquityQuote", "equity", "quote", skip=True, skip_reason="YFinance covers equity quotes"),
        FetcherEndpoint("CboeEquitySearch", "equity", "search", skip=True, skip_reason="YFinance covers equity search"),
        FetcherEndpoint("CboeFuturesCurve", "derivatives", "futures.curve", "VIX/commodity futures term structure"),
        FetcherEndpoint("CboeIndexConstituents", "index", "constituents", "Index membership (S&P500, Russell)"),
        FetcherEndpoint("CboeIndexHistorical", "index", "price.historical", "VIX, SKEW, PUT index history"),
        FetcherEndpoint("CboeIndexSearch", "index", "search", "Search CBOE indices"),
        FetcherEndpoint("CboeIndexSnapshots", "index", "snapshots", "Real-time index snapshots"),
        FetcherEndpoint("CboeOptionsChains", "derivatives", "options.chains", "Options chain data"),
    ],
)

_ECB = ProviderConfig(
    name="ecb",
    website="https://data.ecb.europa.eu",
    data_type="central-bank-release",
    description="European Central Bank — EUR rates, yield curves, balance of payments",
    source_domain="data.ecb.europa.eu",
    rate_limit_rpm=30,
    fetchers=[
        FetcherEndpoint("ECBBalanceOfPayments", "economy", "balance_of_payments", "Eurozone BoP data"),
        FetcherEndpoint("ECBCurrencyReferenceRates", "currency", "reference_rates", "Daily EUR reference rates"),
        FetcherEndpoint("ECBYieldCurve", "fixedincome", "government.yield_curve", "Euro area yield curve"),
    ],
)

_ECONDB = ProviderConfig(
    name="econdb",
    website="https://www.econdb.com",
    data_type="macro-indicators",
    description="EconDB — cross-country macro indicators, GDP, trade flows, port volumes",
    source_domain="econdb.com",
    publisher_tag="econdb",
    rate_limit_rpm=30,
    fetchers=[
        FetcherEndpoint("EconDbAvailableIndicators", "economy", "available_indicators", "List all available macro indicators", default_params={"query": "gdp"}),
        FetcherEndpoint("EconDbCountryProfile", "economy", "country_profile", "Country macro snapshot", default_params={"country": "united_states"}),
        FetcherEndpoint("EconDbEconomicIndicators", "economy", "indicators", "Cross-country economic indicators", default_params={"symbol": "GDP", "country": "united_states", "frequency": "quarter"}),
        FetcherEndpoint("EconDbExportDestinations", "economy", "export_destinations", skip=True, skip_reason="upstream econdb endpoint returns 500 HTML error"),
        FetcherEndpoint("EconDbGdpNominal", "economy", "gdp.nominal", "Nominal GDP by country"),
        FetcherEndpoint("EconDbGdpReal", "economy", "gdp.real", "Real GDP by country"),
        FetcherEndpoint("EconDbPortVolume", "economy", "shipping.port_volume", "Global port throughput volume"),
        FetcherEndpoint("EconDbYieldCurve", "fixedincome", "government.yield_curve", skip=True, skip_reason="ECB yield curve covers EU; use FRED for US"),
    ],
)

_FAMAFRENCH = ProviderConfig(
    name="famafrench",
    website="https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
    data_type="factor-model",
    description="Fama-French academic factor model datasets",
    source_domain="dartmouth.edu",
    rate_limit_rpm=10,
    fetchers=[
        FetcherEndpoint("FamaFrenchAvailable", "famafrench", "available", "List available FF datasets"),
        FetcherEndpoint("FamaFrenchFactors", "famafrench", "factors", "SMB, HML, UMD factor returns"),
    ],
)

_FEDERAL_RESERVE = ProviderConfig(
    name="federal_reserve",
    website="https://www.federalreserve.gov",
    data_type="central-bank-release",
    description="Federal Reserve — holdings, FOMC docs, money supply, inflation expectations",
    source_domain="federalreserve.gov",
    rate_limit_rpm=20,
    fetchers=[
        FetcherEndpoint("FedCentralBankHoldings", "economy", "central_bank_holdings", "Fed balance sheet SOMA holdings"),
        FetcherEndpoint("FedFederalFundsRate", "fixedincome", "rate.effr", skip=True, skip_reason="FRED covers fed funds rate via fredapi"),
        FetcherEndpoint("FedFomcDocuments", "regulators", "fomc_documents", "FOMC minutes, statements, projections"),
        FetcherEndpoint("FedInflationExpectations", "economy", "inflation_expectations", "Survey-based inflation expectations"),
        FetcherEndpoint("FedMoneyMeasures", "economy", "money_measures", "M1/M2 money supply aggregates"),
        FetcherEndpoint("FedOvernightBankFundingRate", "fixedincome", "rate.obfr", skip=True, skip_reason="FRED covers OBFR via fredapi"),
        FetcherEndpoint("FedTreasuryRates", "fixedincome", "government.treasury_rates", skip=True, skip_reason="FRED covers treasury rates"),
        FetcherEndpoint("FedYieldCurve", "fixedincome", "government.yield_curve", "US Treasury yield curve from Fed H.15"),
    ],
)

_FINRA = ProviderConfig(
    name="finra",
    website="https://www.finra.org/finra-data",
    data_type="market-microstructure",
    description="FINRA — equity short interest, OTC/dark pool aggregate volume",
    source_domain="finra.org",
    publisher_tag="finra",
    rate_limit_rpm=15,
    fetchers=[
        FetcherEndpoint("FinraShortInterest", "equity", "short_interest", "Equity short interest by symbol"),
        FetcherEndpoint("FinraOTCAggregate", "equity", "otc_aggregate", "OTC/dark pool aggregate volume"),
    ],
)

_FINVIZ = ProviderConfig(
    name="finviz",
    website="https://finviz.com",
    data_type="equity-screening",
    description="Finviz — stock screener, sector comparison, key metrics",
    source_domain="finviz.com",
    rate_limit_rpm=10,
    fetchers=[
        FetcherEndpoint("FinvizCompareGroups", "equity", "compare.groups", "Sector/industry group comparison"),
        FetcherEndpoint("FinvizEquityProfile", "equity", "profile", skip=True, skip_reason="YFinance covers equity profile"),
        FetcherEndpoint("FinvizEquityScreener", "equity", "screener", "Multi-filter stock screener"),
        FetcherEndpoint("FinvizKeyMetrics", "equity", "key_metrics", "Key financial metrics snapshot"),
        FetcherEndpoint("FinvizPricePerformance", "equity", "price_performance", "Performance ranking by sector/industry"),
    ],
)

_GOVERNMENT_US = ProviderConfig(
    name="government_us",
    website="https://home.treasury.gov",
    data_type="fixed-income",
    description="US Government — Treasury auctions, bond prices, USDA commodity data",
    source_domain="home.treasury.gov",
    rate_limit_rpm=20,
    fetchers=[
        FetcherEndpoint("GovUSCommodityPsdData", "commodity", "psd_data", "USDA commodity production/supply/demand"),
        FetcherEndpoint("GovUSCommodityPsdReport", "commodity", "psd_report", "USDA PSD report"),
        FetcherEndpoint("GovUSTreasuryAuctions", "fixedincome", "government.treasury_auctions", "US Treasury auction results"),
        FetcherEndpoint("GovUSTreasuryPrices", "fixedincome", "government.treasury_prices", "Daily Treasury bond prices"),
        FetcherEndpoint("GovUSWeatherBulletins", "commodity", "weather_bulletins", "USDA weather bulletins"),
    ],
)

_IMF = ProviderConfig(
    name="imf",
    website="https://data.imf.org",
    data_type="official-statistics",
    description="International Monetary Fund — global economic indicators, trade, maritime chokepoints",
    source_domain="data.imf.org",
    rate_limit_rpm=15,
    fetchers=[
        FetcherEndpoint("ImfAvailableIndicators", "economy", "available_indicators", "List available IMF datasets", default_params={"query": "gdp"}),
        FetcherEndpoint("ImfConsumerPriceIndex", "economy", "cpi", skip=True, skip_reason="FRED covers CPI via fredapi"),
        FetcherEndpoint("ImfDirectionOfTrade", "economy", "direction_of_trade", "Bilateral trade flows between countries", default_params={"country": "united_states", "counterpart": "germany"}),
        FetcherEndpoint("ImfEconomicIndicators", "economy", "indicators", "Cross-country macro indicators", default_params={"country": "united_states", "symbol": "WEO::NGDP_RPCH"}),
        FetcherEndpoint("ImfMaritimeChokePointInfo", "economy", "shipping.chokepoint_info", "Global maritime chokepoint throughput"),
        FetcherEndpoint("ImfMaritimeChokePointVolume", "economy", "shipping.chokepoint_volume", "Volume through Suez, Panama, etc.", default_params={"start_date": "2026-09-01"}),
    ],
)

_MULTPL = ProviderConfig(
    name="multpl",
    website="https://www.multpl.com",
    data_type="valuation-multiples",
    description="Multpl — S&P 500 PE ratio, earnings yield, Shiller CAPE",
    source_domain="multpl.com",
    rate_limit_rpm=5,
    fetchers=[
        FetcherEndpoint("MultplSP500Multiples", "index", "sp500_multiples", "S&P 500 PE, CAPE, earnings yield, dividend yield"),
    ],
)

_NASDAQ = ProviderConfig(
    name="nasdaq",
    website="https://www.nasdaq.com",
    data_type="calendar-events",
    description="Nasdaq — IPO calendar, top retailers, most active, economic calendar",
    source_domain="nasdaq.com",
    publisher_tag="nasdaq",
    rate_limit_rpm=15,
    fetchers=[
        FetcherEndpoint("NasdaqTopRetailers", "equity", "discovery.top_retailers", "Top retail-searched tickers"),
        FetcherEndpoint("NasdaqCalendarIPO", "equity", "calendar.ipo", "Upcoming IPO calendar"),
        FetcherEndpoint("NasdaqEconomicCalendar", "economy", "calendar", skip=True, skip_reason="MT5 Python package (5.0.6090) exposes no calendar API; ForexFactory is the live source"),
    ],
)

_SEC = ProviderConfig(
    name="sec",
    website="https://www.sec.gov/cgi-bin/browse-edgar",
    data_type="regulatory-filing",
    description="SEC EDGAR — XBRL financials, 13F holdings, FTD, CIK mapping, insider transactions",
    source_domain="sec.gov",
    rate_limit_rpm=10,
    fetchers=[
        FetcherEndpoint("SecBalanceSheet", "equity", "fundamental.balance", "XBRL-parsed balance sheet"),
        FetcherEndpoint("SecBalanceSheetGrowth", "equity", "fundamental.balance_growth", "Balance sheet growth rates"),
        FetcherEndpoint("SecCashFlow", "equity", "fundamental.cash", "XBRL-parsed cash flow"),
        FetcherEndpoint("SecCashFlowGrowth", "equity", "fundamental.cash_growth", "Cash flow growth rates"),
        FetcherEndpoint("SecCikMap", "regulators", "sec.cik_map", "Ticker to CIK mapping"),
        FetcherEndpoint("SecCompanyFilings", "regulators", "sec.filings", skip=True, skip_reason="api_manager.py already does SEC filing search"),
        FetcherEndpoint("SecCompareCompanyFacts", "equity", "fundamental.compare", "Cross-company XBRL fact comparison"),
        FetcherEndpoint("SecEquityFtd", "equity", "ftd", "Failure-to-deliver data"),
        FetcherEndpoint("SecEquitySearch", "equity", "search", skip=True, skip_reason="YFinance covers equity search"),
        FetcherEndpoint("SecForm13FHR", "regulators", "sec.form_13f", "Institutional 13F-HR holdings"),
        FetcherEndpoint("SecIncomeStatement", "equity", "fundamental.income", "XBRL-parsed income statement"),
        FetcherEndpoint("SecInstitutionalOwnership", "equity", "ownership.institutional", "Institutional ownership changes"),
        FetcherEndpoint("SecRssLitigation", "regulators", "sec.litigation_rss", "SEC litigation releases RSS"),
        FetcherEndpoint("SecSicSearch", "regulators", "sec.sic_search", "SIC industry code search"),
    ],
)

_PORTFOLIO123 = ProviderConfig(
    name="portfolio123",
    website="https://www.portfolio123.com",
    data_type="factor-model",
    description="Portfolio123 — multi-factor stock screening, ranking system scores, bucket performance, point-in-time factor data",
    source_domain="portfolio123.com",
    publisher_tag="portfolio123",
    rate_limit_rpm=15,
    fetchers=[
        FetcherEndpoint("P123ScreenRun", "equity", "factor.screen_run", "Execute multi-factor stock screen"),
        FetcherEndpoint("P123ScreenBacktest", "equity", "factor.screen_backtest", "Execute historical screen backtest"),
        FetcherEndpoint("P123RankRanks", "equity", "factor.rank_ranks", "Multi-factor rank scores (Value, Quality, Momentum, Growth)"),
        FetcherEndpoint("P123RankPerf", "equity", "factor.rank_perf", "Ranking system decile/bucket performance"),
        FetcherEndpoint("P123DataUniverse", "equity", "factor.data_universe", "Point-in-time factor matrix for stock universe"),
    ],
)

_SEEKING_ALPHA = ProviderConfig(
    name="seeking_alpha",
    website="https://seekingalpha.com",
    data_type="calendar-events",
    description="Seeking Alpha — earnings releases calendar, forward EPS/sales estimates, news analysis, call transcripts, quant ratings",
    source_domain="seekingalpha.com",
    publisher_tag="seeking_alpha",
    rate_limit_rpm=15,
    fetchers=[
        FetcherEndpoint("SeekingAlphaUpcomingReleases", "equity", "calendar.earnings", "Upcoming earnings releases schedule"),
        FetcherEndpoint("SAForwardEpsEstimates", "equity", "estimates.forward_eps", "Forward EPS consensus estimates and target range"),
        FetcherEndpoint("SAForwardSalesEstimates", "equity", "estimates.forward_sales", "Forward sales/revenue consensus estimates"),
        FetcherEndpoint("SANewsArticles", "news", "company", "Seeking Alpha headlines, ticker analysis, and editorial market news"),
        FetcherEndpoint("SATranscripts", "equity", "transcripts", "Earnings call transcripts and management Q&A summaries"),
        FetcherEndpoint("SAQuantRatings", "equity", "quant_ratings", "Quant Score (1.0-5.0), Wall St Rating, SA Author Rating, Factor Grades (A+..F)"),
    ],
)

_STOCKGRID = ProviderConfig(
    name="stockgrid",
    website="https://stockgrid.io",
    data_type="market-microstructure",
    description="Stockgrid — dark pool data, short volume by ticker",
    source_domain="stockgrid.io",
    publisher_tag="stockgrid",
    rate_limit_rpm=10,
    fetchers=[
        FetcherEndpoint("StockgridShortInterest", "equity", "darkpool.short_volume", "Short volume data"),
    ],
)

_TMX = ProviderConfig(
    name="tmx",
    website="https://www.tmx.com",
    data_type="regional-exchange",
    description="TMX (Toronto) — Canadian equity, ETF, options, bonds",
    source_domain="tmx.com",
    rate_limit_rpm=15,
    fetchers=[
        FetcherEndpoint("TmxEquityHistorical", "equity", "price.historical", skip=True, skip_reason="YFinance covers CA equity OHLCV via .TO suffix"),
        FetcherEndpoint("TmxBondPrices", "fixedincome", "corporate.bond_prices", "Canadian corporate bond prices"),
        FetcherEndpoint("TmxEtfSearch", "etf", "search", "Canadian ETF search"),
        FetcherEndpoint("TmxEtfHoldings", "etf", "holdings", "Canadian ETF holdings"),
        FetcherEndpoint("TmxIndexConstituents", "index", "constituents", "TSX index constituents"),
        FetcherEndpoint("TmxOptionsChains", "derivatives", "options.chains", "TMX options chains"),
        FetcherEndpoint("TmxPricePerformance", "equity", "price_performance", "TSX equity performance"),
    ],
)

_WSJ = ProviderConfig(
    name="wsj",
    website="https://www.wsj.com",
    data_type="news-sentiment",
    description="Wall Street Journal — market movers, sector performance",
    source_domain="wsj.com",
    rate_limit_rpm=10,
    fetchers=[
        FetcherEndpoint("WsjGainers", "equity", "discovery.gainers", "WSJ top gainers"),
        FetcherEndpoint("WsjLosers", "equity", "discovery.losers", "WSJ top losers"),
        FetcherEndpoint("WsjActive", "equity", "discovery.active", "WSJ most active"),
    ],
)

_YFINANCE_ADDITIVE = ProviderConfig(
    name="yfinance",
    website="https://finance.yahoo.com",
    data_type="equity-screening",
    description="Yahoo Finance additive — screener presets, gainers/losers, ETF info (non-duplicate only)",
    source_domain="finance.yahoo.com",
    rate_limit_rpm=30,
    fetchers=[
        FetcherEndpoint("YFActive", "equity", "discovery.active", "Most active stocks"),
        FetcherEndpoint("YFAggressiveSmallCaps", "equity", "discovery.aggressive_small_caps", "Aggressive small cap screener"),
        FetcherEndpoint("YFGainers", "equity", "discovery.gainers", "Top gainers"),
        FetcherEndpoint("YFLosers", "equity", "discovery.losers", "Top losers"),
        FetcherEndpoint("YFinanceETFInfo", "etf", "info", "ETF overview and holdings summary"),
        FetcherEndpoint("YFinanceAvailableIndices", "index", "available", "Available world indices"),
        FetcherEndpoint("YFinanceEquityHistorical", "equity", "price.historical", skip=True, skip_reason="yahoo_finance_provider.py covers OHLCV"),
        FetcherEndpoint("YFinanceCryptoHistorical", "crypto", "price.historical", skip=True, skip_reason="yahoo_finance_provider.py covers crypto"),
        FetcherEndpoint("YFinanceCurrencyHistorical", "currency", "price.historical", skip=True, skip_reason="yahoo_finance_provider.py covers forex"),
        FetcherEndpoint("YFinanceEquityProfile", "equity", "profile", skip=True, skip_reason="yahoo_finance_provider.py covers profile"),
        FetcherEndpoint("YFinanceEquityQuote", "equity", "quote", skip=True, skip_reason="yahoo_finance_provider.py covers quote"),
        FetcherEndpoint("YFinanceCompanyNews", "news", "company", skip=True, skip_reason="yahoo_finance_provider.py covers news"),
        FetcherEndpoint("YFinanceBalanceSheet", "equity", "fundamental.balance", skip=True, skip_reason="SEC XBRL is authoritative"),
        FetcherEndpoint("YFinanceCashFlow", "equity", "fundamental.cash", skip=True, skip_reason="SEC XBRL is authoritative"),
        FetcherEndpoint("YFinanceKeyMetrics", "equity", "key_metrics", "Key financial ratios from Yahoo"),
    ],
)


# ── public registry ─────────────────────────────────────────────────────────

PROVIDERS: Dict[str, ProviderConfig] = {
    p.name: p for p in [
        _CBOE, _ECB, _ECONDB, _FAMAFRENCH, _FEDERAL_RESERVE,
        _FINRA, _FINVIZ, _GOVERNMENT_US, _IMF, _MULTPL,
        _NASDAQ, _PORTFOLIO123, _SEC, _SEEKING_ALPHA, _STOCKGRID, _TMX,
        _WSJ, _YFINANCE_ADDITIVE,
    ]
}

ALL_DATA_TYPES = DATA_TYPES  # re-export for external use


def active_providers() -> Dict[str, ProviderConfig]:
    """Return only providers that have at least one non-skipped fetcher."""
    return {k: v for k, v in PROVIDERS.items() if v.active_fetchers}


def unique_endpoint_count() -> int:
    """Total number of unique (non-duplicate) fetcher endpoints across all providers."""
    return sum(len(p.active_fetchers) for p in PROVIDERS.values())


def providers_by_type(data_type: str) -> List[ProviderConfig]:
    """Return all providers matching a given data_type."""
    return [p for p in PROVIDERS.values() if p.data_type == data_type]


def dedup_report() -> Dict[str, List[str]]:
    """Return {provider: [skipped fetcher reasons]} for transparency."""
    report: Dict[str, List[str]] = {}
    for p in PROVIDERS.values():
        skipped = [f"{f.name}: {f.skip_reason}" for f in p.fetchers if f.skip]
        if skipped:
            report[p.name] = skipped
    return report
