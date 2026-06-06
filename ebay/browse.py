#!/usr/bin/env python3
"""
techndev-providers  ebay/browse.py  v1.0.0
===========================================
eBay Browse API — aktive Angebote (Marktpreisspiegel).
Endpoint: GET /buy/browse/v1/item_summary/search

Breiter verfuegbar als Marketplace Insights: Basic Scope genuegt.
Liefert aktuelle Angebotslandschaft (Preis, Anbieteranzahl, Zustand).

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - search_active(): Suche nach aktiven Sofort-Kaufen-Neu-Angeboten.
    Rueckgabe: (total, items, error_or_None) — identisches Muster zu sold.py.
  - _parse_items(): Browse-API-Felder → ActiveItem.
"""
from __future__ import annotations

import requests

from ._auth   import get_token, is_gtin, api_base, SCOPE_BASIC
from ._models import ActiveItem, ActiveResult, _price_stats, now_iso
from ._rate   import _retry, browse_limiter

__version__ = "1.1.0"

TIMEOUT = 30


# ══════════════════════════════════════════════════════════════════════════════
# Oeffentliche API — analog amazon_sp
# ══════════════════════════════════════════════════════════════════════════════

@_retry
def get_active_listings(
    query:            str,
    credentials:      dict,
    marketplace:      str  = 'EBAY_DE',
    limit:            int  = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
) -> ActiveResult:
    """
    Aktive eBay-Angebote fuer eine EAN/GTIN oder Freitext-Query via Browse API.

    Analog zu amazon_sp.get_offers():
      Gibt immer ein ActiveResult zurueck — kein raise, kein Tuple.
      result.ok()          → True wenn kein Fehler
      result.best_price    → Median-Preis (Fallback: Mean)
      result.median_price  → Median aller aktiven Preise
      result.items         → Liste der ActiveItem-Objekte
      result.total         → Gesamtanzahl laut eBay Browse API

    Benoetigt nur SCOPE_BASIC (kein Business-Approval noetig).

    query:            EAN/GTIN (z.B. '4010232075488') oder Freitext ('LEGO 75192').
    credentials:      {'client_id': ..., 'client_secret': ..., 'env': 'production'}
    marketplace:      eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:            Max. Ergebnisse (1-200, Default: 50).
    new_only:         Nur Zustand Neu (Default: True).
    fixed_price_only: Nur Sofort-Kaufen (Default: True).
    """
    ts = now_iso()
    total, items, error = search_active(query, credentials, marketplace, limit, new_only, fixed_price_only)
    prices = [i.price for i in items if i.price is not None]
    med, mn_mean, mn, mx = _price_stats(prices)

    return ActiveResult(
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
        error        = error,
    )


def search_active(
    query:            str,
    credentials:      dict,
    marketplace:      str = "EBAY_DE",
    limit:            int = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
) -> tuple[int | None, list[ActiveItem], str | None]:
    """
    Sucht nach aktiven eBay-Angeboten via Browse API item_summary/search.

    query:        EAN/GTIN oder Freitext-Suchbegriff.
    credentials:  {'client_id': ..., 'client_secret': ..., 'env': 'production'}.
    marketplace:  eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:        Max. Ergebnisse (1-200).
    new_only:     Nur Zustand Neu (conditionId 1000).
    fixed_price_only: Nur Sofort-Kaufen (FIXED_PRICE).

    Rueckgabe: (total, items, error_or_None)
    """
    client_id     = credentials["client_id"]
    client_secret = credentials["client_secret"]
    env           = credentials.get("env", "production")

    try:
        token = get_token(client_id, client_secret, scope=SCOPE_BASIC, env=env)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return None, [], f"Token-Fehler: HTTP {code}"
    except Exception as e:
        return None, [], f"Token-Fehler: {e}"

    browse_limiter.wait()

    # Filter: Browse API nutzt komma-getrenntes Format
    filters: list[str] = []
    if fixed_price_only:
        filters.append("buyingOptions:{FIXED_PRICE}")
    if new_only:
        filters.append("conditionIds:{1000}")
    filter_str = ",".join(filters)

    params: dict = {
        "limit":  max(1, min(limit, 200)),
        "offset": 0,
    }
    if filter_str:
        params["filter"] = filter_str
    if is_gtin(query):
        params["gtin"] = query
    else:
        params["q"] = query

    url = f"{api_base(env)}/buy/browse/v1/item_summary/search"
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization":           f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                "Accept":                  "application/json",
            },
            params=params,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return None, [], f"HTTP {code}"
    except requests.RequestException as e:
        return None, [], f"Netzwerkfehler: {e}"

    data     = resp.json()
    raw_list = data.get("itemSummaries") or []
    total    = data.get("total")

    items = _parse_items(raw_list)
    return total, items, None


def get_item_gtin(item_id: str, credentials: dict, marketplace: str = 'EBAY_DE') -> str | None:
    """GTIN/EAN eines eBay-Items via Browse API getItem (oder None).

    item_summary/search liefert KEINE GTIN -> dieser Detail-Call (1 Request/Item)
    schliesst die Luecke fuer das Produkt-Matching (z.B. WOW-Deals als Preisquelle).
    Robust: gibt None bei jedem Fehler (Token/HTTP/fehlende GTIN) zurueck."""
    if not item_id:
        return None
    try:
        token = get_token(credentials["client_id"], credentials["client_secret"],
                          scope=SCOPE_BASIC, env=credentials.get("env", "production"))
    except Exception:                                    # noqa: BLE001
        return None
    browse_limiter.wait()
    url = f"{api_base(credentials.get('env', 'production'))}/buy/browse/v1/item/{item_id}"
    try:
        resp = requests.get(url, headers={
            "Authorization":           f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
            "Accept":                  "application/json",
        }, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    data = resp.json()
    gtin = str(data.get("gtin") or "").strip()
    if gtin:
        return gtin
    # Fallback: GTIN/EAN/UPC aus den localizedAspects
    for asp in data.get("localizedAspects") or []:
        if str(asp.get("name") or "").upper() in ("GTIN", "EAN", "UPC"):
            v = str(asp.get("value") or "").strip()
            if v:
                return v
    return None


def _parse_items(raw_list: list[dict]) -> list[ActiveItem]:
    """Parst Browse-API-Ergebnisse in ActiveItem-Objekte."""
    items: list[ActiveItem] = []
    for raw in raw_list:
        price_raw = raw.get("price") or {}
        val_raw   = price_raw.get("value")
        try:
            price = float(val_raw) if val_raw is not None else None
        except (TypeError, ValueError):
            price = None

        opts = raw.get("buyingOptions") or []
        buying_options = ", ".join(str(x) for x in opts) if isinstance(opts, list) else str(opts or "")

        items.append(ActiveItem(
            title          = raw.get("title") or "-",
            price          = price,
            currency       = price_raw.get("currency") or "",
            condition      = raw.get("condition") or "",
            buying_options = buying_options,
            item_id        = str(raw.get("itemId") or raw.get("legacyItemId") or ""),
            url            = raw.get("itemWebUrl") or raw.get("itemAffiliateWebUrl") or "",
        ))
    return items
