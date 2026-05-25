#!/usr/bin/env python3
"""
techndev-providers  brickmerge/_models.py  v1.0.0
===================================================
Gemeinsame Datenklassen fuer den brickmerge-Provider.
Vorher: SetInfo in mydealz-watcher/setcatalog.py,
        MarketPrices + now_iso() in mydealz-watcher/pricesource.py.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - SetInfo: wie setcatalog.SetInfo + status ('active'|'eol') + eol_year.
    Defaults: status='active', eol_year=None → 100 % rueckwaertskompatibel.
  - MarketPrices: wie pricesource.MarketPrices + seller_count (None=unbekannt).
    Bestehende Aufrufer, die seller_count nicht setzen, bekommen None.
  - now_iso(): einheitlicher ISO-Timestamp fuer fetched_at.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime

__version__ = "1.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# Set-Stammdaten
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SetInfo:
    """
    Alle katalogisierten Felder zu einem LEGO-Set.

    status: 'active' = im aktuellen Brickmerge-Katalog,
            'eol'    = aus einem EOL-Jahrgang-CSV.
    eol_year: None fuer aktive Sets; Jahreszahl fuer EOL-Sets
              (z.B. 2023 = EOL-Liste 2023).
    """
    set_no:         str
    name:           str
    theme:          str
    uvp:            float | None
    year:           int   | None
    ean:            str   | None
    asin:           str   | None
    brickmerge_url: str   | None
    status:         str         = "active"   # 'active' | 'eol'
    eol_year:       int | None  = None

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Marktpreis-Snapshot
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketPrices:
    """
    Marktpreis-Snapshot fuer ein LEGO-Set aus einer Preisquelle.

    Alle Preise in EUR inkl. MwSt.
    None = Feld beim Provider nicht verfuegbar oder nicht parsebar.

    seller_count: Anzahl aktiver Haendler bei brickmerge.de zum Abfragezeitpunkt.
                  None wenn nicht gescrapt oder nicht verfuegbar.
    """
    set_no:                       str
    name:                         str   | None
    ean:                          str   | None
    uvp_original:                 float | None
    uvp_current:                  float | None
    best_price_alltime:           float | None
    best_price_alltime_days_ago:  int   | None
    best_price_180d:              float | None
    best_price_current:           float | None
    source:                       str
    url:                          str
    fetched_at:                   str
    seller_count:                 int   | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Hilfen
# ══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    """Einheitlicher ISO-Timestamp fuer fetched_at-Felder."""
    return datetime.now().isoformat(timespec="seconds")
