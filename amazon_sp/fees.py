#!/usr/bin/env python3
"""
amazon_sp  fees.py  v1.7.0
============================
FBA-Gebuehrenschaetzung via Product Fees API.
Extrahiert aus amz-einkauf data_collector._add_fees.

CHANGELOG
---------
v1.7.0  (2026-05-30)
  - credentials-Parameter optional (Default None): Auto-Load via _credentials.py.

v1.6.0  (2026-05-29)
  - get_fees_breakdown(): Retry (bis 3x, Backoff) bei transienten Fees-API-
    Fehlern (Status ServerError/ClientError ohne TotalFeesEstimate.Amount).

v1.5.0  (2026-05-28)
  - get_fees_breakdown(): ek_price-Parameter + Margenberechnung.
    Rückgabe enthält profit, profit_pct (Nettomarge) und roi.

v1.4.0  (2026-05-28)
  - get_fees_breakdown(): MwSt-Berechnung ergänzt (mwst_pct, Default 19 %).
    Rückgabe enthält price_net, vat_on_price, vat_on_fees, total_all_in
    jetzt auf Basis Nettopreis.

v1.3.0  (2026-05-28)
  - get_fees_breakdown(): Pauschale Verkäuferkosten als Parameter ergänzt:
    storage_fee_monthly (0,15 €), prep_fee (0,50 €), inbound_fee (1,00 €).
    Rückgabe enthält jetzt seller_costs und total_all_in.

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
import time
from typing import Optional

from sp_api.api import ProductFees

from ._rate import _retry, pricing_limiter
from ._credentials import get_credentials
from ._helpers import get_marketplace, get_marketplace_id

__version__ = "1.7.0"

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
    credentials: Optional[dict] = None,
    marketplace: str = 'DE',
) -> Optional[float]:
    """
    FBA-Gebuehr fuer den gegebenen Preis schaetzen.
    Gibt None zurueck bei Fehler oder fehlender Gebuehrenantwort (kein raise).
    HTTP 429 wird propagiert fuer @_retry.

    credentials: SP-API-Creds dict oder None (dann Auto-Load via _credentials.py).
    Bei Fehler: Fehlermeldung wird auf sys.stderr ausgegeben UND via
    get_last_fee_error() abrufbar gespeichert.
    Bei Erfolg: get_last_fee_error() gibt None zurueck.
    """
    credentials    = get_credentials(credentials)
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
    credentials: Optional[dict] = None,
    marketplace: str = 'DE',
    storage_fee_monthly: float = 0.15,
    prep_fee: float = 0.50,
    inbound_fee: float = 1.00,
    mwst_pct: float = 19.0,
    ek_price: float = 0.0,
) -> Optional[dict]:
    """
    Detaillierte Gebührenaufschlüsselung für eine ASIN.

    price wird als Brutto-Verkaufspreis (inkl. MwSt) erwartet.
    Amazon liefert Gebühren netto (ohne MwSt) — MwSt wird separat berechnet.

    Parameter (alle überschreibbar):
        storage_fee_monthly  Lagergebühr pro Einheit/Monat  (Default: 0,15 €)
        prep_fee             Prep-/Vorbereitungskosten       (Default: 0,50 €)
        inbound_fee          Transport du → Amazon-Lager     (Default: 1,00 €)
        mwst_pct             MwSt-Satz in Prozent            (Default: 19,0 %)
        ek_price             Einkaufspreis (netto)            (Default: 0,00 €)

    Rückgabe-Dict:
        {
            # Preis-Aufschlüsselung
            "price_gross":          float,   # Brutto-VK (Eingabe)
            "vat_rate":             float,   # MwSt-Satz (z.B. 0.19)
            "vat_on_price":         float,   # MwSt-Anteil im VK → ans Finanzamt
            "price_net":            float,   # Netto-VK (Erlös vor Gebühren)
            # Amazon API-Gebühren (netto, ohne MwSt)
            "total":                float,   # Amazon-Gesamtgebühr (netto)
            "referral_fee":         float,   # Provision (netto)
            "fba_fee":              float,   # FBA-Fulfillment (netto)
            "variable_closing_fee": float,   # Variabler Abschluss (netto)
            "per_item_fee":         float,   # Pro-Artikel-Gebühr (netto)
            "other_fees":           float,   # Sonstige (netto)
            "vat_on_fees":          float,   # 19% MwSt auf Amazon-Gebühren
                                             # (als Vorsteuer absetzbar)
            # Pauschale Verkäuferkosten
            "storage_fee_monthly":  float,   # Lager (je Monat)
            "prep_fee":             float,   # Prep
            "inbound_fee":          float,   # Inbound-Transport
            "seller_costs":         float,   # Summe Verkäuferkosten
            # Gesamt & Marge
            "total_all_in":         float,   # Amazon (netto) + Verkäuferkosten
            "ek_price":             float,   # Einkaufspreis (netto)
            "profit":               float,   # Netto-Erlös - all-in - EK
            "profit_pct":           float,   # profit / price_net * 100
            "roi":                  float,   # profit / ek_price * 100 (0 wenn kein EK)
            "details": [...],
            "error": None | str,
        }

    Bei API-Fehler: None (Fehler via get_last_fee_error() abrufbar).
    credentials: SP-API-Creds dict oder None (dann Auto-Load via _credentials.py).
    """
    credentials    = get_credentials(credentials)
    _tl.last_error = None

    mktpl    = get_marketplace(marketplace)
    mktpl_id = get_marketplace_id(marketplace)

    try:
        estimate  = {}
        total_raw = None
        for attempt in range(3):
            pricing_limiter.wait()
            api  = ProductFees(credentials=credentials, marketplace=mktpl)
            resp = api.get_product_fees_estimate_for_asin(
                asin,
                price=float(price),
                currency='EUR',
                is_fba=True,
                marketplace_id=mktpl_id,
            )
            result    = (resp.payload or {}).get('FeesEstimateResult', {})
            estimate  = result.get('FeesEstimate', {})
            total_raw = estimate.get('TotalFeesEstimate', {}).get('Amount')
            if total_raw is not None:
                break
            # Server-/ClientError sind bei der Fees-API haeufig transient → kurz erneut.
            status = result.get('Status', '?')
            if status in ('ServerError', 'ClientError') and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            err = f"TotalFeesEstimate.Amount fehlt (Status: {status})"
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

        amazon_total = float(total_raw)
        vat_rate     = mwst_pct / 100.0
        vat_on_price = round(float(price) * vat_rate / (1 + vat_rate), 2)
        price_net    = round(float(price) - vat_on_price,               2)
        vat_on_fees  = round(amazon_total * vat_rate,                   2)
        seller_costs = round(storage_fee_monthly + prep_fee + inbound_fee, 2)
        total_all_in = round(amazon_total + seller_costs,                2)
        profit       = round(price_net - total_all_in - ek_price,        2)
        profit_pct   = round(profit / price_net * 100, 2) if price_net else 0.0
        roi          = round(profit / ek_price * 100,  2) if ek_price  else 0.0

        return {
            'price_gross':          round(float(price),                    2),
            'vat_rate':             vat_rate,
            'vat_on_price':         vat_on_price,
            'price_net':            price_net,
            'total':                round(amazon_total,                    2),
            'referral_fee':         round(buckets['referral_fee'],         2),
            'fba_fee':              round(buckets['fba_fee'],              2),
            'variable_closing_fee': round(buckets['variable_closing_fee'], 2),
            'per_item_fee':         round(buckets['per_item_fee'],         2),
            'other_fees':           round(buckets['other_fees'],           2),
            'vat_on_fees':          vat_on_fees,
            'storage_fee_monthly':  round(storage_fee_monthly,            2),
            'prep_fee':             round(prep_fee,                        2),
            'inbound_fee':          round(inbound_fee,                     2),
            'seller_costs':         seller_costs,
            'total_all_in':         total_all_in,
            'ek_price':             round(ek_price,                        2),
            'profit':               profit,
            'profit_pct':           profit_pct,
            'roi':                  roi,
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
