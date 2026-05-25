#!/usr/bin/env python3
"""
amazon_sp  restrictions.py  v1.0.0
=====================================
Verkaufserlaubnis via Listings Restrictions API.
Extrahiert aus amz-einkauf data_collector._add_restrictions.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - Initiales Release
  - check_restrictions(): leere restrictions-Liste -> True (Verkauf erlaubt)
    conditionType='new_new' explizit — SP-API erwartet Condition fuer
    zuverlaessige Pruefung (ohne: teils leere Antwort oder 400 Bad Request)
"""
from __future__ import annotations

from typing import Optional

from sp_api.api import ListingsRestrictions

from ._rate import _retry, pricing_limiter
from ._helpers import get_marketplace, get_marketplace_id

__version__ = "1.0.0"


@_retry
def check_restrictions(
    asin: str,
    seller_id: str,
    credentials: dict,
    marketplace: str = 'DE',
) -> Optional[bool]:
    """
    Prueft ob der Verkauf des Produkts fuer den gegebenen Seller erlaubt ist.

    Gibt zurueck:
      True   = Verkauf erlaubt (leere restrictions-Liste)
      False  = gesperrt (restrictions vorhanden)
      None   = API-Fehler oder seller_id leer

    HTTP 429 wird propagiert fuer @_retry.
    """
    if not seller_id:
        return None

    mktpl    = get_marketplace(marketplace)
    mktpl_id = get_marketplace_id(marketplace)

    try:
        pricing_limiter.wait()
        api  = ListingsRestrictions(credentials=credentials, marketplace=mktpl)
        resp = api.get_listings_restrictions(
            asin=asin,
            conditionType='new_new',
            sellerId=seller_id,
            marketplaceIds=[mktpl_id],
        )
        restrictions = (resp.payload or {}).get('restrictions', [])
        return len(restrictions) == 0

    except Exception as e:
        if '429' in str(e) or 'throttl' in str(e).lower():
            raise
        return None
