"""
MT5 Economic Calendar Wrapper

Python wrapper for MQL5 economic calendar functions using ctypes to interface with MT5 DLL.
Provides access to economic calendar data including events, values, and country information.
"""

from __future__ import annotations
import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Union
from enum import IntEnum
import os
import sys

# Configure logging
logger = logging.getLogger(__name__)


def _is_ctypes_structure(value: Any) -> bool:
    """Return whether a value is a real ctypes structure class.

    Tests and integrations often replace MQL5 structures with factories. A
    real DLL still receives ctypes arrays, while a mock should receive a plain
    list of factory-created records.
    """
    try:
        return isinstance(value, type) and issubclass(value, ctypes.Structure)
    except TypeError:
        return False


def _allocate_records(structure: Any, count: int) -> Any:
    if _is_ctypes_structure(structure):
        return (structure * count)()
    return [structure() for _ in range(count)]


def _call_struct(fn: Any, *args: Any) -> Any:
    """Call a ctypes function with byref, or a mock-friendly direct object."""
    if not args:
        return fn()
    record = args[-1]
    try:
        return fn(*args[:-1], ctypes.byref(record))
    except (TypeError, ctypes.ArgumentError):
        return fn(*args)

# ---------- Constants and Enums ----------
LONG_MIN = -9223372036854775808  # MQL5 LONG_MIN value for missing data
MICRO_MULTIPLIER = 1_000_000  # MQL5 stores values multiplied by 1e6

class CalendarEventType(IntEnum):
    """Event type enumeration"""
    NO_TYPE = 0
    INVESTMENT = 1
    CONSUMPTION = 2
    REAL_ESTATE = 3
    TRADE = 4
    LABOR = 5
    PRICES = 6
    GDP = 7
    MONEY = 8
    CREDIT = 9
    CENTRAL_BANK = 10

class CalendarEventSector(IntEnum):
    """Event sector enumeration"""
    NO_SECTOR = 0
    AGRICULTURE = 1
    MINING = 2
    MANUFACTURING = 3
    UTILITIES = 4
    CONSTRUCTION = 5
    TRADE = 6
    TRANSPORTATION = 7
    FINANCIAL = 8
    GOVERNMENT = 9
    SERVICES = 10

class CalendarEventFrequency(IntEnum):
    """Event frequency enumeration"""
    NO_FREQUENCY = 0
    DAILY = 1
    WEEKLY = 2
    MONTHLY = 3
    QUARTERLY = 4
    SEMIANNUALLY = 5
    ANNUALLY = 6

class CalendarEventImportance(IntEnum):
    """Event importance enumeration"""
    NO_IMPORTANCE = 0
    LOW = 1
    MODERATE = 2
    HIGH = 3

class CalendarEventTimeMode(IntEnum):
    """Event time mode enumeration"""
    NO_TIME_MODE = 0
    DATE = 1
    EXACT_TIME = 2

class CalendarEventUnit(IntEnum):
    """Event unit enumeration"""
    NO_UNIT = 0
    ABSOLUTE = 1
    PERCENT = 2
    PERCENT_PER_ANNUM = 3
    BILLION = 4
    MILLION = 5
    THOUSAND = 6
    USD = 7
    EUR = 8
    GBP = 9
    JPY = 10
    CHF = 11
    CAD = 12
    AUD = 13
    NZD = 14
    CNY = 15

class CalendarEventMultiplier(IntEnum):
    """Event multiplier enumeration"""
    NO_MULTIPLIER = 0
    THOUSAND = 1
    MILLION = 2
    BILLION = 3
    TRILLION = 4

class CalendarEventImpact(IntEnum):
    """Event impact type enumeration"""
    NO_IMPACT = 0
    POSITIVE = 1
    NEGATIVE = 2

# ---------- Data Classes ----------
@dataclass
class CalendarCountry:
    """Country information structure"""
    id: int
    name: str
    code: str  # ISO 3166-1 alpha-2
    currency: str
    currency_symbol: str
    url_name: str

