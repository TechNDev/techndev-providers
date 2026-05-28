#!/usr/bin/env python3
"""
techndev-providers  ebay/sold.py  v2.0.0
=========================================
Verkaufte eBay-Angebote — primaer via HTML-Scraper.

Primary:  scraper.scrape_sold() (HTML-Scraping von /sch/i.html?LH_Sold=1)
Backlog:  Marketplace Insights API (_search_sold_api) — reaktivieren sobald
          eBay Business Approval fuer Scope buy.marketplace.insights erteilt.

CHANGELOG
---------
v2.0.0  (2026-05-28)
  - BREAKING: search_sold() ruft direkt scrape_sold() auf; kein API-Versuch mehr.
    scrape_fallback-Parameter entfernt (war Workaround, jetzt Normalzustand).
  - BACKLOG: _search_sold_api() isoliert die MI-API-Logik fuer spaetere
    Reaktivierung. Zugehoerige Importe (_auth, requests) nur dort referenziert.

v1.1.0  (2026-05-25)
  - Scraper-Fallback: bei Token HTTP 400/403 (Marketplace Insights nicht freigeschaltet)
    oder API HTTP 403 wird scraper.scrape_sold() als Fallback aufgerufen.

v1.0.0  (2026-05-25)
  - search_sold(): Suche nach verkauften Sofort-Kaufen-Neu-Angeboten via GTIN
    oder Freitext. Gibt SoldResult-kompatible Daten zurueck.
  - _parse_items(): EbaySoldApiItem-Parser.
  - Graceful 403-Handling.
"""
from __future__ import annotations

from ._models import SoldItem, SoldResult, _price_stats, now_iso
from ._rate   import _retry, scraper_limiter
from .scraper import scrape_sold

__version__ = "2.1.0"


# ══════════════════════════════════════════════════════════════════════════════
# Oeffentliche API — analog amazon_sp
# ══════════════════════════════════════════════════════════════════════════════

@_retry
def get_sold_listings(
    query:            str,
    credentials:      dict,
    marketplace:      str  = 'EBAY_DE',
    limit:            int  = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
) -> SoldResult:
    """
    Verkaufte eBay-Angebote fuer eine EAN/GTIN oder Freitext-Query.

    Analog zu amazon_sp.search_by_ean():
      Gibt immer ein SoldResult zurueck — kein raise, kein Tuple.
      result.ok()          → True wenn kein Fehler
      result.best_price    → Median-Preis (Fallback: Mean)
      result.median_price  → Median aller verkauften Preise
      result.items         → Liste der SoldItem-Objekte
      result.total         → Gesamtanzahl laut eBay
      result.source        → 'scraper' | 'api'

    Primary:  HTML-Scraper (scraper.scrape_sold).
    Backlog:  Marketplace Insights API (_search_sold_api) — nach Business Approval.

    query:            EAN/GTIN (z.B. '4010232075488') oder Freitext ('LEGO 75192').
    credentials:      {'client_id': ..., 'client_secret': ..., 'env': 'production'}
    marketplace:      eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:            Max. Ergebnisse (1-200, Default: 50).
    new_only:         Nur Zustand Neu (Default: True).
    fixed_price_only: Nur Sofort-Kaufen (Default: True).
    """
    ts = now_iso()
    scraper_limiter.wait()
    total, items, error = scrape_sold(query, marketplace, limit, new_only, fixed_price_only)
    prices = [i.price for i in items if i.price is not None]
    med, mn_mean, mn, mx = _price_stats(prices)

    return SoldResult(
        query        = query,
        marketplace  = marketplace,
        fetched_at   = ts,
        total        = total,
        count        = len(prices),
        median_price = med,
        mean_price   = mn_mean,
        min_price    = mn,
        max_price    = mx,
        items        = items,
        source       = 'scraper',
        error        = error,
    )


def search_sold(
    query:            str,
    credentials:      dict,       # wird an _search_sold_api weitergegeben (Backlog)
    marketplace:      str  = "EBAY_DE",
    limit:            int  = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
) -> tuple[int | None, list[SoldItem], str | None]:
    """
    Sucht nach verkauften eBay-Angeboten.

    Primary:  HTML-Scraper (scraper.scrape_sold).
    Backlog:  Marketplace Insights API — sobald Business Approval erteilt,
              _search_sold_api() als Primary, scrape_sold() als Fallback einsetzen.

    query:            EAN/GTIN oder Freitext-Suchbegriff.
    credentials:      {'client_id': ..., 'client_secret': ..., 'env': 'production'}
                      (derzeit nicht genutzt; Backlog: _search_sold_api).
    marketplace:      eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:            Max. Ergebnisse (1-200).
    new_only:         Nur Zustand Neu (LH_ItemCondition=3).
    fixed_price_only: Nur Sofort-Kaufen (LH_BIN=1).

    Rueckgabe: (total, items, error_or_None)
    """
    return scrape_sold(query, marketplace, limit, new_only, fixed_price_only)


