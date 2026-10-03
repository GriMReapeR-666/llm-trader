# MT5 Economic Calendar Wrapper - API Documentation

## Overview

The MT5 Economic Calendar Wrapper provides a comprehensive Python interface to MetaTrader 5's economic calendar functionality. This wrapper bridges the gap between the official Python MT5 package (which lacks calendar functions) and the powerful MQL5 calendar API.

## Features

- **Complete MQL5 Calendar Integration**: Access to all calendar functions via ctypes
- **Thread-Safe Operations**: Concurrent access protection with RLock
- **Synchronous & Asynchronous APIs**: Support for both blocking and non-blocking operations
- **Advanced Filtering**: Date ranges, countries, currencies, importance levels
- **DataFrame Integration**: Seamless pandas DataFrame support for analysis
- **Comprehensive Error Handling**: Detailed error messages and exception types
- **Performance Benchmarking**: Built-in performance monitoring
- **Type Safety**: Full type hints and data class definitions

## Installation

### Prerequisites

- Python 3.8+
- MetaTrader 5 platform installed
- Windows OS (MT5 requirement)
- Required Python packages:
  ```bash
  pip install pandas numpy
  ```

### Quick Start

```python
from mt5_calendar_wrapper import MT5CalendarWrapper

# Initialize the wrapper
wrapper = MT5CalendarWrapper()
wrapper.initialize()

# Get available countries
countries = wrapper.get_countries()
print(f"Available countries: {len(countries)}")

# Get economic calendar data for last 30 days
from datetime import datetime, timedelta, timezone

date_to = datetime.now(timezone.utc)
date_from = date_to - timedelta(days=30)

calendar_values = wrapper.get_value_history(
    date_from=date_from,
    date_to=date_to
)

print(f"Retrieved {len(calendar_values)} calendar events")

# Shutdown when done
wrapper.shutdown()
```

## Core Classes

### MT5CalendarWrapper

The main wrapper class that provides access to MT5 calendar functions.

#### Constructor Parameters

- `dll_path` (str, optional): Path to MT5 terminal64.dll. Auto-detected if not provided.
- `timeout_seconds` (int, optional): Timeout for MT5 operations. Default: 30.

#### Methods

##### `initialize() -> bool`

Initializes connection to MT5 and loads calendar functions.

**Returns:** `True` if successful, raises exception otherwise

**Raises:**
- `MT5CalendarConnectionError`: If MT5 connection fails
- `MT5CalendarError`: For general initialization errors

##### `shutdown() -> None`

Closes MT5 connection and cleans up resources.

##### `get_countries(max_count: int = 1000) -> List[CalendarCountry]`

Retrieves all available countries from the economic calendar.

**Parameters:**
- `max_count`: Maximum number of countries to retrieve

**Returns:** List of `CalendarCountry` objects

##### `get_event_by_id(event_id: int) -> Optional[CalendarEvent]`

Retrieves detailed information about a specific calendar event.

**Parameters:**
- `event_id`: Unique identifier for the calendar event

**Returns:** `CalendarEvent` object or `None` if not found

##### `get_value_history(date_from: datetime, date_to: datetime, country_code: str = None, currency: str = None, max_count: int = 10000) -> List[CalendarValue]`

Retrieves historical calendar values within a date range.

**Parameters:**
- `date_from`: Start date (timezone-aware datetime)
- `date_to`: End date (timezone-aware datetime)
- `country_code`: Optional 2-letter country code filter (e.g., "US", "EU")
- `currency`: Optional 3-letter currency code filter (e.g., "USD", "EUR")
- `max_count`: Maximum number of values to retrieve

**Returns:** List of `CalendarValue` objects

##### `get_value_history_by_event(event_id: int, max_count: int = 1000) -> List[CalendarValue]`

Retrieves historical values for a specific calendar event.

**Parameters:**
- `event_id`: Unique identifier for the calendar event
- `max_count`: Maximum number of historical values to retrieve

**Returns:** List of `CalendarValue` objects

### Data Classes

#### CalendarCountry

Represents a country in the economic calendar.

```python
@dataclass
class CalendarCountry:
    id: int                    # Internal MT5 country ID
    code: str                  # ISO 3166-1 alpha-2 country code (2 letters)
    name: str                  # Full country name
    currency: str             # ISO 4217 currency code (3 letters)
```

