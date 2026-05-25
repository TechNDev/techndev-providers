#!/usr/bin/env python3
"""
amazon_sp  catalog.py  v1.0.0
================================
EAN / ASIN -> Produktdaten via Amazon CatalogItems API v2022-04-01.

Merges beider bisheriger Implementierungen:
  EAN2JTL:     AmazonClient.fetch_by_ean/fetch_by_asin
               (Bilder, Abmessungen, Bullet-Points, MPN, Klassifikation)
  amz-einkauf: ean_lookup.lookup_ean
               (GTIN-14/UPC-Fallback, Bewertung/Review-Count, BSR-Ranks)

Suchreihenfolge bei EAN-Suche: EAN-13 -> GTIN-14 (EAN + fuehrende '0') -> UPC.
Amazon indiziert Bundles/Multipacks je nach Produkt nur unter einem dieser Typen.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - Initiales Release
  - CatalogResult: vereinheitlichtes Datenmodell mit allen Feldern beider Tools
  - search_by_ean(): EAN-Suche mit GTIN-14/UPC-Fallback, @_retry
  - search_by_asin(): ASIN-Direktsuche, @_retry
  - _parse_item(): gemeinsamer Parser fuer EAN- und ASIN-Suche
  - BSR-Semantik: bsr/bsr_category = primaer (displayGroup first, class fallback)
                  bsr_display/bsr_display_category = immer displayGroupRank
                  bsr_class_ranks = alle classificationRanks (EAN2JTL-Kompatibilitaet)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from sp_api.api import CatalogItemsV20220401

from ._rate import _retry, catalog_limiter
from ._helpers import (
    get_marketplace, get_marketplace_id,
    collect_amazon_images, _to_cm, _to_kg,
)

__version__ = "1.0.0"

# Attribute-Labels fuer Features (SP-API-Key -> Deutsch)
_ATTR_LABELS: dict[str, str] = {
    'color':           'Farbe',
    'size':            'Groesse',
    'material':        'Material',
    'wattage':         'Leistung',
    'voltage':         'Spannung',
    'number_of_items': 'Anzahl Einheiten',
}

# includedData fuer vollstaendige Produktdaten (Superset beider Tools)
_INCLUDED_ALL = [
    'summaries', 'images', 'attributes',
    'classifications', 'identifiers', 'dimensions', 'salesRanks',
]


# ══════════════════════════════════════════════════════════════════════════════
# Datenmodell
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CatalogResult:
    """
    Vereinheitlichtes Ergebnis-Modell fuer Amazon SP-API Catalog-Abfragen.

    Felder haben sinnvolle Defaults (leerer String / 0 / leere Liste) —
    kein None-Check noetig ausser fuer optionale numerische Felder.
    Nur 'error' signalisiert ob ein Fehler aufgetreten ist (None = kein Fehler).

    BSR-Semantik:
      bsr / bsr_category       = primaerer BSR fuer Scoring:
                                  displayGroupRank zuerst, classificationRank als Fallback.
      bsr_display / ..category = immer der displayGroupRank (Hauptkategorie wie auf PDP).
      bsr_display_ranks        = alle displayGroupRanks [{rank, title}, ...]
      bsr_class_ranks          = alle classificationRanks [{rank, title}, ...]
                                  -> EAN2JTL nutzt diese fuer den spezifischen Kategorie-BSR.

    title / name: identisch — title fuer amz-einkauf, name-Property fuer EAN2JTL.
    """
    # ── Identifikatoren ──────────────────────────────────────────────────────
    ean:  str = ''
    asin: str = ''

    # ── Basisdaten ───────────────────────────────────────────────────────────
    title:  str = ''
    brand:  str = ''
    mpn:    str = ''

    # ── Content ──────────────────────────────────────────────────────────────
    category:      str = ''
    short_desc:    str = ''
    long_desc:     str = ''
    bullet_points: list[str]  = field(default_factory=list)
    features:      list[dict] = field(default_factory=list)  # [{name, value}]

    # ── Medien ───────────────────────────────────────────────────────────────
    main_image: str       = ''
    all_images: list[str] = field(default_factory=list)

    # ── Abmessungen & Gewicht ─────────────────────────────────────────────────
    weight_kg: float = 0.0
    ship_kg:   float = 0.0   # Versandgewicht (aus Paketabmessungen)
    width_cm:  float = 0.0
    height_cm: float = 0.0
    length_cm: float = 0.0

    # ── BSR ──────────────────────────────────────────────────────────────────
    bsr:                  Optional[int] = None
    bsr_category:         str = ''
    bsr_display:          Optional[int] = None
    bsr_display_category: str = ''
    bsr_display_ranks:    list[dict] = field(default_factory=list)
    bsr_class_ranks:      list[dict] = field(default_factory=list)

    # ── Bewertung ────────────────────────────────────────────────────────────
    rating:       Optional[float] = None
    review_count: int = 0

    # ── Fehler ───────────────────────────────────────────────────────────────
    error: Optional[str] = None

    @property
    def name(self) -> str:
        """Alias fuer EAN2JTL-Kompatibilitaet (EAN2JTL nutzt 'name', amz-einkauf 'title')."""
        return self.title

    def ok(self) -> bool:
        """True wenn kein Fehler und ASIN vorhanden."""
        return self.error is None and bool(self.asin)


# ══════════════════════════════════════════════════════════════════════════════
# Oeffentliche API
# ══════════════════════════════════════════════════════════════════════════════

@_retry
def search_by_ean(
    ean: str,
    credentials: dict,
    marketplace: str = 'DE',
) -> CatalogResult:
    """
    EAN -> CatalogResult via Amazon CatalogItems API v2022-04-01.

    Suchreihenfolge: EAN-13 -> GTIN-14 (EAN + fuehrende '0') -> UPC.
    HTTP 429 wird propagiert fuer @_retry. Andere Fehler -> error-Feld.
    """
    mktpl    = get_marketplace(marketplace)
    mktpl_id = mktpl.marketplace_id

    try:
        catalog = CatalogItemsV20220401(credentials=credentials, marketplace=mktpl)
    except Exception as e:
        return CatalogResult(ean=ean, error=f'SP-API-Client-Fehler: {e}')

    gtin14   = ('0' + ean) if len(ean) == 13 else ean
    searches = [('EAN', ean), ('GTIN', gtin14), ('UPC', ean)]

    items      = []
    last_error = None
    for id_type, id_val in searches:
        try:
            catalog_limiter.wait()
            resp  = catalog.search_catalog_items(
                identifiers=[id_val],
                identifiersType=id_type,
                includedData=_INCLUDED_ALL,
                marketplaceIds=[mktpl_id],
            )
            items = (resp.payload or {}).get('items', [])
            if items:
                break
        except Exception as e:
            if '429' in str(e) or 'throttl' in str(e).lower():
                raise                       # @_retry uebernimmt
            last_error = e
            continue                        # anderer Fehler -> naechsten Typ

    if not items:
        msg = f'EAN-Lookup fehlgeschlagen: {last_error}' if last_error else 'Kein Produkt gefunden'
        return CatalogResult(ean=ean, error=msg)

    return _parse_item(items[0], ean=ean, mktpl_id=mktpl_id)


@_retry
def search_by_asin(
    asin: str,
    credentials: dict,
    marketplace: str = 'DE',
) -> CatalogResult:
    """
    ASIN -> CatalogResult via getCatalogItem (Direktsuche ohne EAN-Fallback).
    HTTP 429 wird propagiert fuer @_retry. Andere Fehler -> error-Feld.
    """
    if not asin:
        return CatalogResult(asin=asin, error='Leere ASIN')

    mktpl    = get_marketplace(marketplace)
    mktpl_id = mktpl.marketplace_id

    result = None
    for attempt in range(3):
        try:
            catalog_limiter.wait()
            cat    = CatalogItemsV20220401(credentials=credentials, marketplace=mktpl)
            result = cat.get_catalog_item(
                asin=asin,
                marketplaceIds=[mktpl_id],
                includedData=_INCLUDED_ALL,
            )
            break
        except Exception as exc:
            if '429' in str(exc) or 'throttl' in str(exc).lower():
                if attempt < 2:
                    time.sleep(2 ** attempt * 2)
                    continue
            break

    if result is None:
        return CatalogResult(asin=asin, error='Keine API-Antwort')

    item = result.payload or {}
    if not item:
        return CatalogResult(asin=asin, error='Leere API-Antwort')

    return _parse_item(item, ean='', mktpl_id=mktpl_id)


# ══════════════════════════════════════════════════════════════════════════════
# Interner Parser — gemeinsam fuer search_by_ean und search_by_asin
# ══════════════════════════════════════════════════════════════════════════════

def _parse_item(item: dict, ean: str, mktpl_id: str) -> CatalogResult:
    """Vollstaendiges Parsen eines SP-API CatalogItems-Eintrags."""
    asin      = item.get('asin', '')
    summaries = item.get('summaries') or [{}]
    img_sets  = item.get('images') or []
    attrs     = item.get('attributes') or {}
    clsfs     = item.get('classifications') or []
    dims_list = item.get('dimensions') or []
    sales_rks = item.get('salesRanks') or []

    # ── Name & Brand ──────────────────────────────────────────────────────────
    title, brand = _parse_summaries(summaries, mktpl_id)

    # ── MPN: model_number -> manufacturer_part_number -> part_number ──────────
    def _attr_val(key: str) -> str:
        lst = attrs.get(key) or []
        return lst[0].get('value', '') if lst else ''

    mpn = (_attr_val('model_number') or
           _attr_val('manufacturer_part_number') or
           _attr_val('part_number'))

    # ── Kategorie aus Klassifikations-Hierarchie ──────────────────────────────
    category = ''
    if clsfs:
        last = clsfs[-1]
        if isinstance(last, dict):
            category = last.get('displayName') or last.get('classificationId', '')

    # ── Bullet-Points, Short-Desc, Long-Desc ─────────────────────────────────
    bullet_list   = [b.get('value', '') for b in attrs.get('bullet_point', [])   if b.get('value')]
    special_feats = [f.get('value', '') for f in attrs.get('special_features', []) if f.get('value')]
    short_desc    = bullet_list[0][:255] if bullet_list else ''

    if bullet_list:
        long_desc = '\n'.join(f'• {b}' for b in bullet_list)
        sf_extra  = [f for f in special_feats if f not in bullet_list]
        if sf_extra:
            long_desc += '\n\n' + '\n'.join(f'• {f}' for f in sf_extra)
    elif special_feats:
        long_desc = '\n'.join(f'• {f}' for f in special_feats)
    else:
        long_desc = _attr_val('item_description') or _attr_val('product_description')

    # ── Merkmale: Farbe, Groesse, Material … ─────────────────────────────────
    features = [
        {'name': lbl, 'value': _attr_val(k)}
        for k, lbl in _ATTR_LABELS.items()
        if _attr_val(k)
    ]

    # ── Abmessungen & Gewicht ─────────────────────────────────────────────────
    dim_entry = dims_list[0] if dims_list else {}
    item_dim  = dim_entry.get('item') or {}
    pkg_dim   = dim_entry.get('package') or {}

    def _dim_val(d: dict, key: str) -> tuple:
        node = d.get(key) or {}
        return node.get('value', 0), node.get('unit', '')

    w_v,  w_u  = _dim_val(item_dim, 'weight')
    h_v,  h_u  = _dim_val(item_dim, 'height')
    l_v,  l_u  = _dim_val(item_dim, 'length')
    wi_v, wi_u = _dim_val(item_dim, 'width')
    pw_v, pw_u = _dim_val(pkg_dim,  'weight')

    weight_kg = _to_kg(w_v,  w_u)  if w_v  else 0.0
    height_cm = _to_cm(h_v,  h_u)  if h_v  else 0.0
    length_cm = _to_cm(l_v,  l_u)  if l_v  else 0.0
    width_cm  = _to_cm(wi_v, wi_u) if wi_v else 0.0
    ship_kg   = _to_kg(pw_v, pw_u) if pw_v else weight_kg

    # ── Bilder (dedupliziert, hoechste Aufloesung) ────────────────────────────
    all_images = collect_amazon_images(img_sets)

    # ── BSR ───────────────────────────────────────────────────────────────────
    bsr, bsr_cat, bsr_disp, bsr_disp_cat, disp_ranks, class_ranks = (
        _parse_sales_ranks(sales_rks, mktpl_id)
    )

    # ── Bewertung ────────────────────────────────────────────────────────────
    rating, review_count = _parse_rating(attrs)

    return CatalogResult(
        ean=ean,
        asin=asin,
        title=title,
        brand=brand,
        mpn=mpn,
        category=category,
        short_desc=short_desc,
        long_desc=long_desc,
        bullet_points=bullet_list,
        features=features,
        main_image=all_images[0] if all_images else '',
        all_images=all_images,
        weight_kg=weight_kg,
        ship_kg=ship_kg,
        width_cm=width_cm,
        height_cm=height_cm,
        length_cm=length_cm,
        bsr=bsr,
        bsr_category=bsr_cat,
        bsr_display=bsr_disp,
        bsr_display_category=bsr_disp_cat,
        bsr_display_ranks=disp_ranks,
        bsr_class_ranks=class_ranks,
        rating=rating,
        review_count=review_count,
        error=None,
    )


def _parse_summaries(summaries: list, mktpl_id: str) -> tuple[str, str]:
    """Titel und Brand aus dem marktplatz-spezifischen Summary; Fallback: erster Eintrag."""
    for s in summaries:
        if s.get('marketplaceId') == mktpl_id:
            return (
                s.get('itemName') or s.get('item_name') or '',
                s.get('brandName') or s.get('brand') or '',
            )
    if summaries:
        s = summaries[0]
        return (
            s.get('itemName') or s.get('item_name') or '',
            s.get('brandName') or s.get('brand') or '',
        )
    return '', ''


def _parse_sales_ranks(
    sales_ranks: list, mktpl_id: str
) -> tuple[Optional[int], str, Optional[int], str, list, list]:
    """
    BSR aus salesRanks extrahieren.

    Gibt zurueck:
      (bsr, bsr_cat, bsr_display, bsr_display_cat, disp_ranks, class_ranks)

    bsr:         primaerer BSR fuer Scoring — displayGroupRank zuerst, classificationRank als Fallback.
    bsr_display: displayGroupRank (Hauptkategorie wie auf Produktdetailseite).
    class_ranks: alle classificationRanks [{rank, title}] — EAN2JTL-Kompatibilitaet.
    disp_ranks:  alle displayGroupRanks  [{rank, title}].

    Sucht zuerst marktplatz-spezifischen Eintrag; Fallback: erster verfuegbarer.
    """
    disp_ranks:  list = []
    class_ranks: list = []

    # Marktplatz-spezifisch suchen, dann Fallback auf ersten Eintrag
    target = None
    for sr in sales_ranks:
        if sr.get('marketplaceId') == mktpl_id:
            target = sr
            break
    if target is None and sales_ranks:
        target = sales_ranks[0]

    if target:
        disp_ranks = [
            {'rank': e['rank'], 'title': e.get('title', '')}
            for e in target.get('displayGroupRanks', [])
            if e.get('rank') is not None
        ]
        class_ranks = [
            {'rank': e['rank'], 'title': e.get('title', '')}
            for e in target.get('classificationRanks', [])
            if e.get('rank') is not None
        ]

    bsr_display     = disp_ranks[0]['rank']  if disp_ranks  else None
    bsr_display_cat = disp_ranks[0]['title'] if disp_ranks  else ''
    bsr_class       = class_ranks[0]['rank'] if class_ranks else None
    bsr_class_cat   = class_ranks[0]['title'] if class_ranks else ''

    # primaerer BSR: displayGroup (Hauptseite) zuerst, Klassifikation als Fallback
    if bsr_display is not None:
        return bsr_display, bsr_display_cat, bsr_display, bsr_display_cat, disp_ranks, class_ranks
    if bsr_class is not None:
        return bsr_class, bsr_class_cat, None, '', disp_ranks, class_ranks
    return None, '', None, '', [], []


def _parse_rating(attributes: dict) -> tuple[Optional[float], int]:
    """
    Best-Effort: Sternebewertung + Review-Count aus Attributen.
    Attribut-Keys variieren je Kategorie und Marktplatz -> mehrere Fallbacks.
    None fuer rating ist normaler Zustand (API liefert es nicht garantiert).
    """
    rating = None
    for key in ('average_customer_reviews', 'average_customer_review',
                'customerAverageReview', 'customer_reviews'):
        val = attributes.get(key)
        if isinstance(val, list) and val:
            try:
                rating = float(str(val[0].get('value', '')).replace(',', '.'))
                break
            except (ValueError, AttributeError):
                pass

    review_count = 0
    for key in ('number_of_customer_reviews', 'customer_review_count', 'customerReviewCount'):
        val = attributes.get(key)
        if isinstance(val, list) and val:
            try:
                review_count = int(val[0].get('value', 0))
                break
            except (ValueError, AttributeError):
                pass

    return rating, review_count
