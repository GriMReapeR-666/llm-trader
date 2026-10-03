"""Data package for LLM Trader system."""
from __future__ import annotations

from data.market_data_provider import MarketDataProvider, DOMCollector, PerformanceMonitor, fetch_ohlc
from data.data_quality import DataQualityGate, DataQualityResult
from data.data_requirements import evaluate as evaluate_data_requirements
from data.data_source_pipeline import catalog_fred_series
from data.realtime_news_ingest import collect_rss_articles, ingest_articles
from data.mt5_calendar_enhanced import EnhancedMT5CalendarWrapper, CalendarFilter
from data.seeking_alpha_provider import SeekingAlphaProvider
from data.openbb_data_bridge import OpenBBDataBridge
from data.forexfactory_provider import ForexFactoryProvider, ForexFactoryEvent
from data.exness_provider import ExnessProvider, ExnessMarketInsight
from data.cftc_cot_provider import CFTCCOTProvider, COTReportRow
from data.eia_provider import EIAProvider, EIAPetroleumReport
from data.sec_edgar_provider import SECEDGARProvider, SECFilingsRow
from data.global_macro_bridge import GlobalMacroBridge, GlobalMacroSnapshot
from data.llm_router import LLMRouter, CerebrasProvider, OpenRouterProvider

__all__ = [
    "MarketDataProvider",
    "DOMCollector",
    "PerformanceMonitor",
    "fetch_ohlc",
    "DataQualityGate",
    "DataQualityResult",
    "evaluate_data_requirements",
    "catalog_fred_series",
    "collect_rss_articles",
    "ingest_articles",
    "EnhancedMT5CalendarWrapper",
    "CalendarFilter",
    "SeekingAlphaProvider",
    "OpenBBDataBridge",
    "ForexFactoryProvider",
    "ForexFactoryEvent",
    "ExnessProvider",
    "ExnessMarketInsight",
    "CFTCCOTProvider",
    "COTReportRow",
    "EIAProvider",
    "EIAPetroleumReport",
    "SECEDGARProvider",
    "SECFilingsRow",
    "GlobalMacroBridge",
    "GlobalMacroSnapshot",
    "LLMRouter",
    "CerebrasProvider",
    "OpenRouterProvider",
]
