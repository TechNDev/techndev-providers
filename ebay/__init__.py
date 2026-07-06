"""
techndev-providers  ebay  v2.0.0
==================================
eBay Datenprovider fuer TechNDev Tools.
Gemeinsame Bibliothek fuer amz-einkauf, mydealz-watcher, reseller-profitability.

Oeffentliche API
----------------
  from ebay import get_sold_listings, get_active_listings   # Marktdaten einzeln
  from ebay import get_market_snapshot                       # Sold + Active kombiniert
  from ebay import get_seller_analytics                      # Traffic-Report (User-Token)
  from ebay import get_seller_standards                      # Performance-Level (User-Token)
  from ebay import SoldResult, ActiveResult, MarketSnapshot  # Haupt-Datenmodelle
  from ebay import SoldItem, ActiveItem                      # Einzel-Angebote
  from ebay import TrafficReport, SellerStandards            # Analytics-Modelle

Import-Pattern (Git-Submodul unter providers/)
----------------------------------------------
  import sys as _sys
  from pathlib import Path as _Path
  _PROV = _Path(__file__).resolve().parent / 'providers'
  if str(_PROV) not in _sys.path:
      _sys.path.insert(0, str(_PROV))

  from ebay import get_sold_listings, SoldResult

Credentials-Formate
-------------------
  # Marktdaten (kein Business-Approval noetig):
  creds = {
      'client_id':     '...',
      'client_secret': '...',
      'env':           'production',   # optional, Default: 'production'
  }

  # Analytics (User-Token, sell.analytics.readonly):
  creds_analytics = {
      'client_id':     '...',
      'client_secret': '...',
      'refresh_token': '...',          # aus OAuth Authorization Code Flow
      'env':           'production',
  }

Datenmodelle
------------
  SoldResult      result = get_sold_listings(ean, creds)
    .ok()         → True wenn kein Fehler
    .best_price   → Median-Preis (Fallback: Mean)
    .median_price → Median aller verkauften Preise
    .mean_price   → Durchschnitt
    .min_price    → Minimum
    .max_price    → Maximum
    .count        → Anzahl Angebote mit Preis
    .total        → Gesamtanzahl laut eBay
    .items        → list[SoldItem]
    .source       → 'scraper' | 'api'
    .error        → None = OK, str = Fehlermeldung

  ActiveResult    result = get_active_listings(ean, creds)
    .ok(), .best_price, .median_price, ... (identisches Interface)

  MarketSnapshot  snap = get_market_snapshot(ean, creds)
    .sold         → SoldResult
    .active       → ActiveResult
    .sell_through_rate → sold.total / (sold.total + active.total)
    .ok()         → True wenn mind. eine Seite erfolgreich

Hinweis Sold-Daten
------------------
  Verkaufte Angebote werden primaer per HTML-Scraper ermittelt.
  Marketplace Insights API (buy.marketplace.insights) ist als Backlog
  in sold._search_sold_api() hinterlegt — Reaktivierung nach Business Approval.
  Application Growth Check gestellt: 2026-05-28.
"""
from ._models   import (
    SoldItem, ActiveItem,
    SoldResult, ActiveResult, MarketSnapshot,
    now_iso, _price_stats, _calc_str,
)
from ._auth     import (
    get_token, get_user_token, make_oauth_url,
    SCOPE_BASIC, SCOPE_SOLD, SCOPE_ANALYTICS,
)
from .sold      import get_sold_listings, search_sold
from .browse    import get_active_listings, search_active, get_item_gtin, get_item
from .scraper   import scrape_sold
from .analytics import (
    get_traffic_report  as get_seller_analytics,
    get_seller_standards,
    TrafficRow, TrafficReport, SellerStandards, StandardsMetric,
    ALL_METRICS, DEFAULT_METRICS,
)

__version__ = "2.1.0"

__all__ = [
    # ── Hauptfunktionen (analog amazon_sp) ───────────────────────────────────
    'get_sold_listings',          # EAN/Query → SoldResult
    'get_active_listings',        # EAN/Query → ActiveResult
    'get_item_gtin',              # itemId → GTIN/EAN (Browse getItem)
    'get_item',                   # itemId → {title,price,gtin,condition,url,...}
    'get_market_snapshot',        # EAN/Query → MarketSnapshot (sold + active)
    # ── Analytics (User-Token erforderlich) ──────────────────────────────────
    'get_seller_analytics',       # Traffic-Report → TrafficReport
    'get_seller_standards',       # Performance-Level → SellerStandards
    # ── Datenmodelle ─────────────────────────────────────────────────────────
    'SoldResult',
    'ActiveResult',
    'MarketSnapshot',
    'SoldItem',
    'ActiveItem',
    'TrafficReport',
    'TrafficRow',
    'SellerStandards',
    'StandardsMetric',
    'ALL_METRICS',
    'DEFAULT_METRICS',
    # ── Auth (fuer direkte Token-Nutzung) ────────────────────────────────────
    'get_token',
    'get_user_token',
    'make_oauth_url',
    'SCOPE_BASIC',
    'SCOPE_SOLD',
    'SCOPE_ANALYTICS',
    # ── Legacy (abwaertskompatibel) ───────────────────────────────────────────
    'search_sold',
    'search_active',
    'scrape_sold',
]


def get_market_snapshot(
    query:             str,
    credentials:       dict,
    marketplace:       str        = 'EBAY_DE',
    limit:             int        = 50,
    new_only:          bool       = True,
    fixed_price_only:  bool       = True,
    min_price_filter:  float | None = None,
) -> MarketSnapshot:
    """
    Kombinierter eBay-Markt-Snapshot: sold + active in einem Aufruf.

    Analog zu amazon_sp: beide Seiten werden unabhaengig abgerufen —
    Fehler einer Seite beeinflusst die andere nicht (graceful degradation).

    query:            EAN/GTIN (z.B. '4010232075488') oder Freitext ('LEGO 75192').
    credentials:      {'client_id': ..., 'client_secret': ..., 'env': 'production'}
    marketplace:      eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:            Max. Ergebnisse pro Seite (1-200, Default: 50).
    new_only:         Nur Zustand Neu.
    fixed_price_only: Nur Sofort-Kaufen.
    min_price_filter: Preisuntergrenze fuer Sold-Items (Ausreisser-Filter).
                      None = kein Filter (Default).

    Rueckgabe: MarketSnapshot
      .sold              → SoldResult  (Scraper)
      .active            → ActiveResult (Browse API)
      .sell_through_rate → float | None
      .ok()              → True wenn mind. eine Seite OK
    """
    ts = now_iso()

    sold   = get_sold_listings(query, credentials, marketplace, limit, new_only, fixed_price_only, min_price_filter)
    active = get_active_listings(query, credentials, marketplace, limit, new_only, fixed_price_only)

    return MarketSnapshot(
        query             = query,
        marketplace_id    = marketplace,
        fetched_at        = ts,
        sold              = sold,
        active            = active,
        sell_through_rate = _calc_str(sold.total, active.total),
    )
