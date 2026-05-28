#!/usr/bin/env python3
"""
pipelines.arbitrage  v0.1.0
============================
Gemeinsamer Arbitrage-Flow: EAN/ASIN + Einkaufspreis → Amazon-Angebot (SP-API) +
eBay-Markt → Profitabilitaet je Plattform (reseller_profitability).

Verdrahtet die TechNDev-Provider zu EINER Funktion, die Consumer (z.B.
product-catalog, von MarginPilot per CLI angesteuert) aufrufen. Diese Funktion
macht KEINE DB-Zugriffe und KEIN Caching — beides liegt im Consumer.

Eingabe-Konvention:
  ek_netto wird bereits NORMALISIERT erwartet (Brutto/Netto-Umrechnung und ggf.
  Inbound-Versand erledigt der Consumer). vk_brutto-Werte von Amazon/eBay sind
  Brutto (inkl. MwSt), wie von den Plattformen geliefert.

Referral-Behandlung (wichtig):
  estimate_fba_fees() liefert die GESAMTgebuehr (inkl. Referral). qualify_amazon_fba
  berechnet die Referral aber selbst aus referral_pct. Um Doppelzaehlung zu
  vermeiden, wird die Referral aus get_fees_breakdown() herausgerechnet:
    fba_fee_netto = total - referral_fee     (alle Nicht-Referral-Gebuehren)
    referral_pct  = referral_fee / vk_netto  (reproduziert die echte Referral exakt)

Import-Pattern (Consumer mit Git-Submodul providers/ + profitability/):
  import sys as _sys
  from pathlib import Path as _Path
  for _sub in ('providers', 'profitability'):
      _p = _Path(__file__).resolve().parent / _sub
      if str(_p) not in _sys.path:
          _sys.path.insert(0, str(_p))
  from pipelines.arbitrage import evaluate_arbitrage

CHANGELOG
---------
v0.1.0  (2026-05-28)
  - Initiales Release.
  - evaluate_arbitrage(): EAN/ASIN + ek_netto → ArbitrageResult.
  - ArbitrageResult: asin, amazon_offer, ebay_snapshot, results, errors.
  - Referral aus get_fees_breakdown() abgeleitet (kein Doppelzaehlen),
    Fallback ueber estimate_fba_fees() + get_referral_pct(category).
  - Graceful Degradation: Einzel-Provider-Ausfaelle landen in errors, brechen
    den Gesamtlauf nicht ab.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from amazon_sp import (
    search_by_ean, search_by_asin,
    get_offers, get_fees_breakdown, estimate_fba_fees, get_last_fee_error,
    check_restrictions,
)
from ebay import get_market_snapshot

from reseller_profitability import qualify_all, get_referral_pct, PlatformResult

__version__ = "0.1.0"


# ══════════════════════════════════════════════════════════════════════════════
# Datenmodell
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ArbitrageResult:
    """
    Ergebnis von evaluate_arbitrage().

    ean / asin:       Identifikatoren (mind. einer gesetzt).
    title / category: Katalog-Stammdaten (aus SP-API), leer wenn nicht ermittelbar.
    ek_netto:         Verwendeter Einkaufspreis netto (vom Aufrufer normalisiert).
    amazon_offer:     SP-API-Angebotsdaten (None wenn nicht gelistet / Fehler).
    ebay_snapshot:    eBay-Marktdaten (None wenn eBay uebersprungen / Fehler).
    results:          dict[platform, PlatformResult] aus qualify_all().
    errors:           Nicht-fatale Fehlermeldungen einzelner Schritte.
    """
    ean:           Optional[str] = None
    asin:          Optional[str] = None
    title:         str = ''
    category:      str = ''
    ek_netto:      float = 0.0
    amazon_offer:  Optional[dict] = None
    ebay_snapshot: Optional[dict] = None
    results:       dict[str, PlatformResult] = field(default_factory=dict)
    errors:        list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        # asdict() rekursiert in die PlatformResult-Dataclasses im results-dict.
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Oeffentliche API
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_arbitrage(
    *,
    ek_netto:           float,
    ean:                Optional[str] = None,
    asin:               Optional[str] = None,
    amazon_credentials: dict,
    seller_id:          str = '',
    ebay_credentials:   Optional[dict] = None,
    profile:            str = 'standard',
    marketplace:        str = 'DE',
    ebay_marketplace:   str = 'EBAY_DE',
    include_ebay:       bool = True,
    mwst_rate:          float = 0.19,
    ebay_shipping_cost: float = 6.0,
) -> ArbitrageResult:
    """
    Bewertet den Wiederverkauf eines Produkts auf Amazon (FBA) und eBay.

    Pflicht: ek_netto und mindestens einer von ean/asin sowie amazon_credentials.

    ek_netto:            Einkaufspreis netto (bereits normalisiert: Brutto/Netto +
                         ggf. Inbound-Versand erledigt der Aufrufer).
    ean / asin:          EAN bevorzugt (liefert Katalogdaten in einem Call). Ist nur
                         asin gesetzt, wird search_by_asin fuer Katalogdaten genutzt.
    amazon_credentials:  SP-API-Creds {refresh_token, lwa_app_id, lwa_client_secret}.
    seller_id:           Eigene Seller-ID fuer Verkaufserlaubnis (leer → uebersprungen).
    ebay_credentials:    {client_id, client_secret, env} — None → eBay uebersprungen.
    profile:             reseller_profitability-Profil ('standard'|'eol_lego'|'high_margin').
    ebay_shipping_cost:  Versandkosten-Basis fuer die eBay-Gebuehrenrechnung (Default 6 €).

    Rueckgabe: ArbitrageResult.
    """
    if not (ean or asin):
        raise ValueError("Mindestens 'ean' oder 'asin' muss gesetzt sein.")

    res = ArbitrageResult(ean=ean, asin=asin, ek_netto=round(ek_netto, 2))

    # ── 1. Katalog: ASIN + Stammdaten (BSR, Kategorie, Rating) auflösen ──────────
    category     = ''
    bsr          = None
    rating       = None
    review_count = 0
    try:
        if ean:
            cat = search_by_ean(ean, amazon_credentials, marketplace)
        else:
            cat = search_by_asin(asin, amazon_credentials, marketplace)
        if cat.error:
            res.errors.append(f"catalog: {cat.error}")
        if cat.asin:
            res.asin = asin = cat.asin
        res.title    = cat.title
        res.category = category = cat.category
        bsr          = cat.bsr
        rating       = cat.rating
        review_count = cat.review_count
    except Exception as e:                                   # noqa: BLE001
        res.errors.append(f"catalog: {type(e).__name__}: {e}")

    # ── 2. Amazon-Angebot + Gebühren (nur wenn ASIN bekannt) ─────────────────────
    amazon_fba_input: Optional[dict] = None
    if asin:
        try:
            offers = get_offers(asin, amazon_credentials, marketplace)
            if offers.error:
                res.errors.append(f"offers: {offers.error}")

            # VK = Buy-Box-Preis, Fallback niedrigster Neupreis (amz-einkauf-Standard).
            buy_box       = offers.best_price
            fba_fee_netto = None
            referral_pct  = get_referral_pct(category)

            # Herkunft des Preises kennzeichnen (fuer die Anzeige).
            if offers.buy_box_price is not None:
                price_source = 'buy_box'
            elif offers.lowest_new_price is not None:
                price_source = 'lowest_new'
            else:
                price_source = offers.price_source or ''

            if buy_box is not None:
                vk_netto = buy_box / (1 + mwst_rate)
                # Referral aus Breakdown herausrechnen (kein Doppelzählen).
                breakdown = get_fees_breakdown(
                    asin, buy_box, amazon_credentials, marketplace,
                    ek_price=res.ek_netto, mwst_pct=mwst_rate * 100,
                )
                if breakdown is not None:
                    referral_fee  = breakdown.get('referral_fee', 0.0)
                    total_fee     = breakdown.get('total', 0.0)
                    fba_fee_netto = round(total_fee - referral_fee, 2)
                    if vk_netto > 0 and referral_fee > 0:
                        referral_pct = round(referral_fee / vk_netto, 4)
                else:
                    fee_err = get_last_fee_error()
                    if fee_err:
                        res.errors.append(f"fees_breakdown: {fee_err}")
                    # Fallback: Gesamtgebühr schätzen, Referral per Kategorie abziehen.
                    total_fee = estimate_fba_fees(asin, buy_box, amazon_credentials, marketplace)
                    if total_fee is not None:
                        fba_fee_netto = round(max(0.0, total_fee - vk_netto * referral_pct), 2)
                    else:
                        err = get_last_fee_error()
                        res.errors.append(f"fees: {err or 'keine Gebührenschätzung'}")

            # Verkaufserlaubnis (nur wenn seller_id konfiguriert).
            selling_allowed = None
            if seller_id:
                selling_allowed = check_restrictions(asin, seller_id, amazon_credentials, marketplace)

            res.amazon_offer = {
                'asin':              asin,
                'buy_box_brutto':    buy_box,
                'fba_fee_netto':     fba_fee_netto,
                'referral_pct':      referral_pct,
                'bsr':               bsr,
                'rating':            rating,
                'review_count':      review_count,
                'fba_sellers':       offers.fba_sellers_new,
                'total_sellers':     offers.total_sellers_new,
                'selling_allowed':   selling_allowed,
                'category':          category,
                'amazon_on_listing': offers.amazon_on_listing,
                'buy_box_dominant':  offers.buy_box_dominant,
                'price_source':      price_source,
            }

            if buy_box is not None:
                amazon_fba_input = {
                    'vk_brutto':         buy_box,
                    'fba_fee_netto':     fba_fee_netto if fba_fee_netto is not None else 0.0,
                    'referral_pct':      referral_pct,
                    'bsr':               bsr,
                    'category':          category,
                    'rating':            rating,
                    'review_count':      review_count,
                    'fba_sellers':       offers.fba_sellers_new,
                    'total_sellers':     offers.total_sellers_new,
                    'selling_allowed':   selling_allowed,
                    'amazon_on_listing': offers.amazon_on_listing,
                    'buy_box_dominant':  offers.buy_box_dominant,
                }
        except Exception as e:                               # noqa: BLE001
            res.errors.append(f"amazon: {type(e).__name__}: {e}")

    # ── 3. eBay-Markt (optional) ─────────────────────────────────────────────────
    ebay_input: Optional[dict] = None
    if include_ebay and ebay_credentials:
        query = ean or res.title
        if query:
            try:
                snap       = get_market_snapshot(query, ebay_credentials, ebay_marketplace)
                sold       = snap.sold
                active     = snap.active
                ebay_price = sold.best_price if sold.best_price is not None else active.best_price

                res.ebay_snapshot = {
                    'query':             query,
                    'median_sold':       sold.median_price,
                    'best_price':        ebay_price,
                    'sold_count':        sold.count,
                    'sold_total':        sold.total,
                    'active_total':      active.total,
                    'sell_through_rate': snap.sell_through_rate,
                }
                if not snap.ok():
                    res.errors.append(
                        f"ebay: sold={sold.error or '-'} active={active.error or '-'}"
                    )

                if ebay_price is not None:
                    ebay_input = {
                        'item_price':    ebay_price,
                        'shipping_cost': ebay_shipping_cost,
                        'sold_count':    sold.count,
                    }
            except Exception as e:                           # noqa: BLE001
                res.errors.append(f"ebay: {type(e).__name__}: {e}")

    # ── 4. Profitabilität je Plattform ───────────────────────────────────────────
    if amazon_fba_input or ebay_input:
        try:
            res.results = qualify_all(
                ek_netto   = res.ek_netto,
                profile    = profile,
                mwst_rate  = mwst_rate,
                amazon_fba = amazon_fba_input,
                ebay       = ebay_input,
            )
        except Exception as e:                               # noqa: BLE001
            res.errors.append(f"qualify: {type(e).__name__}: {e}")

    return res
