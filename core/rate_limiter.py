#!/usr/bin/env python3
"""
Rate Limiter - Token Bucket Algorithm
Manages API rate limits to prevent 429 errors
"""

import time
import threading
from typing import Dict


class RateLimiter:
    """Token bucket rate limiter for API calls"""
    
    def __init__(self, max_requests: int, time_window: int):
        """
        Initialize rate limiter
        
        Args:
            max_requests: Maximum number of requests allowed
            time_window: Time window in seconds
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.tokens = max_requests
        self.last_refill = time.time()
        self.lock = threading.Lock()
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Calculate tokens to add based on time passed
        tokens_to_add = (elapsed / self.time_window) * self.max_requests
        
        if tokens_to_add >= 1:
            self.tokens = min(self.max_requests, self.tokens + tokens_to_add)
            self.last_refill = now
    
    def acquire(self) -> bool:
        """
        Try to acquire a token
        
        Returns:
            True if token acquired, False if rate limited
        """
        with self.lock:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False
    
    def wait_if_needed(self):
        """Block until a token is available"""
        while not self.acquire():
            time.sleep(0.1)
    
    def get_wait_time(self) -> float:
        """
        Get estimated wait time in seconds
        
        Returns:
            Seconds until next token available
        """
        with self.lock:
            self._refill()
            if self.tokens >= 1:
                return 0.0
            
            # Calculate time needed to get 1 token
            time_per_token = self.time_window / self.max_requests
            tokens_needed = 1 - self.tokens
            return time_per_token * tokens_needed


class MultiAPIRateLimiter:
    """Manages rate limiters for multiple APIs"""
    
    def __init__(self):
        self.limiters: Dict[str, RateLimiter] = {}
    
    def add_limiter(self, api_name: str, max_requests: int, time_window: int):
        """Add a rate limiter for a specific API"""
        self.limiters[api_name] = RateLimiter(max_requests, time_window)
    
    def acquire(self, api_name: str) -> bool:
        """Try to acquire a token for specific API"""
        if api_name not in self.limiters:
            return True  # No limiter = always allowed
        return self.limiters[api_name].acquire()
    
    def wait_if_needed(self, api_name: str):
        """Wait if needed for specific API"""
        if api_name in self.limiters:
            self.limiters[api_name].wait_if_needed()
