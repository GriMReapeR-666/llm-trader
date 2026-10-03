# Economic News Terminal

A comprehensive terminal interface application for viewing and analyzing economic news events with both manual and programmatic (LLM) access capabilities.

## Features

### 🎯 Core Functionality
- **Date Range Filtering**: Filter events by start/end dates with timezone support
- **Multiple View Modes**: Detailed and short display formats
- **Advanced Filtering**: Filter by impact level, event type, region, country, and currency
- **Interactive Navigation**: Page through results with keyboard shortcuts
- **Search Functionality**: Find events by title, description, country, or currency
- **Export Capabilities**: Export to CSV or JSON formats

### 🤖 LLM Programmatic Access
- **Structured JSON API**: Clean API interface for LLM consumption
- **CSV Output**: Tabular data format for data analysis
- **Comprehensive Metadata**: Full event details with market impact analysis
- **Error Handling**: Robust error responses for API failures

### 🎨 Terminal Features
- **Color-Coded Output**: Visual impact level indicators
- **Keyboard Shortcuts**: Quick navigation commands
- **Cross-Platform**: Works on Windows, macOS, and Linux
- **Interactive Mode**: Real-time filtering and search

## Installation

### Prerequisites
```bash
# Install required Python packages
pip install pandas pytz colorama requests
```

### Quick Start
```bash
# Make the script executable (Unix-like systems)
chmod +x economic_news_terminal.py

# Run with help
python economic_news_terminal.py --help
```

## Usage Examples

### Basic Usage
```bash
# View all events for January 2024
python economic_news_terminal.py --start-date 2024-01-01 --end-date 2024-01-31

# View high impact events only
python economic_news_terminal.py --start-date 2024-01-01 --end-date 2024-01-31 --impact-levels HIGH

# Short format view
python economic_news_terminal.py --start-date 2024-01-01 --end-date 2024-01-31 --format short
```

### Advanced Filtering
```bash
# Filter by multiple criteria
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --impact-levels HIGH MODERATE \
  --event-types EMPLOYMENT MONETARY_POLICY \
  --countries "United States" "European Union" \
  --currencies USD EUR

# Filter by geographic region
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --regions NORTH_AMERICA EUROPE
```

### Search Functionality
```bash
# Search for employment-related events
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --search "employment"

# Search for inflation events
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --search "inflation CPI"
```

### LLM API Access
```bash
# Get JSON data for LLM consumption
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --llm-api --output json

# Get CSV data for analysis
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --llm-api --output csv
```

### Export Data
```bash
# Export to CSV
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --export csv --export-file january_events

# Export to JSON
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --export json --export-file january_events
```

### Timezone Support
```bash
# Use specific timezone
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --timezone "America/New_York"

# Use UTC (default)
python economic_news_terminal.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --timezone UTC
```

## Interactive Mode

When running without export options, the application enters interactive mode with these commands:

### Navigation Commands
- `[N]ext` - Go to next page of results
- `[P]revious` - Go to previous page of results
- `[Q]uit` - Exit the application

### Utility Commands
- `[H]elp` - Show available commands
- `[S]earch` - Search events interactively
- `[E]xport` - Export current view to file
- `[F]ilters` - Show currently applied filters

### Interactive Example
```bash
# Start interactive session
python economic_news_terminal.py --start-date 2024-01-01 --end-date 2024-01-31

# Within the interactive session:
# Press 'N' for next page
# Press 'S' to search for events
# Press 'E' to export current view
# Press 'Q' to quit
```

## Data Structure

