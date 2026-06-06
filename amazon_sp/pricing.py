#!/usr/bin/env python3
"""
amazon_sp  pricing.py  v1.3.0
================================
Angebote, Buy-Box-Preis und Wettbewerbspreise via ProductsV0 API.

Merges beider bisheriger Implementierungen:
  amz-einkauf: data_collector._add_offers / _add_competitive_price
  EAN2JTL:     AmazonClient._fetch_price (Buy-Box / niedrigster Neupreis)

CHANGELOG
---------
v1.3.0  (2026-06-06)
  - get_offers_batch(): Buy-Box/Angebote fuer bis zu 20 ASINs/Call via
    getItemOffersBatch (eigener pricing_batch_limiter, 0,1 Req/s). ~4x Durchsatz
    + ~20x weniger HTTP-Calls vs. Einzelabruf. Identische Payload-Parsing.
  - Parsing in _parse_offers_payload() ausgelagert (von get_offers + Batch genutzt).
  - Bulk-Competitive-Fallback (_get_competitive_prices, get_competitive_pricing_
    for_asins, 20/Call) fuer ASINs ohne Buy-Box.

v1.2.0  (2026-05-30)
  - credentials-Parameter optional (Default None): Auto-Load via _credentials.py.

v1.1.0  (2026-05-29)
  - OffersResult.offers_detail: aktueller Angebots-Snapshot je Seller
    (Landed-Preis, FBA/FBM, Buy-Box-Gewinner, Feedback) fuer Buy-Box-Tracking.

v1.0.0  (2026-05-25)
  - Initiales Release
  - OffersResult: Datenmodell mit Seller-Anzahl, Buy-Box, FBA, Dominanz
  - get_offers(): getItemOffers + CompetitivePricing-Fallback
  - get_item_price(): vereinfachter Preis-only-Aufruf (fuer EAN2JTL amazon_price)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from sp_api.api import ProductsV0

from ._rate import _retry, pricing_limiter, pricing_batch_limiter
from ._credentials import get_credentials
from ._helpers import get_marketplace, get_marketplace_id, get_amazon_seller_id

__version__ = "1.3.0"


# ══════════════════════════════════════════════════════════════════════════════
# Datenmodell
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class OffersResult:
    """
    Ergebnis von get_offers(). Alle Felder haben sinnvolle Defaults.
    error != None signalisiert Fehler.
    """
    total_sellers_new: Optional[int]   = None
    fba_sellers_new:   Optional[int]   = None
    buy_box_price:     Optional[float] = None
    lowest_new_price:  Optional[float] = None   # EAN2JTL: Fallback wenn kein Buy-Box
    amazon_on_listing: bool            = False
    buy_box_dominant:  bool            = False
    price_source:      str             = ''     # 'offers' | 'competitive' | ''
    # Aktueller Angebots-Snapshot (max. 20, API-Limit). Je Eintrag:
    # {seller_id, price (landed), is_fba, is_buy_box_winner, feedback_count, feedback_pct}
    offers_detail:     list            = field(default_factory=list)
    error:             Optional[str]   = None

    def ok(self) -> bool:
        return self.error is None

    @property
    def best_price(self) -> Optional[float]:
        """Buy-Box-Preis; Fallback: niedrigster Neupreis."""
        return self.buy_box_price if self.buy_box_price is not None else self.lowest_new_price


# ══════════════════════════════════════════════════════════════════════════════
# Payload-Parsing (gemeinsam fuer Einzel- und Batch-Abruf)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_offers_payload(payload: dict, amazon_seller: str) -> OffersResult:
    """
    Parst eine getItemOffers-Payload (Summary + Offers) in ein OffersResult.
    OHNE Competitive-Fallback — den steuert der Aufrufer (Einzel- vs. Bulk-Pfad).
    """
    payload = payload or {}
    summary = payload.get('Summary', {})
    offers  = payload.get('Offers', [])

    # ── Seller-Anzahl aus Summary (zuverlässiger als len(Offers), API max. 20) ──
    total_new = 0
    fba_new   = 0
    for entry in summary.get('NumberOfOffers', []):
        if str(entry.get('condition', '')).lower() == 'new':
            count = entry.get('OfferCount', 0)
            total_new += count
            if entry.get('fulfillmentChannel') == 'Amazon':
                fba_new += count

    # ── Buy-Box-Preis (Neu-Zustand) ───────────────────────────────────────────
    buy_box_price = None
    for bb in summary.get('BuyBoxPrices', []):
        if str(bb.get('condition', '')).lower() == 'new':
            lp  = bb.get('LandedPrice') or bb.get('ListingPrice') or {}
            amt = lp.get('Amount')
            if amt is not None:
                buy_box_price = float(amt)
                break

    # ── Niedrigster Neupreis (EAN2JTL Fallback fuer amazon_price) ──────────────
    lowest_new_price = None
    for lp in summary.get('LowestPrices', []):
        if lp.get('condition', '').lower() in ('new', 'neu'):
            node = lp.get('LandedPrice') or lp.get('ListingPrice') or {}
            amt  = node.get('Amount')
            if amt is not None:
                lowest_new_price = float(amt)
                break

    # ── Buy-Box-Dominanz & Amazon-Praesenz ─────────────────────────────────────
    amazon_on_listing = any(o.get('SellerId') == amazon_seller for o in offers)
    winner_count      = sum(1 for o in offers if o.get('IsBuyBoxWinner'))
    buy_box_dominant  = winner_count == 1 and total_new >= 3

    # ── Angebots-Snapshot (Seller, Landed-Preis, FBA/FBM, Buy-Box, Feedback) ───
    offers_detail = []
    for o in offers:
        lp   = (o.get('ListingPrice') or {}).get('Amount')
        ship = (o.get('Shipping') or {}).get('Amount') or 0.0
        fb   = o.get('SellerFeedbackRating') or {}
        offers_detail.append({
            'seller_id':         o.get('SellerId'),
            'price':             round(float(lp) + float(ship), 2) if lp is not None else None,
            'is_fba':            bool(o.get('IsFulfilledByAmazon')),
            'is_buy_box_winner': bool(o.get('IsBuyBoxWinner')),
            'feedback_count':    fb.get('FeedbackCount'),
            'feedback_pct':      fb.get('SellerPositiveFeedbackRating'),
        })
    # Buy-Box-Gewinner zuerst, dann nach Landed-Preis aufsteigend.
    offers_detail.sort(key=lambda x: (not x['is_buy_box_winner'],
                                      x['price'] if x['price'] is not None else 9e9))

    return OffersResult(
        total_sellers_new=total_new,
        fba_sellers_new=fba_new,
        buy_box_price=buy_box_price,
        lowest_new_price=lowest_new_price,
        amazon_on_listing=amazon_on_listing,
        buy_box_dominant=buy_box_dominant,
        price_source='offers',
        offers_detail=offers_detail,
    )


def _batch_asin(response_item: dict) -> Optional[str]:
    """ASIN aus einer getItemOffersBatch-Teilantwort extrahieren (Payload, sonst URI)."""
    body    = response_item.get('body') or {}
    payload = body.get('payload') or {}
    asin    = payload.get('ASIN')
    if asin:
        return asin
    req = response_item.get('request') or {}
    m   = re.search(r'/items/([^/]+)/offers', req.get('uri') or '')
    return m.group(1) if m else None


# ══════════════════════════════════════════════════════════════════════════════
# Oeffentliche API
# ══════════════════════════════════════════════════════════════════════════════

@_retry
def get_offers(
    asin: str,
    credentials: Optional[dict] = None,
    marketplace: str = 'DE',
) -> OffersResult:
    """
    Angebote via getItemOffers (Condition=New).
    Bei fehlendem Buy-Box-Preis automatischer Fallback auf CompetitivePricing.

    Gibt OffersResult mit error-Feld zurueck statt Exception zu werfen.
    HTTP 429 wird propagiert fuer @_retry.

    credentials: SP-API-Creds dict oder None (dann Auto-Load via _credentials.py).
    """
    credentials   = get_credentials(credentials)
    mktpl         = get_marketplace(marketplace)
    mktpl_id      = get_marketplace_id(marketplace)
    amazon_seller = get_amazon_seller_id(marketplace)

    try:
        pricing_limiter.wait()
        api    = ProductsV0(credentials=credentials, marketplace=mktpl)
        resp   = api.get_item_offers(asin=asin, item_condition='New')
        result = _parse_offers_payload(resp.payload or {}, amazon_seller)

        # ── CompetitivePricing-Fallback wenn kein Buy-Box-Preis ───────────────
        if result.buy_box_price is None:
            cp = _get_competitive_price(asin, credentials, mktpl, mktpl_id)
            if cp is not None:
                result.buy_box_price = cp
                result.price_source  = 'competitive'
        return result

    except Exception as e:
        if '429' in str(e) or 'throttl' in str(e).lower():
            raise
        return OffersResult(error=str(e))


def get_offers_batch(
    asins: list[str],
    credentials: Optional[dict] = None,
    marketplace: str = 'DE',
    competitive_fallback: bool = True,
) -> dict[str, OffersResult]:
    """
    Buy-Box/Angebote fuer mehrere ASINs via getItemOffersBatch (bis 20 ASINs/Call).

    Liefert {asin: OffersResult} — identisches Parsing wie get_offers. Fuer ASINs
    ohne Buy-Box-Preis optionaler Competitive-Fallback (gebuendelt, 20/Call).

    Rate: getItemOffersBatch 0,1 Req/s (1 Call/10 s) x 20 ASINs = ~2 ASIN/s
    (~4x Durchsatz + ~20x weniger HTTP-Calls vs. Einzel-get_offers).

    Robustheit: 429 wird je Chunk (nicht global) per @_retry wiederholt; bleibt
    ein Chunk fehlerhaft, bekommen seine ASINs ein OffersResult(error=...), sodass
    der Aufrufer gezielt auf den Einzelabruf zurueckfallen kann. Jede angefragte
    ASIN ist im Ergebnis-Dict vertreten (kein KeyError).
    """
    credentials   = get_credentials(credentials)
    mktpl         = get_marketplace(marketplace)
    mktpl_id      = get_marketplace_id(marketplace)
    amazon_seller = get_amazon_seller_id(marketplace)

    uniq = list(dict.fromkeys(str(a).strip() for a in asins if a and str(a).strip()))
    results: dict[str, OffersResult] = {}
    if not uniq:
        return results

    @_retry
    def _batch_call(api, reqs):
        pricing_batch_limiter.wait()
        return api.get_item_offers_batch(reqs)

    api = ProductsV0(credentials=credentials, marketplace=mktpl)
    for start in range(0, len(uniq), 20):
        chunk = uniq[start:start + 20]
        reqs  = [{'uri': f'/products/pricing/v0/items/{a}/offers',
                  'method': 'GET', 'MarketplaceId': mktpl_id,
                  'ItemCondition': 'New'} for a in chunk]
        try:
            resp = _batch_call(api, reqs)
        except Exception as e:                                # noqa: BLE001
            for a in chunk:
                results[a] = OffersResult(error=str(e))
            continue
        for r in (resp.payload or {}).get('responses', []) or []:
            asin = _batch_asin(r)
            if not asin:
                continue
            status = (r.get('status') or {}).get('statusCode')
            if status is not None and int(status) >= 400:
                results[asin] = OffersResult(error=f"batch status {status}")
                continue
            body    = r.get('body') or {}
            payload = body.get('payload') or body or {}
            results[asin] = _parse_offers_payload(payload, amazon_seller)

    # ── Competitive-Fallback gebuendelt fuer ASINs ohne Buy-Box ───────────────
    if competitive_fallback:
        missing = [a for a in uniq
                   if a not in results
                   or (results[a].error is None and results[a].buy_box_price is None)]
        if missing:
            for a, cp in _get_competitive_prices(missing, credentials, mktpl, mktpl_id).items():
                if cp is None:
                    continue
                cur = results.get(a)
                if cur is None or cur.error is not None:
                    results[a] = OffersResult(buy_box_price=cp, price_source='competitive')
                else:
                    cur.buy_box_price = cp
                    cur.price_source  = 'competitive'

    # Jede angefragte ASIN MUSS im Dict stehen (Aufrufer-Fallback braucht das).
    for a in uniq:
        results.setdefault(a, OffersResult(error='keine Batch-Antwort'))
    return results


def get_item_price(
    asin: str,
    credentials: Optional[dict] = None,
    marketplace: str = 'DE',
) -> Optional[float]:
    """
    Buy-Box-Preis oder niedrigster Neupreis fuer eine ASIN.
    Vereinfachter Aufruf fuer EAN2JTL (amazon_price-Feld).
    Gibt None zurueck wenn kein Preis verfuegbar oder bei Fehler.

    credentials: SP-API-Creds dict oder None (dann Auto-Load via _credentials.py).
    """
    result = get_offers(asin, credentials, marketplace)
    return result.best_price


# ══════════════════════════════════════════════════════════════════════════════
# Interner Fallback
# ══════════════════════════════════════════════════════════════════════════════

def _get_competitive_price(
    asin: str, credentials: dict, mktpl, mktpl_id: str,
) -> Optional[float]:
    """
    Fallback-Preis via CompetitivePricing API (Einzel-ASIN).
    Typisch fuer Amazon-Vendor-Artikel ohne Marktplatz-Seller.
    CompetitivePriceId='1' = Neu-Zustand Buy Box (inkl. Amazon Retail).
    """
    return _get_competitive_prices([asin], credentials, mktpl, mktpl_id).get(asin)


def _get_competitive_prices(
    asins: list[str], credentials: dict, mktpl, mktpl_id: str,
) -> dict[str, Optional[float]]:
    """
    Bulk-Fallback-Preise via CompetitivePricing API (get_competitive_pricing_
    for_asins, bis 20 ASINs/Call). Liefert {asin: price|None}.
    CompetitivePriceId='1' = Neu-Zustand Buy Box (inkl. Amazon Retail).
    """
    out: dict[str, Optional[float]] = {}
    uniq = list(dict.fromkeys(a for a in asins if a))
    for start in range(0, len(uniq), 20):
        chunk = uniq[start:start + 20]
        try:
            pricing_limiter.wait()
            api  = ProductsV0(credentials=credentials, marketplace=mktpl)
            resp = api.get_competitive_pricing_for_asins(
                asin_list=list(chunk),
                MarketplaceId=mktpl_id,
            )
            for entry in (resp.payload or []):
                a = (entry.get('ASIN')
                     or entry.get('Product', {}).get('Identifiers', {})
                              .get('MarketplaceASIN', {}).get('ASIN'))
                if not a or entry.get('status') != 'Success':
                    continue
                comp_prices = (
                    entry.get('Product', {})
                         .get('CompetitivePricing', {})
                         .get('CompetitivePrices', [])
                )
                for cp in comp_prices:
                    if str(cp.get('CompetitivePriceId')) == '1':
                        amount = cp.get('Price', {}).get('ListingPrice', {}).get('Amount')
                        if amount is not None:
                            out[a] = float(amount)
                        break
        except Exception:                                     # noqa: BLE001
            continue
    return out