#### CalendarEvent

Represents an economic calendar event type.

```python
@dataclass
class CalendarEvent:
    id: int                                    # Internal MT5 event ID
    name: str                                  # Event name (e.g., "Non-Farm Payrolls")
    importance: CalendarEventImportance         # Event importance level
    type: CalendarEventType                   # Event category type
    sector: CalendarEventSector               # Economic sector
    unit: str                                 # Measurement unit
    multiplier: float                         # Value multiplier
    country_id: int                           # Reference to country
    frequency: CalendarEventFrequency         # How often event occurs
    source_url: str                          # Source information URL
    event_code: str                          # Unique event identifier
```

#### CalendarValue

Represents a specific calendar event occurrence with actual values.

```python
@dataclass
class CalendarValue:
    id: int                                    # Internal MT5 value ID
    event_id: int                             # Reference to CalendarEvent
    time: datetime                           # Event release time (UTC)
    period: datetime                         # Period the data represents
    actual_value: Optional[float]            # Released value (None if not yet released)
    forecast_value: Optional[float]          # Analyst consensus forecast
    prev_value: Optional[float]              # Previous period value
    revised_value: Optional[float]           # Revised previous value
    impact_type: CalendarEventImpact         # Market impact classification
    revision: int                            # Revision number (0 = initial)
```

### Enumerations

#### CalendarEventImportance

```python
class CalendarEventImportance(Enum):
    NO_IMPORTANCE = 0    # Low market impact
    LOW = 1               # Minor market impact
    MODERATE = 2          # Moderate market impact
    HIGH = 3              # High market impact
```

#### CalendarEventType

```python
class CalendarEventType(Enum):
    NO_TYPE = 0           # Uncategorized
    PAYROLL = 1            # Employment data
    CPI = 2               # Consumer Price Index
    GDP = 3               # Gross Domestic Product
    RETAIL = 4            # Retail sales
    TRADE = 5             # Trade balance
    FOMC = 6              # Federal Open Market Committee
    JOBLESS = 7            # Jobless claims
    INDUSTRY = 8          # Industrial production
    HOUSING = 9            # Housing data
    PPI = 10              # Producer Price Index
    PMI = 11              # Purchasing Managers Index
    CONSUMER = 12          # Consumer confidence/sentiment
    CONSTRUCTION = 13      # Construction data
    INFLATION = 14         # General inflation metrics
    COMMODITY = 15         # Commodity-related data
    BANKING = 16           # Banking sector data
    CORPORATE = 17         # Corporate sector data
    ECONOMIC = 18          # General economic indicators
    ENVIRONMENT = 19       # Environmental data
    HEALTH = 20            # Health sector data
    EDUCATION = 21         # Education sector data
    MILITARY = 22          # Military/defense data
    POLITICS = 23          # Political events
    SECURITY = 24          # Security-related events
    TECHNOLOGY = 25        # Technology sector data
    TRANSPORT = 26         # Transportation data
    SOCIAL = 27            # Social indicators
    CULTURE = 28           # Cultural events
    RELIGION = 29          # Religious holidays/events
    SCIENCE = 30           # Scientific data
    AGRICULTURE = 31       # Agriculture data
    WEATHER = 32           # Weather-related data
    NATURAL_GAS = 33       # Natural gas data
    OIL = 34               # Oil/petroleum data
    GOLD = 35              # Gold/precious metals data
    SILVER = 36            # Silver data
    CRYPTOCURRENCY = 37    # Cryptocurrency data
    STOCK_MARKET = 38      # Stock market data
    BOND_MARKET = 39       # Bond market data
    CURRENCY_MARKET = 40   # Currency/FX market data
    COMMODITY_MARKET = 41  # General commodity markets
    REAL_ESTATE = 42       # Real estate data
    INSURANCE = 43         # Insurance sector data
    TOURISM = 44           # Tourism/travel data
    ENTERTAINMENT = 45     # Entertainment industry data
    SPORTS = 46            # Sports-related data
    OTHER = 47             # Other/miscellaneous
```

## Enhanced Features

### EnhancedMT5CalendarWrapper

Extended wrapper with advanced filtering, async operations, and DataFrame support.

#### Additional Methods

##### `get_calendar_dataframe(date_from: datetime, date_to: datetime, **kwargs) -> pd.DataFrame`

