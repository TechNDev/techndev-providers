#!/usr/bin/env python3
"""
techndev-providers  ebay/scraper.py  v1.0.0
=============================================
eBay-Scraper fuer abgeschlossene (verkaufte) Listings.
Fallback wenn Marketplace Insights API (buy.marketplace.insights) nicht
freigeschaltet ist (HTTP 400/403 beim Token-Request).

Endpoint: https://www.ebay.<tld>/sch/i.html?LH_Sold=1&LH_Complete=1&...

Gibt identisches Format wie sold.search_sold() zurueck:
  (total: int|None, items: list[SoldItem], error: str|None)

WICHTIG: Scraping unterliegt den eBay-Nutzungsbedingungen.
Nur fuer eigene Recherche / nicht-kommerziellen Eigengebrauch.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - scrape_sold(): Abgeschlossene Listings scraepen, kompatibel zu search_sold().
  - HTML-Parsing: Preis (DE+EN Format), Titel, Item-URL, Item-ID.
    Sold-Datum: Best-Effort — aus statischem HTML nicht zuverlaessig extrahierbar.
  - Gesamtanzahl: JSON-Blob-Suche -> Ergebnistext-Regex -> None als Fallback.
  - Marketplace-Domain-Map: EBAY_DE/AT/US/UK/FR/IT/ES/NL/BE/PL/AU/CA.
"""
from __future__ import annotations

import re

import requests

from ._models import SoldItem

__version__ = "1.0.0"

TIMEOUT = 30

# ── Domain-Map: Marketplace-ID → eBay-Domain ──────────────────────────────────
_DOMAINS: dict[str, str] = {
    'EBAY_DE': 'www.ebay.de',
    'EBAY_AT': 'www.ebay.at',
    'EBAY_CH': 'www.ebay.ch',
    'EBAY_US': 'www.ebay.com',
    'EBAY_UK': 'www.ebay.co.uk',
    'EBAY_FR': 'www.ebay.fr',
    'EBAY_IT': 'www.ebay.it',
    'EBAY_ES': 'www.ebay.es',
    'EBAY_NL': 'www.ebay.nl',
    'EBAY_BE': 'www.ebay.be',
    'EBAY_PL': 'www.ebay.pl',
    'EBAY_AU': 'www.ebay.com.au',
    'EBAY_CA': 'www.ebay.ca',
}

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
}

# ── Parse-Regexes ──────────────────────────────────────────────────────────────

# Gesamtanzahl: JSON-Einbettung (zuverlaessiger) — "totalItems":"3456" / "totalItems":3456
_RE_TOTAL_JSON = re.compile(r'"totalItems"\s*:\s*"?(\d+)"?')
# Gesamtanzahl: sichtbarer Ergebnistext DE/EN
_RE_TOTAL_TEXT = re.compile(
    r'([\d.,]+)\s+(?:Ergebniss?e?|results?)\b',
    re.IGNORECASE,
)

# Preis im s-item__price-Element.
# Erkennt: "EUR 149,99" / "149,99 EUR" / "$ 149.99" / "149.99" / "1.299,99"
_RE_PRICE = re.compile(
    r'class="[^"]*\bs-item__price\b[^"]*"[^>]*>'
    r'\s*(?:[€$£]\s*|(?:EUR|USD|GBP|CHF|PLN|AUD|CAD)\s*)?'
    r'([\d][0-9.,]+)',
    re.IGNORECASE,
)

# Titel im s-item__title-Element (h3 oder div oder span)
_RE_TITLE = re.compile(
    r'class="[^"]*\bs-item__title\b[^"]*"[^>]*>(.*?)</(?:h3|div|span)\b',
    re.DOTALL | re.IGNORECASE,
)

# Item-URL (ebay.<tld>/itm/<id>)
_RE_URL = re.compile(
    r'href="(https://www\.ebay\.[^/\s"]+/itm/(\d+))[^"]*"',
    re.IGNORECASE,
)

# HTML-Tags entfernen
_RE_TAGS = re.compile(r'<[^>]+>')

# Datum im Block — Best-Effort: "20. Mai 2026" (DE) oder "May 20, 2026" (EN)
# oder ISO-aehnlich "2026-05-20"
_RE_DATE_DE  = re.compile(
    r'\b(\d{1,2}\.\s*(?:Jan|Feb|M[äa]r|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)[a-z]*\.?\s+\d{4})\b',
    re.IGNORECASE,
)
_RE_DATE_EN  = re.compile(
    r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b',
    re.IGNORECASE,
)
_RE_DATE_ISO = re.compile(r'\b(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}:\d{2})?\b')


