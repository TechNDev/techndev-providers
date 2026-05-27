#!/usr/bin/env python3
"""
amazon_sp  fees.py  v1.2.0
============================
FBA-Gebuehrenschaetzung via Product Fees API.
Extrahiert aus amz-einkauf data_collector._add_fees.

CHANGELOG
---------
v1.2.0  (2026-05-28)
  - get_fees_breakdown(): Gibt Gesamt- UND Einzelgebühren zurück
    (ReferralFee, FBAFees, VariableClosingFee, etc.) als strukturiertes Dict.

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

__version__ = "1.2.0"

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


@_retry
def get_fees_breakdown(
    asin: str,
    price: float,
    credentials: dict,
    marketplace: str = 'DE',
) -> Optional[dict]:
    """
    Detaillierte Gebührenaufschlüsselung für eine ASIN.

    Rückgabe-Dict:
        {
            "total":                float,   # Gesamtgebühr
            "referral_fee":         float,   # Provision (%)
            "fba_fee":              float,   # FBA-Fulfillment
            "variable_closing_fee": float,   # Variabler Abschluss
            "per_item_fee":         float,   # Pro-Artikel-Gebühr
            "other_fees":           float,   # Sonstige
            "details": [                     # Rohliste aller Posten
                {"name": str, "amount": float, "promotion": float,
                 "tax": float, "final": float},
                ...
            ],
            "error": None | str,
        }

    Bei API-Fehler: None (Fehler via get_last_fee_error() abrufbar).
    """
    _tl.last_error = None

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
        estimate = result.get('FeesEstimate', {})

        total_raw = estimate.get('TotalFeesEstimate', {}).get('Amount')
        if total_raw is None:
            err = f"TotalFeesEstimate.Amount fehlt (Status: {result.get('Status', '?')})"
            _tl.last_error = err
            print(f"[amazon_sp.fees] WARNING: {err}", file=sys.stderr)
            return None

        # Einzelposten aus FeeDetailList aufschlüsseln
        detail_list = estimate.get('FeeDetailList', [])
        details = []
        buckets = {
            'referral_fee':         0.0,
            'fba_fee':              0.0,
            'variable_closing_fee': 0.0,
            'per_item_fee':         0.0,
            'other_fees':           0.0,
        }
        _bucket_map = {
            'referralfee':          'referral_fee',
            'referral fee':         'referral_fee',
            'fbafees':              'fba_fee',
            'fba fees':             'fba_fee',
            'fba fee':              'fba_fee',
            'variableclosingfee':   'variable_closing_fee',
            'variable closing fee': 'variable_closing_fee',
            'peritemfee':           'per_item_fee',
            'per item fee':         'per_item_fee',
        }

        def _amt(node: dict, key: str) -> float:
            return float((node.get(key) or {}).get('Amount') or 0)

        for item in detail_list:
            name      = item.get('FeeType', 'Unknown')
            amount    = _amt(item, 'FeeAmount')
            promotion = _amt(item, 'FeePromotion')
            tax       = _amt(item, 'TaxAmount')
            final     = _amt(item, 'FinalFee')
            details.append({
                'name': name, 'amount': amount,
                'promotion': promotion, 'tax': tax, 'final': final,
            })
            bucket = _bucket_map.get(name.lower().replace('_', ' '), 'other_fees')
            buckets[bucket] += final if final else amount

        return {
            'total':                float(total_raw),
            'referral_fee':         round(buckets['referral_fee'],         2),
            'fba_fee':              round(buckets['fba_fee'],              2),
            'variable_closing_fee': round(buckets['variable_closing_fee'], 2),
            'per_item_fee':         round(buckets['per_item_fee'],         2),
            'other_fees':           round(buckets['other_fees'],           2),
            'details':              details,
            'error':                None,
        }

    except Exception as e:
        if '429' in str(e) or 'throttl' in str(e).lower():
            raise
        err_msg = f"{type(e).__name__}: {e}"
        _tl.last_error = err_msg
        print(f"[amazon_sp.fees] ERROR: {err_msg}", file=sys.stderr)
        return None
