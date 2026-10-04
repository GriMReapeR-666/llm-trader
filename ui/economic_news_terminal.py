#!/usr/bin/env python3
"""
import logging

logger = logging.getLogger(__name__)
Economic News Terminal Interface
A comprehensive terminal application for viewing and accessing economic news events
with both manual and programmatic (LLM) interfaces.
"""

import argparse
import json
import csv
import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
import pytz
from colorama import init, Fore, Back, Style
import requests
from urllib.parse import urlencode
import re

# Initialize colorama for cross-platform colored output
init(autoreset=True)

class ImpactLevel(Enum):
    """Economic event impact levels"""
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class EventType(Enum):
    """Types of economic events"""
    EMPLOYMENT = "Employment"
    INFLATION = "Inflation"
    GDP = "GDP"
    MONETARY_POLICY = "Monetary Policy"
    TRADE = "Trade"
    CONSUMER = "Consumer"
    HOUSING = "Housing"
    BUSINESS = "Business"

class GeographicRegion(Enum):
    """Geographic regions for filtering"""
    NORTH_AMERICA = "North America"
    EUROPE = "Europe"
    ASIA_PACIFIC = "Asia Pacific"
    EMERGING_MARKETS = "Emerging Markets"
    GLOBAL = "Global"

@dataclass
class EconomicEvent:
    """Enhanced economic event data structure"""
    id: str
    title: str
    date: datetime
    timezone: str
    source: str
    description: str
    impact_level: ImpactLevel
    event_type: EventType
    region: GeographicRegion
    country: str
    currency: str
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    deviation: Optional[str] = None
    market_reaction: Optional[str] = None
    volatility_expected: Optional[str] = None
    historical_significance: Optional[str] = None
    next_release: Optional[datetime] = None
    reliability: Optional[str] = None
    revision_risk: Optional[str] = None
    related_indicators: Optional[List[str]] = None
    trading_volatility: Optional[str] = None

class TerminalColors:
    """Color schemes for terminal output"""
    HEADER = Fore.CYAN + Style.BRIGHT
    HIGH_IMPACT = Fore.RED + Style.BRIGHT
    MODERATE_IMPACT = Fore.YELLOW + Style.BRIGHT
    LOW_IMPACT = Fore.GREEN
    CRITICAL_IMPACT = Fore.MAGENTA + Style.BRIGHT
    INFO = Fore.BLUE
    SUCCESS = Fore.GREEN + Style.BRIGHT
    WARNING = Fore.YELLOW
    ERROR = Fore.RED
    RESET = Style.RESET_ALL

