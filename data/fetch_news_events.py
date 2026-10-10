#!/usr/bin/env python3
"""
MT5 Economic Calendar News Events Fetcher

This script demonstrates how to fetch and display economic calendar events
using the MT5 Economic Calendar Wrapper.
"""

import sys
import os
from datetime import datetime, timedelta, timezone
import logging
import pandas as pd

# Single import root (P1-C1/M3): repo root only — never this folder itself
# (adding data/ would double-load every data module under a bare name).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from data.mt5_calendar_wrapper import MT5CalendarWrapper
    from data.mt5_calendar_enhanced import EnhancedMT5CalendarWrapper
    from data.mt5_calendar_wrapper import CalendarEventImportance, CalendarEventType
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Please ensure all required files are in the current directory")
    sys.exit(1)

def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('mt5_calendar_news.log'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

def fetch_basic_calendar_events(logger):
    """Fetch basic calendar events using core wrapper"""
    logger.info("🚀 Starting basic calendar events fetch...")
    
    wrapper = MT5CalendarWrapper()
    
    try:
        # Initialize connection
        logger.info("Initializing MT5 connection...")
        success = wrapper.initialize()
        
        if not success:
            logger.error("❌ Failed to initialize MT5 connection")
            return None
            
        logger.info("✅ MT5 connection established")
        
        # Get available countries
        logger.info("Fetching available countries...")
        countries = wrapper.get_countries(max_count=50)
        logger.info(f"✅ Found {len(countries)} countries")
        
        # Display first 10 countries
        print("\n📍 Available Countries (Top 10):")
        for i, country in enumerate(countries[:10], 1):
            print(f"  {i:2d}. {country.name:<20} ({country.code}) - {country.currency}")
        
        # Get recent calendar events (last 7 days)
        logger.info("Fetching recent calendar events...")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        calendar_values = wrapper.get_value_history(
            date_from=date_from,
            date_to=date_to,
            max_count=100
        )
        
        logger.info(f"✅ Retrieved {len(calendar_values)} calendar events")
        
        # Filter high-impact events
        high_impact_events = [v for v in calendar_values if v.importance == 'HIGH']
        
        print(f"\n📊 Event Summary (Last 7 Days):")
        print(f"  Total Events: {len(calendar_values)}")
        print(f"  High Impact:  {len(high_impact_events)}")
        
        # Display high-impact events
        if high_impact_events:
            print(f"\n🔥 High-Impact Events (Last 7 Days):")
            for event in sorted(high_impact_events, key=lambda x: x.time, reverse=True)[:10]:
                print(f"  📅 {event.time.strftime('%Y-%m-%d %H:%M')} - {event.event_name}")
                print(f"     Country: {event.country_code} | Actual: {event.actual_value} | Forecast: {event.forecast_value}")
                if event.actual_value is not None and event.forecast_value is not None:
                    deviation = ((event.actual_value - event.forecast_value) / event.forecast_value * 100)
                    print(f"     Deviation: {deviation:+.1f}%")
                print()
        
        return calendar_values
        
    except Exception as e:
        logger.error(f"❌ Error fetching basic calendar events: {e}")
        return None
        
    finally:
        wrapper.shutdown()
        logger.info("🔄 MT5 connection closed")

def fetch_advanced_calendar_events(logger):
    """Fetch advanced calendar events using enhanced wrapper"""
    logger.info("🚀 Starting advanced calendar events fetch...")
    
    enhanced = EnhancedMT5CalendarWrapper()
    
    try:
        # Initialize connection
        logger.info("Initializing enhanced MT5 connection...")
        success = enhanced.initialize()
        
        if not success:
            logger.error("❌ Failed to initialize enhanced MT5 connection")
            return None
            
        logger.info("✅ Enhanced MT5 connection established")
        
        # Get calendar data as DataFrame (last 14 days)
        logger.info("Fetching calendar data as DataFrame...")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=14)
        
        df = enhanced.get_calendar_dataframe(
            date_from=date_from,
            date_to=date_to,
            max_count=200
        )
        
        logger.info(f"✅ Retrieved {len(df)} events as DataFrame")
        
        if df.empty:
            logger.warning("⚠️ No calendar data retrieved")
            return df
        
        # Analyze data
        print(f"\n📈 DataFrame Analysis (Last 14 Days):")
        print(f"  Shape: {df.shape}")
        print(f"  Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        print(f"  Countries: {df['country_code'].nunique()}")
        print(f"  Event Types: {df['event_type'].nunique()}")
        
        # Importance distribution
        importance_counts = df['importance'].value_counts()
        print(f"\n📊 Importance Distribution:")
        for importance, count in importance_counts.items():
            print(f"  {importance:<12}: {count:4d} events ({count/len(df)*100:5.1f}%)")
        
        # Top countries by event count
        country_counts = df['country_code'].value_counts()
        print(f"\n🌍 Top Countries by Event Count:")
        for country, count in country_counts.head().items():
            print(f"  {country:<3}: {count:3d} events")
        
        # Upcoming events (next 24 hours)
        logger.info("Fetching upcoming events...")
        upcoming = enhanced.get_upcoming_events(
            hours_ahead=24,
            importance_threshold=CalendarEventImportance.MODERATE
        )
        
        print(f"\n⏰ Upcoming Events (Next 24 Hours):")
        print(f"  Total Upcoming: {len(upcoming)}")
        
        if not upcoming.empty:
            # Show high-impact upcoming events
            high_impact_upcoming = upcoming[upcoming['importance'] == 'HIGH']
            if not high_impact_upcoming.empty:
                print(f"\n🔥 High-Impact Upcoming Events:")
                for _, event in high_impact_upcoming.head(5).iterrows():
                    print(f"  📅 {event['timestamp'].strftime('%Y-%m-%d %H:%M')} - {event['event_name']}")
                    print(f"     {event['country_code']} | Forecast: {event['forecast_value']}")
                    print()
        
        # Surprise events (large deviations from forecast)
        surprise_events = df.dropna(subset=['actual_value', 'forecast_value']).copy()
        if not surprise_events.empty:
            surprise_events['deviation_pct'] = abs(
                (surprise_events['actual_value'] - surprise_events['forecast_value']) / 
                surprise_events['forecast_value'] * 100
            )
            
            # Filter for significant surprises (≥10% deviation)
            significant_surprises = surprise_events[surprise_events['deviation_pct'] >= 10.0]
            
            if not significant_surprises.empty:
                print(f"\n⚡ Significant Surprises (≥10% deviation):")
                top_surprises = significant_surprises.nlargest(5, 'deviation_pct')
                
                for _, event in top_surprises.iterrows():
                    print(f"  📅 {event['timestamp'].strftime('%Y-%m-%d %H:%M')} - {event['event_name']}")
                    print(f"     {event['country_code']} | Actual: {event['actual_value']} | Forecast: {event['forecast_value']}")
                    print(f"     Deviation: {event['deviation_pct']:+.1f}%")
                    print()
        
        # Performance benchmark
        logger.info("Running performance benchmark...")
        benchmark = enhanced.benchmark_calendar_operations(iterations=3)
        
        print(f"\n⚡ Performance Benchmark:")
        print(f"  Countries (avg):     {benchmark['countries_avg_ms']:6.1f}ms")
        print(f"  Event by ID (avg):   {benchmark['event_by_id_avg_ms']:6.1f}ms")
        print(f"  Value History (avg): {benchmark['value_history_avg_ms']:6.1f}ms")
        
        return df
        
    except Exception as e:
        logger.error(f"❌ Error fetching advanced calendar events: {e}")
        return None
        
    finally:
        enhanced.shutdown()
        logger.info("🔄 Enhanced MT5 connection closed")

def save_results_to_csv(data, filename):
    """Save results to CSV file"""
    try:
        if isinstance(data, pd.DataFrame):
            data.to_csv(filename, index=False)
            print(f"💾 DataFrame saved to {filename}")
        elif isinstance(data, list) and data:
            # Convert list of CalendarValue objects to DataFrame
            df_data = []
            for item in data:
                df_data.append({
                    'timestamp': item.time,
                    'event_id': item.event_id,
                    'event_name': item.event_name,
                    'country_code': item.country_code,
                    'actual_value': item.actual_value,
                    'forecast_value': item.forecast_value,
                    'previous_value': item.prev_value,
                    'importance': item.importance,
                    'impact_type': item.impact_type
                })
            
            df = pd.DataFrame(df_data)
            df.to_csv(filename, index=False)
            print(f"💾 List data saved to {filename}")
        else:
            print(f"⚠️ No data to save")
            
    except Exception as e:
        print(f"❌ Error saving to CSV: {e}")

def create_sample_calendar_data():
    """Create sample calendar data for demonstration when MT5 is not available"""
    from datetime import datetime, timedelta
    import pandas as pd
    
    # Enhanced sample economic events data with more details
    sample_data = [
        {
            'time': (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S'),
            'country': 'United States',
            'currency': 'USD',
            'event': 'Non-Farm Payrolls',
            'actual': '250K',
            'forecast': '200K',
            'previous': '180K',
            'importance': 'HIGH',
            'impact': 'Positive',
            'description': 'Monthly employment data showing job growth',
            'event_type': 'Employment',
            'volatility_expected': 'HIGH',
            'deviation': '+50K',
            'deviation_percent': '+25.0%',
            'typical_range': '150K - 300K',
            'frequency': 'Monthly',
            'source': 'Bureau of Labor Statistics',
            'market_reaction': 'USD strengthened 0.8% vs EUR',
            'trading_volatility': 'High - 50-100 pips typical',
            'historical_significance': 'Critical employment indicator for Fed policy',
            'next_release': (datetime.now() + timedelta(days=27)).strftime('%Y-%m-%d'),
            'reliability': 'High - Government official data',
            'revision_risk': 'Moderate - Subject to monthly revisions'
        },
        {
            'time': (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S'),
            'country': 'European Union',
            'currency': 'EUR',
            'event': 'CPI Flash Estimate',
            'actual': '2.8%',
            'forecast': '2.5%',
            'previous': '2.4%',
            'importance': 'HIGH',
            'impact': 'Negative',
            'description': 'Consumer Price Index inflation measure',
            'event_type': 'Inflation',
            'volatility_expected': 'HIGH',
            'deviation': '+0.3%',
            'deviation_percent': '+12.0%',
            'typical_range': '1.5% - 3.5%',
            'frequency': 'Monthly',
            'source': 'Eurostat',
            'market_reaction': 'EUR weakened 0.5% vs USD',
            'trading_volatility': 'High - 40-80 pips typical',
            'historical_significance': 'Primary ECB policy target',
            'next_release': (datetime.now() + timedelta(days=28)).strftime('%Y-%m-%d'),
            'reliability': 'High - Official EU statistics',
            'revision_risk': 'Low - Flash estimate rarely revised significantly'
        },
        {
            'time': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'country': 'United Kingdom',
            'currency': 'GBP',
            'event': 'GDP Monthly Estimate',
            'actual': '0.3%',
            'forecast': '0.2%',
            'previous': '0.1%',
            'importance': 'MODERATE',
            'impact': 'Positive',
            'description': 'Monthly Gross Domestic Product estimate',
            'event_type': 'Growth',
            'volatility_expected': 'MODERATE',
            'deviation': '+0.1%',
            'deviation_percent': '+50.0%',
            'typical_range': '-0.5% - +0.8%',
            'frequency': 'Monthly',
            'source': 'Office for National Statistics',
            'market_reaction': 'GBP strengthened 0.3% vs EUR',
            'trading_volatility': 'Moderate - 20-40 pips typical',
            'historical_significance': 'Key indicator for BoE policy decisions',
            'next_release': (datetime.now() + timedelta(days=29)).strftime('%Y-%m-%d'),
            'reliability': 'Moderate - Subject to quarterly revisions',
            'revision_risk': 'High - Monthly data often revised'
        },
        {
            'time': (datetime.now() - timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S'),
            'country': 'Japan',
            'currency': 'JPY',
            'event': 'BoJ Interest Rate Decision',
            'actual': '-0.10%',
            'forecast': '-0.10%',
            'previous': '-0.10%',
            'importance': 'HIGH',
            'impact': 'Neutral',
            'description': 'Bank of Japan monetary policy decision',
            'event_type': 'Monetary Policy',
            'volatility_expected': 'HIGH',
            'deviation': '0.00%',
            'deviation_percent': '0.0%',
            'typical_range': '-0.10% - +0.50%',
            'frequency': '8 times per year',
            'source': 'Bank of Japan',
            'market_reaction': 'JPY remained stable vs USD',
            'trading_volatility': 'Very High - 100-200 pips typical',
            'historical_significance': 'Primary driver of JPY policy direction',
            'next_release': (datetime.now() + timedelta(days=45)).strftime('%Y-%m-%d'),
            'reliability': 'High - Central bank official decision',
            'revision_risk': 'None - Policy decision not revised'
        },
        {
            'time': (datetime.now() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S'),
            'country': 'Canada',
            'currency': 'CAD',
            'event': 'Employment Change',
            'actual': '',
            'forecast': '15.0K',
            'previous': '12.5K',
            'importance': 'MODERATE',
            'impact': 'Pending',
            'description': 'Monthly employment change data',
            'event_type': 'Employment',
            'volatility_expected': 'MODERATE',
            'deviation': '',
            'deviation_percent': '',
            'typical_range': '-50K - +100K',
            'frequency': 'Monthly',
            'source': 'Statistics Canada',
            'market_reaction': 'Awaiting release',
            'trading_volatility': 'Moderate - 30-60 pips typical',
            'historical_significance': 'Key indicator for BoC policy decisions',
            'next_release': (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            'reliability': 'High - Government official data',
            'revision_risk': 'Moderate - Subject to monthly revisions'
        },
        {
            'time': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'country': 'Australia',
            'currency': 'AUD',
            'event': 'RBA Rate Statement',
            'actual': '',
            'forecast': '4.35%',
            'previous': '4.35%',
            'importance': 'HIGH',
            'impact': 'Pending',
            'description': 'Reserve Bank of Australia monetary policy statement',
            'event_type': 'Monetary Policy',
            'volatility_expected': 'HIGH',
            'deviation': '',
            'deviation_percent': '',
            'typical_range': '3.0% - 5.0%',
            'frequency': '11 times per year',
            'source': 'Reserve Bank of Australia',
            'market_reaction': 'Awaiting release',
            'trading_volatility': 'High - 50-100 pips typical',
            'historical_significance': 'Primary driver of AUD monetary policy',
            'next_release': (datetime.now() + timedelta(days=32)).strftime('%Y-%m-%d'),
            'reliability': 'High - Central bank official statement',
            'revision_risk': 'None - Policy statement not revised'
        }
    ]
    
    return pd.DataFrame(sample_data)

def display_sample_summary(df):
    """Display summary of sample calendar data"""
    print("\n📊 Sample Economic Calendar Data Summary")
    print("=" * 50)
    
    print(f"\n📈 Total Events: {len(df)}")
    print(f"\n🌍 Countries:")
    for country in df['country'].unique():
        count = len(df[df['country'] == country])
        print(f"  • {country}: {count} events")
    
    print(f"\n💱 Currencies:")
    for currency in df['currency'].unique():
        count = len(df[df['currency'] == currency])
        print(f"  • {currency}: {count} events")
    
    print(f"\n⚡ Importance Levels:")
    for importance in df['importance'].unique():
        count = len(df[df['importance'] == importance])
        print(f"  • {importance}: {count} events")
    
    print(f"\n📅 Time Distribution:")
    past_events = len(df[df['time'] < datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    future_events = len(df[df['time'] >= datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    print(f"  • Past Events: {past_events}")
    print(f"  • Upcoming Events: {future_events}")
    
    print(f"\n📋 High Impact Events:")
    high_impact = df[df['importance'] == 'HIGH']
    for _, event in high_impact.iterrows():
        print(f"  • {event['event']} ({event['country']}) - {event['time']}")

def display_detailed_event_analysis(df):
    """Display detailed analysis for each economic event"""
    print("\n🔍 Detailed Event Analysis")
    print("=" * 80)
    
    for idx, event in df.iterrows():
        print(f"\n📋 EVENT {idx + 1}: {event['event']}")
        print("-" * 60)
        
        # Basic Information
        print(f"🕐 Time: {event['time']}")
        print(f"🌍 Country: {event['country']}")
        print(f"💱 Currency: {event['currency']}")
        print(f"📊 Event Type: {event['event_type']}")
        print(f"⚡ Importance: {event['importance']}")
        print(f"📈 Expected Volatility: {event['volatility_expected']}")
        print(f"📅 Frequency: {event['frequency']}")
        print(f"🏛️  Source: {event['source']}")
        
        # Data Analysis
        print(f"\n📈 DATA ANALYSIS:")
        print(f"   Actual: {event['actual'] if event['actual'] else 'Pending'}")
        print(f"   Forecast: {event['forecast']}")
        print(f"   Previous: {event['previous']}")
        
        if event['deviation']:
            print(f"   Deviation: {event['deviation']} ({event['deviation_percent']})")
        print(f"   Typical Range: {event['typical_range']}")
        
        # Market Impact
        print(f"\n💹 MARKET IMPACT:")
        print(f"   Impact Direction: {event['impact']}")
        print(f"   Market Reaction: {event['market_reaction']}")
        print(f"   Trading Volatility: {event['trading_volatility']}")
        
        # Event Significance
        print(f"\n📚 EVENT SIGNIFICANCE:")
        print(f"   Description: {event['description']}")
        print(f"   Historical Significance: {event['historical_significance']}")
        print(f"   Next Release: {event['next_release']}")
        print(f"   Data Reliability: {event['reliability']}")
        print(f"   Revision Risk: {event['revision_risk']}")
        
        print("-" * 60)
        input("\nPress Enter to continue to next event...")

def display_event_comparison(df):
    """Display comparison analysis between events"""
    print("\n📊 EVENT COMPARISON ANALYSIS")
    print("=" * 80)
    
    # Compare by event type
    print(f"\n🔍 ANALYSIS BY EVENT TYPE:")
    event_types = df['event_type'].unique()
    for event_type in event_types:
        type_events = df[df['event_type'] == event_type]
        print(f"\n📋 {event_type.upper()} Events ({len(type_events)}):")
        for _, event in type_events.iterrows():
            status = "✅ Released" if event['actual'] else "⏳ Pending"
            print(f"   • {event['event']} ({event['country']}) - {status}")
            if event['actual'] and event['deviation']:
                print(f"     └─ Deviation: {event['deviation']} ({event['deviation_percent']})")
    
    # Compare by volatility
    print(f"\n⚡ VOLATILITY ANALYSIS:")
    volatility_levels = df['volatility_expected'].unique()
    for vol_level in volatility_levels:
        vol_events = df[df['volatility_expected'] == vol_level]
        print(f"\n{vol_level} Volatility Events ({len(vol_events)}):")
        for _, event in vol_events.iterrows():
            print(f"   • {event['event']} - {event['trading_volatility']}")
    
    # Compare by impact
    print(f"\n💹 MARKET IMPACT ANALYSIS:")
    impacts = df['impact'].unique()
    for impact in impacts:
        impact_events = df[df['impact'] == impact]
        print(f"\n{impact} Impact Events ({len(impact_events)}):")
        for _, event in impact_events.iterrows():
            print(f"   • {event['event']} ({event['currency']}) - {event['market_reaction']}")

def display_trading_recommendations(df):
    """Display trading recommendations based on event analysis"""
    print("\n💰 TRADING RECOMMENDATIONS")
    print("=" * 80)
    
    # High volatility events
    high_vol_events = df[df['volatility_expected'] == 'HIGH']
    if len(high_vol_events) > 0:
        print(f"\n⚠️  HIGH VOLATILITY EVENTS - CAUTION ADVISED:")
        for _, event in high_vol_events.iterrows():
            if event['actual']:
                print(f"   • {event['event']} ({event['currency']})")
                print(f"     └─ Already released with {event['deviation_percent']} deviation")
                print(f"     └─ Market impact: {event['market_reaction']}")
            else:
                print(f"   • {event['event']} ({event['currency']}) - UPCOMING")
                print(f"     └─ Expected volatility: {event['trading_volatility']}")
                print(f"     └─ Recommendation: Wait for release, trade the breakout")
    
    # Pending events with high importance
    pending_high_impact = df[(df['actual'] == '') & (df['importance'] == 'HIGH')]
    if len(pending_high_impact) > 0:
        print(f"\n🎯 HIGH-IMPACT PENDING EVENTS - TRADING OPPORTUNITIES:")
        for _, event in pending_high_impact.iterrows():
            print(f"   • {event['event']} ({event['currency']})")
            print(f"     └─ Forecast: {event['forecast']}, Previous: {event['previous']}")
            print(f"     └─ Expected range: {event['typical_range']}")
            print(f"     └─ Strategy: Position for deviation from forecast")
    
    # Events with significant deviations
    significant_deviations = df[df['deviation_percent'].str.contains('%', na=False)]
    significant_deviations = significant_deviations[significant_deviations['deviation_percent'].str.len() > 3]
    if len(significant_deviations) > 0:
        print(f"\n📈 SIGNIFICANT DEVIATIONS - TREND CONFIRMATION:")
        for _, event in significant_deviations.iterrows():
            deviation_value = float(event['deviation_percent'].replace('%', '').replace('+', ''))
            if abs(deviation_value) > 10:
                print(f"   • {event['event']} - {event['deviation_percent']} deviation")
                print(f"     └─ Strong signal for {event['currency']} direction")
                print(f"     └─ Consider following the trend in related pairs")

def main():
    """Main function to fetch and display news events"""
    print("🎯 MT5 Economic Calendar News Events Fetcher")
    print("=" * 50)
    
    # Setup logging
    logger = setup_logging()
    
    try:
        # Check if MT5 is available
        print("\n🔍 Checking MT5 installation...")
        mt5_available = False
        try:
            from data.mt5_calendar_wrapper import MT5CalendarWrapper
            test_wrapper = MT5CalendarWrapper()
            test_wrapper.initialize()
            test_wrapper.shutdown()
            print("✅ MT5 installation detected")
            mt5_available = True
        except Exception as e:
            print(f"⚠️  MT5 not available: {str(e)}")
            print("\n📋 To use this script with real MT5 data, you need:")
            print("1. Install MetaTrader 5 terminal")
            print("2. Run the script with MT5 terminal path:")
            print("   python fetch_news_events.py --mt5-path 'C:\\Program Files\\MetaTrader 5\\terminal64.exe'")
            mt5_available = False
        
        if not mt5_available:
            print("\n💡 Creating sample data for demonstration...")
            # Create sample data for demonstration
            sample_data = create_sample_calendar_data()
            save_results_to_csv(sample_data, "sample_calendar_events.csv")
            
            # Display different analysis options
            print("\n📊 Choose analysis type:")
            print("1. Basic Summary")
            print("2. Detailed Event Analysis")
            print("3. Event Comparison")
            print("4. Trading Recommendations")
            print("5. All Analyses")
            
            try:
                choice = input("\nEnter your choice (1-5): ").strip()
                if choice == '1':
                    display_sample_summary(sample_data)
                elif choice == '2':
                    display_detailed_event_analysis(sample_data)
                elif choice == '3':
                    display_event_comparison(sample_data)
                elif choice == '4':
                    display_trading_recommendations(sample_data)
                elif choice == '5':
                    display_sample_summary(sample_data)
                    display_detailed_event_analysis(sample_data)
                    display_event_comparison(sample_data)
                    display_trading_recommendations(sample_data)
                else:
                    display_sample_summary(sample_data)
            except KeyboardInterrupt:
                display_sample_summary(sample_data)
            
            print("\n✅ Sample data saved to 'sample_calendar_events.csv'")
            return
        
        # Fetch basic calendar events
        print("\n📊 Fetching Basic Calendar Events...")
        basic_data = fetch_basic_calendar_events(logger)
        
        if basic_data:
            # Save basic results
            save_results_to_csv(basic_data, "basic_calendar_events.csv")
        
        print("\n" + "="*50)
        
        # Fetch advanced calendar events
        print("\n🔬 Fetching Advanced Calendar Events...")
        advanced_data = fetch_advanced_calendar_events(logger)
        
        if advanced_data is not None:
            # Save advanced results
            save_results_to_csv(advanced_data, "advanced_calendar_events.csv")
            
            # Display summary statistics
            print(f"\n📈 Summary Statistics:")
            print(f"  Total Events Retrieved: {len(advanced_data) if hasattr(advanced_data, '__len__') else 0}")
            
            if hasattr(advanced_data, 'columns'):
                print(f"  DataFrame Shape: {advanced_data.shape}")
                print(f"  Memory Usage: {advanced_data.memory_usage(deep=True).sum() / 1024:.1f} KB")
        
        print("\n✅ Calendar events fetch completed!")
        
    except KeyboardInterrupt:
        print("\n⚠️ Operation interrupted by user")
        logger.info("Operation interrupted by user")
        
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.error(f"Unexpected error: {e}", exc_info=True)
        
    finally:
        print("\n🎯 Script execution completed")

if __name__ == "__main__":
    main()