Retrieves calendar data as a pandas DataFrame for analysis.

**Returns:** DataFrame with columns:
- `timestamp`: Event release time
- `event_id`: Event identifier
- `event_name`: Event name
- `country_code`: 2-letter country code
- `country_name`: Full country name
- `currency`: 3-letter currency code
- `importance`: Event importance level
- `actual_value`: Released value
- `forecast_value`: Analyst forecast
- `previous_value`: Previous period value
- `impact_type`: Market impact classification
- `event_type`: Event category

##### `get_upcoming_events(hours_ahead: int = 24, importance_threshold: CalendarEventImportance = CalendarEventImportance.MODERATE) -> pd.DataFrame`

Retrieves upcoming high-impact events.

**Parameters:**
- `hours_ahead`: Look ahead window in hours
- `importance_threshold`: Minimum importance level to include

**Returns:** DataFrame with upcoming events

##### `get_countries_filtered(filter_obj: CalendarFilter) -> List[CalendarCountry]`

Retrieves countries filtered by criteria.

##### `async_get_countries(max_count: int = 1000) -> List[CalendarCountry]`

Asynchronously retrieves countries.

##### `async_get_calendar_dataframe(date_from: datetime, date_to: datetime, **kwargs) -> pd.DataFrame`

Asynchronously retrieves calendar data as DataFrame.

##### `benchmark_calendar_operations(iterations: int = 5) -> Dict[str, float]`

Benchmarks calendar operation performance.

**Returns:** Dictionary with average execution times in milliseconds

### CalendarFilter

Advanced filtering for calendar operations.

```python
@dataclass
class CalendarFilter:
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    country_codes: Optional[List[str]] = None
    currencies: Optional[List[str]] = None
    importance_levels: Optional[List[CalendarEventImportance]] = None
    event_types: Optional[List[CalendarEventType]] = None
    impact_types: Optional[List[CalendarEventImpact]] = None
    max_count: int = 10000
```

## Error Handling

### Exception Classes

#### MT5CalendarError

Base exception for all calendar wrapper errors.

#### MT5CalendarConnectionError

Raised when MT5 connection fails or terminal is not available.

#### MT5CalendarDataError

Raised when calendar data retrieval fails or data is corrupted.

### Error Codes

The wrapper maps MQL5 error codes to meaningful messages:

- `1`: "Operation not permitted"
- `2`: "No such file or directory"
- `3`: "No such process"
- `4`: "Interrupted system call"
- `5`: "I/O error"
- `6`: "No such device or address"
- `7`: "Argument list too long"
- `8`: "Exec format error"
- `9`: "Bad file number"
- `10`: "No child processes"
- `11`: "Try again"
- `12`: "Out of memory"
- `13`: "Permission denied"
- `14`: "Bad address"
- `15`: "Block device required"
- `16`: "Device or resource busy"
- `17`: "File exists"
- `18`: "Cross-device link"
- `19`: "No such device"
- `20`: "Not a directory"
- `21`: "Is a directory"
- `22`: "Invalid argument"
- `23`: "File table overflow"
- `24`: "Too many open files"
- `25`: "Not a typewriter"
- `26`: "Text file busy"
- `27`: "File too large"
- `28`: "No space left on device"
- `29`: "Illegal seek"
- `30`: "Read-only file system"
- `31`: "Too many links"
- `32`: "Broken pipe"
- `33`: "Math argument out of domain"
- `34`: "Math result not representable"
- `35`: "Resource deadlock would occur"
- `36`: "File name too long"
- `37`: "No record locks available"
- `38`: "Function not implemented"
- `39`: "Directory not empty"
- `40`: "Too many symbolic links"
- `41`: "No message of desired type"
- `42`: "Identifier removed"
- `43`: "Channel number out of range"
- `44`: "Level 2 not synchronized"
- `45`: "Level 3 halted"
- `46`: "Level 3 reset"
- `47`: "Link number out of range"
- `48`: "Protocol driver not attached"
- `49`: "No CSI structure available"
- `50`: "Level 2 halted"
- `51`: "Invalid exchange"
- `52`: "Invalid request descriptor"
- `53`: "Exchange full"
- `54`: "No anode"
- `55`: "Invalid request code"
- `56`: "Invalid slot"
- `57`: "Resource deadlock would occur"
- `58`: "Bad font file format"
- `59`: "Device not a stream"
- `60`: "No data available"
- `61`: "Timer expired"
- `62`: "Out of streams resources"
- `63`: "Machine is not on the network"
- `64`: "Package not installed"
- `65`: "Object is remote"
- `66`: "Link has been severed"
- `67`: "Advertise error"
- `68`: "Srmount error"
- `69`: "Communication error on send"
- `70`: "Protocol error"
- `71`: "Multihop attempted"
- `72`: "RFS specific error"
- `73`: "Not a data message"
- `74`: "Value too large for defined data type"
- `75`: "Name not unique on network"
- `76`: "File descriptor in bad state"
- `77`: "Remote address changed"
- `78`: "Can not access a needed shared library"
- `79`: "Accessing a corrupted shared library"
- `80`: ".lib section in a.out corrupted"
- `81`: "Attempting to link in too many shared libraries"
- `82`: "Cannot exec a shared library directly"
- `83`: "Illegal byte sequence"
- `84`: "Interrupted system call should be restarted"
- `85`: "Streams pipe error"
- `86`: "Too many users"
- `87`: "Socket operation on non-socket"
- `88`: "Destination address required"
- `89`: "Message too long"
- `90`: "Protocol wrong type for socket"
- `91`: "Protocol not available"
- `92`: "Protocol not supported"
- `93`: "Socket type not supported"
- `94`: "Operation not supported on transport endpoint"
- `95`: "Protocol family not supported"
- `96`: "Address family not supported by protocol"
- `97`: "Address already in use"
- `98`: "Network is unreachable"
- `99`: "Network is down"
- `100`: "Network dropped connection because of reset"
- `101`: "Software caused connection abort"
- `102`: "Connection reset by peer"
- `103`: "No buffer space available"
- `104`: "Transport endpoint is already connected"
- `105`: "Transport endpoint is not connected"
- `106`: "Cannot send after transport endpoint shutdown"
- `107`: "Too many references: cannot splice"
- `108`: "Connection timed out"
- `109`: "Connection refused"
- `110`: "Host is down"
- `111`: "No route to host"
- `112`: "Operation already in progress"
- `113`: "Operation now in progress"
- `114`: "Stale file handle"
- `115`: "Structure needs cleaning"
- `116`: "Not a XENIX named type file"
- `117`: "No XENIX semaphores available"
- `118`: "Is a named type file"
- `119`: "Remote I/O error"
- `120`: "Quota exceeded"
- `121`: "No medium found"
- `122`: "Wrong medium type"
- `123`: "Operation Canceled"
- `124`: "Required key not available"
- `125`: "Key has expired"
- `126`: "Key has been revoked"
- `127`: "Key was rejected by service"
- `128`: "For weak key"
- `129`: "Owner died"
- `130`: "State not recoverable"
- `131`: "Operation not possible due to RF-kill"
- `132`: "Memory page has hardware error"

## Thread Safety

The wrapper implements thread-safe operations using RLock (reentrant lock), allowing:
- Multiple threads to safely access calendar functions
- Recursive calls within the same thread
- Proper cleanup and resource management

## Performance Considerations

- **Batch Operations**: Use `max_count` parameter to limit data retrieval
- **Date Filtering**: Always specify date ranges to reduce data volume
- **Country/Currency Filtering**: Use specific filters to narrow results
- **Async Operations**: Use async methods for non-blocking operations
- **Caching**: Consider caching frequently accessed data

## Examples

See [USAGE_EXAMPLES.md](USAGE_EXAMPLES.md) for comprehensive usage examples.

## Testing

Run the test suite:

```bash
# Unit tests (no MT5 required)
python -m pytest tests/test_calendar_wrapper.py -v

# Integration tests (requires MT5)
python -m pytest tests/test_calendar_integration.py -v

# All tests
python -m pytest tests/ -v
```

## Troubleshooting

### Common Issues

1. **MT5 Not Found**: Ensure MetaTrader 5 is installed in standard location
2. **Permission Denied**: Run as administrator if needed
3. **No Data**: Check date ranges and ensure calendar is enabled in MT5
4. **Performance Issues**: Use filtering and limit `max_count` parameter

### Debug Mode

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## License

This project is provided as-is for educational and research purposes. Use at your own risk for trading decisions.