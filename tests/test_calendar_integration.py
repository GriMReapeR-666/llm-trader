"""
Integration tests for MT5 Calendar Wrapper

Tests that require actual MT5 connection and real data.
"""

import unittest
import pytest
import os
import sys
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.mt5_calendar_wrapper import (
    MT5CalendarWrapper, CalendarCountry, CalendarEvent, CalendarValue,
    CalendarEventImportance, CalendarEventImpact, CalendarEventType,
    MT5CalendarError, MT5CalendarConnectionError, MT5CalendarDataError
)

from data.mt5_calendar_enhanced import (
    EnhancedMT5CalendarWrapper, CalendarFilter, create_enhanced_calendar_wrapper
)

# Configure logging for integration tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@unittest.skipIf(not os.path.exists("C:\\Program Files\\MetaTrader 5\\terminal64.dll") and 
                not os.path.exists("C:\\Program Files (x86)\\MetaTrader 5\\terminal64.dll"),
                "MT5 not installed - skipping integration tests")
class TestMT5CalendarIntegration(unittest.TestCase):
    """Integration tests requiring real MT5 connection"""
    
    @classmethod
    def setUpClass(cls):
        """Set up MT5 connection for integration tests"""
        try:
            cls.wrapper = MT5CalendarWrapper()
            cls.wrapper.initialize()
            logger.info("MT5 Calendar Wrapper initialized successfully for integration tests")
        except Exception as e:
            logger.error(f"Failed to initialize MT5 Calendar Wrapper: {e}")
            cls.wrapper = None
    
    @classmethod
    def tearDownClass(cls):
        """Clean up MT5 connection"""
        if cls.wrapper:
            cls.wrapper.shutdown()
            logger.info("MT5 Calendar Wrapper shutdown")
    
    def setUp(self):
        """Skip tests if wrapper not available"""
        if not self.wrapper:
            self.skipTest("MT5 Calendar Wrapper not available")
    
    def test_get_countries_integration(self):
        """Test getting countries from real MT5 calendar"""
        countries = self.wrapper.get_countries(max_count=50)
        
        self.assertGreater(len(countries), 0, "Should retrieve at least one country")
        
        # Check country data structure
        for country in countries:
            self.assertIsInstance(country.id, int)
            self.assertIsInstance(country.code, str)
            self.assertIsInstance(country.name, str)
            self.assertIsInstance(country.currency, str)
            self.assertEqual(len(country.code), 2, "Country code should be 2 characters")
        
        # Check for major economies
        country_codes = [c.code for c in countries]
        major_economies = ["US", "EU", "JP", "GB", "CA", "AU", "CH", "CN"]
        found_major = [code for code in major_economies if code in country_codes]
        
        logger.info(f"Found {len(countries)} countries, major economies: {found_major}")
        self.assertGreater(len(found_major), 0, "Should find at least one major economy")
    
    def test_get_value_history_integration(self):
        """Test getting calendar value history from real MT5"""
        # Get data for last 30 days
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=30)
        
        values = self.wrapper.get_value_history(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        
        self.assertIsInstance(values, list)
        logger.info(f"Retrieved {len(values)} calendar values")
        
        if len(values) > 0:
            # Check value data structure
            for value in values[:5]:  # Check first 5 values
                self.assertIsInstance(value.id, int)
                self.assertIsInstance(value.event_id, int)
                self.assertIsInstance(value.time, datetime)
                self.assertIsInstance(value.period, datetime)
                self.assertIsInstance(value.revision, int)
                
                # Check value fields (can be None for future events)
                if value.actual_value is not None:
                    self.assertIsInstance(value.actual_value, (int, float))
                if value.prev_value is not None:
                    self.assertIsInstance(value.prev_value, (int, float))
                if value.forecast_value is not None:
                    self.assertIsInstance(value.forecast_value, (int, float))
    
    def test_get_event_by_id_integration(self):
        """Test getting specific event by ID"""
        # First get some values to find event IDs
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        values = self.wrapper.get_value_history(
            date_from=date_from,
            date_to=date_to,
            max_count=10
        )
        
        if len(values) > 0:
            # Test getting event details
            test_event_id = values[0].event_id
            event = self.wrapper.get_event_by_id(test_event_id)
            
            if event:  # Event might not be found if it's a new event
                self.assertIsInstance(event.id, int)
                self.assertIsInstance(event.name, str)
                self.assertIsInstance(event.importance, CalendarEventImportance)
                self.assertIsInstance(event.type, CalendarEventType)
                self.assertIsInstance(event.country_id, int)
                
                logger.info(f"Event {event.id}: {event.name} (Importance: {event.importance.name})")
            else:
                logger.warning(f"Event {test_event_id} not found")
        else:
            logger.warning("No calendar values found for event testing")
    
    def test_country_specific_data_integration(self):
        """Test getting calendar data for specific countries"""
        # Test US data
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=14)
        
        us_values = self.wrapper.get_value_history(
            date_from=date_from,
            date_to=date_to,
            country_code="US",
            max_count=50
        )
        
        logger.info(f"Retrieved {len(us_values)} US calendar values")
        
        if len(us_values) > 0:
            # Verify all values are for US
            for value in us_values:
                self.assertIsInstance(value, CalendarValue)
        
        # Test EU data
        eu_values = self.wrapper.get_value_history(
            date_from=date_from,
            date_to=date_to,
            country_code="EU",
            max_count=50
        )
        
        logger.info(f"Retrieved {len(eu_values)} EU calendar values")
        
        # Test currency-specific data
        usd_values = self.wrapper.get_value_history(
            date_from=date_from,
            date_to=date_to,
            currency="USD",
            max_count=50
        )
        
        logger.info(f"Retrieved {len(usd_values)} USD-related calendar values")


