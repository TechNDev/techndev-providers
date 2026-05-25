#!/usr/bin/env python3
"""
amazon_sp  fees.py  v1.1.0
============================
FBA-Gebuehrenschaetzung via Product Fees API.
Extrahiert aus amz-einkauf data_collector._add_fees.

CHANGELOG
---------
v1.1.0  (2026-05-25)
  - Fehler-Sichtbarkeit: stille None-Rueckgabe protokolliert jetzt Ausnahme auf
    sys.stderr und speichert sie thread-lokal (get_last_fee_error()).
    data_collector.py kann so fees_error korrekt befuellen — sichtbar in UI.

v1.0.0  (2026-05-25)
  - Initiales Release
  - estimate_fba_fees(): FBA-Gebuehr fuer einen Buy-Box-Preis schaetzen
    ACHTUNG: Methode heisst get_product_fees_estimate_for_asin —
    get_my_fees_estimate_for_asin existiert nicht (AttributeError -> stille None-Fee)
"""
from __future__ import annotations

import sys
import threading
from typing import Optional

from sp_api.api import ProductFees

from ._rate import _retry, pricing_limiter
from ._helpers import get_marketplace, get_marketplace_id

__version__ = "1.1.0"

# ── Thread-lokaler Fehlerspeicher ─────────────────────────────────────────────
# get_last_fee_error() gibt den Fehler des letzten gescheiterten Aufrufs zurueck.
# Thread-safe: jeder Thread hat seinen eigenen Fehlerzustand.
_tl = threading.local()


def get_last_fee_error() -> Optional[str]:
    """
    Gibt den Fehlertext des letzten estimate_fba_fees()-Aufrufs zurueck,
    der None geliefert hat (d.h. intern eine Exception gefangen hat).
    None wenn der letzte Aufruf erfolgreich war oder noch kein Aufruf erfolgte.

    Typische Verwendung in data_collector.py:
        fba_fee    = estimate_fba_fees(asin, price, creds)
        fees_error = get_last_fee_error()   # None = OK, str = Fehler
    """
    return getattr(_tl, 'last_error', None)


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

    Bei Fehler: Fehlermeldung wird auf sys.stderr ausgegeben UND via
    get_last_fee_error() abrufbar gespeichert.
    Bei Erfolg: get_last_fee_error() gibt None zurueck.
    """
    _tl.last_error = None   # Reset: neuer Aufruf loescht vorigen Fehler

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
        if fee is None:
            # API hat geantwortet, aber kein Amount-Feld — kein harter Fehler
            _tl.last_error = (
                f"FeesEstimateResult.FeesEstimate.TotalFeesEstimate.Amount fehlt "
                f"(Status: {result.get('FeesEstimateIdentifier', {}).get('SellerInputIdentifier', '?')})"
            )
            print(f"[amazon_sp.fees] WARNING: Amount-Feld fehlt — {_tl.last_error}", file=sys.stderr)
        return float(fee) if fee is not None else None

    except Exception as e:
        if '429' in str(e) or 'throttl' in str(e).lower():
            raise
        err_msg = f"{type(e).__name__}: {e}"
        _tl.last_error = err_msg
        print(f"[amazon_sp.fees] ERROR: {err_msg}", file=sys.stderr)
        return None
