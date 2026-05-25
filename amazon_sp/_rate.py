#!/usr/bin/env python3
"""
amazon_sp  _rate.py  v1.0.0
=============================
Rate-Limiter und Retry-Decorator fuer Amazon SP-API-Aufrufe.
Intern — nicht direkt importieren; oeffentliche Exporte via amazon_sp/__init__.py.

Rate-Limits Amazon SP-API (konservative Werte fuer DE):
  CatalogItems:                   2 req/s, Burst 2  -> 0,6 s Mindestabstand
  ProductsV0 / Fees / Restrict:  ~0,5 req/s         -> 2,2 s Mindestabstand
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
    def __init__(self, min_interval_s: float = 0.5):
        self.min_interval = min_interval_s
        self._last        = 0.0
        self._lock        = threading.Lock()

    def wait(self):
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last = time.monotonic()


def _retry(func):
    """
    Decorator: Exponential Backoff bei HTTP 429 / Throttling.
    3 Versuche; Wartezeiten: 2 s -> 4 s. Alle anderen Fehler sofort weiterwerfen.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if '429' in str(e) or 'throttl' in str(e).lower():
                    if attempt < 2:
                        time.sleep(2 ** attempt * 2)
                        continue
                raise
    return wrapper


# Modul-weite Instanzen — von catalog.py, pricing.py, fees.py, restrictions.py genutzt
catalog_limiter = RateLimiter(min_interval_s=0.6)
pricing_limiter = RateLimiter(min_interval_s=2.2)
