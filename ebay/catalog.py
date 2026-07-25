#!/usr/bin/env python3
"""
techndev-providers  ebay/catalog.py  v1.0.0
============================================
eBay Commerce Catalog API — Produkt-Struktur per GTIN/Freitext ("kopieren").
Endpoints (App-Token; Buying-App: SCOPE_CATALOG):
  GET /commerce/catalog/v1_beta/product_summary/search?gtin=<ean>|q=<text>
  GET /commerce/catalog/v1_beta/product/{epid}

Rolle im Listing-Workflow: liefert Titel, Marke, Item-Specifics (aspects) und
Bilder eines Katalog-Produkts — die "Struktur", von der ein neues eigenes Angebot
uebernommen wird (faktische Felder, kein 1:1-Klon eines Fremdangebots).

Zugang: Die Catalog API ist keyset-abhaengig oft gesperrt ("Insufficient
permissions"). lookup_product() faellt darum automatisch auf die Browse API
zurueck (bestehendes Live-Angebot → getItem → localizedAspects + categoryId).
Der Fallback braucht kein Approval und traegt den Workflow allein.

CHANGELOG
---------
v1.0.0  (2026-07-25)
  - search_catalog_by_gtin() / search_catalog() : product_summary/search.
  - get_catalog_product(epid)                   : getProduct (Beschreibung + Aspects).
  - lookup_product()                            : High-Level Catalog→Browse-Fallback.
"""
from __future__ import annotations

import requests

from ._auth   import get_token, is_gtin, api_base, SCOPE_CATALOG
from ._models import CatalogProduct, now_iso
from ._rate   import _retry, catalog_limiter
from .        import browse

__version__ = "1.0.0"

TIMEOUT = 30


# ══════════════════════════════════════════════════════════════════════════════
# Commerce Catalog API
# ══════════════════════════════════════════════════════════════════════════════

def _app_token(credentials: dict) -> str:
    return get_token(
        credentials["client_id"],
        credentials["client_secret"],
        scope = SCOPE_CATALOG,
        env   = credentials.get("env", "production"),
    )


def _headers(token: str, marketplace: str) -> dict:
    return {
        "Authorization":           f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
        "Accept":                  "application/json",
    }


def _aspects_to_dict(raw_aspects: list) -> dict:
    """Catalog-Aspect-Liste [{localizedName, localizedValues:[..]}] → {name: [values]}."""
    out: dict[str, list[str]] = {}
    for asp in raw_aspects or []:
        name = str(asp.get("localizedName") or asp.get("name") or "").strip()
        if not name:
            continue
        vals = [str(v).strip() for v in (asp.get("localizedValues") or asp.get("values") or []) if str(v).strip()]
        if vals:
            out[name] = vals
    return out


def _brand_from(data: dict, aspects: dict) -> str | None:
    """Marke aus Top-Level-Feld oder aus dem 'Brand'/'Marke'-Aspect."""
    b = data.get("brand")
    if isinstance(b, str) and b.strip():
        return b.strip()
    brands = data.get("brands")
    if isinstance(brands, list) and brands:
        return str(brands[0]).strip() or None
    for key in ("Brand", "Marke", "Hersteller"):
        if aspects.get(key):
            return aspects[key][0]
    return None


def _images_from(data: dict) -> tuple[str | None, list[str]]:
    """(Hauptbild-URL, [Zusatzbild-URLs]) aus einem Catalog-Produkt/ProductSummary."""
    img = (data.get("image") or {}).get("imageUrl")
    extra = [
        (a or {}).get("imageUrl")
        for a in (data.get("additionalImages") or [])
        if (a or {}).get("imageUrl")
    ]
    return (img or None), extra


