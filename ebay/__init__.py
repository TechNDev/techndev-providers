"""
techndev-providers  ebay  v1.1.0
==================================
eBay Datenprovider: Marktpreise (aktive Angebote) + Sell-Through (verkaufte Angebote).

Auth:   OAuth 2.0 Application Token (Client Credentials) — kein User-Token noetig.
APIs:   Browse API (aktive Listings) + Marketplace Insights API (verkaufte Listings).
        Fallback: HTML-Scraper fuer abgeschlossene Listings wenn Insights gesperrt.

Exports:
  SoldItem, ActiveItem, MarketSnapshot  — Datenklassen
  get_market_snapshot()                 — Kombinierter Haupt-Einstiegspunkt
  search_sold()                         — Nur verkaufte Angebote (API + Scraper-Fallback)
  search_active()                       — Nur aktive Angebote (Browse API)
  scrape_sold()                         — Nur Scraper (direkt, ohne API-Versuch)
  get_token()                           — OAuth-Token direkt
  SCOPE_BASIC, SCOPE_SOLD               — Scope-Konstanten

Credentials-Format:
  {
    'client_id':     '...',   # eBay App-ID / Client-ID
    'client_secret': '...',   # eBay Cert-ID / Client-Secret
    'env':           'production',  # oder 'sandbox'  (optional, Default: production)
  }

Hinweis Marketplace Insights:
  Fuer verkaufte Angebote ist der Scope buy.marketplace.insights erforderlich,
  der eine separate eBay-Business-Genehmigung erfordert.
  Bei HTTP 400/403 greift automatisch der HTML-Scraper als Fallback (scrape_fallback=True).
  Scraper: https://www.ebay.<tld>/sch/i.html?LH_Sold=1&LH_Complete=1
"""
from ._models  import SoldItem, ActiveItem, MarketSnapshot, now_iso, _price_stats, _calc_str
from ._auth    import get_token, SCOPE_BASIC, SCOPE_SOLD
from .sold     import search_sold
from .browse   import search_active
from .scraper  import scrape_sold

__all__ = [
    "SoldItem",
    "ActiveItem",
    "MarketSnapshot",
    "get_market_snapshot",
    "search_sold",
    "search_active",
    "scrape_sold",
    "get_token",
    "SCOPE_BASIC",
    "SCOPE_SOLD",
]
__version__ = "1.1.0"


def get_market_snapshot(
    query:            str,
    credentials:      dict,
    marketplace:      str  = "EBAY_DE",
    limit:            int  = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
    scrape_fallback:  bool = True,
) -> MarketSnapshot:
    """
    Kombinierter eBay-Markt-Snapshot: ruft sold + active nacheinander ab.
    Jede Seite wird unabhaengig abgerufen — Fehler einer Seite
    beeinflussen die andere nicht (graceful degradation).

    Bei gesperrtem Marketplace Insights (HTTP 400/403) greift automatisch
    der HTML-Scraper fuer verkaufte Listings (scrape_fallback=True).

    query:            EAN (z.B. '4010232075488') oder Freitext ('LEGO 10294').
    credentials:      {'client_id': ..., 'client_secret': ..., 'env': 'production'}.
    marketplace:      eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:            Max. Ergebnisse pro Seite (1-200).
    new_only:         Nur Zustand Neu.
    fixed_price_only: Nur Sofort-Kaufen.
    scrape_fallback:  Scraper als Fallback wenn Insights nicht verfuegbar (Default: True).

    Rueckgabe: MarketSnapshot mit sold_*/active_* + sell_through_rate.
    """
    ts = now_iso()

    # ── Verkaufte Angebote (Marketplace Insights → Scraper-Fallback) ──────────
    sold_total, sold_items, sold_error = search_sold(
        query, credentials, marketplace, limit, new_only, fixed_price_only,
        scrape_fallback=scrape_fallback,
    )
    sold_prices = [i.price for i in sold_items if i.price is not None]
    sold_med, sold_mean, sold_min, sold_max = _price_stats(sold_prices)

    # ── Aktive Angebote (Browse API) ───────────────────────────────────────────
    active_total, active_items, active_error = search_active(
        query, credentials, marketplace, limit, new_only, fixed_price_only
    )
    act_prices = [i.price for i in active_items if i.price is not None]
    act_med, act_mean, act_min, act_max = _price_stats(act_prices)

    return MarketSnapshot(
        query          = query,
        marketplace_id = marketplace,
        fetched_at     = ts,
        # Sold
        sold_total     = sold_total,
        sold_items     = sold_items,
        sold_median    = sold_med,
        sold_mean      = sold_mean,
        sold_min       = sold_min,
        sold_max       = sold_max,
        sold_count     = len(sold_prices),
        sold_error     = sold_error,
        # Active
        active_total   = active_total,
        active_items   = active_items,
        active_median  = act_med,
        active_mean    = act_mean,
        active_min     = act_min,
        active_max     = act_max,
        active_count   = len(act_prices),
        active_error   = active_error,
        # STR
        sell_through_rate = _calc_str(sold_total, active_total),
    )
