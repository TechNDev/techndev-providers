"""
techndev-providers  brickmerge  v1.0.0
========================================
Brickmerge.de Datenprovider fuer LEGO-Set-Daten.

Exports:
  SetInfo            — LEGO-Set-Stammdaten (set_no, ean, uvp, status, ...)
  MarketPrices       — Marktpreis-Snapshot (Preise, Bestpreis, seller_count)
  SetCatalog         — SQLite-Cache + CSV-Downloads (aktiv + EOL)
  BrickmergeProvider — Live-Scraper (Preise + Haendleranzahl)
  get_catalog()      — Modulweiter Singleton-Katalog
  now_iso()          — ISO-Timestamp-Helfer
"""
from ._models  import SetInfo, MarketPrices, now_iso
from .catalog  import SetCatalog, get_catalog
from .scraper  import BrickmergeProvider

__all__ = [
    "SetInfo",
    "MarketPrices",
    "now_iso",
    "SetCatalog",
    "get_catalog",
    "BrickmergeProvider",
]
__version__ = "1.0.0"
