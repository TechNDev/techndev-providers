#!/usr/bin/env python3
"""
amazon_sp  fees.py  v1.0.0
============================
FBA-Gebuehrenschaetzung via Product Fees API.
Extrahiert aus amz-einkauf data_collector._add_fees.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - Initiales Release
  - estimate_fba_fees(): FBA-Gebuehr fuer einen Buy-Box-Preis schaetzen
    ACHTUNG: Methode heisst get_product_fees_estimate_for_asin —
    get_my_fees_estimate_for_asin existiert nicht (AttributeError -> stille None-Fee)
"""
from __future__ import annotations

from typing import Optional

from sp_api.api import ProductFees

from ._rate import _retry, pricing_limiter
from ._helpers import get_marketplace, get_marketplace_id

__version__ = "1.0.0"


@_retry
def estimate_fba_fees(
    asin: str,
    price: float,
    credentials: dict,
    marketplace: str = 'DE',
) -> Optional[float]:
    """
    FBA-Gebuehr fuer den gegebenen Preis schaetzen.
    Gibt None zurueck bei Fehler oder fehlender Gebuehrenantwort (kein raise).
    HTTP 429 wird propagiert fuer @_retry.
    """
    mktpl    = get_marketplace(marketplace)
    mktpl_id = get_marketplace_id(marketplace)

    try:
        pricing_limiter.wait()
        api  = ProductFees(credentials=credentials, marketplace=mktpl)
        resp = api.get_product_fees_estimate_for_asin(
            asin,
            price=float(price),
            currency='EUR',
            is_fba=True,
            marketplace_id=mktpl_id,
        )
        result = (resp.payload or {}).get('FeesEstimateResult', {})
        fee    = (
            result
            .get('FeesEstimate', {})
            .get('TotalFeesEstimate', {})
            .get('Amount')
        )
        return float(fee) if fee is not None else None

    except Exception as e:
        if '429' in str(e) or 'throttl' in str(e).lower():
            raise
        return None
