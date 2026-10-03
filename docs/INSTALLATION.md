# MT5 Economic Calendar Wrapper - Installation Guide

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Prerequisites](#prerequisites)
3. [Installation Steps](#installation-steps)
4. [Configuration](#configuration)
5. [Verification](#verification)
6. [Troubleshooting](#troubleshooting)
7. [Performance Optimization](#performance-optimization)
8. [Security Considerations](#security-considerations)

## System Requirements

### Operating System
- **Windows 10/11** (64-bit)
- MetaTrader 5 is Windows-only software
- Python 3.8+ must be installed on Windows

### Hardware Requirements
- **RAM**: Minimum 4GB, Recommended 8GB+
- **Storage**: 2GB free space for MetaTrader 5 + Python environment
- **CPU**: Any modern x64 processor
- **Network**: Stable internet connection for MT5 data feeds

### Software Dependencies
- **MetaTrader 5**: Latest version from metaquotes.net
- **Python**: 3.8 or higher (64-bit version)
- **Git**: For cloning repository (optional)

## Prerequisites

### 1. Install MetaTrader 5

#### Download and Install
1. Visit [MetaTrader 5 Official Website](https://www.metatrader5.com/)
2. Download the Windows version
3. Run the installer and follow setup wizard
4. Complete installation (default path: `C:\Program Files\MetaTrader 5`)

#### Verify Installation
```powershell
# Check if MT5 is installed
if (Test-Path "C:\Program Files\MetaTrader 5\terminal64.dll") {
    Write-Host "✓ MetaTrader 5 found at default location"
} else {
    Write-Host "⚠ MetaTrader 5 not found at default location"
}
```

### 2. Install Python

#### Download Python
1. Visit [Python Official Website](https://www.python.org/downloads/)
2. Download Python 3.8+ (64-bit version)
3. **IMPORTANT**: Check "Add Python to PATH" during installation

#### Verify Python Installation
```powershell
# Check Python version
python --version
# Should return: Python 3.8.x or higher

# Check pip
pip --version
# Should return pip version information
```

## Installation Steps

### Step 1: Create Virtual Environment

```powershell
# Create project directory
mkdir MT5CalendarProject
cd MT5CalendarProject

# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\activate

# Verify activation (should show venv in prompt)
# Prompt should look like: (venv) C:\...\MT5CalendarProject>
```

### Step 2: Install Required Packages

```powershell
# Upgrade pip first
python -m pip install --upgrade pip

# Install required packages
pip install pandas numpy

# Optional: Install development packages
pip install pytest pytest-cov pytest-asyncio
pip install matplotlib seaborn jupyter

# Install additional packages for enhanced functionality
pip install requests beautifulsoup4
```

### Step 3: Download MT5 Calendar Wrapper

#### Option A: Clone from Repository
```powershell
# Clone the repository (if available)
git clone https://github.com/your-repo/mt5-calendar-wrapper.git
cd mt5-calendar-wrapper
```

#### Option B: Manual Download
1. Download the following files to your project directory:
   - `mt5_calendar_wrapper.py`
   - `mt5_calendar_enhanced.py`
   - `tests/test_calendar_wrapper.py`
   - `tests/test_calendar_integration.py`

### Step 4: Install MT5 Python Package (Optional)

```powershell
# Install official MT5 Python package (optional, for comparison)
pip install MetaTrader5
```

## Configuration

### 1. MT5 Terminal Path Configuration

#### Auto-Detection (Recommended)
The wrapper automatically detects MT5 installation paths:
- `C:\Program Files\MetaTrader 5\terminal64.dll`
- `C:\Program Files (x86)\MetaTrader 5\terminal64.dll`

#### Manual Configuration
If MT5 is installed in a custom location:

```python
from mt5_calendar_wrapper import MT5CalendarWrapper

# Specify custom MT5 path
wrapper = MT5CalendarWrapper(
    dll_path="D:\\Custom\\Path\\MetaTrader 5\\terminal64.dll"
)
```

### 2. Environment Variables

Create a `.env` file in your project directory:

```env
# MT5 Configuration
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.dll
MT5_TIMEOUT_SECONDS=30

# Logging Configuration
LOG_LEVEL=INFO
LOG_FILE=mt5_calendar.log

# Performance Settings
MAX_WORKERS=4
CACHE_SIZE=1000
```

### 3. Configuration File

Create `config.ini`:

```ini
[MT5]
terminal_path = C:\Program Files\MetaTrader 5\terminal64.dll
timeout_seconds = 30
max_retries = 3

[Performance]
max_workers = 4
cache_size = 1000
batch_size = 100

[Logging]
level = INFO
file = mt5_calendar.log
format = %%(asctime)s - %%(name)s - %%(levelname)s - %%(message)s
```

## Verification

### 1. Basic Import Test

```python
# Test basic imports
try:
    from mt5_calendar_wrapper import MT5CalendarWrapper
    from mt5_calendar_enhanced import EnhancedMT5CalendarWrapper
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import error: {e}")
```

### 2. MT5 Connection Test

```python
# Test MT5 connection
from mt5_calendar_wrapper import MT5CalendarWrapper

wrapper = MT5CalendarWrapper()
try:
    success = wrapper.initialize()
    if success:
        print("✓ MT5 connection successful")
        
        # Test basic functionality
        countries = wrapper.get_countries(max_count=5)
        print(f"✓ Retrieved {len(countries)} countries")
        
        for country in countries[:3]:
            print(f"  - {country.name} ({country.code})")
            
    else:
        print("✗ MT5 connection failed")
        
except Exception as e:
    print(f"✗ Connection error: {e}")
    
finally:
    wrapper.shutdown()
```

### 3. Run Unit Tests

```powershell
# Run unit tests (no MT5 required)
python -m pytest tests/test_calendar_wrapper.py -v

# Run integration tests (requires MT5)
python -m pytest tests/test_calendar_integration.py -v
```

### 4. Performance Test

```python
# Test performance
from mt5_calendar_enhanced import EnhancedMT5CalendarWrapper

enhanced_wrapper = EnhancedMT5CalendarWrapper()
enhanced_wrapper.initialize()

try:
    benchmark = enhanced_wrapper.benchmark_calendar_operations()
    print("Performance Benchmark Results:")
    print(f"  Countries: {benchmark['countries_avg_ms']:.2f}ms")
    print(f"  Event by ID: {benchmark['event_by_id_avg_ms']:.2f}ms")
    print(f"  Value History: {benchmark['value_history_avg_ms']:.2f}ms")
    
except Exception as e:
    print(f"✗ Benchmark error: {e}")
    
finally:
    enhanced_wrapper.shutdown()
```

## Troubleshooting

### Common Issues

#### 1. MT5 Not Found

**Problem**: `MT5CalendarConnectionError: MetaTrader 5 terminal not found`

**Solutions**:
```python
# Check common installation paths
import os
paths = [
    "C:\\Program Files\\MetaTrader 5\\terminal64.dll",
    "C:\\Program Files (x86)\\MetaTrader 5\\terminal64.dll",
    "D:\\MetaTrader 5\\terminal64.dll"
]

for path in paths:
    if os.path.exists(path):
        print(f"✓ Found MT5 at: {path}")
        wrapper = MT5CalendarWrapper(dll_path=path)
        break
else:
    print("✗ MT5 not found in common locations")
```

#### 2. Permission Denied

**Problem**: `PermissionError: [WinError 5] Access is denied`

**Solutions**:
- Run Python as Administrator
- Check Windows Defender/antivirus settings
- Verify MT5 installation integrity

#### 3. DLL Load Failed

**Problem**: `OSError: [WinError 193] %1 is not a valid Win32 application`

**Solutions**:
- Ensure 64-bit Python installation
- Verify MT5 is 64-bit version
- Check Python and MT5 architecture compatibility

#### 4. No Data Retrieved

**Problem**: Empty results from calendar functions

**Solutions**:
```python
# Check date ranges
date_to = datetime.now(timezone.utc)
date_from = date_to - timedelta(days=30)  # Extend date range

# Check MT5 calendar settings
# Ensure economic calendar is enabled in MT5 terminal

# Try different parameters
values = wrapper.get_value_history(
    date_from=date_from,
    date_to=date_to,
    max_count=1000,  # Increase limit
    country_code=None,  # Remove filters
    currency=None
)
```

### Debug Mode

Enable detailed logging:

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mt5_calendar_debug.log'),
        logging.StreamHandler()
    ]
)

# Test with debug logging
wrapper = MT5CalendarWrapper()
try:
    wrapper.initialize()
    # Operations will now log detailed information
except Exception as e:
    logging.error(f"Error: {e}", exc_info=True)
```

## Performance Optimization

### 1. Memory Management

```python
# Use batch processing for large datasets
def process_large_dataset():
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        # Process in chunks
        date_to = datetime.now(timezone.utc)
        chunk_size = 30  # days
        
        for i in range(12):  # 12 months
            date_from = date_to - timedelta(days=chunk_size)
            
            # Get chunk of data
            chunk = enhanced_wrapper.get_calendar_dataframe(
                date_from=date_from,
                date_to=date_to,
                max_count=1000
            )
            
            # Process chunk
            process_chunk(chunk)
            
            # Move to next chunk
            date_to = date_from
            
    finally:
        enhanced_wrapper.shutdown()
```

### 2. Caching Strategy

```python
# Implement simple caching
from functools import lru_cache
import pickle
import os

class CachedMT5CalendarWrapper(EnhancedMT5CalendarWrapper):
    def __init__(self, cache_dir="cache"):
        super().__init__()
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def get_cached_countries(self, max_count=1000):
        cache_file = os.path.join(self.cache_dir, f"countries_{max_count}.pkl")
        
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        
        # Get fresh data
        countries = self.get_countries(max_count=max_count)
        
        # Cache for future use
        with open(cache_file, 'wb') as f:
            pickle.dump(countries, f)
        
        return countries
```

### 3. Concurrent Processing

```python
# Use ThreadPoolExecutor for concurrent operations
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_multiple_currencies_concurrently():
    enhanced_wrapper = EnhancedMT5CalendarWrapper()
    enhanced_wrapper.initialize()
    
    try:
        currencies = ["USD", "EUR", "GBP", "JPY", "CHF"]
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)
        
        def fetch_currency_data(currency):
            return enhanced_wrapper.get_calendar_dataframe(
                date_from=date_from,
                date_to=date_to,
                currency=currency,
                max_count=100
            )
        
        # Fetch concurrently
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_currency = {
                executor.submit(fetch_currency_data, currency): currency 
                for currency in currencies
            }
            
            results = {}
            for future in as_completed(future_to_currency):
                currency = future_to_currency[future]
                try:
                    df = future.result()
                    results[currency] = df
                    print(f"✓ Retrieved {len(df)} events for {currency}")
                except Exception as e:
                    print(f"✗ Error fetching {currency}: {e}")
        
        return results
        
    finally:
        enhanced_wrapper.shutdown()
```

## Security Considerations

### 1. Path Validation

```python
import os
import pathlib

def validate_mt5_path(dll_path):
    """Validate MT5 DLL path for security"""
    
    # Convert to absolute path
    abs_path = os.path.abspath(dll_path)
    
    # Check if file exists
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"MT5 DLL not found: {abs_path}")
    
    # Verify it's a DLL file
    if not abs_path.endswith('terminal64.dll'):
        raise ValueError("Invalid MT5 DLL filename")
    
    # Check file signature (basic validation)
    try:
        with open(abs_path, 'rb') as f:
            # Check MZ header (DOS executable)
            if f.read(2) != b'MZ':
                raise ValueError("Invalid executable format")
    except Exception as e:
        raise ValueError(f"File validation failed: {e}")
    
    # Additional security checks
    suspicious_paths = [
        'temp', 'tmp', 'appdata', 'downloads'
    ]
    
    path_lower = abs_path.lower()
    for suspicious in suspicious_paths:
        if suspicious in path_lower:
            print(f"⚠ Warning: MT5 path contains suspicious directory: {suspicious}")
    
    return abs_path

# Use validated path
validated_path = validate_mt5_path("C:\\Program Files\\MetaTrader 5\\terminal64.dll")
wrapper = MT5CalendarWrapper(dll_path=validated_path)
```

### 2. Input Validation

```python
def validate_calendar_parameters(date_from, date_to, max_count):
    """Validate calendar function parameters"""
    
    from datetime import datetime, timezone
    
    # Validate date types
    if not isinstance(date_from, datetime):
        raise TypeError("date_from must be a datetime object")
    if not isinstance(date_to, datetime):
        raise TypeError("date_to must be a datetime object")
    
    # Validate timezone awareness
    if date_from.tzinfo is None:
        raise ValueError("date_from must be timezone-aware")
    if date_to.tzinfo is None:
        raise ValueError("date_to must be timezone-aware")
    
    # Validate date range
    if date_from >= date_to:
        raise ValueError("date_from must be before date_to")
    
    # Validate max count
    if not isinstance(max_count, int) or max_count <= 0:
        raise ValueError("max_count must be a positive integer")
    
    if max_count > 10000:
        print(f"⚠ Warning: Large max_count ({max_count}) may impact performance")
    
    # Validate date range limit
    max_days = 365
    if (date_to - date_from).days > max_days:
        print(f"⚠ Warning: Large date range ({(date_to - date_from).days} days)")
    
    return True
```

### 3. Secure Configuration

```python
# config_secure.py
import os
import json
from cryptography.fernet import Fernet

class SecureConfig:
    """Secure configuration management"""
    
    def __init__(self, config_file="secure_config.enc"):
        self.config_file = config_file
        self.key_file = "config.key"
        self.cipher = self._get_or_create_cipher()
    
    def _get_or_create_cipher(self):
        """Get or create encryption cipher"""
        if os.path.exists(self.key_file):
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
        
        return Fernet(key)
    
    def save_config(self, config):
        """Save encrypted configuration"""
        config_json = json.dumps(config)
        encrypted = self.cipher.encrypt(config_json.encode())
        
        with open(self.config_file, 'wb') as f:
            f.write(encrypted)
    
    def load_config(self):
        """Load and decrypt configuration"""
        if not os.path.exists(self.config_file):
            return self._get_default_config()
        
        with open(self.config_file, 'rb') as f:
            encrypted = f.read()
        
        decrypted = self.cipher.decrypt(encrypted)
        return json.loads(decrypted.decode())
    
    def _get_default_config(self):
        """Get default configuration"""
        return {
            "mt5": {
                "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.dll",
                "timeout_seconds": 30,
                "max_retries": 3
            },
            "performance": {
                "max_workers": 4,
                "cache_size": 1000,
                "batch_size": 100
            },
            "security": {
                "validate_paths": True,
                "max_date_range_days": 365,
                "max_count_limit": 10000
            }
        }

# Usage
secure_config = SecureConfig()
config = secure_config.load_config()
wrapper = MT5CalendarWrapper(dll_path=config["mt5"]["terminal_path"])
```

## Final Verification

Run comprehensive installation verification:

```python
# installation_verify.py
import sys
import platform
import subprocess
from datetime import datetime, timezone

def verify_installation():
    """Comprehensive installation verification"""
    
    print("🔍 MT5 Economic Calendar Wrapper - Installation Verification")
    print("=" * 60)
    
    # System information
    print(f"\n📋 System Information:")
    print(f"  OS: {platform.system()} {platform.release()}")
    print(f"  Architecture: {platform.architecture()[0]}")
    print(f"  Python: {platform.python_version()}")
    print(f"  Python Path: {sys.executable}")
    
    # Check MT5 installation
    print(f"\n🔧 MetaTrader 5 Check:")
    mt5_paths = [
        "C:\\Program Files\\MetaTrader 5\\terminal64.dll",
        "C:\\Program Files (x86)\\MetaTrader 5\\terminal64.dll"
    ]
    
    mt5_found = False
    for path in mt5_paths:
        if os.path.exists(path):
            print(f"  ✓ Found at: {path}")
            mt5_found = True
            break
    
    if not mt5_found:
        print(f"  ✗ MetaTrader 5 not found in standard locations")
        return False
    
    # Check Python packages
    print(f"\n📦 Python Packages Check:")
    required_packages = ['pandas', 'numpy', 'pytest']
    optional_packages = ['matplotlib', 'seaborn', 'jupyter']
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  ✗ {package} - Install with: pip install {package}")
            return False
    
    for package in optional_packages:
        try:
            __import__(package)
            print(f"  ✓ {package} (optional)")
        except ImportError:
            print(f"  ⚠ {package} (optional) - Install with: pip install {package}")
    
    # Test wrapper imports
    print(f"\n📄 Wrapper Module Check:")
    try:
        from mt5_calendar_wrapper import MT5CalendarWrapper
        from mt5_calendar_enhanced import EnhancedMT5CalendarWrapper
        print(f"  ✓ All wrapper modules imported successfully")
    except ImportError as e:
        print(f"  ✗ Import error: {e}")
        return False
    
    # Test basic functionality
    print(f"\n🧪 Basic Functionality Test:")
    try:
        wrapper = MT5CalendarWrapper()
        success = wrapper.initialize()
        
        if success:
            print(f"  ✓ MT5 connection established")
            
            # Test countries retrieval
            countries = wrapper.get_countries(max_count=3)
            print(f"  ✓ Retrieved {len(countries)} countries")
            
            # Test calendar data retrieval
            date_to = datetime.now(timezone.utc)
            date_from = date_to - timedelta(days=7)
            
            values = wrapper.get_value_history(
                date_from=date_from,
                date_to=date_to,
                max_count=10
            )
            print(f"  ✓ Retrieved {len(values)} calendar values")
            
            wrapper.shutdown()
            print(f"  ✓ Cleanup successful")
            
        else:
            print(f"  ✗ MT5 connection failed")
            return False
            
    except Exception as e:
        print(f"  ✗ Functionality test failed: {e}")
        return False
    
    # Performance test
    print(f"\n⚡ Performance Test:")
    try:
        enhanced_wrapper = EnhancedMT5CalendarWrapper()
        enhanced_wrapper.initialize()
        
        benchmark = enhanced_wrapper.benchmark_calendar_operations(iterations=3)
        
        print(f"  ✓ Performance benchmark completed:")
        print(f"    Countries: {benchmark['countries_avg_ms']:.1f}ms")
        print(f"    Event by ID: {benchmark['event_by_id_avg_ms']:.1f}ms")
        print(f"    Value History: {benchmark['value_history_avg_ms']:.1f}ms")
        
        enhanced_wrapper.shutdown()
        
    except Exception as e:
        print(f"  ⚠ Performance test failed: {e}")
    
    print(f"\n🎉 Installation verification completed successfully!")
    print(f"📚 Next steps:")
    print(f"  1. Read API_DOCUMENTATION.md for detailed usage")
    print(f"  2. Check USAGE_EXAMPLES.md for practical examples")
    print(f"  3. Run tests: python -m pytest tests/ -v")
    
    return True

if __name__ == "__main__":
    import os
    from datetime import timedelta
    
    success = verify_installation()
    sys.exit(0 if success else 1)
```

Run the verification script:

```powershell
python installation_verify.py
```

This completes the comprehensive installation guide for the MT5 Economic Calendar Wrapper. The installation process ensures proper setup, configuration, and verification of all components needed for successful operation.