class EconomicNewsTerminal:
    """Main terminal interface class"""
    
    def __init__(self):
        self.events: List[EconomicEvent] = []
        self.filtered_events: List[EconomicEvent] = []
        self.current_page = 1
        self.items_per_page = 10
        self.view_mode = "detailed"  # detailed or short
        self.color_enabled = True
        
    def load_sample_data(self) -> None:
        """Load sample economic events data"""
        try:
            # Import sample data from our existing fetch_news_events.py
            from data.fetch_news_events import create_sample_calendar_data
            
            sample_df = create_sample_calendar_data()
            self.events = self._convert_sample_data_to_events(sample_df)
            self.filtered_events = self.events.copy()
            
        except ImportError:
            # Fallback to built-in sample data
            self.events = self._create_builtin_sample_data()
            self.filtered_events = self.events.copy()
    
    def _convert_sample_data_to_events(self, df) -> List[EconomicEvent]:
        """Convert pandas DataFrame to EconomicEvent objects"""
        events = []
        
        for idx, row in df.iterrows():
            try:
                # Parse datetime
                event_time = datetime.strptime(row['time'], '%Y-%m-%d %H:%M:%S')
                
                # Map impact level
                impact_map = {
                    'HIGH': ImpactLevel.HIGH,
                    'MODERATE': ImpactLevel.MODERATE,
                    'LOW': ImpactLevel.LOW
                }
                impact = impact_map.get(row['importance'], ImpactLevel.MODERATE)
                
                # Map event type
                event_type_map = {
                    'Employment': EventType.EMPLOYMENT,
                    'Inflation': EventType.INFLATION,
                    'Growth': EventType.GDP,
                    'Monetary Policy': EventType.MONETARY_POLICY
                }
                event_type = event_type_map.get(row['event_type'], EventType.BUSINESS)
                
                # Determine region
                region_map = {
                    'United States': GeographicRegion.NORTH_AMERICA,
                    'European Union': GeographicRegion.EUROPE,
                    'United Kingdom': GeographicRegion.EUROPE,
                    'Japan': GeographicRegion.ASIA_PACIFIC,
                    'Canada': GeographicRegion.NORTH_AMERICA,
                    'Australia': GeographicRegion.ASIA_PACIFIC
                }
                region = region_map.get(row['country'], GeographicRegion.GLOBAL)
                
                event = EconomicEvent(
                    id=f"event_{idx}",
                    title=row['event'],
                    date=event_time,
                    timezone="UTC",
                    source=row.get('source', 'Unknown'),
                    description=row['description'],
                    impact_level=impact,
                    event_type=event_type,
                    region=region,
                    country=row['country'],
                    currency=row['currency'],
                    actual=row.get('actual', ''),
                    forecast=row.get('forecast', ''),
                    previous=row.get('previous', ''),
                    deviation=row.get('deviation', ''),
                    market_reaction=row.get('market_reaction', ''),
                    volatility_expected=row.get('volatility_expected', ''),
                    historical_significance=row.get('historical_significance', ''),
                    reliability=row.get('reliability', ''),
                    revision_risk=row.get('revision_risk', ''),
                    trading_volatility=row.get('trading_volatility', ''),
                    related_indicators=[]
                )
                
                # Parse next release if available
                if 'next_release' in row and row['next_release']:
                    try:
                        event.next_release = datetime.strptime(row['next_release'], '%Y-%m-%d')
                    except Exception as exc:
                        logger.debug("event emission failed: %s", exc)
                
                events.append(event)
                
            except Exception as e:
                print(f"Warning: Could not convert row {idx}: {e}")
                continue
        
        return events
    
    def _create_builtin_sample_data(self) -> List[EconomicEvent]:
        """Create built-in sample data as fallback"""
        now = datetime.now()
        
        return [
            EconomicEvent(
                id="event_1",
                title="Non-Farm Payrolls",
                date=now - timedelta(days=3),
                timezone="UTC",
                source="Bureau of Labor Statistics",
                description="Monthly employment data showing job growth",
                impact_level=ImpactLevel.HIGH,
                event_type=EventType.EMPLOYMENT,
                region=GeographicRegion.NORTH_AMERICA,
                country="United States",
                currency="USD",
                actual="250K",
                forecast="200K",
                previous="180K",
                deviation="+50K",
                market_reaction="USD strengthened 0.8% vs EUR",
                volatility_expected="HIGH",
                historical_significance="Critical employment indicator for Fed policy",
                reliability="High - Government official data",
                revision_risk="Moderate - Subject to monthly revisions",
                trading_volatility="High - 50-100 pips typical",
                related_indicators=["Unemployment Rate", "Average Hourly Earnings"]
            ),
            EconomicEvent(
                id="event_2",
                title="CPI Flash Estimate",
                date=now - timedelta(days=2),
                timezone="UTC",
                source="Eurostat",
                description="Consumer Price Index inflation measure",
                impact_level=ImpactLevel.HIGH,
                event_type=EventType.INFLATION,
                region=GeographicRegion.EUROPE,
                country="European Union",
                currency="EUR",
                actual="2.8%",
                forecast="2.5%",
                previous="2.4%",
                deviation="+0.3%",
                market_reaction="EUR weakened 0.5% vs USD",
                volatility_expected="HIGH",
                historical_significance="Primary ECB policy target",
                reliability="High - Official EU statistics",
                revision_risk="Low - Flash estimate rarely revised",
                trading_volatility="High - 40-80 pips typical",
                related_indicators=["Core CPI", "HICP"]
            ),
            EconomicEvent(
                id="event_3",
                title="GDP Monthly Estimate",
                date=now - timedelta(days=1),
                timezone="UTC",
                source="Office for National Statistics",
                description="Monthly Gross Domestic Product estimate",
                impact_level=ImpactLevel.MODERATE,
                event_type=EventType.GDP,
                region=GeographicRegion.EUROPE,
                country="United Kingdom",
                currency="GBP",
                actual="0.3%",
                forecast="0.2%",
                previous="0.1%",
                deviation="+0.1%",
                market_reaction="GBP strengthened 0.3% vs EUR",
                volatility_expected="MODERATE",
                historical_significance="Key indicator for BoE policy decisions",
                reliability="Moderate - Subject to quarterly revisions",
                revision_risk="High - Monthly data often revised",
                trading_volatility="Moderate - 20-40 pips typical",
                related_indicators=["Industrial Production", "Services PMI"]
            ),
            EconomicEvent(
                id="event_4",
                title="BoJ Interest Rate Decision",
                date=now - timedelta(hours=12),
                timezone="UTC",
                source="Bank of Japan",
                description="Bank of Japan monetary policy decision",
                impact_level=ImpactLevel.HIGH,
                event_type=EventType.MONETARY_POLICY,
                region=GeographicRegion.ASIA_PACIFIC,
                country="Japan",
                currency="JPY",
                actual="-0.10%",
                forecast="-0.10%",
                previous="-0.10%",
                deviation="0.00%",
                market_reaction="JPY remained stable vs USD",
                volatility_expected="HIGH",
                historical_significance="Primary driver of JPY policy direction",
                reliability="High - Central bank official decision",
                revision_risk="None - Policy decision not revised",
                trading_volatility="Very High - 100-200 pips typical",
                related_indicators=["BoJ Policy Statement", "Yen Exchange Rate"]
            ),
            EconomicEvent(
                id="event_5",
                title="Employment Change",
                date=now + timedelta(hours=6),
                timezone="UTC",
                source="Statistics Canada",
                description="Monthly employment change data",
                impact_level=ImpactLevel.MODERATE,
                event_type=EventType.EMPLOYMENT,
                region=GeographicRegion.NORTH_AMERICA,
                country="Canada",
                currency="CAD",
                forecast="15.0K",
                previous="12.5K",
                market_reaction="Awaiting release",
                volatility_expected="MODERATE",
                historical_significance="Key indicator for BoC policy decisions",
                reliability="High - Government official data",
                revision_risk="Moderate - Subject to monthly revisions",
                trading_volatility="Moderate - 30-60 pips typical",
                related_indicators=["Unemployment Rate", "Labor Force Participation"]
            ),
            EconomicEvent(
                id="event_6",
                title="RBA Rate Statement",
                date=now + timedelta(days=1),
                timezone="UTC",
                source="Reserve Bank of Australia",
                description="Reserve Bank of Australia monetary policy statement",
                impact_level=ImpactLevel.HIGH,
                event_type=EventType.MONETARY_POLICY,
                region=GeographicRegion.ASIA_PACIFIC,
                country="Australia",
                currency="AUD",
                forecast="4.35%",
                previous="4.35%",
                market_reaction="Awaiting release",
                volatility_expected="HIGH",
                historical_significance="Primary driver of AUD monetary policy",
                reliability="High - Central bank official statement",
                revision_risk="None - Policy statement not revised",
                trading_volatility="High - 50-100 pips typical",
                related_indicators=["RBA Cash Rate", "AUD Exchange Rate"]
            )
        ]
    
    def filter_events(self, start_date: datetime, end_date: datetime, 
                     impact_levels: Optional[List[ImpactLevel]] = None,
                     event_types: Optional[List[EventType]] = None,
                     regions: Optional[List[GeographicRegion]] = None,
                     countries: Optional[List[str]] = None,
                     currencies: Optional[List[str]] = None) -> None:
        """Filter events based on criteria"""
        
        self.filtered_events = []
        
        # Ensure consistent timezone handling
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=pytz.UTC)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=pytz.UTC)
        
        for event in self.events:
            # Convert event date to timezone-aware if needed
            event_date = event.date
            if event_date.tzinfo is None:
                event_date = event_date.replace(tzinfo=pytz.UTC)
            
            # Date range filter
            if not (start_date <= event_date <= end_date):
                continue
            
            # Impact level filter
            if impact_levels and event.impact_level not in impact_levels:
                continue
            
            # Event type filter
            if event_types and event.event_type not in event_types:
                continue
            
            # Region filter
            if regions and event.region not in regions:
                continue
            
            # Country filter
            if countries and event.country not in countries:
                continue
            
            # Currency filter
            if currencies and event.currency not in currencies:
                continue
            
            self.filtered_events.append(event)
        
        # Sort by date
        self.filtered_events.sort(key=lambda x: x.date)
        self.current_page = 1
    
    def get_color_for_impact(self, impact: ImpactLevel) -> str:
        """Get color code for impact level"""
        if not self.color_enabled:
            return ""
        
        color_map = {
            ImpactLevel.LOW: TerminalColors.LOW_IMPACT,
            ImpactLevel.MODERATE: TerminalColors.MODERATE_IMPACT,
            ImpactLevel.HIGH: TerminalColors.HIGH_IMPACT,
            ImpactLevel.CRITICAL: TerminalColors.CRITICAL_IMPACT
        }
        return color_map.get(impact, TerminalColors.RESET)
    
    def display_event_detailed(self, event: EconomicEvent) -> None:
        """Display detailed event information"""
        color = self.get_color_for_impact(event.impact_level)
        
        print(f"\n{color}{'='*80}{TerminalColors.RESET}")
        print(f"{color}≡ {event.title}{TerminalColors.RESET}")
        print(f"{color}{'='*80}{TerminalColors.RESET}")
        
        print(f" Time: {event.date.strftime('%Y-%m-%d %H:%M:%S')} {event.timezone}")
        print(f" Country: {event.country}")
        print(f"¤ Currency: {event.currency}")
        print(f"▣ Event Type: {event.event_type.value}")
        print(f"{color} Impact Level: {event.impact_level.value}{TerminalColors.RESET}")
        print(f" Expected Volatility: {event.volatility_expected or 'Unknown'}")
        print(f"▣  Source: {event.source}")
        print(f" Frequency: Monthly")
        
        # Data Analysis Section
        if event.actual or event.forecast or event.previous:
            print(f"\n{color} DATA ANALYSIS:{TerminalColors.RESET}")
            if event.actual:
                print(f"   Actual: {event.actual}")
            if event.forecast:
                print(f"   Forecast: {event.forecast}")
            if event.previous:
                print(f"   Previous: {event.previous}")
            if event.deviation:
                print(f"   Deviation: {event.deviation}")
        
        # Market Impact Section
        if event.market_reaction:
            print(f"\n{color}¤ MARKET IMPACT:{TerminalColors.RESET}")
            print(f"   Market Reaction: {event.market_reaction}")
            if event.trading_volatility:
                print(f"   Trading Volatility: {event.trading_volatility}")
        
        # Event Significance Section
        print(f"\n{color}≡ EVENT SIGNIFICANCE:{TerminalColors.RESET}")
        print(f"   Description: {event.description}")
        if event.historical_significance:
            print(f"   Historical Significance: {event.historical_significance}")
        if event.reliability:
            print(f"   Data Reliability: {event.reliability}")
        if event.revision_risk:
            print(f"   Revision Risk: {event.revision_risk}")
        
        # Related Indicators
        if event.related_indicators:
            print(f"   Related Indicators: {', '.join(event.related_indicators)}")
        
        print(f"{color}{'='*80}{TerminalColors.RESET}")
    
    def display_event_short(self, event: EconomicEvent) -> None:
        """Display short event information"""
        color = self.get_color_for_impact(event.impact_level)
        
        # Create brief summary (50-100 characters)
        summary = event.description[:80] + "..." if len(event.description) > 80 else event.description
        
        # Format date
        date_str = event.date.strftime('%Y-%m-%d %H:%M')
        
        # Create short display line
        status = "✔ Released" if event.actual else " Pending"
        
        print(f"{color}{date_str} | {event.title} ({event.currency}) | {event.impact_level.value} | {status}{TerminalColors.RESET}")
        print(f"   {summary}")
        if event.actual and event.deviation:
            print(f"   Deviation: {event.deviation}")
        print()
    
    def display_events(self, page: int = 1) -> None:
        """Display events for current page"""
        if not self.filtered_events:
            print(f"{TerminalColors.WARNING}No events found matching your criteria.{TerminalColors.RESET}")
            return
        
        total_pages = (len(self.filtered_events) + self.items_per_page - 1) // self.items_per_page
        
        if page < 1 or page > total_pages:
            print(f"{TerminalColors.ERROR}Invalid page number. Available pages: 1-{total_pages}{TerminalColors.RESET}")
            return
        
        start_idx = (page - 1) * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.filtered_events))
        
        page_events = self.filtered_events[start_idx:end_idx]
        
        # Display header
        print(f"\n{TerminalColors.HEADER}▣ Economic Events - Page {page}/{total_pages}{TerminalColors.RESET}")
        print(f"{TerminalColors.INFO}Showing events {start_idx + 1}-{end_idx} of {len(self.filtered_events)}{TerminalColors.RESET}")
        print(f"{TerminalColors.INFO}View Mode: {self.view_mode.title()}{TerminalColors.RESET}")
        print()
        
        # Display events
        for event in page_events:
            if self.view_mode == "detailed":
                self.display_event_detailed(event)
            else:
                self.display_event_short(event)
        
        # Display navigation help
        print(f"\n{TerminalColors.INFO}Navigation: [N]ext page | [P]revious page | [Q]uit | [H]elp{TerminalColors.RESET}")
    
    def export_to_csv(self, filename: str) -> bool:
        """Export events to CSV file"""
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'id', 'title', 'date', 'timezone', 'source', 'description',
                    'impact_level', 'event_type', 'region', 'country', 'currency',
                    'actual', 'forecast', 'previous', 'deviation', 'market_reaction',
                    'volatility_expected', 'historical_significance', 'reliability',
                    'revision_risk', 'trading_volatility', 'related_indicators',
                    'next_release'
                ]
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for event in self.filtered_events:
                    row = {}
                    # Manually construct the row to avoid dict issues
                    row['id'] = event.id
                    row['title'] = event.title
                    row['date'] = event.date.strftime('%Y-%m-%d %H:%M:%S')
                    row['timezone'] = event.timezone
                    row['source'] = event.source
                    row['description'] = event.description
                    row['impact_level'] = event.impact_level.value
                    row['event_type'] = event.event_type.value
                    row['region'] = event.region.value
                    row['country'] = event.country
                    row['currency'] = event.currency
                    row['actual'] = event.actual or ''
                    row['forecast'] = event.forecast or ''
                    row['previous'] = event.previous or ''
                    row['deviation'] = event.deviation or ''
                    row['market_reaction'] = event.market_reaction or ''
                    row['volatility_expected'] = event.volatility_expected or ''
                    row['historical_significance'] = event.historical_significance or ''
                    row['reliability'] = event.reliability or ''
                    row['revision_risk'] = event.revision_risk or ''
                    row['trading_volatility'] = event.trading_volatility or ''
                    row['related_indicators'] = '; '.join(event.related_indicators) if event.related_indicators else ''
                    row['next_release'] = event.next_release.strftime('%Y-%m-%d') if event.next_release else ''
                    
                    writer.writerow(row)
            
            print(f"{TerminalColors.SUCCESS}✔ Successfully exported {len(self.filtered_events)} events to {filename}{TerminalColors.RESET}")
            return True
            
        except Exception as e:
            print(f"{TerminalColors.ERROR} Error exporting to CSV: {e}{TerminalColors.RESET}")
            return False
    
    def export_to_json(self, filename: str) -> bool:
        """Export events to JSON file"""
        try:
            data = {
                "export_info": {
                    "timestamp": datetime.now().isoformat(),
                    "total_events": len(self.filtered_events),
                    "filters_applied": "Various filters applied"
                },
                "events": []
            }
            
            for event in self.filtered_events:
                event_dict = asdict(event)
                # Convert enums to strings
                event_dict['impact_level'] = event.impact_level.value
                event_dict['event_type'] = event.event_type.value
                event_dict['region'] = event.region.value
                # Convert datetime to ISO format
                event_dict['date'] = event.date.isoformat()
                if event.next_release:
                    event_dict['next_release'] = event.next_release.isoformat()
                
                data["events"].append(event_dict)
            
            with open(filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(data, jsonfile, indent=2, ensure_ascii=False)
            
            print(f"{TerminalColors.SUCCESS}✔ Successfully exported {len(self.filtered_events)} events to {filename}{TerminalColors.RESET}")
            return True
            
        except Exception as e:
            print(f"{TerminalColors.ERROR} Error exporting to JSON: {e}{TerminalColors.RESET}")
            return False
    
    def search_events(self, query: str) -> List[EconomicEvent]:
        """Search events by query string"""
        query = query.lower()
        results = []
        
        for event in self.filtered_events:
            # Search in title, description, country, currency
            searchable_text = f"{event.title} {event.description} {event.country} {event.currency}".lower()
            
            if query in searchable_text:
                results.append(event)
        
        return results
    
    def get_llm_api_response(self, format_type: str = "json") -> Dict[str, Any]:
        """Get structured data for LLM consumption"""
        events_data = []
        
        for event in self.filtered_events:
            event_dict = {
                "id": event.id,
                "title": event.title,
                "date": event.date.isoformat(),
                "timezone": event.timezone,
                "source": event.source,
                "description": event.description,
                "impact_level": event.impact_level.value,
                "event_type": event.event_type.value,
                "region": event.region.value,
                "country": event.country,
                "currency": event.currency,
                "actual": event.actual,
                "forecast": event.forecast,
                "previous": event.previous,
                "deviation": event.deviation,
                "market_reaction": event.market_reaction,
                "volatility_expected": event.volatility_expected,
                "historical_significance": event.historical_significance,
                "reliability": event.reliability,
                "revision_risk": event.revision_risk,
                "trading_volatility": event.trading_volatility
            }
            
            if event.next_release:
                event_dict["next_release"] = event.next_release.isoformat()
            
            if event.related_indicators:
                event_dict["related_indicators"] = event.related_indicators
            
            events_data.append(event_dict)
        
        response = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "total_events": len(events_data),
            "date_range": {
                "start": min(event.date.isoformat() for event in self.filtered_events) if self.filtered_events else None,
                "end": max(event.date.isoformat() for event in self.filtered_events) if self.filtered_events else None
            },
            "events": events_data
        }
        
        if format_type == "json":
            return response
        elif format_type == "csv":
            # Convert to CSV format
            import io
            output = io.StringIO()
            if events_data:
                writer = csv.DictWriter(output, fieldnames=events_data[0].keys())
                writer.writeheader()
                writer.writerows(events_data)
            return {"csv_data": output.getvalue()}
        else:
            return response

