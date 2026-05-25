#!/usr/bin/env python3
"""
techndev-providers  ebay/scraper.py  v1.2.0
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
v1.2.0  (2026-05-25)
  - Parser auf aktuelles eBay-HTML umgestellt: Klasse s-card (war s-item),
    Preis in s-card__price, Titel in s-card__title, Datum aus
    aria-label="Verkaufter Artikel". URL-Filter erkennt Sponsored-Items
    (ebay.com ohne www → kein Match → automatisch ausgefiltert).
  - _is_challenge(): erkennt Akamai-Bot-Challenge-Seite (< 100 KB + Keyword).
    Bei Challenge: Session-Reset + 1 Retry; danach klar benannter Fehler.

v1.1.0  (2026-05-25)
  - Session-Cache pro Domain: erst Homepage besuchen (immer 200) fuer
    Akamai-Session-Cookies (dp1/nonsession/s/ds2/ebay), dann Suche.
    Loest HTTP 403 von Akamai-WAF auf /sch/i.html fuer DataCenter-IPs.
    Bei erneutem 403: Session-Neuinitialisierung + 1 Retry.

v1.0.0  (2026-05-25)
  - Initiales Release: scrape_sold(), HTML-Parsing, Domain-Map.
"""
from __future__ import annotations

import re

import requests

from ._models import SoldItem

__version__ = "1.2.0"

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

# ── Session-Cache pro Domain ───────────────────────────────────────────────────
# Akamai-WAF blockt /sch/i.html von DataCenter-IPs ohne gueltige Session-Cookies.
# Loesung: erst Homepage besuchen (immer 200) -> Cookies (dp1/nonsession/s/...) ->
# dann Suche mit Session. Session pro Domain gecacht; bei 403 neu initialisiert.
_sessions: dict[str, requests.Session] = {}


def _get_session(domain: str) -> requests.Session:
    """
    Gibt eine gueltige requests.Session fuer die eBay-Domain zurueck.
    Beim ersten Aufruf: Homepage besuchen fuer Akamai-Session-Cookies.
    """
    if domain not in _sessions:
        session = requests.Session()
        session.headers.update(_HEADERS)
        try:
            session.get(f'https://{domain}/', timeout=20)   # Cookie-Warm-up
        except Exception:
            pass   # Wenn Homepage scheitert, trotzdem versuchen
        _sessions[domain] = session
    return _sessions[domain]


# ── Challenge-Erkennung ───────────────────────────────────────────────────────
# Akamai Bot Manager serviert bei verdaechtigen Sessions eine ~13KB-Seite mit
# dem Titel "Bitte entschuldigen Sie die Störung" statt der eigentlichen Seite.
_CHALLENGE_MARKER = 'entschuldigen'   # DE-Challenge
_CHALLENGE_SIZE   = 100_000           # Echte Ergebnisseiten sind immer > 100 KB


def _is_challenge(resp: requests.Response) -> bool:
    """True wenn eBay eine Bot-Challenge-Seite statt Ergebnisse geliefert hat."""
    if len(resp.content) < _CHALLENGE_SIZE:
        return _CHALLENGE_MARKER in resp.text or 'splash' in resp.url.lower()
    return False


# ── Parse-Regexes (eBay-HTML Stand 2026-05) ───────────────────────────────────

# Gesamtanzahl: JSON-Einbettung (zuverlaessiger) — "totalItems":"3456"
_RE_TOTAL_JSON = re.compile(r'"totalItems"\s*:\s*"?(\d+)"?')
# Gesamtanzahl: sichtbarer Ergebnistext DE/EN
_RE_TOTAL_TEXT = re.compile(
    r'([\d.,]+)\s+(?:Ergebniss?e?|results?)\b',
    re.IGNORECASE,
)

# Preis: class="... s-card__price">EUR 229,00
# Erkennt: "EUR 229,00" / "229,00 EUR" / "EUR 1.299,00"
_RE_PRICE = re.compile(
    r's-card__price[^>]*>\s*(?:EUR\s*|USD\s*|GBP\s*|CHF\s*)?'
    r'([\d][0-9.,]+)',
    re.IGNORECASE,
)

