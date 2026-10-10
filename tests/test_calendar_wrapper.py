"""
Unit tests for MT5 Calendar Wrapper

Tests for both basic and enhanced calendar wrapper functionality.
"""

import unittest
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import ctypes
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.mt5_calendar_wrapper import (
    MT5CalendarWrapper, CalendarCountry, CalendarEvent, CalendarValue,
    CalendarEventImportance, CalendarEventImpact, CalendarEventType,
    CalendarEventSector, CalendarEventFrequency, CalendarEventTimeMode,
    CalendarEventUnit, CalendarEventMultiplier,
    MT5CalendarError, MT5CalendarConnectionError, MT5CalendarDataError,
    LONG_MIN, MICRO_MULTIPLIER
)

from data.mt5_calendar_enhanced import (
    EnhancedMT5CalendarWrapper, CalendarFilter, create_enhanced_calendar_wrapper
)


class TestMT5CalendarWrapper(unittest.TestCase):
    """Test cases for basic MT5 calendar wrapper"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.wrapper = MT5CalendarWrapper(dll_path="mock_dll.dll")
        self.wrapper.dll = Mock()
        self.wrapper._initialized = True
        
        # Mock function pointers
        self.wrapper._calendar_countries = Mock()
        self.wrapper._calendar_event_by_id = Mock()
        self.wrapper._calendar_value_history = Mock()
        self.wrapper._calendar_value_history_by_event = Mock()
        self.wrapper._calendar_value_by_id = Mock()
    
    def test_calendar_country_creation(self):
        """Test CalendarCountry dataclass creation"""
        country = CalendarCountry(
            id=1,
            name="United States",
            code="US",
            currency="USD",
            currency_symbol="$",
            url_name="united-states"
        )
        
        self.assertEqual(country.id, 1)
        self.assertEqual(country.name, "United States")
        self.assertEqual(country.code, "US")
        self.assertEqual(country.currency, "USD")
        self.assertEqual(country.currency_symbol, "$")
        self.assertEqual(country.url_name, "united-states")
    
    def test_calendar_event_creation(self):
        """Test CalendarEvent dataclass creation"""
        event = CalendarEvent(
            id=123,
            type=CalendarEventType.CENTRAL_BANK,
            sector=CalendarEventSector.FINANCIAL,
            frequency=CalendarEventFrequency.MONTHLY,
            time_mode=CalendarEventTimeMode.EXACT_TIME,
            country_id=1,
            unit=CalendarEventUnit.PERCENT,
            importance=CalendarEventImportance.HIGH,
            multiplier=CalendarEventMultiplier.NO_MULTIPLIER,
            digits=2,
            source_url="https://example.com",
            event_code="fed-rate-decision",
            name="Fed Interest Rate Decision"
        )
        
        self.assertEqual(event.id, 123)
        self.assertEqual(event.type, CalendarEventType.CENTRAL_BANK)
        self.assertEqual(event.importance, CalendarEventImportance.HIGH)
        self.assertEqual(event.name, "Fed Interest Rate Decision")
    
    def test_calendar_value_creation(self):
        """Test CalendarValue dataclass creation"""
        now = datetime.now(timezone.utc)
        value = CalendarValue(
            id=456,
            event_id=123,
            time=now,
            period=now,
            revision=0,
            actual_value=2.5,
            prev_value=2.25,
            revised_prev_value=None,
            forecast_value=2.5,
            impact_type=CalendarEventImpact.POSITIVE
        )
        
        self.assertEqual(value.id, 456)
        self.assertEqual(value.event_id, 123)
        self.assertEqual(value.actual_value, 2.5)
        self.assertEqual(value.prev_value, 2.25)
        self.assertIsNone(value.revised_prev_value)
        self.assertEqual(value.impact_type, CalendarEventImpact.POSITIVE)
    
    def test_value_conversion(self):
        """Test MQL5 value conversion logic"""
        # Test normal value conversion
        mql_value = 2500000  # 2.5 * 1e6
        expected = 2.5
        actual = mql_value / MICRO_MULTIPLIER
        self.assertEqual(actual, expected)
        
        # Test LONG_MIN (missing value)
        self.assertEqual(LONG_MIN, -9223372036854775808)
    
    @patch('os.path.exists')
    def test_find_mt5_dll_auto_detection(self, mock_exists):
        """Test automatic MT5 DLL detection"""
        # Mock file existence
        mock_exists.side_effect = [False, True]  # First path doesn't exist, second does
        
        wrapper = MT5CalendarWrapper()
        
        # Should find the second path
        expected_path = "C:\\Program Files (x86)\\MetaTrader 5\\terminal64.dll"
        self.assertEqual(wrapper.dll_path, expected_path)
    
    def test_find_mt5_dll_not_found(self):
        """Test MT5 DLL detection failure"""
        with patch('os.path.exists', return_value=False):
            with self.assertRaises(MT5CalendarConnectionError):
                MT5CalendarWrapper()
    
    @pytest.mark.skipif(sys.platform != "win32", reason="ctypes.WinDLL exists only on Windows")
    def test_initialize_success(self):
        """Test successful initialization"""
        with patch('ctypes.WinDLL') as mock_dll:
            wrapper = MT5CalendarWrapper(dll_path="test.dll")
            
            # Mock the DLL methods
            mock_dll_instance = Mock()
            mock_dll.return_value = mock_dll_instance
            
            result = wrapper.initialize()
            
            self.assertTrue(result)
            self.assertTrue(wrapper._initialized)
            mock_dll.assert_called_once_with("test.dll")
    
    @pytest.mark.skipif(sys.platform != "win32", reason="ctypes.WinDLL exists only on Windows")
    def test_initialize_failure(self):
        """Test initialization failure"""
        with patch('ctypes.WinDLL', side_effect=Exception("DLL error")):
            wrapper = MT5CalendarWrapper(dll_path="test.dll")
            
            with self.assertRaises(MT5CalendarConnectionError):
                wrapper.initialize()
    
    def test_get_countries_success(self):
        """Test successful countries retrieval"""
        # Mock MQL5 structure
        mock_country = Mock()
        mock_country.id = 1
        mock_country.name = b"United States"
        mock_country.code = b"US"
        mock_country.currency = b"USD"
        mock_country.currency_symbol = b"$"
        mock_country.url_name = b"united-states"
        
        # Mock function call
        self.wrapper._calendar_countries.return_value = 1
        
        with patch('data.mt5_calendar_wrapper.MqlCalendarCountry') as mock_struct:
            mock_struct.return_value = mock_country
            
            countries = self.wrapper.get_countries(max_count=1)
            
            self.assertEqual(len(countries), 1)
            self.assertEqual(countries[0].code, "US")
            self.assertEqual(countries[0].name, "United States")
    
    def test_get_countries_error(self):
        """Test countries retrieval with error"""
        self.wrapper._calendar_countries.return_value = -1  # Error code
        
        with self.assertRaises(MT5CalendarDataError) as context:
            self.wrapper.get_countries()
        
        self.assertIn("Internal error", str(context.exception))
    
    def test_get_event_by_id_success(self):
        """Test successful event retrieval by ID"""
        # Mock MQL5 structure
        mock_event = Mock()
        mock_event.id = 123
        mock_event.type = 10  # CENTRAL_BANK
        mock_event.sector = 8  # FINANCIAL
        mock_event.frequency = 3  # MONTHLY
        mock_event.time_mode = 2  # EXACT_TIME
        mock_event.country_id = 1
        mock_event.unit = 2  # PERCENT
        mock_event.importance = 3  # HIGH
        mock_event.multiplier = 0  # NO_MULTIPLIER
        mock_event.digits = 2
        mock_event.source_url = b"https://example.com"
        mock_event.event_code = b"fed-rate-decision"
        mock_event.name = b"Fed Interest Rate Decision"
        
        # Mock function call
        self.wrapper._calendar_event_by_id.return_value = True
        
        with patch('data.mt5_calendar_wrapper.MqlCalendarEvent') as mock_struct:
            mock_struct.return_value = mock_event
            
            event = self.wrapper.get_event_by_id(123)
            
            self.assertIsNotNone(event)
            self.assertEqual(event.id, 123)
            self.assertEqual(event.name, "Fed Interest Rate Decision")
            self.assertEqual(event.importance, CalendarEventImportance.HIGH)
    
    def test_get_event_by_id_not_found(self):
        """Test event retrieval when not found"""
        self.wrapper._calendar_event_by_id.return_value = False
        
        event = self.wrapper.get_event_by_id(999)
        
        self.assertIsNone(event)
    
    def test_get_value_history_success(self):
        """Test successful value history retrieval"""
        # Mock MQL5 structure
        mock_value = Mock()
        mock_value.id = 456
        mock_value.event_id = 123
        mock_value.time = int(datetime.now(timezone.utc).timestamp())
        mock_value.period = int(datetime.now(timezone.utc).timestamp())
        mock_value.revision = 0
        mock_value.actual_value = 2500000  # 2.5 * 1e6
        mock_value.prev_value = 2250000  # 2.25 * 1e6
        mock_value.revised_prev_value = LONG_MIN
        mock_value.forecast_value = 2500000  # 2.5 * 1e6
        mock_value.impact_type = 1  # POSITIVE
        
        # Mock function call
        self.wrapper._calendar_value_history.return_value = 1
        
        date_from = datetime.now(timezone.utc) - timedelta(days=7)
        date_to = datetime.now(timezone.utc)
        
        with patch('data.mt5_calendar_wrapper.MqlCalendarValue') as mock_struct:
            mock_struct.return_value = mock_value
            
            values = self.wrapper.get_value_history(date_from=date_from, date_to=date_to, max_count=1)
            
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0].id, 456)
            self.assertEqual(values[0].actual_value, 2.5)
            self.assertEqual(values[0].prev_value, 2.25)
            self.assertIsNone(values[0].revised_prev_value)
    
    def test_get_value_history_error(self):
        """Test value history retrieval with error"""
        self.wrapper._calendar_value_history.return_value = -5401  # Timeout error
        
        date_from = datetime.now(timezone.utc) - timedelta(days=7)
        date_to = datetime.now(timezone.utc)
        
        with self.assertRaises(MT5CalendarDataError) as context:
            self.wrapper.get_value_history(date_from=date_from, date_to=date_to)
        
        self.assertIn("Request timeout", str(context.exception))
    
    def test_not_initialized_error(self):
        """Test operations when wrapper not initialized"""
        wrapper = MT5CalendarWrapper(dll_path="test.dll")
        wrapper._initialized = False
        
        with self.assertRaises(MT5CalendarConnectionError) as context:
            wrapper.get_countries()
        
        self.assertIn("not initialized", str(context.exception))
    
    def test_shutdown(self):
        """Test wrapper shutdown"""
        self.wrapper.shutdown()
        
        self.assertIsNone(self.wrapper.dll)
        self.assertFalse(self.wrapper._initialized)


class TestEnhancedMT5CalendarWrapper(unittest.TestCase):
    """Test cases for enhanced MT5 calendar wrapper"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.enhanced_wrapper = EnhancedMT5CalendarWrapper(dll_path="mock_dll.dll")
        self.enhanced_wrapper.dll = Mock()
        self.enhanced_wrapper._initialized = True
        
        # Mock base wrapper methods
        self.enhanced_wrapper.get_countries = Mock()
        self.enhanced_wrapper.get_event_by_id = Mock()
        self.enhanced_wrapper.get_value_history = Mock()
        self.enhanced_wrapper.get_events_by_country = Mock()
    
    def test_calendar_filter_creation(self):
        """Test CalendarFilter creation"""
        filter_obj = CalendarFilter(
            date_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2023, 12, 31, tzinfo=timezone.utc),
            country_codes=["US", "EU", "JP"],
            currencies=["USD", "EUR", "JPY"],
            importance_levels=[CalendarEventImportance.HIGH, CalendarEventImportance.MODERATE],
            impact_types=[CalendarEventImpact.POSITIVE]
        )
        
        self.assertEqual(len(filter_obj.country_codes), 3)
        self.assertEqual(len(filter_obj.currencies), 3)
        self.assertIn("US", filter_obj.country_codes)
        self.assertIn("USD", filter_obj.currencies)
        self.assertEqual(len(filter_obj.importance_levels), 2)
    
    def test_calendar_filter_matching(self):
        """Test CalendarFilter matching logic"""
        filter_obj = CalendarFilter(
            country_codes=["US", "EU"],
            currencies=["USD", "EUR"],
            importance_levels=[CalendarEventImportance.HIGH]
        )
        
        # Test country matching
        self.assertTrue(filter_obj.matches_country("US", "USD"))
        self.assertTrue(filter_obj.matches_country("EU", "EUR"))
        self.assertFalse(filter_obj.matches_country("JP", "JPY"))
        
        # Test currency matching
        self.assertTrue(filter_obj.matches_country("US", "USD"))
        self.assertFalse(filter_obj.matches_country("US", "GBP"))
    
    def test_get_countries_filtered(self):
        """Test filtered countries retrieval"""
        # Mock countries
        mock_countries = [
            CalendarCountry(1, "United States", "US", "USD", "$", "united-states"),
            CalendarCountry(2, "European Union", "EU", "EUR", "€", "european-union"),
            CalendarCountry(3, "Japan", "JP", "JPY", "¥", "japan"),
        ]
        
        self.enhanced_wrapper.get_countries.return_value = mock_countries
        
        filter_obj = CalendarFilter(country_codes=["US", "EU"])
        filtered = self.enhanced_wrapper.get_countries_filtered(filter_obj)
        
        self.assertEqual(len(filtered), 2)
        country_codes = [c.code for c in filtered]
        self.assertIn("US", country_codes)
        self.assertIn("EU", country_codes)
        self.assertNotIn("JP", country_codes)
    
    def test_get_high_impact_events(self):
        """Test high impact events retrieval"""
        # Mock values
        now = datetime.now(timezone.utc)
        mock_values = [
            CalendarValue(1, 101, now, now, 0, 2.5, 2.25, None, 2.5, CalendarEventImpact.POSITIVE),
            CalendarValue(2, 102, now, now, 0, 1.8, 1.9, None, 1.85, CalendarEventImpact.NEGATIVE),
        ]
        
        # Mock events
        mock_event_high = CalendarEvent(
            101, CalendarEventType.CENTRAL_BANK, CalendarEventSector.FINANCIAL,
            CalendarEventFrequency.MONTHLY, CalendarEventTimeMode.EXACT_TIME,
            1, CalendarEventUnit.PERCENT, CalendarEventImportance.HIGH,
            CalendarEventMultiplier.NO_MULTIPLIER, 2, "https://example.com",
            "fed-rate", "Fed Rate Decision"
        )
        
        mock_event_low = CalendarEvent(
            102, CalendarEventType.PRICES, CalendarEventSector.NO_SECTOR,
            CalendarEventFrequency.MONTHLY, CalendarEventTimeMode.EXACT_TIME,
            1, CalendarEventUnit.PERCENT, CalendarEventImportance.LOW,
            CalendarEventMultiplier.NO_MULTIPLIER, 1, "https://example.com",
            "cpi", "CPI"
        )
        
        self.enhanced_wrapper.get_value_history.return_value = mock_values
        self.enhanced_wrapper.get_event_by_id.side_effect = [mock_event_high, mock_event_low]
        
        date_from = datetime.now(timezone.utc) - timedelta(days=7)
        date_to = datetime.now(timezone.utc)
        
        high_impact = self.enhanced_wrapper.get_high_impact_events(
            date_from=date_from,
            date_to=date_to,
            importance=CalendarEventImportance.HIGH
        )
        
        # Should only return the high importance event
        self.assertEqual(len(high_impact), 1)
        self.assertEqual(high_impact[0].event_id, 101)
    
    def test_get_calendar_dataframe(self):
        """Test calendar data retrieval as DataFrame"""
        # Mock data
        now = datetime.now(timezone.utc)
        mock_values = [
            CalendarValue(1, 101, now, now, 0, 2.5, 2.25, None, 2.5, CalendarEventImpact.POSITIVE),
        ]
        
        mock_event = CalendarEvent(
            101, CalendarEventType.CENTRAL_BANK, CalendarEventSector.FINANCIAL,
            CalendarEventFrequency.MONTHLY, CalendarEventTimeMode.EXACT_TIME,
            1, CalendarEventUnit.PERCENT, CalendarEventImportance.HIGH,
            CalendarEventMultiplier.NO_MULTIPLIER, 2, "https://example.com",
            "fed-rate", "Fed Rate Decision"
        )
        
        mock_countries = [
            CalendarCountry(1, "United States", "US", "USD", "$", "united-states"),
        ]
        
        self.enhanced_wrapper.get_value_history.return_value = mock_values
        self.enhanced_wrapper.get_event_by_id.return_value = mock_event
        self.enhanced_wrapper.get_countries.return_value = mock_countries
        
        date_from = datetime.now(timezone.utc) - timedelta(days=7)
        date_to = datetime.now(timezone.utc)
        
        df = self.enhanced_wrapper.get_calendar_dataframe(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        
        self.assertIsInstance(df, pd.DataFrame)
        self.assertGreater(len(df), 0)
        expected_columns = ['timestamp', 'event_id', 'event_name', 'country_code', 'actual_value']
        for col in expected_columns:
            self.assertIn(col, df.columns)
    
    def test_get_upcoming_events(self):
        """Test upcoming events retrieval"""
        # Mock DataFrame
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=12)
        
        mock_df = pd.DataFrame({
            'timestamp': [now + timedelta(hours=1), now + timedelta(hours=6)],
            'event_name': ['Fed Rate Decision', 'ECB Rate Decision'],
            'country_code': ['US', 'EU'],
            'importance': ['HIGH', 'HIGH'],
            'actual_value': [2.5, 1.5],
            'forecast_value': [2.5, 1.5],
        })
        
        with patch.object(self.enhanced_wrapper, "get_calendar_dataframe", return_value=mock_df):
            upcoming = self.enhanced_wrapper.get_upcoming_events(
                hours_ahead=24,
                importance_threshold=CalendarEventImportance.HIGH
            )
        
        self.assertIsInstance(upcoming, pd.DataFrame)
        self.assertEqual(len(upcoming), 2)
    
    def test_benchmark_calendar_operations(self):
        """Test calendar operations benchmarking"""
        # Mock countries for benchmark
        mock_countries = [
            CalendarCountry(1, "United States", "US", "USD", "$", "united-states"),
        ]
        
        # Mock events
        mock_event = CalendarEvent(
            101, CalendarEventType.CENTRAL_BANK, CalendarEventSector.FINANCIAL,
            CalendarEventFrequency.MONTHLY, CalendarEventTimeMode.EXACT_TIME,
            1, CalendarEventUnit.PERCENT, CalendarEventImportance.HIGH,
            CalendarEventMultiplier.NO_MULTIPLIER, 2, "https://example.com",
            "fed-rate", "Fed Rate Decision"
        )
        
        # Mock values
        now = datetime.now(timezone.utc)
        mock_values = [
            CalendarValue(1, 101, now, now, 0, 2.5, 2.25, None, 2.5, CalendarEventImpact.POSITIVE),
        ]
        
        self.enhanced_wrapper.get_countries.return_value = mock_countries
        self.enhanced_wrapper.get_events_by_country.return_value = [mock_event]
        self.enhanced_wrapper.get_value_history.return_value = mock_values
        
        benchmark = self.enhanced_wrapper.benchmark_calendar_operations(iterations=2)
        
        self.assertIsInstance(benchmark, dict)
        self.assertIn('countries_avg_ms', benchmark)
        self.assertIn('event_by_id_avg_ms', benchmark)
        self.assertIn('value_history_avg_ms', benchmark)
        
        # Check that times are positive
        for key, value in benchmark.items():
            self.assertGreater(value, 0)
    
    def test_shutdown(self):
        """Test enhanced wrapper shutdown"""
        self.enhanced_wrapper.shutdown()
        
        self.assertIsNone(self.enhanced_wrapper.dll)
        self.assertFalse(self.enhanced_wrapper._initialized)


