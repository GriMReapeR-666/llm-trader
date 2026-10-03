# MT5 Economic Calendar Wrapper - Usage Examples

## Table of Contents

1. [Basic Usage](#basic-usage)
2. [Country and Currency Filtering](#country-and-currency-filtering)
3. [Event Analysis](#event-analysis)
4. [DataFrame Operations](#dataframe-operations)
5. [Asynchronous Operations](#asynchronous-operations)
6. [Advanced Filtering](#advanced-filtering)
7. [Performance Monitoring](#performance-monitoring)
8. [Error Handling](#error-handling)
9. [Trading Integration](#trading-integration)
10. [Data Analysis Examples](#data-analysis-examples)

## Basic Usage

### Initialize and Get Countries

```python
from mt5_calendar_wrapper import MT5CalendarWrapper
from datetime import datetime, timedelta, timezone

# Initialize the wrapper
wrapper = MT5CalendarWrapper()
try:
    wrapper.initialize()
    print("✓ MT5 Calendar Wrapper initialized successfully")
    
    # Get all available countries
    countries = wrapper.get_countries()
    print(f"✓ Found {len(countries)} countries in economic calendar")
    
    # Display first 5 countries
    for country in countries[:5]:
        print(f"  - {country.name} ({country.code}) - Currency: {country.currency}")
        
except Exception as e:
    print(f"✗ Failed to initialize: {e}")
finally:
    wrapper.shutdown()
```

### Get Recent Calendar Events

```python
# Get calendar events for last 7 days
date_to = datetime.now(timezone.utc)
date_from = date_to - timedelta(days=7)

print(f"Retrieving calendar events from {date_from.date()} to {date_to.date()}")

calendar_values = wrapper.get_value_history(
    date_from=date_from,
    date_to=date_to,
    max_count=100
)

print(f"✓ Retrieved {len(calendar_values)} calendar events")

# Display high-impact events
high_impact_events = [v for v in calendar_values if v.importance == 'HIGH']
print(f"✓ Found {len(high_impact_events)} high-impact events")

for event in high_impact_events[:3]:
    print(f"  - {event.event_name} ({event.country_code}): {event.actual_value}")
```

## Country and Currency Filtering

### Get Events by Country

```python
# Get US economic events for last 30 days
date_to = datetime.now(timezone.utc)
date_from = date_to - timedelta(days=30)

us_events = wrapper.get_value_history(
    date_from=date_from,
    date_to=date_to,
    country_code="US",
    max_count=50
)

print(f"✓ Found {len(us_events)} US economic events")

# Group by event type
from collections import defaultdict
event_types = defaultdict(list)

for event in us_events:
    event_types[event.event_name].append(event)

# Display event summary
for event_name, events in event_types.items():
    if len(events) >= 2:  # Show events with multiple occurrences
        latest = events[-1]  # Most recent
        print(f"  - {event_name}: {len(events)} occurrences, latest: {latest.actual_value}")
```

### Get Events by Currency

```python
# Get EUR-related events
eur_events = wrapper.get_value_history(
    date_from=date_from,
    date_to=date_to,
    currency="EUR",
    max_count=50
)

print(f"✓ Found {len(eur_events)} EUR-related events")

# Analyze EUR event importance
eur_importance = {}
for event in eur_events:
    importance = event.importance
    if importance not in eur_importance:
        eur_importance[importance] = 0
    eur_importance[importance] += 1

for importance, count in eur_importance.items():
    print(f"  - {importance} importance: {count} events")
```

## Event Analysis

### Get Event Details by ID

```python
# First get some events to find IDs
recent_events = wrapper.get_value_history(
    date_from=datetime.now(timezone.utc) - timedelta(days=7),
    date_to=datetime.now(timezone.utc),
    max_count=10
)

if recent_events:
    # Get detailed information about first event
    event_id = recent_events[0].event_id
    event_details = wrapper.get_event_by_id(event_id)
    
    if event_details:
        print(f"Event Details:")
        print(f"  Name: {event_details.name}")
        print(f"  Type: {event_details.type}")
        print(f"  Importance: {event_details.importance}")
        print(f"  Sector: {event_details.sector}")
        print(f"  Frequency: {event_details.frequency}")
        print(f"  Unit: {event_details.unit}")
        
        # Get historical values for this event
        historical_values = wrapper.get_value_history_by_event(
            event_id=event_id,
            max_count=12  # Last 12 occurrences
        )
        
        print(f"✓ Found {len(historical_values)} historical values")
        
        # Show recent trend
        if len(historical_values) >= 3:
            recent = historical_values[-3:]
            values = [v.actual_value for v in recent if v.actual_value is not None]
            if len(values) >= 2:
                trend = "increasing" if values[-1] > values[0] else "decreasing"
                print(f"  Recent trend: {trend}")
```

### Analyze Event Impact

```python
# Get high-impact events and analyze their market impact
from mt5_calendar_enhanced import EnhancedMT5CalendarWrapper

enhanced_wrapper = EnhancedMT5CalendarWrapper()
enhanced_wrapper.initialize()

date_to = datetime.now(timezone.utc)
date_from = date_to - timedelta(days=14)

# Get calendar data as DataFrame
df = enhanced_wrapper.get_calendar_dataframe(
    date_from=date_from,
    date_to=date_to,
    max_count=200
)

print(f"✓ Retrieved {len(df)} calendar events as DataFrame")

# Analyze high-impact events
high_impact = df[df['importance'] == 'HIGH']
print(f"✓ Found {len(high_impact)} high-impact events")

# Show top events by actual value deviation from forecast
if not high_impact.empty:
    # Calculate deviation where both actual and forecast exist
    deviation_data = high_impact.dropna(subset=['actual_value', 'forecast_value'])
    if not deviation_data.empty:
        deviation_data['deviation_pct'] = abs(
            (deviation_data['actual_value'] - deviation_data['forecast_value']) / 
            deviation_data['forecast_value'] * 100
        )
        
        # Show events with largest deviations
        top_deviations = deviation_data.nlargest(5, 'deviation_pct')
        print("\nTop 5 high-impact events by forecast deviation:")
        for _, event in top_deviations.iterrows():
            print(f"  - {event['event_name']} ({event['country_code']}): "
                  f"{event['deviation_pct']:.1f}% deviation")
```

## DataFrame Operations

### Convert to DataFrame for Analysis

```python
# Get comprehensive calendar data
enhanced_wrapper = EnhancedMT5CalendarWrapper()
enhanced_wrapper.initialize()

date_to = datetime.now(timezone.utc)
date_from = date_to - timedelta(days=90)  # Last 3 months

df = enhanced_wrapper.get_calendar_dataframe(
    date_from=date_from,
    date_to=date_to,
    max_count=1000
)

print(f"✓ Retrieved {len(df)} events as DataFrame")
print(f"✓ DataFrame columns: {list(df.columns)}")

# Basic statistics
print(f"\nData Summary:")
print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
print(f"  Countries: {df['country_code'].nunique()}")
print(f"  Event types: {df['event_type'].nunique()}")

# Importance distribution
importance_counts = df['importance'].value_counts()
print(f"\nImportance Distribution:")
for importance, count in importance_counts.items():
    print(f"  - {importance}: {count} events ({count/len(df)*100:.1f}%)")

# Country with most events
country_counts = df['country_code'].value_counts()
print(f"\nTop 5 Countries by Event Count:")
for country, count in country_counts.head().items():
    print(f"  - {country}: {count} events")
```

### Time Series Analysis

```python
import matplotlib.pyplot as plt
import seaborn as sns

# Resample to daily event counts
daily_events = df.set_index('timestamp').resample('D').size()

# Plot daily event frequency
plt.figure(figsize=(12, 6))
daily_events.plot(kind='line')
plt.title('Daily Economic Calendar Event Frequency')
plt.xlabel('Date')
plt.ylabel('Number of Events')
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

# High-impact events over time
high_impact_daily = df[df['importance'] == 'HIGH'].set_index('timestamp').resample('D').size()

plt.figure(figsize=(12, 6))
high_impact_daily.plot(kind='line', color='red', linewidth=2)
plt.title('Daily High-Impact Economic Events')
plt.xlabel('Date')
plt.ylabel('Number of High-Impact Events')
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()
```

## Asynchronous Operations

### Async Data Retrieval

```python
import asyncio

async def fetch_calendar_data_async():
    """Asynchronously fetch calendar data"""
    
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        # Async countries retrieval
        print("Fetching countries asynchronously...")
        countries = await enhanced_wrapper.get_countries_async(max_count=20)
        print(f"✓ Retrieved {len(countries)} countries asynchronously")
        
        # Async DataFrame retrieval
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=14)
        
        print("Fetching calendar data asynchronously...")
        df = await enhanced_wrapper.get_calendar_dataframe_async(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        print(f"✓ Retrieved {len(df)} events asynchronously")
        
        return countries, df
        
    finally:
        enhanced_wrapper.shutdown()

# Run async operations
async def main():
    countries, df = await fetch_calendar_data_async()
    
    # Process async results
    print(f"\nAsync Results Summary:")
    print(f"  Countries: {len(countries)}")
    print(f"  Events: {len(df)}")
    
    if not df.empty:
        print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

# Execute async operations
if __name__ == "__main__":
    asyncio.run(main())
```

### Concurrent Async Operations

```python
async def fetch_multiple_countries_concurrently():
    """Fetch data for multiple countries concurrently"""
    
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        countries = ["US", "EU", "JP", "GB", "CA"]
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        # Create async tasks for each country
        tasks = []
        for country in countries:
            task = enhanced_wrapper.get_calendar_dataframe_async(
                date_from=date_from,
                date_to=date_to,
                country_code=country,
                max_count=50
            )
            tasks.append(task)
        
        print(f"Fetching data for {len(countries)} countries concurrently...")
        
        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks)
        
        # Process results
        country_data = {}
        for country, df in zip(countries, results):
            country_data[country] = df
            print(f"  - {country}: {len(df)} events")
        
        return country_data
        
    finally:
        enhanced_wrapper.shutdown()

# Run concurrent operations
async def main_concurrent():
    country_data = await fetch_multiple_countries_concurrently()
    
    # Analyze concurrent results
    total_events = sum(len(df) for df in country_data.values())
    print(f"\nConcurrent Results:")
    print(f"  Total events across all countries: {total_events}")

if __name__ == "__main__":
    asyncio.run(main_concurrent())
```

## Advanced Filtering

### Complex Filter Combinations

```python
from mt5_calendar_enhanced import CalendarFilter, CalendarEventImportance, CalendarEventType

# Create complex filter
filter_obj = CalendarFilter(
    date_from=datetime.now(timezone.utc) - timedelta(days=30),
    date_to=datetime.now(timezone.utc),
    country_codes=["US", "EU", "JP", "GB"],
    currencies=["USD", "EUR", "JPY", "GBP"],
    importance_levels=[CalendarEventImportance.MODERATE, CalendarEventImportance.HIGH],
    event_types=[CalendarEventType.CPI, CalendarEventType.GDP, CalendarEventType.PAYROLL],
    max_count=200
)

enhanced_wrapper = EnhancedMT5CalendarWrapper()
enhanced_wrapper.initialize()

# Get filtered countries
filtered_countries = enhanced_wrapper.get_countries_filtered(filter_obj)
print(f"✓ Found {len(filtered_countries)} countries matching filter criteria")

# Get filtered calendar data
df = enhanced_wrapper.get_calendar_dataframe(
    date_from=filter_obj.date_from,
    date_to=filter_obj.date_to,
    country_code="US",  # Can still override filter
    max_count=filter_obj.max_count
)

print(f"✓ Retrieved {len(df)} events matching filter criteria")

# Analyze filtered results
if not df.empty:
    # Importance distribution
    importance_dist = df['importance'].value_counts()
    print(f"\nFiltered Data Importance Distribution:")
    for importance, count in importance_dist.items():
        print(f"  - {importance}: {count} events")
    
    # Event type distribution
    type_dist = df['event_type'].value_counts()
    print(f"\nFiltered Data Event Type Distribution:")
    for event_type, count in type_dist.items():
        print(f"  - {event_type}: {count} events")
```

### Custom Filtering Logic

```python
def get_surprise_events(df, deviation_threshold=10.0):
    """
    Identify events with significant deviations from forecasts
    
    Args:
        df: Calendar DataFrame
        deviation_threshold: Minimum percentage deviation to consider
    
    Returns:
        DataFrame with surprise events
    """
    # Filter events with both actual and forecast values
    surprise_candidates = df.dropna(subset=['actual_value', 'forecast_value'])
    
    if surprise_candidates.empty:
        return pd.DataFrame()
    
    # Calculate percentage deviation
    surprise_candidates = surprise_candidates.copy()
    surprise_candidates['deviation_pct'] = abs(
        (surprise_candidates['actual_value'] - surprise_candidates['forecast_value']) / 
        surprise_candidates['forecast_value'] * 100
    )
    
    # Filter by threshold
    surprise_events = surprise_candidates[
        surprise_candidates['deviation_pct'] >= deviation_threshold
    ].sort_values('deviation_pct', ascending=False)
    
    return surprise_events

# Get surprise events
surprise_events = get_surprise_events(df, deviation_threshold=15.0)

print(f"✓ Found {len(surprise_events)} surprise events (≥15% deviation)")

if not surprise_events.empty:
    print(f"\nTop 5 Surprise Events:")
    for _, event in surprise_events.head().iterrows():
        print(f"  - {event['event_name']} ({event['country_code']}): "
              f"{event['deviation_pct']:.1f}% deviation")
```

## Performance Monitoring

### Benchmark Calendar Operations

```python
# Run performance benchmark
print("Running performance benchmark...")
benchmark = enhanced_wrapper.benchmark_calendar_operations(iterations=5)

print(f"\nPerformance Benchmark Results:")
print(f"  Countries retrieval: {benchmark['countries_avg_ms']:.2f}ms avg")
print(f"  Event by ID retrieval: {benchmark['event_by_id_avg_ms']:.2f}ms avg")
print(f"  Value history retrieval: {benchmark['value_history_avg_ms']:.2f}ms avg")

# Custom performance test
def custom_performance_test():
    """Custom performance testing"""
    import time
    
    operations = [
        ("get_countries", lambda: enhanced_wrapper.get_countries(max_count=50)),
        ("get_value_history_7d", lambda: enhanced_wrapper.get_value_history(
            date_from=datetime.now(timezone.utc) - timedelta(days=7),
            date_to=datetime.now(timezone.utc),
            max_count=100
        )),
        ("get_value_history_US", lambda: enhanced_wrapper.get_value_history(
            date_from=datetime.now(timezone.utc) - timedelta(days=30),
            date_to=datetime.now(timezone.utc),
            country_code="US",
            max_count=50
        ))
    ]
    
    results = {}
    
    for name, operation in operations:
        times = []
        for _ in range(3):  # 3 iterations
            start = time.time()
            result = operation()
            end = time.time()
            times.append((end - start) * 1000)  # Convert to ms
        
        avg_time = sum(times) / len(times)
        results[name] = {
            'avg_ms': avg_time,
            'results_count': len(result) if hasattr(result, '__len__') else 1
        }
        
        print(f"  {name}: {avg_time:.2f}ms avg ({len(result)} results)")
    
    return results

print(f"\nCustom Performance Test:")
custom_results = custom_performance_test()
```

## Error Handling

### Comprehensive Error Handling

```python
from mt5_calendar_wrapper import (
    MT5CalendarWrapper, MT5CalendarError, 
    MT5CalendarConnectionError, MT5CalendarDataError
)

def robust_calendar_operations():
    """Demonstrate robust error handling"""
    
    wrapper = MT5CalendarWrapper()
    
    try:
        # Initialize with error handling
        print("Initializing MT5 Calendar Wrapper...")
        wrapper.initialize()
        print("✓ Initialization successful")
        
        # Try various operations with error handling
        operations = [
            ("get_countries", lambda: wrapper.get_countries(max_count=10)),
            ("get_value_history", lambda: wrapper.get_value_history(
                date_from=datetime.now(timezone.utc) - timedelta(days=1),
                date_to=datetime.now(timezone.utc),
                max_count=5
            )),
            ("get_event_by_id", lambda: wrapper.get_event_by_id(999999)),  # Non-existent ID
        ]
        
        for name, operation in operations:
            try:
                print(f"\nAttempting {name}...")
                result = operation()
                
                if result is None:
                    print(f"⚠ {name} returned None (no data found)")
                elif hasattr(result, '__len__'):
                    print(f"✓ {name} successful: {len(result)} results")
                else:
                    print(f"✓ {name} successful: {result}")
                    
            except MT5CalendarDataError as e:
                print(f"✗ Data error in {name}: {e}")
            except Exception as e:
                print(f"✗ Unexpected error in {name}: {e}")
        
    except MT5CalendarConnectionError as e:
        print(f"✗ Connection error: {e}")
        print("  Please ensure MetaTrader 5 is installed and running")
    except MT5CalendarError as e:
        print(f"✗ Calendar error: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
    finally:
        try:
            wrapper.shutdown()
            print("\n✓ Cleanup successful")
        except Exception as e:
            print(f"⚠ Cleanup error: {e}")

# Run robust operations
robust_calendar_operations()
```

### Retry Logic

```python
import time
from functools import wraps

def retry_on_error(max_attempts=3, delay=1.0):
    """Decorator for retrying operations on errors"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except (MT5CalendarConnectionError, MT5CalendarDataError) as e:
                    if attempt < max_attempts - 1:
                        print(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                        time.sleep(delay)
                    else:
                        print(f"All {max_attempts} attempts failed. Giving up.")
                        raise
                except Exception as e:
                    print(f"Unexpected error on attempt {attempt + 1}: {e}")
                    raise
            return None
        return wrapper
    return decorator

@retry_on_error(max_attempts=3, delay=2.0)
def get_countries_with_retry(wrapper):
    """Get countries with automatic retry"""
    return wrapper.get_countries(max_count=20)

# Use retry logic
wrapper = MT5CalendarWrapper()
try:
    wrapper.initialize()
    countries = get_countries_with_retry(wrapper)
    print(f"✓ Successfully retrieved {len(countries)} countries with retry")
except Exception as e:
    print(f"✗ Failed after retries: {e}")
finally:
    wrapper.shutdown()
```

## Trading Integration

### Economic Calendar for Trading Decisions

```python
def get_trading_signals():
    """
    Generate trading signals based on economic calendar events
    
    Returns:
        Dictionary with trading signals by currency
    """
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        # Get upcoming high-impact events
        upcoming = enhanced_wrapper.get_upcoming_events(
            hours_ahead=24,
            importance_threshold=CalendarEventImportance.HIGH
        )
        
        if upcoming.empty:
            return {"signal": "NO_SIGNAL", "reason": "No high-impact events upcoming"}
        
        # Analyze events by currency
        currency_signals = {}
        
        for currency in upcoming['currency'].unique():
            currency_events = upcoming[upcoming['currency'] == currency]
            
            # Count high-impact events
            high_impact_count = len(currency_events)
            
            # Calculate average forecast deviation for recent events
            recent_events = currency_events.dropna(subset=['actual_value', 'forecast_value'])
            if not recent_events.empty:
                avg_deviation = abs(
                    (recent_events['actual_value'] - recent_events['forecast_value']) / 
                    recent_events['forecast_value']
                ).mean() * 100
            else:
                avg_deviation = 0
            
            # Generate signal based on event count and deviation
            if high_impact_count >= 2 and avg_deviation > 5:
                signal = "HIGH_VOLATILITY_EXPECTED"
            elif high_impact_count >= 1 and avg_deviation > 3:
                signal = "MODERATE_VOLATILITY_EXPECTED"
            elif high_impact_count >= 1:
                signal = "WATCH_FOR_NEWS"
            else:
                signal = "NORMAL_CONDITIONS"
            
            currency_signals[currency] = {
                "signal": signal,
                "event_count": high_impact_count,
                "avg_deviation_pct": avg_deviation,
                "next_event": currency_events.iloc[0]['timestamp'] if not currency_events.empty else None
            }
        
        return currency_signals
        
    finally:
        enhanced_wrapper.shutdown()

# Get trading signals
signals = get_trading_signals()
print("Trading Signals Based on Economic Calendar:")

if isinstance(signals, dict) and "signal" in signals:
    print(f"  Overall: {signals['signal']} - {signals['reason']}")
else:
    for currency, signal_info in signals.items():
        print(f"  {currency}: {signal_info['signal']}")
        print(f"    Events: {signal_info['event_count']}")
        print(f"    Avg Deviation: {signal_info['avg_deviation_pct']:.1f}%")
        if signal_info['next_event']:
            print(f"    Next Event: {signal_info['next_event']}")
```

### Risk Management with Calendar Events

```python
def assess_calendar_risk():
    """
    Assess risk based on upcoming calendar events
    
    Returns:
        Risk assessment dictionary
    """
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        # Get events for next 48 hours
        upcoming_24h = enhanced_wrapper.get_upcoming_events(
            hours_ahead=24,
            importance_threshold=CalendarEventImportance.MODERATE
        )
        
        upcoming_48h = enhanced_wrapper.get_upcoming_events(
            hours_ahead=48,
            importance_threshold=CalendarEventImportance.MODERATE
        )
        
        risk_assessment = {
            "risk_level": "LOW",
            "reasons": [],
            "recommendations": [],
            "upcoming_events": {
                "24h": len(upcoming_24h),
                "24_48h": len(upcoming_48h) - len(upcoming_24h)
            }
        }
        
        # Assess 24-hour risk
        if not upcoming_24h.empty:
            high_impact_24h = upcoming_24h[upcoming_24h['importance'] == 'HIGH']
            
            if len(high_impact_24h) >= 3:
                risk_assessment["risk_level"] = "HIGH"
                risk_assessment["reasons"].append(f"{len(high_impact_24h)} high-impact events in next 24h")
                risk_assessment["recommendations"].append("Consider reducing position sizes")
                risk_assessment["recommendations"].append("Avoid new positions before major events")
            elif len(high_impact_24h) >= 1:
                risk_assessment["risk_level"] = "MODERATE"
                risk_assessment["reasons"].append(f"{len(high_impact_24h)} high-impact events in next 24h")
                risk_assessment["recommendations"].append("Monitor events closely")
            
            # Check for clustered events (multiple events within 2 hours)
            upcoming_24h_sorted = upcoming_24h.sort_values('timestamp')
            for i in range(len(upcoming_24h_sorted) - 1):
                time_diff = (upcoming_24h_sorted.iloc[i+1]['timestamp'] - 
                           upcoming_24h_sorted.iloc[i]['timestamp']).total_seconds() / 3600
                
                if time_diff <= 2 and time_diff > 0:
                    if risk_assessment["risk_level"] == "LOW":
                        risk_assessment["risk_level"] = "MODERATE"
                    risk_assessment["reasons"].append("Clustered events detected")
                    risk_assessment["recommendations"].append("Watch for event clustering effects")
                    break
        
        # Assess 48-hour risk
        if len(upcoming_48h) - len(upcoming_24h) >= 5:
            if risk_assessment["risk_level"] == "LOW":
                risk_assessment["risk_level"] = "MODERATE"
            risk_assessment["reasons"].append("High event density in 24-48h window")
        
        if risk_assessment["risk_level"] == "LOW":
            risk_assessment["reasons"].append("Normal calendar conditions")
            risk_assessment["recommendations"].append("Standard risk management applies")
        
        return risk_assessment
        
    finally:
        enhanced_wrapper.shutdown()

# Get risk assessment
risk_assessment = assess_calendar_risk()

print(f"Calendar-Based Risk Assessment:")
print(f"  Risk Level: {risk_assessment['risk_level']}")
print(f"  Events (24h): {risk_assessment['upcoming_events']['24h']}")
print(f"  Events (24-48h): {risk_assessment['upcoming_events']['24_48h']}")

if risk_assessment['reasons']:
    print(f"  Reasons:")
    for reason in risk_assessment['reasons']:
        print(f"    - {reason}")

if risk_assessment['recommendations']:
    print(f"  Recommendations:")
    for rec in risk_assessment['recommendations']:
        print(f"    - {rec}")
```

## Data Analysis Examples

### Event Impact Analysis

```python
def analyze_event_impact_history():
    """
    Analyze historical event impact on market volatility
    
    This is a conceptual example - actual market data integration
    would be needed for real analysis
    """
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        # Get historical high-impact events
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=90)
        
        df = enhanced_wrapper.get_calendar_dataframe(
            date_from=date_from,
            date_to=date_to,
            max_count=500
        )
        
        # Filter high-impact events with forecast data
        high_impact = df[
            (df['importance'] == 'HIGH') & 
            (df['actual_value'].notna()) & 
            (df['forecast_value'].notna())
        ].copy()
        
        if high_impact.empty:
            return {"error": "No high-impact events with forecast data found"}
        
        # Calculate surprise magnitude
        high_impact['surprise_magnitude'] = abs(
            (high_impact['actual_value'] - high_impact['forecast_value']) / 
            high_impact['forecast_value']
        )
        
        # Categorize surprises
        high_impact['surprise_category'] = pd.cut(
            high_impact['surprise_magnitude'],
            bins=[0, 0.05, 0.15, 1.0],
            labels=['Small', 'Medium', 'Large']
        )
        
        # Analyze by event type
        event_analysis = {}
        for event_type in high_impact['event_type'].unique():
            type_events = high_impact[high_impact['event_type'] == event_type]
            
            event_analysis[event_type] = {
                'total_events': len(type_events),
                'avg_surprise': type_events['surprise_magnitude'].mean(),
                'surprise_categories': type_events['surprise_category'].value_counts().to_dict(),
                'avg_actual_value': type_events['actual_value'].mean(),
                'avg_forecast_value': type_events['forecast_value'].mean()
            }
        
        return event_analysis
        
    finally:
        enhanced_wrapper.shutdown()

# Analyze event impact
impact_analysis = analyze_event_impact_history()

print("Historical Event Impact Analysis:")
if "error" in impact_analysis:
    print(f"  Error: {impact_analysis['error']}")
else:
    for event_type, analysis in impact_analysis.items():
        print(f"\n  {event_type}:")
        print(f"    Total Events: {analysis['total_events']}")
        print(f"    Average Surprise: {analysis['avg_surprise']:.1%}")
        print(f"    Average Actual: {analysis['avg_actual_value']:.2f}")
        print(f"    Average Forecast: {analysis['avg_forecast_value']:.2f}")
        
        print(f"    Surprise Distribution:")
        for category, count in analysis['surprise_categories'].items():
            print(f"      {category}: {count} events")
```

### Seasonal Event Patterns

```python
def analyze_seasonal_patterns():
    """Analyze seasonal patterns in economic calendar events"""
    
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        # Get one year of data
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=365)
        
        df = enhanced_wrapper.get_calendar_dataframe(
            date_from=date_from,
            date_to=date_to,
            max_count=2000
        )
        
        if df.empty:
            return {"error": "No data available for seasonal analysis"}
        
        # Add time-based features
        df['month'] = df['timestamp'].dt.month
        df['day_of_week'] = df['timestamp'].dt.day_name()
        df['hour'] = df['timestamp'].dt.hour
        
        # Analyze monthly patterns
        monthly_patterns = df.groupby('month').agg({
            'event_id': 'count',
            'importance': lambda x: (x == 'HIGH').sum()
        }).rename(columns={'event_id': 'total_events', 'importance': 'high_impact_events'})
        
        # Analyze day-of-week patterns
        dow_patterns = df.groupby('day_of_week').agg({
            'event_id': 'count',
            'importance': lambda x: (x == 'HIGH').sum()
        }).rename(columns={'event_id': 'total_events', 'importance': 'high_impact_events'})
        
        # Reorder days of week
day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        dow_patterns = dow_patterns.reindex(day_order)
        
        # Analyze hourly patterns
        hourly_patterns = df.groupby('hour').agg({
            'event_id': 'count',
            'importance': lambda x: (x == 'HIGH').sum()
        }).rename(columns={'event_id': 'total_events', 'importance': 'high_impact_events'})
        
        return {
            'monthly_patterns': monthly_patterns.to_dict('index'),
            'day_of_week_patterns': dow_patterns.to_dict('index'),
            'hourly_patterns': hourly_patterns.to_dict('index'),
            'summary': {
                'total_events': len(df),
                'high_impact_events': len(df[df['importance'] == 'HIGH']),
                'date_range': f"{df['timestamp'].min().date()} to {df['timestamp'].max().date()}"
            }
        }
        
    finally:
        enhanced_wrapper.shutdown()

# Analyze seasonal patterns
seasonal_analysis = analyze_seasonal_patterns()

print("Seasonal Event Pattern Analysis:")
if "error" in seasonal_analysis:
    print(f"  Error: {seasonal_analysis['error']}")
else:
    print(f"  Summary: {seasonal_analysis['summary']['total_events']} total events, "
          f"{seasonal_analysis['summary']['high_impact_events']} high-impact")
    
    print(f"\n  Monthly Patterns (Top 3):")
    monthly = seasonal_analysis['monthly_patterns']
    sorted_monthly = sorted(monthly.items(), key=lambda x: x[1]['total_events'], reverse=True)[:3]
    for month, data in sorted_monthly:
        month_name = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month-1]
        print(f"    {month_name}: {data['total_events']} total, {data['high_impact_events']} high-impact")
    
    print(f"\n  Day of Week Patterns:")
    dow = seasonal_analysis['day_of_week_patterns']
    for day, data in dow.items():
        if data['total_events'] > 0:
            print(f"    {day}: {data['total_events']} total, {data['high_impact_events']} high-impact")
    
    print(f"\n  Peak Hours (Top 3):")
    hourly = seasonal_analysis['hourly_patterns']
    sorted_hourly = sorted(hourly.items(), key=lambda x: x[1]['total_events'], reverse=True)[:3]
    for hour, data in sorted_hourly:
        print(f"    {hour:02d}:00 - {data['total_events']} events")
```

These usage examples demonstrate the comprehensive capabilities of the MT5 Economic Calendar Wrapper, from basic data retrieval to advanced trading integration and analysis. The examples cover real-world scenarios that traders and analysts would encounter when working with economic calendar data.