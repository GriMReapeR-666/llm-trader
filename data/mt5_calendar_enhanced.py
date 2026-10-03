"""
Enhanced MT5 Calendar Wrapper with Advanced Filtering and Async Support

This module extends the base MT5 calendar wrapper with advanced filtering capabilities,
asynchronous operations, and additional convenience methods.
"""

from __future__ import annotations
import asyncio
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Callable, Union
import pandas as pd
import logging

from mt5_calendar_wrapper import (
    MT5CalendarWrapper, CalendarCountry, CalendarEvent, CalendarValue,
    CalendarEventImportance, CalendarEventImpact, create_calendar_wrapper,
    MT5CalendarError, MT5CalendarConnectionError, MT5CalendarDataError
)

logger = logging.getLogger(__name__)

class CalendarFilter:
    """Advanced filtering options for calendar data"""
    
    def __init__(self,
                 date_from: Optional[datetime] = None,
                 date_to: Optional[datetime] = None,
                 country_codes: Optional[List[str]] = None,
                 currencies: Optional[List[str]] = None,
                 importance_levels: Optional[List[CalendarEventImportance]] = None,
                 impact_types: Optional[List[CalendarEventImpact]] = None,
                 event_types: Optional[List[str]] = None,
                 event_names: Optional[List[str]] = None):
        """
        Initialize calendar filter.
        
        Args:
            date_from: Start date for filtering (inclusive).
            date_to: End date for filtering (inclusive).
            country_codes: List of ISO 3166-1 alpha-2 country codes.
            currencies: List of currency codes (e.g., ['USD', 'EUR']).
            importance_levels: List of importance levels to include.
            impact_types: List of impact types to include.
            event_types: List of event type names to include.
            event_names: List of specific event names to include.
        """
        self.date_from = date_from
        self.date_to = date_to
        self.country_codes = set(country_codes) if country_codes else None
        self.currencies = set(currencies) if currencies else None
        self.importance_levels = set(importance_levels) if importance_levels else None
        self.impact_types = set(impact_types) if impact_types else None
        self.event_types = set(event_types) if event_types else None
        self.event_names = set(event_names) if event_names else None
    
    def matches_country(self, country_code: str, currency: str) -> bool:
        """Check if country/currency matches filter criteria"""
        if self.country_codes and country_code not in self.country_codes:
            return False
        if self.currencies and currency not in self.currencies:
            return False
        return True
    
    def matches_event(self, event: CalendarEvent) -> bool:
        """Check if event matches filter criteria"""
        if self.importance_levels and event.importance not in self.importance_levels:
            return False
        if self.event_names and event.name not in self.event_names:
            return False
        return True
    
    def matches_value(self, value: CalendarValue) -> bool:
        """Check if calendar value matches filter criteria"""
        if self.date_from and value.time < self.date_from:
            return False
        if self.date_to and value.time > self.date_to:
            return False
        if self.impact_types and value.impact_type not in self.impact_types:
            return False
        return True

