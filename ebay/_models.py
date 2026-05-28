#!/usr/bin/env python3
"""
techndev-providers  ebay/_models.py  v2.0.0
=============================================
Datenklassen fuer den eBay-Provider.

CHANGELOG
---------
v2.0.0  (2026-05-28)
  - SoldResult:   Reiches Ergebnisobjekt fuer get_sold_listings()
                  Analog zu amazon_sp.CatalogResult / OffersResult.
                  Felder: stats (median/mean/min/max/count), items, source, ok().
  - ActiveResult: Reiches Ergebnisobjekt fuer get_active_listings().
  - MarketSnapshot: aktualisiert — verwendet SoldResult + ActiveResult intern.
  - best_price-Property auf SoldResult + ActiveResult (analog OffersResult).

v1.0.0  (2026-05-25)
  - SoldItem, ActiveItem, MarketSnapshot: Basis-Datenmodelle.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
from statistics import mean, median
from typing import Optional

__version__ = "2.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# Einzel-Items (unveraendert)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SoldItem:
    """Ein verkauftes eBay-Angebot."""
    title:          str
    price:          float | None
    currency:       str
    sold_date:      str           # Datum als Text (Scraper: 'DD. Mon YYYY'; API: ISO)
    condition:      str           # z.B. 'New'
    buying_options: str           # z.B. 'FIXED_PRICE'
    item_id:        str
    url:            str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActiveItem:
    """Ein aktives eBay-Angebot (Browse API)."""
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
# Reiche Ergebnisobjekte — analog amazon_sp.CatalogResult / OffersResult
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SoldResult:
    """
    Ergebnis von get_sold_listings().
    Analog zu amazon_sp.OffersResult: alle Felder haben sinnvolle Defaults,
    kein None-Check noetig ausser fuer optionale Preis-Felder.
    error != None signalisiert Fehler; ok() fuer schnelle Pruefung.

    source: 'scraper' | 'api' (Marketplace Insights, sobald freigeschaltet)

    Preisstatistiken beziehen sich auf items MIT price != None.
    """
    # ── Identifikation ───────────────────────────────────────────────────────
    query:        str  = ''
    marketplace:  str  = 'EBAY_DE'
    fetched_at:   str  = ''

    # ── Aggregat-Statistiken ─────────────────────────────────────────────────
    total:        Optional[int]   = None   # Gesamtanzahl laut API/Scraper
    count:        int             = 0      # Items mit Preis (Basis der Stats)
    median_price: Optional[float] = None
    mean_price:   Optional[float] = None
    min_price:    Optional[float] = None
    max_price:    Optional[float] = None

    # ── Rohdata ──────────────────────────────────────────────────────────────
    items:        list[SoldItem]  = field(default_factory=list)
    source:       str             = 'scraper'  # 'scraper' | 'api'

    # ── Status ───────────────────────────────────────────────────────────────
    error:        Optional[str]   = None

    def ok(self) -> bool:
        """True wenn kein Fehler aufgetreten ist."""
        return self.error is None

    @property
    def best_price(self) -> Optional[float]:
        """Median-Preis; Fallback: Mean. Analog OffersResult.best_price."""
        return self.median_price if self.median_price is not None else self.mean_price

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActiveResult:
    """
    Ergebnis von get_active_listings().
    Analog zu SoldResult; basiert auf Browse API (kein Special-Approval noetig).

    Preisstatistiken = Marktpreis-Spiegel der aktuell aktiven Angebote.
    """
    # ── Identifikation ───────────────────────────────────────────────────────
    query:        str  = ''
    marketplace:  str  = 'EBAY_DE'
    fetched_at:   str  = ''

    # ── Aggregat-Statistiken ─────────────────────────────────────────────────
    total:        Optional[int]   = None
    count:        int             = 0
    median_price: Optional[float] = None
    mean_price:   Optional[float] = None
    min_price:    Optional[float] = None
    max_price:    Optional[float] = None

    # ── Rohdata ──────────────────────────────────────────────────────────────
    items:        list[ActiveItem] = field(default_factory=list)

    # ── Status ───────────────────────────────────────────────────────────────
    error:        Optional[str]    = None

    def ok(self) -> bool:
        return self.error is None

    @property
    def best_price(self) -> Optional[float]:
        """Median-Preis; Fallback: Mean."""
        return self.median_price if self.median_price is not None else self.mean_price

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Kombinierter Markt-Snapshot
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """
    Kombinierter eBay-Markt-Snapshot: verkaufte + aktive Angebote.
    Wird von get_market_snapshot() zurueckgegeben.

    Enthaelt SoldResult + ActiveResult als Unter-Objekte sowie
    sell_through_rate als abgeleitete Kenngroe+e.
    """
    query:          str
    marketplace_id: str
    fetched_at:     str

    sold:           SoldResult    = field(default_factory=SoldResult)
    active:         ActiveResult  = field(default_factory=ActiveResult)

    # Abgeleitete Kenngroe+en
    sell_through_rate: Optional[float] = None  # sold.total / (sold.total + active.total)

    # Rueckwaertskompatibilitaet — direkte Zugriffe wie bisher
    @property
    def sold_items(self)   -> list[SoldItem]:   return self.sold.items
    @property
    def sold_total(self)   -> Optional[int]:    return self.sold.total
    @property
    def sold_median(self)  -> Optional[float]:  return self.sold.median_price
    @property
    def sold_mean(self)    -> Optional[float]:  return self.sold.mean_price
    @property
    def sold_min(self)     -> Optional[float]:  return self.sold.min_price
    @property
    def sold_max(self)     -> Optional[float]:  return self.sold.max_price
    @property
    def sold_count(self)   -> int:              return self.sold.count
    @property
    def sold_error(self)   -> Optional[str]:    return self.sold.error
    @property
    def active_items(self) -> list[ActiveItem]: return self.active.items
    @property
    def active_total(self) -> Optional[int]:    return self.active.total
    @property
    def active_median(self)-> Optional[float]:  return self.active.median_price
    @property
    def active_mean(self)  -> Optional[float]:  return self.active.mean_price
    @property
    def active_min(self)   -> Optional[float]:  return self.active.min_price
    @property
    def active_max(self)   -> Optional[float]:  return self.active.max_price
    @property
    def active_count(self) -> int:              return self.active.count
    @property
    def active_error(self) -> Optional[str]:    return self.active.error

    def ok(self) -> bool:
        """True wenn mindestens eine Seite erfolgreich."""
        return self.sold.ok() or self.active.ok()

    def to_dict(self) -> dict:
        return {
            'query':             self.query,
            'marketplace_id':    self.marketplace_id,
            'fetched_at':        self.fetched_at,
            'sold':              self.sold.to_dict(),
            'active':            self.active.to_dict(),
            'sell_through_rate': self.sell_through_rate,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Interne Hilfen
# ══════════════════════════════════════════════════════════════════════════════

def _price_stats(
    prices: list[float],
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """(median, mean, min, max) fuer eine Preis-Liste. Alle None wenn leer."""
    if not prices:
        return None, None, None, None
    return (
        round(median(prices), 2),
        round(mean(prices), 2),
        round(min(prices), 2),
        round(max(prices), 2),
    )


def _calc_str(sold_total: Optional[int], active_total: Optional[int]) -> Optional[float]:
    """Sell-Through-Rate: sold / (sold + active). None wenn unbekannt oder Divisor 0."""
    if sold_total is None or active_total is None:
        return None
    denom = sold_total + active_total
    return round(sold_total / denom, 4) if denom else None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
