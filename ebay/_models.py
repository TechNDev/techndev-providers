#!/usr/bin/env python3
"""
techndev-providers  ebay/_models.py  v1.0.0
=============================================
Datenklassen fuer den eBay-Provider.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - SoldItem: Ein verkauftes eBay-Angebot (Marketplace Insights API).
  - ActiveItem: Ein aktives eBay-Angebot (Browse API).
  - MarketSnapshot: Kombinierter Marktpreis-Snapshot mit Sell-Through-Rate.
    Wird von ebay.get_market_snapshot() zurueckgegeben.
    Jede Seite (sold / active) hat eigenes error-Feld fuer graceful degradation
    (Marketplace Insights kann eingeschraenkt verfuegbar sein).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
from statistics import mean, median

__version__ = "1.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# Einzelne Angebote
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SoldItem:
    """Ein verkauftes eBay-Angebot (aus Marketplace Insights item_sales/search)."""
    title:          str
    price:          float | None
    currency:       str
    sold_date:      str          # ISO-Datum (lastSoldDate)
    condition:      str          # z.B. "New", "Used"
    buying_options: str          # z.B. "FIXED_PRICE"
    item_id:        str
    url:            str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActiveItem:
    """Ein aktives eBay-Angebot (aus Browse API item_summary/search)."""
    title:          str
    price:          float | None
    currency:       str
    condition:      str
    buying_options: str
    item_id:        str
    url:            str

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Kombinierter Markt-Snapshot
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """
    Kombinierter eBay-Markt-Snapshot: verkaufte + aktive Angebote.

    Struktur:
      sold_*   — Daten aus Marketplace Insights API (verkaufte Angebote)
      active_* — Daten aus Browse API (aktive Angebote)
      sell_through_rate — sold_total / (sold_total + active_total), None wenn unbekannt

    sold_error / active_error:
      None   = Abruf erfolgreich
      str    = Fehlermeldung (z.B. Marketplace Insights nicht freigeschaltet)

    Preisstatistiken beziehen sich nur auf Angebote MIT Preis (price != None).
    """
    query:          str
    marketplace_id: str
    fetched_at:     str

    # Verkaufte Angebote (Marketplace Insights)
    sold_total:     int | None          # Gesamt laut API-Antwort
    sold_items:     list[SoldItem]
    sold_median:    float | None
    sold_mean:      float | None
    sold_min:       float | None
    sold_max:       float | None
    sold_count:     int                 # Anzahl mit Preis (fuer Stats)
    sold_error:     str | None

    # Aktive Angebote (Browse)
    active_total:   int | None
    active_items:   list[ActiveItem]
    active_median:  float | None
    active_mean:    float | None
    active_min:     float | None
    active_max:     float | None
    active_count:   int
    active_error:   str | None

    # Sell-Through-Rate
    sell_through_rate: float | None     # sold_total / (sold_total + active_total)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def ok(self) -> bool:
        """True wenn mindestens eine Seite (sold oder active) erfolgreich."""
        return self.sold_error is None or self.active_error is None


# ══════════════════════════════════════════════════════════════════════════════
# Hilfen
# ══════════════════════════════════════════════════════════════════════════════

def _price_stats(prices: list[float]) -> tuple[float | None, float | None, float | None, float | None]:
    """(median, mean, min, max) fuer eine Preis-Liste. Alle None wenn leer."""
    if not prices:
        return None, None, None, None
    return (
        round(median(prices), 2),
        round(mean(prices), 2),
        round(min(prices), 2),
        round(max(prices), 2),
    )


def _calc_str(sold_total: int | None, active_total: int | None) -> float | None:
    """
    Sell-Through-Rate: sold / (sold + active).
    None wenn eine der Groessen unbekannt oder Divisor 0.
    """
    if sold_total is None or active_total is None:
        return None
    denom = sold_total + active_total
    if denom == 0:
        return None
    return round(sold_total / denom, 4)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