def scrape_sold(
    query: str,
    marketplace:      str  = 'EBAY_DE',
    limit:            int  = 50,
    new_only:         bool = True,
    fixed_price_only: bool = True,
) -> tuple[int | None, list[SoldItem], str | None]:
    """
    Scrapt abgeschlossene/verkaufte eBay-Listings als Fallback fuer Marketplace Insights.

    query:            EAN oder Freitext-Suchbegriff.
    marketplace:      EBAY_DE | EBAY_US | EBAY_UK | ... (Default: EBAY_DE)
    limit:            Max. Ergebnisse (1-200).
    new_only:         Nur Zustand Neu (LH_ItemCondition=3).
    fixed_price_only: Nur Sofort-Kaufen (LH_BIN=1).

    Rueckgabe: (total, items, error_or_None) — identisch zu search_sold().
    sold_date ist leer-String (nicht zuverlaessig aus statischem HTML extrahierbar).
    """
    domain = _DOMAINS.get(marketplace.upper(), 'www.ebay.de')

    params: dict = {
        '_nkw':        query,
        'LH_Sold':     '1',
        'LH_Complete': '1',
        '_ipg':        str(min(max(1, limit), 200)),
    }
    if fixed_price_only:
        params['LH_BIN'] = '1'
    if new_only:
        params['LH_ItemCondition'] = '3'   # 3 = Neu / New

    url = f'https://{domain}/sch/i.html'
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else '?'
        return None, [], f"Scraper HTTP {code}"
    except requests.RequestException as e:
        return None, [], f"Scraper Netzwerkfehler: {e}"

    html  = resp.text
    total = _parse_total(html)
    items = _parse_items(html)[:limit]
    return total, items, None


# ── Interne Parser-Funktionen ──────────────────────────────────────────────────

def _parse_total(html: str) -> int | None:
    """Gesamtanzahl der Ergebnisse aus dem Seitenquelltext."""
    m = _RE_TOTAL_JSON.search(html)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    m = _RE_TOTAL_TEXT.search(html)
    if m:
        try:
            # "1.234" (DE) oder "1,234" (EN) → 1234
            return int(m.group(1).replace('.', '').replace(',', ''))
        except ValueError:
            pass
    return None


def _parse_price(block: str) -> float | None:
    """Preis aus einem Item-HTML-Block. Gibt None bei fehlender/unplausibler Zahl."""
    m = _RE_PRICE.search(block)
    if not m:
        return None
    raw = m.group(1)
    # DE-Format: 1.299,99 → 1299.99  |  EN-Format: 1,299.99 → 1299.99
    # Heuristik: wenn letztes Trennzeichen ein Komma, ist es DE-Format
    if ',' in raw and '.' in raw:
        # Bsp: "1.299,99" → Punkt = Tausender, Komma = Dezimal
        if raw.rfind(',') > raw.rfind('.'):
            raw = raw.replace('.', '').replace(',', '.')
        else:
            # Bsp: "1,299.99" → Komma = Tausender, Punkt = Dezimal
            raw = raw.replace(',', '')
    elif ',' in raw:
        # Nur Komma → DE-Dezimalzeichen: "149,99" → "149.99"
        raw = raw.replace(',', '.')
    # Nur Punkt → normale EN-Schreibweise, unveraendert lassen
    try:
        price = float(raw)
    except ValueError:
        return None
    # Plausibilitaetscheck: Preise zwischen 0,01 und 1 Mio.
    return price if 0.01 <= price <= 1_000_000 else None


def _parse_date(block: str) -> str:
    """Verkaufsdatum aus Item-Block — Best-Effort, leerer String wenn nicht gefunden."""
    for pattern in (_RE_DATE_ISO, _RE_DATE_DE, _RE_DATE_EN):
        m = pattern.search(block)
        if m:
            return m.group(1).strip()
    return ''


def _clean_title(raw_html: str) -> str:
    """HTML-Tags entfernen + eBay-Standardfuelltext bereinigen."""
    text = _RE_TAGS.sub('', raw_html).strip()
    for noise in ('Neuer Artikel', 'New Listing', 'SPONSORED', 'Anzeige'):
        text = text.replace(noise, '').strip()
    return text or '-'


def _parse_items(html: str) -> list[SoldItem]:
    """
    Parst alle s-item-Bloecke aus dem HTML.
    Strategie: Auftrennen an <li-Grenzen mit 's-item'-Klasse,
    dann je Block Preis + Titel + URL extrahieren.
    """
    # Aufteilen am Start jedes Item-Tags — vermeidet verschachtelte-Tag-Probleme
    parts = re.split(r'(?=<li\b[^>]*\bclass="[^"]*\bs-item\b)', html, flags=re.IGNORECASE)

    items: list[SoldItem] = []
    for block in parts[1:]:   # parts[0] ist alles vor dem ersten Item
        # URL + Item-ID (Pflichtfeld — ohne ID wird Eintrag verworfen)
        um = _RE_URL.search(block)
        if not um:
            continue
        item_url  = um.group(1)
        item_id   = um.group(2)

        # Preis
        price = _parse_price(block)

        # Titel
        title = '-'
        tm = _RE_TITLE.search(block)
        if tm:
            title = _clean_title(tm.group(1))

        # Datum (Best-Effort)
        sold_date = _parse_date(block)

        items.append(SoldItem(
            title          = title,
            price          = price,
            currency       = 'EUR',          # Scraper primaer DE-fokussiert; TODO: aus Domain ableiten
            sold_date      = sold_date,
            condition      = 'New',          # Angenommen wegen LH_ItemCondition=3
            buying_options = 'FIXED_PRICE',  # Angenommen wegen LH_BIN=1
            item_id        = item_id,
            url            = item_url,
        ))

    return items
