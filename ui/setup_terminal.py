#!/usr/bin/env python3
"""
Setup script for Economic News Terminal
Installs required dependencies and validates the environment
"""

import subprocess
import sys
import os
from pathlib import Path

def install_package(package):
    """Install a package using pip"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"✔ Successfully installed {package}")
        return True
    except subprocess.CalledProcessError as e:
        print(f" Failed to install {package}: {e}")
        return False

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 7):
        print(" Python 3.7 or higher is required")
        print(f"Current version: {sys.version}")
        return False
    print(f"✔ Python version {sys.version_info.major}.{sys.version_info.minor} is compatible")
    return True

def install_requirements():
    """Install all required packages"""
    requirements = [
        "pandas>=1.3.0",
        "pytz>=2021.1", 
        "colorama>=0.4.4",
        "requests>=2.25.0"
    ]
    
    print("□ Installing required packages...")
    
    success_count = 0
    for requirement in requirements:
        if install_package(requirement):
            success_count += 1
    
    if success_count == len(requirements):
        print(f"✔ All {len(requirements)} packages installed successfully!")
        return True
    else:
        print(f" Only {success_count}/{len(requirements)} packages installed successfully")
        return False

def validate_terminal_colors():
    """Test if terminal supports colors"""
    try:
        from colorama import init, Fore, Style
        init(autoreset=True)
        
        print(f"{Fore.RED}● Red color test{Style.RESET_ALL}")
        print(f"{Fore.GREEN}● Green color test{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}● Yellow color test{Style.RESET_ALL}")
        print(f"{Fore.BLUE}● Blue color test{Style.RESET_ALL}")
        
        print("✔ Terminal color support validated")
        return True
    except Exception as e:
        print(f"⚠  Terminal color test failed: {e}")
        print("The application will still work but colors may not display correctly")
        return False

def test_imports():
    """Test if all required modules can be imported"""
    print("⌕ Testing imports...")
    
    try:
        import pandas as pd
        print("✔ pandas import successful")
    except ImportError as e:
        print(f" pandas import failed: {e}")
        return False
    
    try:
        import pytz
        print("✔ pytz import successful")
    except ImportError as e:
        print(f" pytz import failed: {e}")
        return False
    
    try:
        from colorama import init, Fore, Style
        print("✔ colorama import successful")
    except ImportError as e:
        print(f" colorama import failed: {e}")
        return False
    
    try:
        import requests
        print("✔ requests import successful")
    except ImportError as e:
        print(f" requests import failed: {e}")
        return False
    
    return True

def create_sample_data():
    """Create sample data file if it doesn't exist"""
    print("▣ Setting up sample data...")
    
    # Check if fetch_news_events.py exists
    if not os.path.exists("fetch_news_events.py"):
        print("⚠  fetch_news_events.py not found. The terminal will use built-in sample data.")
        return True
    
    try:
        # Test if we can import the sample data function
        from fetch_news_events import create_sample_calendar_data
        
        # Create a small test dataset
        sample_df = create_sample_calendar_data()
        print(f"✔ Sample data loaded successfully ({len(sample_df)} events)")
        return True
        
    except Exception as e:
        print(f"⚠  Sample data test failed: {e}")
        print("The terminal will use built-in sample data.")
        return True

def main():
    """Main setup function"""
    print(" Economic News Terminal Setup")
    print("=" * 50)
    
    # Check Python version
    if not check_python_version():
        return 1
    
    # Install requirements
    if not install_requirements():
        print("\n Setup failed during package installation")
        print("Please install packages manually using:")
        print("pip install -r requirements_terminal.txt")
        return 1
    
    # Test imports
    if not test_imports():
        print("\n Setup failed during import testing")
        return 1
    
    # Validate terminal colors
    validate_terminal_colors()
    
    # Setup sample data
    create_sample_data()
    
    print("\n" + "=" * 50)
    print("✔ Setup completed successfully!")
    print("\nYou can now run the Economic News Terminal:")
    print("python economic_news_terminal.py --help")
    print("\nExample usage:")
    print("python economic_news_terminal.py --start-date 2024-01-01 --end-date 2024-01-31")
    print("python economic_news_terminal.py --impact-levels HIGH --format short")
    print("python economic_news_terminal.py --llm-api --output json")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())