#!/usr/bin/env python3
"""
techndev-providers  ebay/_rate.py  v1.0.0
==========================================
Rate-Limiter und Retry-Decorator fuer eBay API-Aufrufe.
Intern — nicht direkt importieren; oeffentliche Exporte via ebay/__init__.py.

Rate-Limits eBay (konservative Werte):
  Browse API:       5.000 calls/Tag, kein Burst-Limit publiziert  -> 1,0 s Mindestabstand
  Scraper:          Hoeflichkeits-Delay gegen Akamai-WAF           -> 2,0 s Mindestabstand
  Analytics API:    keine Limits publiziert                         -> 0,5 s Mindestabstand

CHANGELOG
---------
v1.0.0  (2026-05-28)
  - RateLimiter: thread-sicher, erster Aufruf sofort
  - _retry: Exponential Backoff bei HTTP 429 / Rate-Limit-Fehlern
  - browse_limiter, scraper_limiter, analytics_limiter: Modul-weite Instanzen
"""
import functools
import threading
import time

__version__ = "1.0.0"


class RateLimiter:
    """
    Thread-sicherer Mindestabstand zwischen aufeinanderfolgenden API-Aufrufen.
    Erster Aufruf ist sofort; Folgeaufrufe warten nur wenn noetig.
    """
    def __init__(self, min_interval_s: float = 1.0):
        self.min_interval = min_interval_s
        self._last        = 0.0
        self._lock        = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last = time.monotonic()


def _retry(func):
    """
    Decorator: Exponential Backoff bei HTTP 429 / Rate-Limiting.
    3 Versuche; Wartezeiten: 5 s -> 10 s. Alle anderen Fehler sofort weiterwerfen.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                msg = str(e).lower()
                if '429' in str(e) or 'rate' in msg or 'throttl' in msg:
                    if attempt < 2:
                        time.sleep(2 ** attempt * 5)
                        continue
                raise
    return wrapper


# Modul-weite Instanzen — von sold.py, browse.py, scraper.py, analytics.py genutzt
browse_limiter    = RateLimiter(min_interval_s=1.0)   # Browse API (aktive Listings)
scraper_limiter   = RateLimiter(min_interval_s=2.0)   # HTML-Scraper (sold)
analytics_limiter = RateLimiter(min_interval_s=0.5)   # Sell Analytics API
catalog_limiter   = RateLimiter(min_interval_s=0.5)   # Commerce Catalog API
taxonomy_limiter  = RateLimiter(min_interval_s=0.5)   # Taxonomy API (Kategorie/Aspects)
inventory_limiter = RateLimiter(min_interval_s=0.3)   # Sell Inventory API (Schreib-Pfad)