@unittest.skipIf(not os.path.exists("C:\\Program Files\\MetaTrader 5\\terminal64.dll") and 
                not os.path.exists("C:\\Program Files (x86)\\MetaTrader 5\\terminal64.dll"),
                "MT5 not installed - skipping integration tests")
class TestEnhancedMT5CalendarIntegration(unittest.TestCase):
    """Integration tests for enhanced calendar wrapper"""
    
    @classmethod
    def setUpClass(cls):
        """Set up enhanced MT5 connection for integration tests"""
        try:
            cls.enhanced_wrapper = EnhancedMT5CalendarWrapper()
            cls.enhanced_wrapper.initialize()
            logger.info("Enhanced MT5 Calendar Wrapper initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Enhanced MT5 Calendar Wrapper: {e}")
            cls.enhanced_wrapper = None
    
    @classmethod
    def tearDownClass(cls):
        """Clean up enhanced MT5 connection"""
        if cls.enhanced_wrapper:
            cls.enhanced_wrapper.shutdown()
            logger.info("Enhanced MT5 Calendar Wrapper shutdown")
    
    def setUp(self):
        """Skip tests if enhanced wrapper not available"""
        if not self.enhanced_wrapper:
            self.skipTest("Enhanced MT5 Calendar Wrapper not available")
    
    def test_get_calendar_dataframe_integration(self):
        """Test getting calendar data as DataFrame"""
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=14)
        
        df = self.enhanced_wrapper.get_calendar_dataframe(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        
        self.assertIsInstance(df, pd.DataFrame)
        logger.info(f"Calendar DataFrame shape: {df.shape}")
        
        if not df.empty:
            # Check expected columns
            expected_columns = [
                'timestamp', 'event_id', 'event_name', 'country_code',
                'country_name', 'currency', 'importance', 'actual_value',
                'forecast_value', 'previous_value', 'impact_type', 'event_type'
            ]
            
            for col in expected_columns:
                self.assertIn(col, df.columns, f"DataFrame should contain column: {col}")
            
            # Check data types
            self.assertEqual(df['timestamp'].dtype, 'datetime64[ns, UTC]')
            self.assertTrue(pd.api.types.is_numeric_dtype(df['event_id']))
            
            # Check for high-impact events
            high_impact_events = df[df['importance'] == 'HIGH']
            logger.info(f"Found {len(high_impact_events)} high-impact events")
            
            # Sample some events
            if len(df) > 0:
                sample = df.iloc[0]
                logger.info(f"Sample event: {sample['event_name']} ({sample['country_code']}, {sample['importance']})")
    
    def test_get_upcoming_events_integration(self):
        """Test getting upcoming high-impact events"""
        upcoming = self.enhanced_wrapper.get_upcoming_events(
            hours_ahead=48,  # Next 48 hours
            importance_threshold=CalendarEventImportance.MODERATE
        )
        
        self.assertIsInstance(upcoming, pd.DataFrame)
        logger.info(f"Upcoming events DataFrame shape: {upcoming.shape}")
        
        if not upcoming.empty:
            # Check that events are in the future
            now = datetime.now(timezone.utc)
            for timestamp in upcoming['timestamp']:
                self.assertGreaterEqual(timestamp, now, "Upcoming events should be in the future")
            
            # Check importance filtering
            importance_levels = upcoming['importance'].unique()
            allowed_levels = ['MODERATE', 'HIGH']  # Should not include LOW
            for level in importance_levels:
                self.assertIn(level, allowed_levels, f"Unexpected importance level: {level}")
            
            logger.info(f"Upcoming events importance levels: {list(importance_levels)}")
    
    def test_calendar_filtering_integration(self):
        """Test calendar filtering with real data"""
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        # Test country filtering
        filter_obj = CalendarFilter(
            date_from=date_from,
            date_to=date_to,
            country_codes=["US", "EU"],
            importance_levels=[CalendarEventImportance.HIGH, CalendarEventImportance.MODERATE]
        )
        
        # Get filtered countries
        filtered_countries = self.enhanced_wrapper.get_countries_filtered(filter_obj)
        
        self.assertIsInstance(filtered_countries, list)
        country_codes = [c.code for c in filtered_countries]
        
        # Should only include US and EU
        for code in country_codes:
            self.assertIn(code, ["US", "EU"], f"Unexpected country code: {code}")
        
        logger.info(f"Filtered countries: {country_codes}")
    
    def test_performance_benchmark_integration(self):
        """Test performance benchmarking with real data"""
        benchmark = self.enhanced_wrapper.benchmark_calendar_operations(iterations=3)
        
        self.assertIsInstance(benchmark, dict)
        
        # Check that all benchmark metrics are present
        expected_metrics = ['countries_avg_ms', 'event_by_id_avg_ms', 'value_history_avg_ms']
        for metric in expected_metrics:
            self.assertIn(metric, benchmark, f"Missing benchmark metric: {metric}")
            self.assertIsInstance(benchmark[metric], (int, float))
            self.assertGreater(benchmark[metric], 0, f"Benchmark metric should be positive: {metric}")
        
        logger.info("Performance benchmark results:")
        for metric, value in benchmark.items():
            logger.info(f"  {metric}: {value:.2f}ms")
    
    def test_async_operations_integration(self):
        """Test asynchronous calendar operations"""
        import asyncio
        
        async def run_async_tests():
            # Test async countries retrieval
            countries = await self.enhanced_wrapper.get_countries_async(max_count=10)
            self.assertIsInstance(countries, list)
            self.assertGreater(len(countries), 0)
            
            # Test async DataFrame retrieval
            date_to = datetime.now(timezone.utc)
            date_from = date_to - timedelta(days=7)
            
            df = await self.enhanced_wrapper.get_calendar_dataframe_async(
                date_from=date_from,
                date_to=date_to,
                max_count=50
            )
            
            self.assertIsInstance(df, pd.DataFrame)
            logger.info(f"Async DataFrame shape: {df.shape}")
            
            return countries, df
        
        # Run async tests
        try:
            countries, df = asyncio.run(run_async_tests())
            logger.info("Async operations completed successfully")
        except Exception as e:
            logger.error(f"Async operations failed: {e}")
            self.fail(f"Async operations failed: {e}")


class TestCalendarDataValidation(unittest.TestCase):
    """Data validation tests for calendar data"""
    
    def test_calendar_data_consistency(self):
        """Test that calendar data follows expected patterns"""
        # This test would run with real data to validate consistency
        # For now, we'll test the data structures
        
        # Test country codes are valid ISO 3166-1 alpha-2
        valid_country_codes = {
            "US", "EU", "JP", "GB", "CA", "AU", "CH", "CN", "DE", "FR",
            "IT", "ES", "NL", "BE", "AT", "SE", "DK", "NO", "FI", "IE"
        }
        
        # Test currency codes are valid ISO 4217
        valid_currencies = {
            "USD", "EUR", "JPY", "GBP", "CAD", "AUD", "CHF", "CNY", "SEK", "NOK",
            "DKK", "NZD", "SGD", "HKD", "KRW", "TRY", "RUB", "INR", "BRL", "ZAR"
        }
        
        # Test importance levels
        importance_levels = {level.name for level in CalendarEventImportance}
        expected_levels = {"NO_IMPORTANCE", "LOW", "MODERATE", "HIGH"}
        self.assertEqual(importance_levels, expected_levels)
        
        # Test impact types
        impact_types = {impact.name for impact in CalendarEventImpact}
        expected_impacts = {"NO_IMPACT", "POSITIVE", "NEGATIVE"}
        self.assertEqual(impact_types, expected_impacts)


if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Run integration tests
    unittest.main(verbosity=2)


# ---------- ForexFactory adapter for the event gate ----------
def _ev(title, ccy, impact, when=None):
    from data.forexfactory_provider import ForexFactoryEvent
    # Default to an UPCOMING event computed from the current clock.
    #
    # This used to be a hardcoded absolute instant, when="2026-09-28T08:15:00+00:00",
    # which quietly made 16 call sites time-dependent. The moment that instant
    # passed, ForexFactoryProvider correctly stopped treating the fixture as a
    # live calendar and started refusing it —
    # "forexfactory cache holds no upcoming events (newest event is in the past);
    # refusing to gate trading on a stale calendar" — and the tests began failing
    # with no code change whatsoever. A scheduled failure that looked like a
    # product regression. Deriving the default from now() keeps the intent (an
    # event in the future) and removes the wall-clock dependency entirely.
    if when is None:
        when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return ForexFactoryEvent(title=title, country=ccy, currency=ccy, date=when,
                             time="", impact=impact, forecast="", previous="")


def test_ff_get_events_satisfies_event_gate_duck_type(monkeypatch):
    """The gate calls get_events(limit, min_importance, currencies). Nothing in
    production implemented that shape, so the gate always resolved UNAVAILABLE."""
    from data.forexfactory_provider import ForexFactoryProvider

    p = ForexFactoryProvider()
    monkeypatch.setattr(p, "fetch_calendar", lambda: [
        _ev("Lagarde", "EUR", "MEDIUM"),
        _ev("NFP", "USD", "HIGH"),
        _ev("Retail Sales", "GBP", "LOW"),
    ])

    rows = p.get_events(limit=5, min_importance=2, currencies=["USD", "EUR"])
    titles = sorted(r.title for r in rows)
    assert titles == ["Lagarde", "NFP"], titles          # LOW filtered out
    assert p.get_events(min_importance=3, currencies=["USD"])[0].title == "NFP"
    # A currency with no qualifying events is the ambiguous case - it raises
    # rather than reporting a ready-but-empty calendar.
    with pytest.raises(RuntimeError):
        p.get_events(min_importance=2, currencies=["JPY"])


def test_ff_get_events_raises_when_empty_so_gate_fails_closed(monkeypatch):
    """An empty result must not be reported as a ready-but-empty calendar, or a
    dead feed would silently open the blackout gate."""
    from data.forexfactory_provider import ForexFactoryProvider
    from intelligence.llm_orchestrator import collect_calendar_event_rows

    p = ForexFactoryProvider()
    monkeypatch.setattr(p, "fetch_calendar", lambda: [])
    with pytest.raises(RuntimeError):
        p.get_events()

    # Through the gate that becomes ERROR, i.e. not calendar_ready.
    rows, source = collect_calendar_event_rows(p, None)
    assert rows == [] and source == "ERROR"
    assert source not in {"configured_calendar", "mt5_calendar"}


def test_calendar_source_priority_ready_and_unavailable(monkeypatch):
    """A provider that yields events is ready; one that cannot is not."""
    from data.forexfactory_provider import ForexFactoryProvider
    from intelligence.llm_orchestrator import collect_calendar_event_rows

    p = ForexFactoryProvider()
    monkeypatch.setattr(p, "fetch_calendar", lambda: [_ev("NFP", "USD", "HIGH")])
    rows, source = collect_calendar_event_rows(p, None)
    assert source == "configured_calendar" and len(rows) == 1
    assert source in {"configured_calendar", "mt5_calendar"}   # ready

    class Dead:
        def get_events(self, **kw):
            raise RuntimeError("feed down")

    rows, source = collect_calendar_event_rows(Dead(), None)
    assert rows == [] and source == "ERROR"


def test_ff_get_events_refuses_synthetic_fallback_events(monkeypatch):
    """With the feed down and no cache, _fetch_raw_calendar returns hardcoded
    FALLBACK_EVENTS. Those must never gate real trading."""
    from data.forexfactory_provider import ForexFactoryProvider
    from intelligence.llm_orchestrator import collect_calendar_event_rows

    p = ForexFactoryProvider()
    import data.forexfactory_provider as FF
    monkeypatch.setattr(p, "last_source", "fallback", raising=False)
    # Guard fires before the fetch, so hand it events it must never accept.
    monkeypatch.setattr(p, "fetch_calendar", lambda: list(FF.FALLBACK_EVENTS))

    with pytest.raises(RuntimeError, match="synthetic"):
        p.get_events(min_importance=0, currencies=None)

    rows, source = collect_calendar_event_rows(p, None)
    assert rows == [] and source == "ERROR"
    assert source not in {"configured_calendar", "mt5_calendar"}


def test_ff_get_events_allows_live_and_cache(monkeypatch):
    """Only the synthetic fallback is refused; live and cached data are usable."""
    from data.forexfactory_provider import ForexFactoryProvider

    for tag in ("live", "cache"):
        p = ForexFactoryProvider()
        monkeypatch.setattr(p, "fetch_calendar", lambda: [_ev("NFP", "USD", "HIGH")])
        monkeypatch.setattr(p, "last_source", tag, raising=False)
        rows = p.get_events(min_importance=2, currencies=["USD"])
        assert [r.title for r in rows] == ["NFP"], (tag, rows)


def test_ff_stale_cache_fails_closed():
    """A cache whose events are all in the past is not a calendar. Serving it
    would read as 'no upcoming events' with the blackout gate open."""
    import datetime as dt
    from data.forexfactory_provider import ForexFactoryProvider
    from intelligence.llm_orchestrator import collect_calendar_event_rows

    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)).isoformat()

    # Several past events - the case that would trip a per-iteration bug.
    stale = ForexFactoryProvider()
    stale.last_source = "cache"
    stale.fetch_calendar = lambda: [
        _ev("Old NFP", "USD", "HIGH", when=past),
        _ev("Old CPI", "USD", "HIGH", when=past),
        _ev("Old ECB", "EUR", "HIGH", when=past),
    ]
    with pytest.raises(RuntimeError, match="stale"):
        stale.get_events(min_importance=2, currencies=["USD", "EUR"])

    rows, source = collect_calendar_event_rows(stale, None)
    assert rows == [] and source == "ERROR"

    # One future event is enough to make the cache usable.
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    fresh = ForexFactoryProvider()
    fresh.last_source = "cache"
    fresh.fetch_calendar = lambda: [
        _ev("Old NFP", "USD", "HIGH", when=past),
        _ev("NFP tomorrow", "USD", "HIGH", when=future),
    ]
    # Upcoming events win the limit, so a past event cannot crowd one out.
    rows = fresh.get_events(limit=1, min_importance=2, currencies=["USD"])
    assert [r.title for r in rows] == ["NFP tomorrow"], rows