### Event Object Fields
- `id`: Unique event identifier
- `title`: Event name/title
- `date`: Event date and time (ISO format)
- `timezone`: Timezone of the event
- `source`: Data source organization
- `description`: Detailed event description
- `impact_level`: Impact level (LOW, MODERATE, HIGH, CRITICAL)
- `event_type`: Type of economic event
- `region`: Geographic region
- `country`: Country of origin
- `currency`: Affected currency
- `actual`: Actual released value
- `forecast`: Forecasted value
- `previous`: Previous period value
- `deviation`: Difference from forecast
- `market_reaction`: Market impact description
- `volatility_expected`: Expected market volatility
- `historical_significance`: Historical importance
- `reliability`: Data reliability assessment
- `revision_risk`: Risk of data revision
- `trading_volatility`: Typical trading volatility range
- `related_indicators`: Related economic indicators

## LLM Integration

### JSON API Response Format
```json
{
  "status": "success",
  "timestamp": "2024-01-15T10:30:00Z",
  "total_events": 25,
  "date_range": {
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-01-31T23:59:59Z"
  },
  "events": [
    {
      "id": "event_1",
      "title": "Non-Farm Payrolls",
      "date": "2024-01-05T13:30:00Z",
      "timezone": "UTC",
      "source": "Bureau of Labor Statistics",
      "description": "Monthly employment data showing job growth",
      "impact_level": "HIGH",
      "event_type": "EMPLOYMENT",
      "region": "NORTH_AMERICA",
      "country": "United States",
      "currency": "USD",
      "actual": "250K",
      "forecast": "200K",
      "previous": "180K",
      "deviation": "+50K",
      "market_reaction": "USD strengthened 0.8% vs EUR",
      "volatility_expected": "HIGH",
      "historical_significance": "Critical employment indicator for Fed policy",
      "reliability": "High - Government official data",
      "revision_risk": "Moderate - Subject to monthly revisions",
      "trading_volatility": "High - 50-100 pips typical",
      "related_indicators": ["Unemployment Rate", "Average Hourly Earnings"]
    }
  ]
}
```

### Error Response Format
```json
{
  "status": "error",
  "timestamp": "2024-01-15T10:30:00Z",
  "error": "Invalid date format. Use YYYY-MM-DD format.",
  "events": []
}
```

## Color Coding

The terminal interface uses color coding for visual impact identification:

- 🔴 **HIGH/CRITICAL Impact**: Red (bright) - Major market moving events
- 🟡 **MODERATE Impact**: Yellow (bright) - Moderate market impact
- 🟢 **LOW Impact**: Green - Minor market impact
- 🔵 **Headers/Info**: Blue - Information and navigation

## Error Handling

The application includes comprehensive error handling for:

- **Invalid Date Formats**: Clear error messages for incorrect date formats
- **Missing Data**: Graceful handling of missing or incomplete data
- **Timezone Issues**: Automatic fallback to UTC for invalid timezones
- **Export Failures**: Detailed error messages for file export issues
- **Network Problems**: Fallback to local data when network is unavailable

## Requirements

### Python Dependencies
```
pandas>=1.3.0
pytz>=2021.1
colorama>=0.4.4
requests>=2.25.0
```

### System Requirements
- Python 3.7 or higher
- Terminal with ANSI color support (recommended)
- Sufficient memory for data processing

## Troubleshooting

### Common Issues

1. **Colors not displaying correctly**
   - Ensure your terminal supports ANSI colors
   - Use `--no-color` flag to disable colors
   - Check terminal emulator settings

2. **Date parsing errors**
   - Use YYYY-MM-DD format for dates
   - Check for leading/trailing spaces
   - Verify date ranges are logical

3. **Export failures**
   - Check file permissions in target directory
   - Ensure sufficient disk space
   - Verify filename doesn't contain invalid characters

4. **Timezone issues**
   - Use IANA timezone names (e.g., "America/New_York")
   - Check pytz installation and updates
   - Use UTC as fallback for unknown timezones

## Contributing

This is a sample application. For production use, consider:
- Adding real-time data feeds
- Implementing user authentication
- Adding data caching mechanisms
- Creating web-based interface
- Adding more sophisticated filtering options

## License

This application is provided as-is for educational and development purposes.