# Titel: class=s-card__title (unquoted attribute eBay-spezifisch)
# Inhalt ist ein <span> mit dem tatsaechlichen Text
_RE_TITLE = re.compile(
    r'class=s-card__title[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)

# Item-URL: href=https://www.ebay.de/itm/XXXXXXXX
# Sponsored Items nutzen ebay.com ohne www → kein Match → automatisch gefiltert
_RE_URL = re.compile(
    r'href=(https://www\.ebay\.[^/\s"\']+/itm/(\d+))',
    re.IGNORECASE,
)

# Verkaufsdatum: aria-label="Verkaufter Artikel">Verkauft  24. Mai 2026
_RE_SOLD_DATE = re.compile(
    r'aria-label="Verkaufter\s+Artikel"[^>]*>\s*Verkauft\s+'
    r'(\d{1,2}\.\s+\w+\s+\d{4})',
    re.IGNORECASE,
)

# HTML-Tags entfernen
_RE_TAGS = re.compile(r'<[^>]+>')


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

    url     = f'https://{domain}/sch/i.html'
    session = _get_session(domain)
    try:
        resp = session.get(url, params=params, timeout=TIMEOUT)
        if resp.status_code == 403 or _is_challenge(resp):
            # Session invalide oder Akamai-Challenge → Session-Reset + 1 Retry
            _sessions.pop(domain, None)
            session = _get_session(domain)
            resp = session.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else '?'
        return None, [], f"Scraper HTTP {code}"
    except requests.RequestException as e:
        return None, [], f"Scraper Netzwerkfehler: {e}"

    if _is_challenge(resp):
        return None, [], "Scraper: eBay Bot-Challenge nicht umgehbar (Session invalide)"

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
            return int(m.group(1).replace('.', '').replace(',', ''))
        except ValueError:
            pass
    return None


def _parse_price(block: str) -> float | None:
    """Preis aus einem Item-HTML-Block (s-card__price). None bei unplausiblem Wert."""
    m = _RE_PRICE.search(block)
    if not m:
        return None
    raw = m.group(1)
    # DE-Format: 1.299,99 → 1299.99  |  EN-Format: 1,299.99 → 1299.99
    if ',' in raw and '.' in raw:
        if raw.rfind(',') > raw.rfind('.'):
            raw = raw.replace('.', '').replace(',', '.')   # DE: Komma = Dezimal
        else:
            raw = raw.replace(',', '')                      # EN: Komma = Tausender
    elif ',' in raw:
        raw = raw.replace(',', '.')   # Nur Komma → DE-Dezimal
    try:
        price = float(raw)
    except ValueError:
        return None
    return price if 0.01 <= price <= 1_000_000 else None


def _parse_items(html: str) -> list[SoldItem]:
    """
    Parst alle s-card-Bloecke aus dem HTML.
    Split an <li class="s-card"-Grenzen; je Block Preis/Titel/URL/Datum extrahieren.
    Sponsored Items (URL ohne www → kein _RE_URL-Match) werden automatisch gefiltert.
    """
    # Aufteilen am Start jedes s-card-Tags
    parts = re.split(r'(?=<li\b[^>]*\bclass="s-card\b)', html, flags=re.IGNORECASE)

    items: list[SoldItem] = []
    for block in parts[1:]:   # parts[0] = alles vor dem ersten Item
        # URL + Item-ID — Pflichtfeld; ohne ID wird Block verworfen
        # Sponsored Items: href=https://ebay.com/itm/... (kein www) → kein Match
        um = _RE_URL.search(block)
        if not um:
            continue
        item_url = um.group(1)
        item_id  = um.group(2)

        # Preis
        price = _parse_price(block)

        # Titel (s-card__title → span → Text)
        title = '-'
        tm = _RE_TITLE.search(block)
        if tm:
            raw = _RE_TAGS.sub('', tm.group(1)).strip()
            # eBay-Fuelltext in manchen Blöcken entfernen
            for noise in ('Wird in neuem Fenster', 'Opens in a new window'):
                raw = raw.split(noise)[0].strip()
            title = raw or '-'

        # Verkaufsdatum (Best-Effort)
        sold_date = ''
        dm = _RE_SOLD_DATE.search(block)
        if dm:
            sold_date = dm.group(1).strip()

        items.append(SoldItem(
            title          = title,
            price          = price,
            currency       = 'EUR',          # Scraper primaer DE-fokussiert
            sold_date      = sold_date,
            condition      = 'New',          # Angenommen wegen LH_ItemCondition=3
            buying_options = 'FIXED_PRICE',  # Angenommen wegen LH_BIN=1
            item_id        = item_id,
            url            = item_url,
        ))

    return items