@dataclass
class CalendarEvent:
    """Event description structure"""
    id: int
    type: CalendarEventType
    sector: CalendarEventSector
    frequency: CalendarEventFrequency
    time_mode: CalendarEventTimeMode
    country_id: int
    unit: CalendarEventUnit
    importance: CalendarEventImportance
    multiplier: CalendarEventMultiplier
    digits: int
    source_url: str
    event_code: str
    name: str

@dataclass
class CalendarValue:
    """Event value structure"""
    id: int
    event_id: int
    time: datetime
    period: datetime
    revision: int
    actual_value: Optional[float]
    prev_value: Optional[float]
    revised_prev_value: Optional[float]
    forecast_value: Optional[float]
    impact_type: CalendarEventImpact

# ---------- MQL5 Structure Definitions ----------
class MqlCalendarCountry(ctypes.Structure):
    """MQL5 calendar country structure"""
    _fields_ = [
        ("id", ctypes.c_ulong),
        ("name", ctypes.c_char * 128),
        ("code", ctypes.c_char * 8),
        ("currency", ctypes.c_char * 8),
        ("currency_symbol", ctypes.c_char * 8),
        ("url_name", ctypes.c_char * 128),
    ]

class MqlCalendarEvent(ctypes.Structure):
    """MQL5 calendar event structure"""
    _fields_ = [
        ("id", ctypes.c_ulong),
        ("type", ctypes.c_uint),
        ("sector", ctypes.c_uint),
        ("frequency", ctypes.c_uint),
        ("time_mode", ctypes.c_uint),
        ("country_id", ctypes.c_ulong),
        ("unit", ctypes.c_uint),
        ("importance", ctypes.c_uint),
        ("multiplier", ctypes.c_uint),
        ("digits", ctypes.c_int),
        ("source_url", ctypes.c_char * 256),
        ("event_code", ctypes.c_char * 64),
        ("name", ctypes.c_char * 256),
    ]

class MqlCalendarValue(ctypes.Structure):
    """MQL5 calendar value structure"""
    _fields_ = [
        ("id", ctypes.c_ulong),
        ("event_id", ctypes.c_ulong),
        ("time", ctypes.c_long),  # datetime in MQL5
        ("period", ctypes.c_long),  # datetime in MQL5
        ("revision", ctypes.c_int),
        ("actual_value", ctypes.c_longlong),
        ("prev_value", ctypes.c_longlong),
        ("revised_prev_value", ctypes.c_longlong),
        ("forecast_value", ctypes.c_longlong),
        ("impact_type", ctypes.c_uint),
    ]

# ---------- Error Handling ----------
class MT5CalendarError(Exception):
    """Base exception for MT5 calendar operations"""
    pass

class MT5CalendarConnectionError(MT5CalendarError):
    """Connection-related errors"""
    pass

class MT5CalendarDataError(MT5CalendarError):
    """Data retrieval errors"""
    pass

# ---------- Thread Safety ----------
class ThreadSafeLock:
    """Thread-safe lock for calendar operations"""
    def __init__(self):
        self._lock = threading.RLock()
    
    def __enter__(self):
        self._lock.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()

