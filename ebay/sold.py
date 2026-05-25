#!/usr/bin/env python3
"""
techndev-providers  ebay/sold.py  v1.0.0
=========================================
eBay Marketplace Insights API — verkaufte Angebote (Terapeak-Aequivalent).
Endpoint: GET /buy/marketplace_insights/v1_beta/item_sales/search

Voraussetzung: eBay Developer Account mit Marketplace Insights Freischaltung
(Business Policy Approval). Scope: buy.marketplace.insights.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - search_sold(): Suche nach verkauften Sofort-Kaufen-Neu-Angeboten via GTIN
    oder Freitext. Gibt SoldResult-kompatible Daten zurueck.
  - _parse_items(): EbaySoldApiItem-Parser aus _test_ebay_api.py uebernommen.
  - Graceful 403-Handling: wenn Marketplace Insights nicht freigeschaltet,
    wird der Fehler als SoldResult mit sold_error gefangen (kein raise).
"""
from __future__ import annotations

import sys

import requests

from ._auth  import get_token, is_gtin, api_base, SCOPE_SOLD
from ._models import SoldItem, _price_stats, now_iso

__version__ = "1.0.0"

# Standard-Filter: Sofort-Kaufen (kein Auktionschaos) + Zustand Neu (conditionId 1000)
_DEFAULT_FILTER = "buyingOptions:{FIXED_PRICE},conditionIds:{1000}"

TIMEOUT = 30


def search_sold(
    query:          str,
    credentials:    dict,
    marketplace:    str = "EBAY_DE",
    limit:          int = 50,
    new_only:       bool = True,
    fixed_price_only: bool = True,
) -> tuple[int | None, list[SoldItem], str | None]:
    """
    Sucht nach verkauften eBay-Angeboten via Marketplace Insights API.

    query:        EAN/GTIN oder Freitext-Suchbegriff.
    credentials:  {'client_id': ..., 'client_secret': ..., 'env': 'production'}.
    marketplace:  eBay-Marketplace-ID (Default: 'EBAY_DE').
    limit:        Max. Ergebnisse (1-200, API-Limit).
    new_only:     Nur Zustand Neu (conditionId 1000).
    fixed_price_only: Nur Sofort-Kaufen (FIXED_PRICE).

    Rueckgabe: (total, items, error_or_None)
      total: Gesamtanzahl laut API (kann > len(items) sein).
      items: Geparste SoldItem-Liste.
      error: None = OK; str = Fehlermeldung (Abruf misslungen, keine Exception).
    """
    client_id     = credentials["client_id"]
    client_secret = credentials["client_secret"]
    env           = credentials.get("env", "production")

    try:
        token = get_token(client_id, client_secret, scope=SCOPE_SOLD, env=env)
    except requests.HTTPError as e:
        return None, [], f"Token-Fehler (Marketplace Insights): HTTP {e.response.status_code if e.response is not None else '?'} — Scope evtl. nicht freigeschaltet"
    except Exception as e:
        return None, [], f"Token-Fehler: {e}"

    # Filter zusammenbauen
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
                "Authorization":          f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                "Accept":                 "application/json",
            },
            params=params,
            timeout=TIMEOUT,
        )
        if resp.status_code == 403:
            return None, [], "HTTP 403 — Marketplace Insights nicht freigeschaltet (Business Approval erforderlich)"
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return None, [], f"HTTP {code}"
    except requests.RequestException as e:
        return None, [], f"Netzwerkfehler: {e}"

    data     = resp.json()
    raw_list = data.get("itemSales") or data.get("itemSummaries") or []
    total    = data.get("total")

    items = _parse_items(raw_list)
    return total, items, None


def _parse_items(raw_list: list[dict]) -> list[SoldItem]:
    """Parst die item_sales-Antwort in SoldItem-Objekte."""
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
