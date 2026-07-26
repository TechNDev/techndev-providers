"""
techndev-providers  brickmerge  v1.3.0
========================================
Brickmerge.de Datenprovider fuer LEGO-Set-Daten.

Exports:
  SetInfo            — LEGO-Set-Stammdaten (set_no, ean, uvp, status, ...)
  MarketPrices       — Marktpreis-Snapshot (alle Preis- und Stammdatenfelder)
  SetCatalog         — SQLite-Cache + CSV-Downloads (aktiv + EOL)
  BrickmergeProvider — Live-Scraper (immer frisch, kein Cache)
  BrickmergeCache    — Zwei-Tier-Cache: get() (Cache-first) / get_live() (immer fresh)
  get_catalog()      — Modulweiter Singleton-Katalog
  now_iso()          — ISO-Timestamp-Helfer
"""
from ._models  import SetInfo, MarketPrices, now_iso
from .catalog  import SetCatalog, get_catalog
from .scraper  import BrickmergeProvider
from .cache    import BrickmergeCache

__all__ = [
    "SetInfo",
    "MarketPrices",
    "now_iso",
    "SetCatalog",
    "get_catalog",
    "BrickmergeProvider",
    "BrickmergeCache",
]
__version__ = "1.3.0"