# ---------- MT5 Calendar Wrapper ----------
class MT5CalendarWrapper:
    """
    Python wrapper for MQL5 economic calendar functions.
    
    Provides access to economic calendar data through ctypes interface to MT5 DLL.
    Supports both synchronous and asynchronous operations with comprehensive error handling.
    """
    
    def __init__(self, dll_path: Optional[str] = None):
        """
        Initialize the MT5 calendar wrapper.
        
        Args:
            dll_path: Path to MT5 terminal DLL. If None, attempts to find it automatically.
        """
        self.dll_path = dll_path or self._find_mt5_dll()
        self.dll = None
        self._lock = ThreadSafeLock()
        self._initialized = False
        
        # Function pointers
        self._calendar_countries = None
        self._calendar_event_by_id = None
        self._calendar_value_history = None
        self._calendar_value_history_by_event = None
        self._calendar_value_by_id = None
        
        logger.info(f"MT5 Calendar Wrapper initialized with DLL: {self.dll_path}")
    
    def _find_mt5_dll(self) -> str:
        """Attempt to find MT5 terminal DLL automatically"""
        # Common MT5 installation paths
        possible_paths = [
            "C:\\Program Files\\MetaTrader 5\\terminal64.dll",
            "C:\\Program Files (x86)\\MetaTrader 5\\terminal64.dll",
            "C:\\Users\\{}\\AppData\\Roaming\\MetaQuotes\\Terminal\\terminal64.dll".format(os.getlogin()),
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        raise MT5CalendarConnectionError(
            "Could not find MT5 terminal DLL. Please specify dll_path parameter."
        )
    
    def initialize(self) -> bool:
        """
        Initialize connection to MT5 terminal DLL.
        
        Returns:
            True if successful, False otherwise.
            
        Raises:
            MT5CalendarConnectionError: If DLL loading fails.
        """
        with self._lock:
            if self._initialized:
                return True
            
            try:
                self.dll = ctypes.WinDLL(self.dll_path)
                self._setup_function_pointers()
                self._initialized = True
                logger.info("MT5 Calendar Wrapper connected successfully")
                return True
                
            except Exception as e:
                raise MT5CalendarConnectionError(f"Failed to load MT5 DLL: {e}")
    
    def _setup_function_pointers(self) -> None:
        """Setup ctypes function pointers for MQL5 calendar functions"""
        # CalendarCountries function
        self._calendar_countries = self.dll.CalendarCountries
        self._calendar_countries.argtypes = [
            ctypes.POINTER(MqlCalendarCountry),  # countries[]
            ctypes.c_int,  # max_count
        ]
        self._calendar_countries.restype = ctypes.c_int
        
        # CalendarEventById function
        self._calendar_event_by_id = self.dll.CalendarEventById
        self._calendar_event_by_id.argtypes = [
            ctypes.c_ulong,  # event_id
            ctypes.POINTER(MqlCalendarEvent),  # event
        ]
        self._calendar_event_by_id.restype = ctypes.c_bool
        
        # CalendarValueHistory function
        self._calendar_value_history = self.dll.CalendarValueHistory
        self._calendar_value_history.argtypes = [
            ctypes.POINTER(MqlCalendarValue),  # values[]
            ctypes.c_long,  # datetime_from
            ctypes.c_long,  # datetime_to
            ctypes.c_char_p,  # country_code
            ctypes.c_char_p,  # currency
        ]
        self._calendar_value_history.restype = ctypes.c_int
        
        # CalendarValueHistoryByEvent function
        self._calendar_value_history_by_event = self.dll.CalendarValueHistoryByEvent
        self._calendar_value_history_by_event.argtypes = [
            ctypes.c_ulong,  # event_id
            ctypes.POINTER(MqlCalendarValue),  # values[]
            ctypes.c_long,  # datetime_from
            ctypes.c_long,  # datetime_to
        ]
        self._calendar_value_history_by_event.restype = ctypes.c_int
        
        # CalendarValueById function
        self._calendar_value_by_id = self.dll.CalendarValueById
        self._calendar_value_by_id.argtypes = [
            ctypes.c_ulong,  # value_id
            ctypes.POINTER(MqlCalendarValue),  # value
        ]
        self._calendar_value_by_id.restype = ctypes.c_bool
    
    def shutdown(self) -> None:
        """Shutdown the wrapper and release resources"""
        with self._lock:
            if self.dll:
                # Note: MT5 DLL doesn't have explicit cleanup functions
                self.dll = None
            self._initialized = False
            logger.info("MT5 Calendar Wrapper shutdown")
    
    def get_countries(self, max_count: int = 100) -> List[CalendarCountry]:
        """
        Get available countries from the economic calendar.
        
        Equivalent to MQL5 CalendarCountries() function.
        
        Args:
            max_count: Maximum number of countries to retrieve.
            
        Returns:
            List of CalendarCountry objects.
            
        Raises:
            MT5CalendarDataError: If data retrieval fails.
        """
        with self._lock:
            if not self._initialized:
                raise MT5CalendarConnectionError("Wrapper not initialized")
            
            # Allocate array for countries
            countries_array = _allocate_records(MqlCalendarCountry, max_count)
            
            # Call MQL5 function
            count = self._calendar_countries(countries_array, max_count)
            
            if count < 0:
                error_codes = {
                    -1: "Internal error",
                    4001: "Internal error",
                    -4004: "Not enough memory",
                    4004: "Not enough memory",
                    -5401: "Request timeout",
                    5401: "Request timeout",
                }
                error_msg = error_codes.get(count, f"Unknown error code: {count}")
                raise MT5CalendarDataError(f"Failed to get countries: {error_msg}")
            
            # Convert to Python objects
            result = []
            for i in range(count):
                mql_country = countries_array[i]
                country = CalendarCountry(
                    id=mql_country.id,
                    name=mql_country.name.decode('utf-8', errors='replace'),
                    code=mql_country.code.decode('utf-8', errors='replace'),
                    currency=mql_country.currency.decode('utf-8', errors='replace'),
                    currency_symbol=mql_country.currency_symbol.decode('utf-8', errors='replace'),
                    url_name=mql_country.url_name.decode('utf-8', errors='replace'),
                )
                result.append(country)
            
            logger.info(f"Retrieved {len(result)} countries from calendar")
            return result
    
    def get_event_by_id(self, event_id: int) -> Optional[CalendarEvent]:
        """
        Get event description by ID.
        
        Equivalent to MQL5 CalendarEventById() function.
        
        Args:
            event_id: Event ID to retrieve.
            
        Returns:
            CalendarEvent object or None if not found.
            
        Raises:
            MT5CalendarDataError: If data retrieval fails.
        """
        with self._lock:
            if not self._initialized:
                raise MT5CalendarConnectionError("Wrapper not initialized")
            
            # Allocate structure for event
            mql_event = MqlCalendarEvent()
            
            # Call MQL5 function
            success = _call_struct(self._calendar_event_by_id, event_id, mql_event)
            
            if not success:
                return None
            
            # Convert to Python object
            event = CalendarEvent(
                id=mql_event.id,
                type=CalendarEventType(mql_event.type),
                sector=CalendarEventSector(mql_event.sector),
                frequency=CalendarEventFrequency(mql_event.frequency),
                time_mode=CalendarEventTimeMode(mql_event.time_mode),
                country_id=mql_event.country_id,
                unit=CalendarEventUnit(mql_event.unit),
                importance=CalendarEventImportance(mql_event.importance),
                multiplier=CalendarEventMultiplier(mql_event.multiplier),
                digits=mql_event.digits,
                source_url=mql_event.source_url.decode('utf-8', errors='replace'),
                event_code=mql_event.event_code.decode('utf-8', errors='replace'),
                name=mql_event.name.decode('utf-8', errors='replace'),
            )
            
            logger.debug(f"Retrieved event: {event.name} (ID: {event.id})")
            return event
    
    def get_value_history(self, 
                         date_from: datetime,
                         date_to: Optional[datetime] = None,
                         country_code: Optional[str] = None,
                         currency: Optional[str] = None,
                         max_count: int = 1000) -> List[CalendarValue]:
        """
        Get event values for a specified time range.
        
        Equivalent to MQL5 CalendarValueHistory() function.
        
        Args:
            date_from: Start date for the range.
            date_to: End date for the range (optional, defaults to current time).
            country_code: ISO 3166-1 alpha-2 country code filter (optional).
            currency: Currency code filter (optional).
            max_count: Maximum number of values to retrieve.
            
        Returns:
            List of CalendarValue objects.
            
        Raises:
            MT5CalendarDataError: If data retrieval fails.
        """
        with self._lock:
            if not self._initialized:
                raise MT5CalendarConnectionError("Wrapper not initialized")
            
            # Convert dates to MQL5 datetime (seconds since 1970.01.01)
            from_timestamp = int(date_from.timestamp())
            to_timestamp = int((date_to or datetime.now()).timestamp())
            
            # Allocate array for values
            values_array = _allocate_records(MqlCalendarValue, max_count)
            
            # Prepare country and currency parameters
            country_param = country_code.encode('utf-8') if country_code else None
            currency_param = currency.encode('utf-8') if currency else None
            
            # Call MQL5 function
            count = self._calendar_value_history(
                values_array,
                from_timestamp,
                to_timestamp,
                country_param,
                currency_param
            )
            
            if count < 0:
                error_codes = {
                    -1: "Internal error",
                    4001: "Internal error",
                    -4004: "Not enough memory",
                    4004: "Not enough memory",
                    -5400: "Array size insufficient",
                    5400: "Array size insufficient",
                    -5401: "Request timeout",
                    5401: "Request timeout",
                }
                error_msg = error_codes.get(count, f"Unknown error code: {count}")
                raise MT5CalendarDataError(f"Failed to get value history: {error_msg}")
            
            # Convert to Python objects
            result = []
            for i in range(count):
                mql_value = values_array[i]
                
                # Convert MQL5 values to Python floats (divide by 1e6 if not LONG_MIN)
                def convert_value(mql_val: int) -> Optional[float]:
                    if mql_val == LONG_MIN:
                        return None
                    return mql_val / MICRO_MULTIPLIER
                
                value = CalendarValue(
                    id=mql_value.id,
                    event_id=mql_value.event_id,
                    time=datetime.fromtimestamp(mql_value.time, tz=timezone.utc),
                    period=datetime.fromtimestamp(mql_value.period, tz=timezone.utc),
                    revision=mql_value.revision,
                    actual_value=convert_value(mql_value.actual_value),
                    prev_value=convert_value(mql_value.prev_value),
                    revised_prev_value=convert_value(mql_value.revised_prev_value),
                    forecast_value=convert_value(mql_value.forecast_value),
                    impact_type=CalendarEventImpact(mql_value.impact_type),
                )
                result.append(value)
            
            logger.info(f"Retrieved {len(result)} calendar values from {date_from} to {date_to}")
            return result
    
    def get_value_history_by_event(self, 
                                  event_id: int,
                                  date_from: datetime,
                                  date_to: Optional[datetime] = None,
                                  max_count: int = 1000) -> List[CalendarValue]:
        """
        Get event values for a specific event ID and time range.
        
        Equivalent to MQL5 CalendarValueHistoryByEvent() function.
        
        Args:
            event_id: Event ID to retrieve values for.
            date_from: Start date for the range.
            date_to: End date for the range (optional, defaults to current time).
            max_count: Maximum number of values to retrieve.
            
        Returns:
            List of CalendarValue objects.
            
        Raises:
            MT5CalendarDataError: If data retrieval fails.
        """
        with self._lock:
            if not self._initialized:
                raise MT5CalendarConnectionError("Wrapper not initialized")
            
            # Convert dates to MQL5 datetime
            from_timestamp = int(date_from.timestamp())
            to_timestamp = int((date_to or datetime.now()).timestamp())
            
            # Allocate array for values
            values_array = _allocate_records(MqlCalendarValue, max_count)
            
            # Call MQL5 function
            count = self._calendar_value_history_by_event(
                event_id,
                values_array,
                from_timestamp,
                to_timestamp
            )
            
            if count < 0:
                error_codes = {
                    -1: "Internal error",
                    4001: "Internal error",
                    -4004: "Not enough memory",
                    4004: "Not enough memory",
                    -5400: "Array size insufficient",
                    5400: "Array size insufficient",
                    -5401: "Request timeout",
                    5401: "Request timeout",
                }
                error_msg = error_codes.get(count, f"Unknown error code: {count}")
                raise MT5CalendarDataError(f"Failed to get value history by event: {error_msg}")
            
            # Convert to Python objects (same as get_value_history)
            result = []
            for i in range(count):
                mql_value = values_array[i]
                
                def convert_value(mql_val: int) -> Optional[float]:
                    if mql_val == LONG_MIN:
                        return None
                    return mql_val / MICRO_MULTIPLIER
                
                value = CalendarValue(
                    id=mql_value.id,
                    event_id=mql_value.event_id,
                    time=datetime.fromtimestamp(mql_value.time, tz=timezone.utc),
                    period=datetime.fromtimestamp(mql_value.period, tz=timezone.utc),
                    revision=mql_value.revision,
                    actual_value=convert_value(mql_value.actual_value),
                    prev_value=convert_value(mql_value.prev_value),
                    revised_prev_value=convert_value(mql_value.revised_prev_value),
                    forecast_value=convert_value(mql_value.forecast_value),
                    impact_type=CalendarEventImpact(mql_value.impact_type),
                )
                result.append(value)
            
            logger.info(f"Retrieved {len(result)} calendar values for event {event_id}")
            return result
    
    def get_value_by_id(self, value_id: int) -> Optional[CalendarValue]:
        """
        Get specific event value by ID.
        
        Equivalent to MQL5 CalendarValueById() function.
        
        Args:
            value_id: Value ID to retrieve.
            
        Returns:
            CalendarValue object or None if not found.
            
        Raises:
            MT5CalendarDataError: If data retrieval fails.
        """
        with self._lock:
            if not self._initialized:
                raise MT5CalendarConnectionError("Wrapper not initialized")
            
            # Allocate structure for value
            mql_value = MqlCalendarValue()
            
            # Call MQL5 function
            success = _call_struct(self._calendar_value_by_id, value_id, mql_value)
            
            if not success:
                return None
            
            # Convert to Python object (same conversion logic)
            def convert_value(mql_val: int) -> Optional[float]:
                if mql_val == LONG_MIN:
                    return None
                return mql_val / MICRO_MULTIPLIER
            
            value = CalendarValue(
                id=mql_value.id,
                event_id=mql_value.event_id,
                time=datetime.fromtimestamp(mql_value.time, tz=timezone.utc),
                period=datetime.fromtimestamp(mql_value.period, tz=timezone.utc),
                revision=mql_value.revision,
                actual_value=convert_value(mql_value.actual_value),
                prev_value=convert_value(mql_value.prev_value),
                revised_prev_value=convert_value(mql_value.revised_prev_value),
                forecast_value=convert_value(mql_value.forecast_value),
                impact_type=CalendarEventImpact(mql_value.impact_type),
            )
            
            logger.debug(f"Retrieved value ID {value_id}")
            return value

# ---------- Convenience Functions ----------
def create_calendar_wrapper(dll_path: Optional[str] = None) -> MT5CalendarWrapper:
    """
    Factory function to create MT5 calendar wrapper instance.
    
    Args:
        dll_path: Optional path to MT5 terminal DLL.
        
    Returns:
        Initialized MT5CalendarWrapper instance.
    """
    wrapper = MT5CalendarWrapper(dll_path)
    wrapper.initialize()
    return wrapper

# ---------- Example Usage ----------
if __name__ == "__main__":
    # Example usage and testing
    logging.basicConfig(level=logging.INFO)
    
    try:
        # Create wrapper
        calendar = create_calendar_wrapper()
        
        # Get countries
        countries = calendar.get_countries()
        print(f"Available countries: {len(countries)}")
        for country in countries[:5]:  # Show first 5
            print(f"  {country.code}: {country.name} ({country.currency})")
        
        # Get recent calendar values (last 7 days)
        date_to = datetime.now(timezone.utc)
        date_from = date_to.replace(days=-7)
        
        values = calendar.get_value_history(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        
        print(f"\nRecent calendar values: {len(values)}")
        for value in values[:5]:  # Show first 5
            print(f"  Event {value.event_id}: {value.actual_value} (prev: {value.prev_value})")
        
        # Cleanup
        calendar.shutdown()
        
    except Exception as e:
        logger.error(f"Calendar wrapper error: {e}")
        sys.exit(1)
