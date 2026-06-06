"""
capybara_fetcher

Data collection + feature cache generation for Korea stock universe.

Design goals:
- Single DataProvider interface (swap pykrx/broker API easily)
- Fail-fast (no fallback)
- Minimal exception catching (catch only at CLI boundary)
- Readability first
"""