def main():
    """Main function for terminal interface"""
    parser = argparse.ArgumentParser(
        description="Economic News Terminal - View and analyze economic events",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --start-date 2024-01-01 --end-date 2024-01-31
  %(prog)s --impact-levels HIGH MODERATE --format short
  %(prog)s --countries "United States" "European Union" --export csv
  %(prog)s --search "employment" --detailed
  %(prog)s --llm-api --output json
        """
    )
    
    # Date range parameters
    parser.add_argument("--start-date", "-s", required=True,
                       help="Start date (YYYY-MM-DD format)")
    parser.add_argument("--end-date", "-e", required=True,
                       help="End date (YYYY-MM-DD format)")
    parser.add_argument("--timezone", "-tz", default="UTC",
                       help="Timezone (default: UTC)")
    
    # Filtering parameters
    parser.add_argument("--impact-levels", "-i", nargs="+", 
                       choices=["LOW", "MODERATE", "HIGH", "CRITICAL"],
                       help="Filter by impact levels")
    parser.add_argument("--event-types", "-t", nargs="+",
                       choices=["EMPLOYMENT", "INFLATION", "GDP", "MONETARY_POLICY", 
                               "TRADE", "CONSUMER", "HOUSING", "BUSINESS"],
                       help="Filter by event types")
    parser.add_argument("--regions", "-r", nargs="+",
                       choices=["NORTH_AMERICA", "EUROPE", "ASIA_PACIFIC", "EMERGING_MARKETS", "GLOBAL"],
                       help="Filter by geographic regions")
    parser.add_argument("--countries", "-c", nargs="+",
                       help="Filter by countries")
    parser.add_argument("--currencies", "-cur", nargs="+",
                       help="Filter by currencies")
    
    # View mode parameters
    parser.add_argument("--format", "-f", choices=["detailed", "short"], default="detailed",
                       help="Display format (default: detailed)")
    parser.add_argument("--no-color", action="store_true",
                       help="Disable colored output")
    parser.add_argument("--items-per-page", "-n", type=int, default=10,
                       help="Number of items per page (default: 10)")
    
    # Search and export
    parser.add_argument("--search", "-q",
                       help="Search query string")
    parser.add_argument("--export", choices=["csv", "json"],
                       help="Export format")
    parser.add_argument("--export-file", default="economic_events_export",
                       help="Export filename (without extension)")
    
    # LLM API mode
    parser.add_argument("--llm-api", action="store_true",
                       help="Enable LLM API mode with JSON output")
    parser.add_argument("--output", choices=["json", "csv"], default="json",
                       help="Output format for LLM API (default: json)")
    
    # Help and version
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")
    
    args = parser.parse_args()
    
    try:
        # Initialize terminal
        terminal = EconomicNewsTerminal()
        terminal.view_mode = args.format
        terminal.color_enabled = not args.no_color
        terminal.items_per_page = args.items_per_page
        
        # Load sample data
        print(f"{TerminalColors.INFO}Loading economic events data...{TerminalColors.RESET}")
        terminal.load_sample_data()
        print(f"{TerminalColors.SUCCESS}✔ Loaded {len(terminal.events)} total events{TerminalColors.RESET}")
        
        # Parse dates
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
            
            # Handle timezone
            if args.timezone != "UTC":
                try:
                    tz = pytz.timezone(args.timezone)
                    start_date = tz.localize(start_date)
                    end_date = tz.localize(end_date)
                except pytz.exceptions.UnknownTimeZoneError:
                    print(f"{TerminalColors.WARNING}⚠  Unknown timezone '{args.timezone}', using UTC{TerminalColors.RESET}")
            
        except ValueError as e:
            print(f"{TerminalColors.ERROR} Invalid date format. Use YYYY-MM-DD format.{TerminalColors.RESET}")
            return 1
        
        # Convert filter arguments to enums
        impact_levels = None
        if args.impact_levels:
            impact_levels = [ImpactLevel(level) for level in args.impact_levels]
        
        event_types = None
        if args.event_types:
            event_types = [EventType(event_type) for event_type in args.event_types]
        
        regions = None
        if args.regions:
            regions = [GeographicRegion(region) for region in args.regions]
        
        # Apply filters
        terminal.filter_events(
            start_date=start_date,
            end_date=end_date,
            impact_levels=impact_levels,
            event_types=event_types,
            regions=regions,
            countries=args.countries,
            currencies=args.currencies
        )
        
        print(f"{TerminalColors.INFO}▣ Filtered to {len(terminal.filtered_events)} events{TerminalColors.RESET}")
        
        # Handle search
        if args.search:
            search_results = terminal.search_events(args.search)
            print(f"{TerminalColors.INFO}⌕ Found {len(search_results)} events matching '{args.search}'{TerminalColors.RESET}")
            terminal.filtered_events = search_results
        
        # Handle LLM API mode
        if args.llm_api:
            response = terminal.get_llm_api_response(format_type=args.output)
            if args.output == "json":
                print(json.dumps(response, indent=2, ensure_ascii=False))
            elif args.output == "csv":
                print(response["csv_data"])
            return 0
        
        # Handle export
        if args.export:
            filename = f"{args.export_file}.{args.export}"
            if args.export == "csv":
                success = terminal.export_to_csv(filename)
            else:
                success = terminal.export_to_json(filename)
            
            if success:
                print(f"{TerminalColors.SUCCESS}Export completed successfully!{TerminalColors.RESET}")
            return 0 if success else 1
        
        # Interactive mode
        if len(terminal.filtered_events) == 0:
            print(f"{TerminalColors.WARNING}No events found matching your criteria.{TerminalColors.RESET}")
            return 0
        
        # Display events interactively
        current_page = 1
        while True:
            terminal.display_events(current_page)
            
            try:
                command = input(f"\n{TerminalColors.INFO}Enter command: {TerminalColors.RESET}").strip().lower()
                
                if command in ['q', 'quit', 'exit']:
                    break
                elif command in ['n', 'next']:
                    total_pages = (len(terminal.filtered_events) + terminal.items_per_page - 1) // terminal.items_per_page
                    if current_page < total_pages:
                        current_page += 1
                    else:
                        print(f"{TerminalColors.WARNING}Already at last page{TerminalColors.RESET}")
                elif command in ['p', 'previous']:
                    if current_page > 1:
                        current_page -= 1
                    else:
                        print(f"{TerminalColors.WARNING}Already at first page{TerminalColors.RESET}")
                elif command in ['h', 'help']:
                    print(f"""
{TerminalColors.HEADER}Available Commands:{TerminalColors.RESET}
  [N]ext page     - Go to next page
  [P]revious page - Go to previous page  
  [Q]uit          - Exit application
  [H]elp          - Show this help
  [S]earch        - Search events
  [E]xport        - Export current view
  [F]ilters       - Show current filters
                    """)
                elif command in ['s', 'search']:
                    query = input("Enter search query: ").strip()
                    if query:
                        search_results = terminal.search_events(query)
                        if search_results:
                            terminal.filtered_events = search_results
                            current_page = 1
                            print(f"Found {len(search_results)} matching events")
                        else:
                            print("No matching events found")
                elif command in ['e', 'export']:
                    format_choice = input("Export format (csv/json): ").strip().lower()
                    if format_choice in ['csv', 'json']:
                        filename = input(f"Filename (without extension): ").strip() or "export"
                        filename = f"{filename}.{format_choice}"
                        if format_choice == "csv":
                            terminal.export_to_csv(filename)
                        else:
                            terminal.export_to_json(filename)
                elif command in ['f', 'filters']:
                    print(f"\n{TerminalColors.INFO}Current Filters:{TerminalColors.RESET}")
                    print(f"Date Range: {args.start_date} to {args.end_date}")
                    if args.impact_levels:
                        print(f"Impact Levels: {', '.join(args.impact_levels)}")
                    if args.event_types:
                        print(f"Event Types: {', '.join(args.event_types)}")
                    if args.regions:
                        print(f"Regions: {', '.join(args.regions)}")
                    if args.countries:
                        print(f"Countries: {', '.join(args.countries)}")
                    if args.currencies:
                        print(f"Currencies: {', '.join(args.currencies)}")
                else:
                    print(f"{TerminalColors.WARNING}Unknown command: {command}{TerminalColors.RESET}")
                    
            except KeyboardInterrupt:
                print(f"\n{TerminalColors.INFO}Use 'Q' to quit{TerminalColors.RESET}")
                continue
            except EOFError:
                break
        
        return 0
        
    except Exception as e:
        print(f"{TerminalColors.ERROR} Unexpected error: {e}{TerminalColors.RESET}")
        if args.llm_api:
            error_response = {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "events": []
            }
            print(json.dumps(error_response, indent=2))
        return 1

if __name__ == "__main__":
    sys.exit(main())