@_retry
def search_catalog(
    query:       str,
    credentials: dict,
    marketplace: str  = "EBAY_DE",
    limit:       int  = 10,
) -> tuple[list[dict], str | None]:
    """
    Rohsuche im eBay-Katalog. query = EAN/GTIN oder Freitext.
    Rueckgabe: (productSummaries, error_or_None). Bei gesperrtem Zugang error='HTTP 403'.
    """
    try:
        token = _app_token(credentials)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return [], f"Token-Fehler: HTTP {code}"
    except Exception as e:                                # noqa: BLE001
        return [], f"Token-Fehler: {e}"

    catalog_limiter.wait()
    params: dict = {"limit": max(1, min(limit, 50))}
    if is_gtin(query):
        params["gtin"] = query
    else:
        params["q"] = query

    url = f"{api_base(credentials.get('env', 'production'))}/commerce/catalog/v1_beta/product_summary/search"
    try:
        resp = requests.get(url, headers=_headers(token, marketplace), params=params, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return [], f"HTTP {code}"
    except requests.RequestException as e:
        return [], f"Netzwerkfehler: {e}"

    return (resp.json().get("productSummaries") or []), None


def search_catalog_by_gtin(
    gtin:        str,
    credentials: dict,
    marketplace: str = "EBAY_DE",
) -> CatalogProduct:
    """
    Bestes Katalog-Produkt zu einer GTIN/EAN als CatalogProduct.
    error != None (z.B. 'HTTP 403') signalisiert gesperrten Zugang oder keinen Treffer.
    """
    ts = now_iso()
    summaries, error = search_catalog(gtin, credentials, marketplace, limit=10)
    if error:
        return CatalogProduct(query=gtin, marketplace=marketplace, fetched_at=ts,
                              source="catalog", error=error)
    if not summaries:
        return CatalogProduct(query=gtin, marketplace=marketplace, fetched_at=ts,
                              source="catalog", error="Kein Katalog-Treffer")

    return _summary_to_product(summaries[0], gtin, marketplace, ts)


def _summary_to_product(summary: dict, query: str, marketplace: str, ts: str) -> CatalogProduct:
    aspects = _aspects_to_dict(summary.get("aspects") or [])
    img, extra = _images_from(summary)
    gtins = [str(g).strip() for g in (summary.get("gtins") or summary.get("gtin") or []) if str(g).strip()]
    return CatalogProduct(
        query             = query,
        marketplace       = marketplace,
        fetched_at        = ts,
        epid              = str(summary.get("epid") or "") or None,
        title             = summary.get("title") or "",
        brand             = _brand_from(summary, aspects),
        gtins             = gtins,
        image_url         = img,
        additional_images = extra,
        aspects           = aspects,
        source            = "catalog",
    )


@_retry
def get_catalog_product(
    epid:        str,
    credentials: dict,
    marketplace: str = "EBAY_DE",
) -> CatalogProduct:
    """
    Volles Katalog-Produkt via getProduct (inkl. Beschreibung + vollstaendiger Aspects).
    Ergaenzt die knappe ProductSummary aus der Suche.
    """
    ts = now_iso()
    try:
        token = _app_token(credentials)
    except Exception as e:                                # noqa: BLE001
        return CatalogProduct(query=epid, marketplace=marketplace, fetched_at=ts,
                              source="catalog", error=f"Token-Fehler: {e}")

    catalog_limiter.wait()
    url = f"{api_base(credentials.get('env', 'production'))}/commerce/catalog/v1_beta/product/{epid}"
    try:
        resp = requests.get(url, headers=_headers(token, marketplace), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        return CatalogProduct(query=epid, marketplace=marketplace, fetched_at=ts,
                              source="catalog", error=f"HTTP {code}")
    except requests.RequestException as e:
        return CatalogProduct(query=epid, marketplace=marketplace, fetched_at=ts,
                              source="catalog", error=f"Netzwerkfehler: {e}")

    data    = resp.json()
    aspects = _aspects_to_dict(data.get("aspects") or [])
    img, extra = _images_from(data)
    gtins = [str(g).strip() for g in (data.get("gtin") or data.get("gtins") or []) if str(g).strip()]
    return CatalogProduct(
        query             = epid,
        marketplace       = marketplace,
        fetched_at        = ts,
        epid              = str(data.get("epid") or epid) or None,
        title             = data.get("title") or "",
        brand             = _brand_from(data, aspects),
        gtins             = gtins,
        image_url         = img,
        additional_images = extra,
        aspects           = aspects,
        description       = data.get("description") or None,
        source            = "catalog",
    )


# ══════════════════════════════════════════════════════════════════════════════
# High-Level: Catalog → Browse-Fallback
# ══════════════════════════════════════════════════════════════════════════════

def lookup_product(
    query:                 str,
    credentials:           dict,
    marketplace:           str  = "EBAY_DE",
    enrich_via_getproduct: bool = True,
    allow_browse_fallback: bool = True,
) -> CatalogProduct:
    """
    Produkt-Struktur zu einer EAN/GTIN oder Freitext — bevorzugt aus dem Katalog,
    mit automatischem Fallback auf ein bestehendes Live-Angebot (Browse API).

    query:                 EAN/GTIN (z.B. '5702017153261') oder Freitext ('LEGO 75192').
    enrich_via_getproduct: bei Katalog-Treffer zusaetzlich getProduct fuer Beschreibung.
    allow_browse_fallback: bei gesperrtem/leerem Katalog ueber Browse getItem gehen.

    Rueckgabe: CatalogProduct. .ok() True wenn Titel vorhanden. .source zeigt Herkunft.
    """
    prod = search_catalog_by_gtin(query, credentials, marketplace)
    if prod.ok():
        if enrich_via_getproduct and prod.epid:
            full = get_catalog_product(prod.epid, credentials, marketplace)
            if full.ok():
                # Suche liefert oft mehr Bilder, getProduct die Beschreibung → mergen.
                if not full.description:
                    full.description = prod.description
                if len(prod.all_images()) > len(full.all_images()):
                    full.image_url         = prod.image_url
                    full.additional_images = prod.additional_images
                if not full.gtins:
                    full.gtins = prod.gtins
                full.query = query
                return full
        return prod

    if not allow_browse_fallback:
        return prod

    fb = _browse_fallback(query, credentials, marketplace)
    if fb.ok():
        return fb
    # Beide leer → den aussagekraeftigeren Fehler zurueckgeben (Katalog zuerst).
    return prod if prod.error else fb


def _browse_fallback(query: str, credentials: dict, marketplace: str) -> CatalogProduct:
    """Struktur aus einem bestehenden Live-Angebot ziehen (Browse getItem).

    Sucht zuerst ueber den gtin-Parameter; liefert der nichts (auf EBAY_DE haeufig),
    wird die EAN als Freitext erneut gesucht — das greift zuverlaessig.
    """
    ts = now_iso()
    total, items, error = browse.search_active(query, credentials, marketplace,
                                               limit=10, new_only=True, fixed_price_only=True)
    if not items and not error and is_gtin(query):
        total, items, error = browse.search_active(query, credentials, marketplace,
                                                   limit=10, new_only=True,
                                                   fixed_price_only=True, as_text=True)
    if error or not items:
        return CatalogProduct(query=query, marketplace=marketplace, fetched_at=ts,
                              source="browse", error=error or "Kein Live-Angebot gefunden")

    data = browse._get_item_json(items[0].item_id, credentials, marketplace)
    if not data:
        return CatalogProduct(query=query, marketplace=marketplace, fetched_at=ts,
                              source="browse", error="getItem lieferte nichts")

    aspects: dict[str, list[str]] = {}
    for asp in data.get("localizedAspects") or []:
        name = str(asp.get("name") or "").strip()
        val  = str(asp.get("value") or "").strip()
        if name and val:
            aspects.setdefault(name, []).append(val)

    img = (data.get("image") or {}).get("imageUrl")
    extra = [
        (a or {}).get("imageUrl")
        for a in (data.get("additionalImages") or [])
        if (a or {}).get("imageUrl")
    ]
    return CatalogProduct(
        query             = query,
        marketplace       = marketplace,
        fetched_at        = ts,
        epid              = str(data.get("epid") or "") or None,
        title             = data.get("title") or "",
        brand             = data.get("brand") or (aspects.get("Marke") or aspects.get("Brand") or [None])[0],
        gtins             = [g for g in [browse._gtin_from(data)] if g],
        image_url         = img or None,
        additional_images = extra,
        aspects           = aspects,
        description       = (data.get("description") or None),
        category_id       = str(data.get("categoryId") or "") or None,
        source            = "browse",
    )