# ═══════════════════════════════════════════════════════════════════════════════
# BACKLOG: Marketplace Insights API
# ─────────────────────────────────────────────────────────────────────────────
# Reaktivieren wenn eBay Business Approval fuer buy.marketplace.insights erteilt.
# Dann in search_sold():
#   1. _search_sold_api() als Primary aufrufen.
#   2. Bei HTTPError 400/403 → scrape_sold() als Fallback.
#   3. scrape_fallback-Parameter wieder einfuehren.
# ═══════════════════════════════════════════════════════════════════════════════

def _search_sold_api(
    query:            str,
    credentials:      dict,
    marketplace:      str  = "EBAY_DE",
    limit:            int  = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
) -> tuple[int | None, list[SoldItem], str | None]:
    """
    [BACKLOG] Marketplace Insights API — buy.marketplace.insights.
    Endpoint: GET /buy/marketplace_insights/v1_beta/item_sales/search
    Voraussetzung: eBay Business Approval fuer Scope buy.marketplace.insights.
    """
    import requests  # lokaler Import — wird erst bei Reaktivierung benoetigt
    from ._auth import get_token, is_gtin, api_base, SCOPE_SOLD

    client_id     = credentials["client_id"]
    client_secret = credentials["client_secret"]
    env           = credentials.get("env", "production")

    try:
        token = get_token(client_id, client_secret, scope=SCOPE_SOLD, env=env)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return None, [], (
            f"Token-Fehler (Marketplace Insights): HTTP {code} "
            f"— Scope nicht freigeschaltet"
        )
    except Exception as e:
        return None, [], f"Token-Fehler: {e}"

    filters: list[str] = []
    if fixed_price_only:
        filters.append("buyingOptions:{FIXED_PRICE}")
    if new_only:
        filters.append("conditionIds:{1000}")
    filter_str = ",".join(filters)

    params: dict = {
        "limit":  max(1, min(limit, 200)),
        "offset": 0,
        "sort":   "-lastSoldDate",
    }
    if filter_str:
        params["filter"] = filter_str
    if is_gtin(query):
        params["gtin"] = query
    else:
        params["q"] = query

    url = f"{api_base(env)}/buy/marketplace_insights/v1_beta/item_sales/search"
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization":           f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                "Accept":                  "application/json",
            },
            params=params,
            timeout=30,
        )
        if resp.status_code == 403:
            return None, [], "HTTP 403 — Marketplace Insights nicht freigeschaltet"
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return None, [], f"HTTP {code}"
    except requests.RequestException as e:
        return None, [], f"Netzwerkfehler: {e}"

    data     = resp.json()
    raw_list = data.get("itemSales") or data.get("itemSummaries") or []
    total    = data.get("total")
    return total, _parse_api_items(raw_list), None


def _parse_api_items(raw_list: list[dict]) -> list[SoldItem]:
    """[BACKLOG] Parst die item_sales-API-Antwort in SoldItem-Objekte."""
    items: list[SoldItem] = []
    for raw in raw_list:
        price_raw = raw.get("price") or raw.get("currentBidPrice") or raw.get("itemPrice") or {}
        val_raw   = price_raw.get("value")
        try:
            price = float(val_raw) if val_raw is not None else None
        except (TypeError, ValueError):
            price = None

        opts = raw.get("buyingOptions") or []
        buying_options = ", ".join(str(x) for x in opts) if isinstance(opts, list) else str(opts or "")

        items.append(SoldItem(
            title          = raw.get("title") or "-",
            price          = price,
            currency       = price_raw.get("currency") or "",
            sold_date      = raw.get("lastSoldDate") or raw.get("itemEndDate") or "",
            condition      = raw.get("condition") or "",
            buying_options = buying_options,
            item_id        = str(raw.get("itemId") or raw.get("legacyItemId") or ""),
            url            = raw.get("itemWebUrl") or raw.get("itemAffiliateWebUrl") or "",
        ))
    return items