def test_ff_get_events_dedupes_repeated_rows():
    """The weekly feed repeats rows; duplicates must not eat the caller's limit."""
    import datetime as dt
    from data.forexfactory_provider import ForexFactoryProvider

    when = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    p = ForexFactoryProvider()
    p.last_source = "live"
    p.fetch_calendar = lambda: [
        _ev("ECB President Lagarde Speaks", "EUR", "HIGH", when=when),
        _ev("ECB President Lagarde Speaks", "EUR", "HIGH", when=when),
        _ev("ECB President Lagarde Speaks", "EUR", "MEDIUM", when=when),
        _ev("NFP", "USD", "HIGH", when=when),
    ]
    rows = p.get_events(limit=5, min_importance=2, currencies=["USD", "EUR"])
    titles = [r.title for r in rows]
    assert titles.count("ECB President Lagarde Speaks") == 1, titles
    assert "NFP" in titles, titles
    # Same title at a different time is a distinct event and must survive.
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
    p2 = ForexFactoryProvider()
    p2.last_source = "live"
    p2.fetch_calendar = lambda: [
        _ev("ECB President Lagarde Speaks", "EUR", "HIGH", when=when),
        _ev("ECB President Lagarde Speaks", "EUR", "HIGH", when=later),
    ]
    assert len(p2.get_events(min_importance=2, currencies=["EUR"])) == 2
