#!/usr/bin/env python3
"""
techndev-providers  ebay/_models.py  v2.0.0
=============================================
Datenklassen fuer den eBay-Provider.

CHANGELOG
---------
v2.2.0  (2026-07-25)
  - CatalogProduct:    Ergebnis der Commerce-Catalog-Suche (epid, title, aspects,
                       gtins, Bilder, brand). source='catalog'|'browse' (Fallback).
  - AspectRequirement: Pflicht/Empfohlen-Item-Specific aus Taxonomy API
                       (name, required, cardinality, mode, erlaubte Werte).
  - EbayOfferDraft:    fertiges, validierbares Angebot — gemeinsames Eingabemodell
                       fuer beide Ausgabe-Adapter (JTL-CSV + eBay Inventory API).
  - Basis fuer den eBay-Listing-Erzeugungs-Workflow (ebay-poster).

v2.1.0  (2026-07-06)
  - _robust_trim(): iteratives Ausreisser-Trimmen (Median-Band [0.5x, 2x]).
    Kernbaustein gegen instabile/verunreinigte median_sold-Werte — entfernt
    Zubehoer/Anleitungen/Bundles/Fehl-Matches, die den Median verzerren.
    Deterministisch (nur von der Preisliste abhaengig, nicht von der Reihenfolge).

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

__version__ = "2.2.0"


# ══════════════════════════════════════════════════════════════════════════════
# Katalog + Taxonomie — Bausteine fuer die Listing-Erzeugung
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CatalogProduct:
    """
    Produkt aus dem eBay-Katalog (Commerce Catalog API) — die "Struktur", von der
    ein neues Angebot kopiert wird: Titel, Marke, Item-Specifics (aspects), Bilder.

    aspects: {Name: [Wert, ...]} — von eBay normalisierte Produkt-Attribute.
    source:  'catalog' (Commerce Catalog API) | 'browse' (Fallback ueber ein
             bestehendes Live-Angebot via Browse getItem, wenn Catalog gesperrt ist).
    category_id: eBay-Kategorie, falls schon bekannt (Browse-Fallback liefert sie mit;
             ueber Catalog i.d.R. leer → per Taxonomy get_category_suggestions ermitteln).
    """
    query:             str  = ''
    marketplace:       str  = 'EBAY_DE'
    fetched_at:        str  = ''

    epid:              Optional[str]     = None
    title:             str               = ''
    brand:             Optional[str]     = None
    gtins:             list[str]         = field(default_factory=list)
    image_url:         Optional[str]     = None
    additional_images: list[str]         = field(default_factory=list)
    aspects:           dict              = field(default_factory=dict)  # {name: [values]}
    description:       Optional[str]     = None
    category_id:       Optional[str]     = None

    source:            str               = 'catalog'
    error:             Optional[str]     = None

    def ok(self) -> bool:
        return self.error is None and bool(self.title)

    def all_images(self) -> list[str]:
        """Hauptbild + Zusatzbilder, dedupliziert, Reihenfolge erhalten."""
        seen: set[str] = set()
        out:  list[str] = []
        for u in [self.image_url, *self.additional_images]:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AspectRequirement:
    """
    Ein Item-Specific laut Taxonomy API (getItemAspectsForCategory) fuer eine
    Leaf-Kategorie. Steuert, welche Felder ein Angebot in dieser Kategorie braucht.

    required:    True = Pflichtfeld (Angebot wird sonst von eBay abgelehnt).
    cardinality: 'SINGLE' | 'MULTI' (ein bzw. mehrere Werte erlaubt).
    mode:        'FREE_TEXT' | 'SELECTION_ONLY' (nur Werte aus `values` erlaubt).
    values:      erlaubte/empfohlene Werte (leer bei reinem Freitext).
    """
    name:        str
    required:    bool      = False
    cardinality: str       = 'SINGLE'
    mode:        str       = 'FREE_TEXT'
    values:      list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EbayOfferDraft:
    """
    Ein fertig zusammengesetztes eBay-Angebot vor der Veroeffentlichung.
    Gemeinsames Eingabemodell fuer beide Ausgabe-Adapter:
      - JTL-Ameise-CSV (export_jtl)      → eazyAuction stellt ein
      - eBay Inventory API (publish_ebay) → createInventoryItem/createOffer/publishOffer

    condition: eBay conditionEnum (z.B. 'NEW', 'USED_EXCELLENT').
    aspects:   {Name: [Wert, ...]} — befuellte Item-Specifics.
    required_missing: Pflicht-Aspects (laut Taxonomy), die NICHT befuellt werden konnten
                      → Angebot ist nicht veroeffentlichungsreif (ready() == False).
    """
    sku:          str            = ''
    ean:          Optional[str]  = None
    title:        str            = ''
    subtitle:     Optional[str]  = None
    brand:        Optional[str]  = None
    mpn:          Optional[str]  = None
    epid:         Optional[str]  = None

    category_id:  Optional[str]  = None
    marketplace:  str            = 'EBAY_DE'
    condition:    str            = 'NEW'

    price:        Optional[float] = None
    currency:     str            = 'EUR'
    quantity:     int            = 1

    description:  str            = ''             # HTML erlaubt
    images:       list[str]      = field(default_factory=list)
    aspects:      dict           = field(default_factory=dict)   # {name: [values]}

    # Versandpaket (Inventory API packageWeightAndSize; CSV Gewicht/Masse)
    weight_kg:    Optional[float] = None
    length_cm:    Optional[float] = None
    width_cm:     Optional[float] = None
    height_cm:    Optional[float] = None

    # Diagnose
    required_missing: list[str]  = field(default_factory=list)
    warnings:         list[str]  = field(default_factory=list)
    source_note:      str        = ''            # Herkunft (catalog/browse + Anreicherung)

    def ready(self) -> bool:
        """True wenn das Angebot ohne manuelle Nacharbeit veroeffentlicht werden kann."""
        return (
            not self.required_missing
            and bool(self.title)
            and self.price is not None
            and bool(self.category_id)
            and bool(self.images)
        )

    def to_dict(self) -> dict:
        return asdict(self)


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

    # ── Ausreisser-Filter ────────────────────────────────────────────────────
    filtered_count: int = 0        # Anzahl gefilterter Items (price < min_price_filter)

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


def _robust_trim(
    prices: list[float],
    lo_factor: float = 0.5,
    hi_factor: float = 2.0,
    max_iter:  int   = 5,
    min_keep:  int   = 3,
) -> list[float]:
    """
    Entfernt Preis-Ausreisser iterativ um den Median herum.

    Motivation: eBay-Sold-Treffer sind (auch nach Relevanz-Filter) mit Zubehoer,
    Anleitungen, Ersatzteilen, Bundles und Fehl-Matches durchsetzt. Deren Preise
    spannen 0.01 .. Vielfaches des echten Set-Preises. Der rohe Median darueber ist
    (a) falsch und (b) instabil, weil eBay je Abruf eine leicht andere Teilmenge
    liefert. Das Trimmen auf ein Median-Band macht die Aggregation robust UND
    reproduzierbar (Ergebnis haengt nur von der Preismenge ab, nicht von Reihenfolge
    oder davon, welche Randfaelle gerade zurueckkamen).

    Vorgehen: Median m berechnen, nur Preise in [lo_factor*m, hi_factor*m] behalten,
    wiederholen bis stabil oder max_iter. Bei < min_keep Preisen wird nicht getrimmt
    (zu wenig Datenbasis fuer eine belastbare Bandbreite).

    Gibt die getrimmte, aufsteigend sortierte Liste zurueck (kann == Eingabe sein).
    """
    ps = sorted(p for p in prices if p is not None)
    if len(ps) < min_keep:
        return ps
    for _ in range(max_iter):
        m = median(ps)
        if m <= 0:
            break
        kept = [p for p in ps if lo_factor * m <= p <= hi_factor * m]
        if len(kept) < min_keep or len(kept) == len(ps):
            # Nicht weiter trimmen: entweder stabil oder zu wenig uebrig.
            if len(kept) >= min_keep:
                ps = kept
            break
        ps = kept
    return ps


def _calc_str(sold_total: Optional[int], active_total: Optional[int]) -> Optional[float]:
    """Sell-Through-Rate: sold / (sold + active). None wenn unbekannt oder Divisor 0."""
    if sold_total is None or active_total is None:
        return None
    denom = sold_total + active_total
    return round(sold_total / denom, 4) if denom else None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
