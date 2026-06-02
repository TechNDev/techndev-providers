#!/usr/bin/env python3
"""
pipelines.arbitrage  v0.7.0
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
v0.8.0  (2026-06-02)
  - eBay-Ausgangsversand gewichts-/massbasiert via techndev-tools/shipping.py
    (optionaler Sibling-Import). _ebay_shipping_for() ersetzt die ebay_shipping_cost-
    Pauschale durch den guenstigsten passenden DHL-/Brief-Tarif anhand der SP-API-
    Masse; ohne Masse parcel_only, ohne Gewicht/Lib Fallback auf die Pauschale.
    Rueckwaertskompatibel: ebay_shipping_cost bleibt der Fallback-Default.

v0.7.0  (2026-05-30)
  - amazon_credentials-Parameter optional (Default None): ruft amazon_sp.configure()
    intern auf wenn uebergeben, sonst nutzt der Provider seinen eigenen Cache /
    Auto-Discovery. Consumer muessen keine Credentials mehr explizit uebergeben.
    Rueckwaertskompatibel: bestehende Aufrufe mit Credentials-Dict unveraendert.

v0.6.0  (2026-05-29)
  - ArbitrageResult: brand, short_desc, long_desc, weight_kg, height_cm,
    length_cm, width_cm — aus CatalogResult durchgereicht. Ermoeglicht
    vollstaendigen JTL-CSV-Export ohne Icecat.

v0.5.0  (2026-05-29)
  - amazon_offer.fee_breakdown: granulare Gebuehren-Aufschluesselung (fba_fee,
    variable_closing_fee, storage_fee_monthly, prep_fee, inbound_fee) wird
    durchgereicht. Ermoeglicht Kalkulator-Webapp die exakte Befuellung
    einzelner Eingabefelder statt gebundelter fba_fee_netto.

v0.4.0  (2026-05-29)
  - Variations: ArbitrageResult.parent_asins/child_asins aus dem Katalog.
  - amazon_offer.offers_detail: aktueller Angebots-Snapshot fuer Buy-Box-Tracking.

v0.3.0  (2026-05-29)
  - FBA-Marge realistischer: Verkaeufer-Nebenkosten (Storage/Prep/Inbound) ueber
    total_all_in eingerechnet statt verworfen (vorher zu optimistisch). EU-Loop
    nutzt ebenfalls total_all_in.
  - amazon_offer.estimated_payout: Amazon-Auszahlung (VK abzgl. Amazon-Gebuehren
    brutto), analog SellerAmp "Estimated Amz. Payout".

v0.2.1  (2026-05-29)
  - EU-Vergleich: Heimatmarkt nicht mehr uebersprungen — steht jetzt als
    Basiszeile in eu_markets (DE in DEFAULT_EU_MARKETPLACES aufgenommen).

v0.2.0  (2026-05-28)
  - EU-Markt-Vergleich: eu_marketplaces + fx_to_eur Parameter. Je Markt
    (UK/FR/ES/IT) Rank + Buy-Box + Gebuehren (laenderspezifische MwSt) -> Preis,
    Profit und ROI in EUR (GBP via fx_to_eur umgerechnet). ArbitrageResult.eu_markets.
  - ArbitrageResult.image: Hauptbild-URL aus dem Katalog (SP-API).

v0.1.2  (2026-05-28)
  - eBay: Fallback auf Produkttitel auch bei 0 Ergebnissen (nicht nur Bot-Challenge).

v0.1.1  (2026-05-28)
  - eBay: Fallback auf Produkttitel wenn EAN-Suche Bot-Challenge ausloest.

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
    configure,
    search_by_ean, search_by_asin,
    get_offers, get_fees_breakdown, estimate_fba_fees, get_last_fee_error,
    check_restrictions,
)
from ebay import get_market_snapshot

from reseller_profitability import qualify_all, get_referral_pct, PlatformResult

# Optional: gewichts-/massbasierte Versandkosten (techndev-tools/shipping.py).
# Sibling-Repo, kein hartes Dependency — fehlt es, faellt der Code auf die
# uebergebene Pauschale (ebay_shipping_cost) zurueck.
_calc_shipping = None
try:
    import sys as _sys
    from pathlib import Path as _Path
    _here = _Path(__file__).resolve()
    for _cand in (
        _here.parents[1] / "tools",            # providers/tools (falls Submodul)
        _here.parents[2] / "tools",            # product-catalog/tools
        _here.parents[3] / "techndev-tools",   # Sibling auf Code-Ebene (Standard)
        _here.parents[3] / "midas-bot" / "tools",
    ):
        if _cand.exists() and str(_cand) not in _sys.path:
            _sys.path.insert(0, str(_cand))
    from shipping import calc_shipping as _calc_shipping
except Exception:                                                            # noqa: BLE001
    _calc_shipping = None

__version__ = "0.8.0"


def _ebay_shipping_for(res, fallback: float) -> float:
    """Gewichts-/massbasierte eBay-Versandkosten (brutto), sonst Pauschale.

    Nutzt techndev-tools/shipping.calc_shipping mit den SP-API-Massen aus res.
    parcel_only=True: bei eBay-Warenversand keine Brief-Produkte waehlen.
    Fehlen Gewicht/Lib oder passt kein Tarif -> fallback (uebergebene Pauschale).
    """
    if _calc_shipping is None or not res.weight_kg:
        return fallback
    has_dims = bool(res.length_cm and res.width_cm and res.height_cm)
    try:
        q = _calc_shipping(
            res.weight_kg * 1000.0,
            res.length_cm or None, res.width_cm or None, res.height_cm or None,
            # Ohne Masse keine (unrealistisch billigen) Brief-Produkte waehlen;
            # mit Massen darf der guenstigste passende Tarif gewinnen.
            parcel_only=not has_dims,
        )
        return q.price_eur if q else fallback
    except Exception:                                                        # noqa: BLE001
        return fallback

# ── Marktplatz-Stammdaten fuer den EU-Vergleich ───────────────────────────────
# MwSt-Regelsatz und Waehrung je Amazon-EU-Marktplatz (Stand 2026).
_MARKET_VAT: dict[str, float] = {
    'DE': 0.19, 'FR': 0.20, 'ES': 0.21, 'IT': 0.22, 'UK': 0.20,
}
_MARKET_CURRENCY: dict[str, str] = {
    'DE': 'EUR', 'FR': 'EUR', 'ES': 'EUR', 'IT': 'EUR', 'UK': 'GBP',
}
# Standard-EU-Vergleichsmaerkte inkl. DE als Basiszeile (konsistente Methode ueber
# alle Maerkte; der Hauptlauf bewertet DE zusaetzlich detailliert mit Gates/Profil).
DEFAULT_EU_MARKETPLACES = ['DE', 'UK', 'FR', 'ES', 'IT']


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
    image:         str = ''
    brand:         str = ''
    short_desc:    str = ''
    long_desc:     str = ''
    weight_kg:     float = 0.0
    height_cm:     float = 0.0
    length_cm:     float = 0.0
    width_cm:      float = 0.0
    parent_asins:  list[str] = field(default_factory=list)
    child_asins:   list[str] = field(default_factory=list)
    ek_netto:      float = 0.0
    amazon_offer:  Optional[dict] = None
    ebay_snapshot: Optional[dict] = None
    eu_markets:    list[dict] = field(default_factory=list)
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
    amazon_credentials: Optional[dict] = None,
    seller_id:          str = '',
    ebay_credentials:   Optional[dict] = None,
    profile:            str = 'standard',
    marketplace:        str = 'DE',
    ebay_marketplace:   str = 'EBAY_DE',
    include_ebay:       bool = True,
    mwst_rate:          float = 0.19,
    ebay_shipping_cost: float = 6.0,
    eu_marketplaces:    Optional[list[str]] = None,
    fx_to_eur:          Optional[dict[str, float]] = None,
) -> ArbitrageResult:
    """
    Bewertet den Wiederverkauf eines Produkts auf Amazon (FBA) und eBay.

    Pflicht: ek_netto und mindestens einer von ean/asin.

    ek_netto:            Einkaufspreis netto (bereits normalisiert: Brutto/Netto +
                         ggf. Inbound-Versand erledigt der Aufrufer).
    ean / asin:          EAN bevorzugt (liefert Katalogdaten in einem Call). Ist nur
                         asin gesetzt, wird search_by_asin fuer Katalogdaten genutzt.
    amazon_credentials:  SP-API-Creds (optional). None → amazon_sp.configure()-Cache
                         oder Auto-Discovery (AMZ_EINKAUF_CONFIG / Sibling-Pfad).
                         Wenn uebergeben: wird intern via configure() gesetzt.
    seller_id:           Eigene Seller-ID fuer Verkaufserlaubnis (leer → uebersprungen).
    ebay_credentials:    {client_id, client_secret, env} — None → eBay uebersprungen.
    profile:             reseller_profitability-Profil ('standard'|'eol_lego'|'high_margin').
    ebay_shipping_cost:  Versandkosten-Basis fuer die eBay-Gebuehrenrechnung (Default 6 EUR).
    eu_marketplaces:     Liste weiterer Amazon-EU-Maerkte fuer den Vergleich (z.B.
                         ['UK','FR','ES','IT']). None/[] → kein EU-Vergleich.
    fx_to_eur:           Wechselkurse {Waehrung: EUR-Wert je 1 Einheit}, z.B.
                         {'GBP': 1.1538}. EUR braucht keinen Eintrag. Fehlt ein
                         benoetigter Kurs, wird der Markt ohne Profit gelistet.

    Rueckgabe: ArbitrageResult.
    """
    if not (ean or asin):
        raise ValueError("Mindestens 'ean' oder 'asin' muss gesetzt sein.")

    # Credentials einmalig fuer diese Session konfigurieren (falls uebergeben).
    # Ist amazon_credentials=None, nutzt amazon_sp seinen eigenen Cache / Auto-Discovery.
    if amazon_credentials is not None:
        configure(amazon_credentials)

    res = ArbitrageResult(ean=ean, asin=asin, ek_netto=round(ek_netto, 2))

    # ── 1. Katalog: ASIN + Stammdaten (BSR, Kategorie, Rating) auflösen ──────────
    category     = ''
    bsr          = None
    rating       = None
    review_count = 0
    try:
        if ean:
            cat = search_by_ean(ean, marketplace=marketplace)
        else:
            cat = search_by_asin(asin, marketplace=marketplace)
        if cat.error:
            res.errors.append(f"catalog: {cat.error}")
        if cat.asin:
            res.asin = asin = cat.asin
        res.title        = cat.title
        res.category     = category = cat.category
        res.image        = cat.main_image
        res.brand        = cat.brand
        res.short_desc   = cat.short_desc
        res.long_desc    = cat.long_desc
        res.weight_kg    = cat.weight_kg
        res.height_cm    = cat.height_cm
        res.length_cm    = cat.length_cm
        res.width_cm     = cat.width_cm
        res.parent_asins = cat.parent_asins
        res.child_asins  = cat.child_asins
        bsr          = cat.bsr
        rating       = cat.rating
        review_count = cat.review_count
    except Exception as e:                                   # noqa: BLE001
        res.errors.append(f"catalog: {type(e).__name__}: {e}")

    # ── 2. Amazon-Angebot + Gebühren (nur wenn ASIN bekannt) ─────────────────────
    amazon_fba_input: Optional[dict] = None
    if asin:
        try:
            offers = get_offers(asin, marketplace=marketplace)
            if offers.error:
                res.errors.append(f"offers: {offers.error}")

            # VK = Buy-Box-Preis, Fallback niedrigster Neupreis (amz-einkauf-Standard).
            buy_box          = offers.best_price
            fba_fee_netto    = None
            estimated_payout = None
            referral_pct     = get_referral_pct(category)

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
                    asin, buy_box,
                    marketplace=marketplace,
                    ek_price=res.ek_netto, mwst_pct=mwst_rate * 100,
                )
                if breakdown is not None:
                    referral_fee  = breakdown.get('referral_fee', 0.0)
                    # total_all_in = Amazon-Gebuehren (netto) + Verkaeufer-Nebenkosten
                    # (Storage/Prep/Inbound). Ohne diese waere die Marge zu optimistisch.
                    total_all_in  = breakdown.get('total_all_in', breakdown.get('total', 0.0))
                    fba_fee_netto = round(total_all_in - referral_fee, 2)
                    # Amazon-Auszahlung: VK abzgl. reiner Amazon-Gebuehren brutto
                    # (Referral+FBA+Closing inkl. MwSt) — Nebenkosten zahlt der Haendler selbst.
                    amazon_fees_net  = breakdown.get('total', 0.0)
                    estimated_payout = round(buy_box - amazon_fees_net * (1 + mwst_rate), 2)
                    if vk_netto > 0 and referral_fee > 0:
                        referral_pct = round(referral_fee / vk_netto, 4)
                else:
                    fee_err = get_last_fee_error()
                    if fee_err:
                        res.errors.append(f"fees_breakdown: {fee_err}")
                    # Fallback: Gesamtgebühr schätzen, Referral per Kategorie abziehen.
                    total_fee = estimate_fba_fees(asin, buy_box, marketplace=marketplace)
                    if total_fee is not None:
                        fba_fee_netto = round(max(0.0, total_fee - vk_netto * referral_pct), 2)
                    else:
                        err = get_last_fee_error()
                        res.errors.append(f"fees: {err or 'keine Gebührenschätzung'}")

            # Verkaufserlaubnis (nur wenn seller_id konfiguriert).
            selling_allowed = None
            if seller_id:
                selling_allowed = check_restrictions(asin, seller_id, marketplace=marketplace)

            # Granulare Gebuehrer-Aufschluesselung (ohne details/error-Rohdaten).
            _fee_bd = (
                {k: v for k, v in breakdown.items() if k not in ('details', 'error')}
                if breakdown is not None else None
            )
            res.amazon_offer = {
                'asin':              asin,
                'buy_box_brutto':    buy_box,
                'fba_fee_netto':     fba_fee_netto,
                'estimated_payout':  estimated_payout,
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
                'offers_detail':     offers.offers_detail,
                'fee_breakdown':     _fee_bd,
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
                snap = get_market_snapshot(query, ebay_credentials, ebay_marketplace)

                # EAN-Suche liefert manchmal 0 Treffer oder eine Bot-Challenge.
                # Fallback: Produkttitel verwenden wenn EAN keine Ergebnisse bringt.
                _ean_empty = (
                    not snap.ok()
                    or (snap.sold.count == 0 and snap.active.total == 0)
                )
                if _ean_empty and query == ean and res.title and res.title != ean:
                    snap  = get_market_snapshot(res.title, ebay_credentials, ebay_marketplace)
                    query = res.title

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
                    # Versand gewichts-/massbasiert (SP-API-Masse), sonst Pauschale.
                    ship = _ebay_shipping_for(res, ebay_shipping_cost)
                    ebay_input = {
                        'item_price':    ebay_price,
                        'shipping_cost': ship,
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

    # ── 5. EU-Markt-Vergleich (optional) ─────────────────────────────────────────
    if asin and eu_marketplaces:
        res.eu_markets = _evaluate_eu_markets(
            asin         = asin,
            ek_netto     = res.ek_netto,
            marketplaces = eu_marketplaces,
            fx_to_eur    = fx_to_eur or {},
            errors       = res.errors,
        )

    return res


# ══════════════════════════════════════════════════════════════════════════════
# EU-Markt-Vergleich
# ══════════════════════════════════════════════════════════════════════════════

def _evaluate_eu_markets(
    *,
    asin:         str,
    ek_netto:     float,
    marketplaces: list[str],
    fx_to_eur:    dict[str, float],
    errors:       list[str],
) -> list[dict]:
    """
    Bewertet dieselbe ASIN auf den uebergebenen Amazon-Maerkten (inkl. DE, wenn
    gelistet — dient als Basiszeile fuer den Vergleich).

    Je Markt: Rank (Katalog) + Buy-Box (Offers) + Amazon-Gebuehren (Fees, mit
    laenderspezifischem MwSt-Satz). Preis und Profit werden ueber fx_to_eur nach
    EUR umgerechnet; der EK (bereits EUR netto) ist marktuebergreifend gleich.

    Einzel-Marktfehler landen in 'errors' und brechen den Lauf nicht ab.
    """
    out: list[dict] = []

    for code in marketplaces:
        mp = code.upper()

        vat      = _MARKET_VAT.get(mp, 0.20)
        currency = _MARKET_CURRENCY.get(mp, 'EUR')
        fx       = 1.0 if currency == 'EUR' else fx_to_eur.get(currency)

        entry: dict = {
            'marketplace':        mp,
            'currency':           currency,
            'fx_to_eur':          fx,
            'bsr':                None,
            'price_local_brutto': None,
            'price_eur_brutto':   None,
            'fees_netto_eur':     None,
            'profit_eur':         None,
            'roi':                None,
            'amazon_on_listing':  False,
            'price_source':       '',
            'note':               '',
        }

        try:
            # Rank (best effort — Fehler hier ist nicht fatal).
            try:
                cat = search_by_asin(asin, marketplace=mp)
                if cat.ok():
                    entry['bsr'] = cat.bsr
            except Exception as e:                           # noqa: BLE001
                errors.append(f"eu:{mp}:catalog: {type(e).__name__}: {e}")

            offers = get_offers(asin, marketplace=mp)
            if offers.error:
                errors.append(f"eu:{mp}:offers: {offers.error}")
            entry['amazon_on_listing'] = offers.amazon_on_listing
            price_local = offers.best_price
            entry['price_local_brutto'] = price_local
            entry['price_source'] = (
                'buy_box'    if offers.buy_box_price is not None else
                'lowest_new' if offers.lowest_new_price is not None else
                offers.price_source or ''
            )

            if price_local is None:
                entry['note'] = 'kein Preis verfuegbar'
                out.append(entry)
                continue

            # Waehrung nicht umrechenbar → Markt ohne Profit listen.
            if fx is None:
                entry['note'] = f'kein Wechselkurs fuer {currency}'
                out.append(entry)
                continue

            entry['price_eur_brutto'] = round(price_local * fx, 2)

            breakdown = get_fees_breakdown(
                asin, price_local, marketplace=mp, mwst_pct=vat * 100,
            )
            if breakdown is None:
                fee_err = get_last_fee_error()
                if fee_err:
                    errors.append(f"eu:{mp}:fees: {fee_err}")
                entry['note'] = 'keine Gebuehrenschaetzung'
                out.append(entry)
                continue

            fees_local_netto = breakdown.get('total_all_in', breakdown.get('total', 0.0))
            vk_local_netto   = price_local / (1 + vat)
            profit_eur       = round((vk_local_netto - fees_local_netto) * fx - ek_netto, 2)

            entry['fees_netto_eur'] = round(fees_local_netto * fx, 2)
            entry['profit_eur']     = profit_eur
            entry['roi']            = round(profit_eur / ek_netto, 4) if ek_netto else None

        except Exception as e:                               # noqa: BLE001
            errors.append(f"eu:{mp}: {type(e).__name__}: {e}")
            if not entry['note']:
                entry['note'] = 'Fehler beim Abruf'

        out.append(entry)

    return out
