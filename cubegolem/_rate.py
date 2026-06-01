#!/usr/bin/env python3
"""
techndev-providers  cubegolem/_rate.py  v1.0.0
================================================
Rate-Limiter und Retry-Decorator fuer cubegolem.de-Requests.
Intern — nicht direkt importieren; oeffentliche Exporte via cubegolem/__init__.py.

Hoeflichkeit:
  Eine vollstaendige Katalog-Erfassung kostet ~2.200 Requests. Ein
  Mindestabstand schuetzt den Shop vor Last und uns vor WAF/Rate-Limits.
  Default 0,8 s ⇒ Vollkatalog grob ~30 Min.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - RateLimiter (thread-sicher) + _retry (Exponential Backoff bei 429/5xx).
  - Modul-weite Instanz: http_limiter.
"""
import functools
import threading
import time

__version__ = "1.0.0"


class RateLimiter:
    """
    Thread-sicherer Mindestabstand zwischen aufeinanderfolgenden Requests.
    Erster Aufruf ist sofort; Folgeaufrufe warten nur wenn noetig.
    """
    def __init__(self, min_interval_s: float = 0.8):
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
    Decorator: Exponential Backoff bei HTTP 429 / 5xx / transienten Netzfehlern.
    3 Versuche; Wartezeiten: 3 s -> 6 s. Alle anderen Fehler sofort weiterwerfen.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                s = str(e)
                msg = s.lower()
                transient = (
                    '429' in s or '500' in s or '502' in s or '503' in s
                    or '504' in s or 'rate' in msg or 'throttl' in msg
                    or 'timed out' in msg or 'timeout' in msg
                    or 'connection' in msg or 'reset' in msg
                )
                if transient and attempt < 2:
                    time.sleep(3 * (2 ** attempt))
                    continue
                raise
    return wrapper


# Modul-weite Instanz — von scraper.py genutzt
http_limiter = RateLimiter(min_interval_s=0.8)