class TestCalendarWrapperIntegration(unittest.TestCase):
    """Integration tests for calendar wrapper (requires MT5)"""
    
    @unittest.skipIf(not os.path.exists("C:\\Program Files\\MetaTrader 5\\terminal64.dll"), 
                     "MT5 not installed")
    def test_real_mt5_connection(self):
        """Test real MT5 connection (only if MT5 is installed)"""
        try:
            wrapper = MT5CalendarWrapper()
            wrapper.initialize()
            
            # Test basic operations
            countries = wrapper.get_countries(max_count=10)
            self.assertGreater(len(countries), 0)
            
            # Test that we get valid country data
            country = countries[0]
            self.assertIsInstance(country.id, int)
            self.assertIsInstance(country.code, str)
            self.assertIsInstance(country.name, str)
            
            wrapper.shutdown()
            
        except Exception as e:
            self.skipTest(f"MT5 connection failed: {e}")


class TestCalendarFilterAdvanced(unittest.TestCase):
    """Advanced tests for calendar filtering"""
    
    def test_complex_filter_combinations(self):
        """Test complex filter combinations"""
        filter_obj = CalendarFilter(
            date_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2023, 12, 31, tzinfo=timezone.utc),
            country_codes=["US", "EU", "GB", "JP"],
            currencies=["USD", "EUR", "GBP", "JPY"],
            importance_levels=[CalendarEventImportance.HIGH, CalendarEventImportance.MODERATE],
            impact_types=[CalendarEventImpact.POSITIVE, CalendarEventImpact.NEGATIVE],
            event_types=["CENTRAL_BANK", "GDP", "INFLATION"],
            event_names=["Fed Rate Decision", "ECB Rate Decision", "BoE Rate Decision"]
        )
        
        # Test that all filter criteria are properly stored
        self.assertEqual(len(filter_obj.country_codes), 4)
        self.assertEqual(len(filter_obj.currencies), 4)
        self.assertEqual(len(filter_obj.importance_levels), 2)
        self.assertEqual(len(filter_obj.impact_types), 2)
        self.assertEqual(len(filter_obj.event_types), 3)
        self.assertEqual(len(filter_obj.event_names), 3)
    
    def test_filter_edge_cases(self):
        """Test filter edge cases"""
        # Empty filter
        empty_filter = CalendarFilter()
        self.assertIsNone(empty_filter.country_codes)
        self.assertIsNone(empty_filter.currencies)
        self.assertIsNone(empty_filter.importance_levels)
        
        # Single value filters
        single_filter = CalendarFilter(
            country_codes=["US"],
            currencies=["USD"],
            importance_levels=[CalendarEventImportance.HIGH]
        )
        
        self.assertEqual(len(single_filter.country_codes), 1)
        self.assertEqual(len(single_filter.currencies), 1)
        self.assertEqual(len(single_filter.importance_levels), 1)


if __name__ == '__main__':
    # Configure logging for tests
    logging.basicConfig(level=logging.DEBUG)
    
    # Run tests
    unittest.main(verbosity=2)