class EnhancedMT5CalendarWrapper(MT5CalendarWrapper):
    """Enhanced MT5 calendar wrapper with advanced filtering and async support"""
    
    def __init__(self, dll_path: Optional[str] = None, max_workers: int = 4):
        """
        Initialize enhanced calendar wrapper.
        
        Args:
            dll_path: Path to MT5 terminal DLL.
            max_workers: Maximum number of worker threads for async operations.
        """
        super().__init__(dll_path)
        self.max_workers = max_workers
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._countries_cache: Optional[List[CalendarCountry]] = None
        self._events_cache: Dict[int, CalendarEvent] = {}
        self._values_cache: Dict[str, List[CalendarValue]] = {}
    
    def shutdown(self) -> None:
        """Shutdown the wrapper and cleanup resources"""
        super().shutdown()
        if hasattr(self, '_thread_pool'):
            self._thread_pool.shutdown(wait=True)
    
    # ---------- Enhanced Filtering Methods ----------
    
    def get_countries_filtered(self, filter_obj: CalendarFilter) -> List[CalendarCountry]:
        """
        Get countries filtered by criteria.
        
        Args:
            filter_obj: CalendarFilter object with filtering criteria.
            
        Returns:
            List of filtered CalendarCountry objects.
        """
        all_countries = self.get_countries()
        
        if not filter_obj.country_codes and not filter_obj.currencies:
            return all_countries
        
        filtered = []
        for country in all_countries:
            if filter_obj.matches_country(country.code, country.currency):
                filtered.append(country)
        
        logger.info(f"Filtered countries: {len(filtered)} from {len(all_countries)}")
        return filtered
    
    def get_events_by_country(self, country_code: str) -> List[CalendarEvent]:
        """
        Get all events for a specific country.
        
        Note: This requires fetching all values and extracting unique events.
        
        Args:
            country_code: ISO 3166-1 alpha-2 country code.
            
        Returns:
            List of CalendarEvent objects for the country.
        """
        # Get recent values for the country (last 30 days as example)
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=30)
        
        values = self.get_value_history(
            date_from=date_from,
            date_to=date_to,
            country_code=country_code,
            max_count=1000
        )
        
        # Extract unique event IDs
        event_ids = set(value.event_id for value in values)
        
        # Get event details
        events = []
        for event_id in event_ids:
            event = self.get_event_by_id(event_id)
            if event:
                events.append(event)
        
        logger.info(f"Retrieved {len(events)} events for country {country_code}")
        return events
    
    def get_high_impact_events(self, 
                              date_from: datetime,
                              date_to: Optional[datetime] = None,
                              importance: CalendarEventImportance = CalendarEventImportance.HIGH) -> List[CalendarValue]:
        """
        Get high impact economic events.
        
        Args:
            date_from: Start date for the range.
            date_to: End date for the range.
            importance: Minimum importance level (default: HIGH).
            
        Returns:
            List of high impact CalendarValue objects.
        """
        filter_obj = CalendarFilter(
            date_from=date_from,
            date_to=date_to,
            importance_levels=[importance]
        )
        
        # Get all values in date range
        all_values = self.get_value_history(
            date_from=date_from,
            date_to=date_to,
            max_count=2000
        )
        
        # Filter by importance (need to get event details)
        filtered_values = []
        for value in all_values:
            event = self.get_event_by_id(value.event_id)
            if event and event.importance >= importance:
                filtered_values.append(value)
        
        logger.info(f"Found {len(filtered_values)} high impact events")
        return filtered_values
    
    def get_calendar_dataframe(self,
                              date_from: datetime,
                              date_to: Optional[datetime] = None,
                              country_code: Optional[str] = None,
                              currency: Optional[str] = None,
                              max_count: int = 1000) -> pd.DataFrame:
        """
        Get calendar data as a pandas DataFrame for analysis.
        
        Args:
            date_from: Start date for the range.
            date_to: End date for the range.
            country_code: Optional country filter.
            currency: Optional currency filter.
            max_count: Maximum number of records.
            
        Returns:
            DataFrame with calendar data including event details.
        """
        # Get values
        values = self.get_value_history(
            date_from=date_from,
            date_to=date_to,
            country_code=country_code,
            currency=currency,
            max_count=max_count
        )
        
        # Build DataFrame with event details
        data = []
        for value in values:
            event = self.get_event_by_id(value.event_id)
            if event:
                # Get country info
                countries = self.get_countries()
                country = next((c for c in countries if c.id == event.country_id), None)
                
                data.append({
                    'timestamp': value.time,
                    'event_id': value.event_id,
                    'event_name': event.name,
                    'country_code': country.code if country else '',
                    'country_name': country.name if country else '',
                    'currency': country.currency if country else '',
                    'importance': event.importance.name,
                    'actual_value': value.actual_value,
                    'forecast_value': value.forecast_value,
                    'previous_value': value.prev_value,
                    'revised_previous_value': value.revised_prev_value,
                    'impact_type': value.impact_type.name,
                    'event_type': event.type.name,
                    'sector': event.sector.name,
                    'frequency': event.frequency.name,
                    'unit': event.unit.name,
                    'source_url': event.source_url,
                })
        
        df = pd.DataFrame(data)
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)
        
        logger.info(f"Created DataFrame with {len(df)} calendar events")
        return df
    
    # ---------- Asynchronous Methods ----------
    
    async def get_countries_async(self, max_count: int = 100) -> List[CalendarCountry]:
        """Asynchronously get countries"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._thread_pool, self.get_countries, max_count)
    
    async def get_event_by_id_async(self, event_id: int) -> Optional[CalendarEvent]:
        """Asynchronously get event by ID"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._thread_pool, self.get_event_by_id, event_id)
    
    async def get_value_history_async(self,
                                     date_from: datetime,
                                     date_to: Optional[datetime] = None,
                                     country_code: Optional[str] = None,
                                     currency: Optional[str] = None,
                                     max_count: int = 1000) -> List[CalendarValue]:
        """Asynchronously get value history"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._thread_pool, 
            self.get_value_history,
            date_from, date_to, country_code, currency, max_count
        )
    
    async def get_calendar_dataframe_async(self,
                                         date_from: datetime,
                                         date_to: Optional[datetime] = None,
                                         country_code: Optional[str] = None,
                                         currency: Optional[str] = None,
                                         max_count: int = 1000) -> pd.DataFrame:
        """Asynchronously get calendar data as DataFrame"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self.get_calendar_dataframe,
            date_from, date_to, country_code, currency, max_count
        )
    
    # ---------- Batch Operations ----------
    
    def get_multiple_events_by_id(self, event_ids: List[int]) -> Dict[int, Optional[CalendarEvent]]:
        """
        Get multiple events by ID in a batch operation.
        
        Args:
            event_ids: List of event IDs to retrieve.
            
        Returns:
            Dictionary mapping event_id to CalendarEvent object or None.
        """
        results = {}
        for event_id in event_ids:
            results[event_id] = self.get_event_by_id(event_id)
        
        logger.info(f"Retrieved {len([e for e in results.values() if e])} events from {len(event_ids)} requested")
        return results
    
    def get_upcoming_events(self,
                           hours_ahead: int = 24,
                           importance_threshold: CalendarEventImportance = CalendarEventImportance.MODERATE,
                           country_codes: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Get upcoming high-impact economic events.
        
        Args:
            hours_ahead: Number of hours to look ahead.
            importance_threshold: Minimum importance level.
            country_codes: Optional list of country codes to filter.
            
        Returns:
            DataFrame with upcoming events sorted by timestamp.
        """
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=hours_ahead)
        
        # Get calendar data
        df = self.get_calendar_dataframe(
            date_from=now,
            date_to=future,
            max_count=500
        )
        
        if df.empty:
            return df
        
        # Filter by importance and country
        importance_map = {
            'LOW': 1,
            'MODERATE': 2,
            'HIGH': 3
        }
        
        threshold_value = importance_map.get(importance_threshold.name, 2)
        df = df[df['importance'].map(lambda x: importance_map.get(x, 0)) >= threshold_value]
        
        if country_codes:
            df = df[df['country_code'].isin(country_codes)]
        
        # Remove events without actual values (future events)
        df = df[df['actual_value'].notna()]
        
        logger.info(f"Found {len(df)} upcoming events in next {hours_ahead} hours")
        return df
    
    # ---------- Performance Monitoring ----------
    
    def benchmark_calendar_operations(self, iterations: int = 10) -> Dict[str, float]:
        """
        Benchmark calendar operations performance.
        
        Args:
            iterations: Number of iterations for each operation.
            
        Returns:
            Dictionary with timing results.
        """
        import time
        
        results = {}
        
        # Benchmark countries retrieval
        start_time = time.time()
        for _ in range(iterations):
            countries = self.get_countries()
        results['countries_avg_ms'] = (time.time() - start_time) / iterations * 1000
        
        # Benchmark event retrieval (if we have countries)
        if countries:
            sample_event_id = None
            for country in countries[:3]:  # Try first 3 countries
                try:
                    events = self.get_events_by_country(country.code)
                    if events:
                        sample_event_id = events[0].id
                        break
                except Exception:
                    continue
            
            if sample_event_id:
                start_time = time.time()
                for _ in range(iterations):
                    event = self.get_event_by_id(sample_event_id)
                results['event_by_id_avg_ms'] = (time.time() - start_time) / iterations * 1000
        
        # Benchmark value history retrieval
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        start_time = time.time()
        for _ in range(iterations):
            values = self.get_value_history(date_from=date_from, date_to=date_to, max_count=100)
        results['value_history_avg_ms'] = (time.time() - start_time) / iterations * 1000
        
        logger.info(f"Benchmark completed: {results}")
        return results

# ---------- Convenience Functions ----------
def create_enhanced_calendar_wrapper(dll_path: Optional[str] = None, max_workers: int = 4) -> EnhancedMT5CalendarWrapper:
    """
    Factory function to create enhanced MT5 calendar wrapper instance.
    
    Args:
        dll_path: Optional path to MT5 terminal DLL.
        max_workers: Maximum number of worker threads for async operations.
        
    Returns:
        Initialized EnhancedMT5CalendarWrapper instance.
    """
    wrapper = EnhancedMT5CalendarWrapper(dll_path, max_workers)
    wrapper.initialize()
    return wrapper

# ---------- Async Example Usage ----------
async def async_calendar_example():
    """Example of using async calendar operations"""
    try:
        # Create wrapper
        calendar = create_enhanced_calendar_wrapper()
        
        # Async countries retrieval
        countries = await calendar.get_countries_async()
        print(f"Async countries: {len(countries)}")
        
        # Async calendar DataFrame
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        df = await calendar.get_calendar_dataframe_async(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        
        print(f"Async DataFrame shape: {df.shape}")
        
        # Cleanup
        calendar.shutdown()
        
    except Exception as e:
        logger.error(f"Async calendar example error: {e}")

# ---------- Main Example ----------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    try:
        # Create enhanced wrapper
        calendar = create_enhanced_calendar_wrapper()
        
        # Example 1: Get high impact events for next 24 hours
        print("=== High Impact Events (Next 24h) ===")
        upcoming = calendar.get_upcoming_events(hours_ahead=24, importance_threshold=CalendarEventImportance.HIGH)
        if not upcoming.empty:
            print(upcoming[['timestamp', 'event_name', 'country_code', 'actual_value', 'forecast_value']].head())
        
        # Example 2: Get calendar data as DataFrame
        print("\n=== Calendar DataFrame (Last 7 days) ===")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        df = calendar.get_calendar_dataframe(date_from=date_from, date_to=date_to, max_count=50)
        if not df.empty:
            print(f"DataFrame shape: {df.shape}")
            print(df[['timestamp', 'event_name', 'country_code', 'importance']].head())
        
        # Example 3: Benchmark operations
        print("\n=== Performance Benchmark ===")
        benchmark = calendar.benchmark_calendar_operations(iterations=5)
        for operation, time_ms in benchmark.items():
            print(f"{operation}: {time_ms:.2f}ms average")
        
        # Example 4: Async operations
        print("\n=== Async Operations ===")
        asyncio.run(async_calendar_example())
        
        # Cleanup
        calendar.shutdown()
        
    except Exception as e:
        logger.error(f"Enhanced calendar wrapper error: {e}")
        import traceback
        traceback.print_exc()