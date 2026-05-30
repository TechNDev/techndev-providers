#!/usr/bin/env python3
"""
amazon_sp  pricing.py  v1.2.0
================================
Angebote, Buy-Box-Preis und Wettbewerbspreise via ProductsV0 API.

Merges beider bisheriger Implementierungen:
  amz-einkauf: data_collector._add_offers / _add_competitive_price
  EAN2JTL:     AmazonClient._fetch_price (Buy-Box / niedrigster Neupreis)

CHANGELOG
---------
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

from dataclasses import dataclass, field
from typing import Optional

from sp_api.api import ProductsV0

from ._rate import _retry, pricing_limiter
from ._credentials import get_credentials
from ._helpers import get_marketplace, get_marketplace_id, get_amazon_seller_id

__version__ = "1.2.0"


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
        api     = ProductsV0(credentials=credentials, marketplace=mktpl)
        resp    = api.get_item_offers(asin=asin, item_condition='New')
        payload = resp.payload or {}
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

        # ── Buy-Box-Preis (Neu-Zustand) ───────────────────────────────────────
        buy_box_price = None
        for bb in summary.get('BuyBoxPrices', []):
            if str(bb.get('condition', '')).lower() == 'new':
                lp  = bb.get('LandedPrice') or bb.get('ListingPrice') or {}
                amt = lp.get('Amount')
                if amt is not None:
                    buy_box_price = float(amt)
                    break

        # ── Niedrigster Neupreis (EAN2JTL Fallback fuer amazon_price) ─────────
        lowest_new_price = None
        for lp in summary.get('LowestPrices', []):
            if lp.get('condition', '').lower() in ('new', 'neu'):
                node = lp.get('LandedPrice') or lp.get('ListingPrice') or {}
                amt  = node.get('Amount')
                if amt is not None:
                    lowest_new_price = float(amt)
                    break

        # ── Buy-Box-Dominanz & Amazon-Praesenz ────────────────────────────────
        amazon_on_listing = any(o.get('SellerId') == amazon_seller for o in offers)
        winner_count      = sum(1 for o in offers if o.get('IsBuyBoxWinner'))
        buy_box_dominant  = winner_count == 1 and total_new >= 3

        # ── Angebots-Snapshot (Seller, Landed-Preis, FBA/FBM, Buy-Box, Feedback) ──
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

        # ── CompetitivePricing-Fallback wenn kein Buy-Box-Preis ───────────────
        source = 'offers'
        if buy_box_price is None:
            cp = _get_competitive_price(asin, credentials, mktpl, mktpl_id)
            if cp is not None:
                buy_box_price = cp
                source = 'competitive'

        return OffersResult(
            total_sellers_new=total_new,
            fba_sellers_new=fba_new,
            buy_box_price=buy_box_price,
            lowest_new_price=lowest_new_price,
            amazon_on_listing=amazon_on_listing,
            buy_box_dominant=buy_box_dominant,
            price_source=source,
            offers_detail=offers_detail,
        )

    except Exception as e:
        if '429' in str(e) or 'throttl' in str(e).lower():
            raise
        return OffersResult(error=str(e))


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
    Fallback-Preis via CompetitivePricing API.
    Typisch fuer Amazon-Vendor-Artikel ohne Marktplatz-Seller.
    CompetitivePriceId='1' = Neu-Zustand Buy Box (inkl. Amazon Retail).
    """
    try:
        pricing_limiter.wait()
        api  = ProductsV0(credentials=credentials, marketplace=mktpl)
        resp = api.get_competitive_pricing_for_asins(
            asin_list=[asin],
            MarketplaceId=mktpl_id,
        )
        for entry in (resp.payload or []):
            if entry.get('status') != 'Success':
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
                        return float(amount)
    except Exception:
        pass
    return None
