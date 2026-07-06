#!/usr/bin/env python3
"""
techndev-providers  ebay/scraper.py  v1.4.0
=============================================
eBay-Scraper fuer abgeschlossene (verkaufte) Listings.

PRIMARY-Implementierung fuer search_sold() — wird direkt aufgerufen,
solange Marketplace Insights API (buy.marketplace.insights) keine
Business Approval hat. Kein API-Versuch vorgelagert.

Endpoint: https://www.ebay.<tld>/sch/i.html?LH_Sold=1&LH_Complete=1&...

Gibt identisches Format wie sold.search_sold() zurueck:
  (total: int|None, items: list[SoldItem], error: str|None)

WICHTIG: Scraping unterliegt den eBay-Nutzungsbedingungen.
Nur fuer eigene Recherche / nicht-kommerziellen Eigengebrauch.

CHANGELOG
---------
v1.4.0  (2026-07-06)
  - REPRODUZIERBARKEIT: median_sold schwankte zwischen Laeufen massiv trotz
    stabilem sold_count. Ursache: (a) Best-Match liefert je Abruf eine ~40%
    andere Top-N-Teilmenge (Rotation), (b) breite Titel-Query zieht Fehl-Matches
    (falsche Sets, Bootlegs, Zubehoer @ 1-5 EUR) in die Preisbasis. Fixes:
    1. Grosse Stichprobe: intern immer _SAMPLE_IPG (200) statt nur `limit` laden
       -> nahezu vollstaendiger Pool -> Rotation faellt weg (Median stabil).
    2. Relevanz-Filter _is_relevant(): je Item Titel gegen Query-Tokens pruefen
       (Set-/Modellnummern 4-7-stellig muessen vorkommen) -> Fehl-Matches raus.
    3. 'Verkauft'-Marker PFLICHT (require_sold=True): schuetzt gegen kuenftiges
       Auffuellen der Sold-Seite mit aktiven/verwandten Listings.
    4. Retry-on-empty: 0 Treffer bei plausibler Vollseite -> Session-Reset + 1
       Retry (faengt Soft-Empty/Soft-Challenge, die _is_challenge (<100KB) verfehlt).
    Robustes Median-Trimmen liegt in _models._robust_trim (via sold.py).

v1.3.0  (2026-05-28)
  - Rolle aenderung: scrape_sold() ist jetzt PRIMARY (nicht mehr Fallback).
    Wird direkt von sold.search_sold() aufgerufen; kein API-Versuch davor.
    Backlog: Rueckstufung auf Fallback sobald MI-API-Approval erteilt.

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

__version__ = "1.4.0"

TIMEOUT = 30

# Interne Stichprobengroesse: unabhaengig vom `limit` des Aufrufers laden wir eine
# grosse Seite, damit wir (nahezu) den vollstaendigen Sold-Pool sehen. Das eliminiert
# die Best-Match-Rotation als Ursache instabiler Mediane. eBay-Maximum fuer _ipg = 240.
_SAMPLE_IPG = 200

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


# ── Relevanz-Filter ────────────────────────────────────────────────────────────
# Die eBay-Sold-Suche liefert (Best Match) auch off-target Treffer: falsche
# Set-/Modellnummern, Zubehoer, Bundles. _is_relevant() haelt nur Listings, deren
# Titel die diskriminierenden Query-Tokens enthaelt.
#
# Diskriminatoren = Zahlen mit 4-7 Stellen (LEGO-Set-Nr, Modell-Nr, Artikel-Nr).
# Eine reine EAN/GTIN (>=12 Stellen) wird NICHT als Titel-Pflichttoken verlangt:
# eBay matcht GTINs katalogseitig, sie stehen selten im Titel. Fehlen 4-7-stellige
# Zahlen ganz, greift eine Mehrheits-Pruefung auf den Alpha-Tokens der Query.
_RE_NUM      = re.compile(r'\d+')
_RE_ALPHATOK = re.compile(r'[A-Za-zÀ-ÿ]{3,}')
_STOPWORDS   = {
    'lego', 'der', 'die', 'das', 'und', 'für', 'fuer', 'the', 'and', 'set', 'sets',
    'neu', 'new', 'ovp', 'misb', 'nib', 'sealed', 'versiegelt', 'original', 'stück',
    'stueck', 'teile', 'pcs', 'con', 'von',
}


def _significant_numbers(query: str) -> list[str]:
    """Set-/Modellnummern der Query (4-7 Stellen). EANs (>=12) ausgeschlossen."""
    return [t for t in _RE_NUM.findall(query) if 4 <= len(t) <= 7]


def _is_relevant(title: str, query: str) -> bool:
    """
    True wenn `title` plausibel zum gesuchten Produkt gehoert.

    Regel 1: Enthaelt die Query 4-7-stellige Nummern (Set-/Modellnr), MUSS jede
             davon im Titel vorkommen -> filtert falsche Sets/Modelle zuverlaessig.
    Regel 2: Keine solche Nummer -> Mehrheit der Alpha-Tokens (ohne Stopwords)
             muss im Titel stehen.
    Leere/degenerierte Query -> True (kein Filter, fail-open).
    """
    nums = _significant_numbers(query)
    tl   = title.lower()
    if nums:
        return all(n in title for n in nums)
    toks = [w.lower() for w in _RE_ALPHATOK.findall(query) if w.lower() not in _STOPWORDS]
    if not toks:
        return True
    hits = sum(1 for w in toks if w in tl)
    return hits >= max(1, (len(toks) + 1) // 2)


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
    limit:            Anzeige-Cap fuer die zurueckgegebene Item-Liste (1-200).
                      HINWEIS: die Statistik-Basis in sold.get_sold_listings ist der
                      VOLLE relevante Pool (grosse Stichprobe), NICHT auf limit
                      geschnitten — sonst haenge der Median an der Best-Match-
                      Reihenfolge und driftete wieder. limit begrenzt nur die Liste.
    new_only:         Nur Zustand Neu (LH_ItemCondition=3).
    fixed_price_only: Nur Sofort-Kaufen (LH_BIN=1).

    Rueckgabe: (total, items, error_or_None) — identisch zu search_sold().
      items = voller relevanz-gefilterter Sold-Pool (nicht auf limit geschnitten).
    """
    domain = _DOMAINS.get(marketplace.upper(), 'www.ebay.de')

    # Intern immer eine grosse Stichprobe laden (nicht nur `limit`): so sehen wir
    # nahezu den gesamten Sold-Pool und sind gegen die Best-Match-Rotation immun.
    params: dict = {
        '_nkw':        query,
        'LH_Sold':     '1',
        'LH_Complete': '1',
        '_ipg':        str(_SAMPLE_IPG),
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
    items = _parse_items(html, query=query)

    # Retry-on-empty: 0 Treffer trotz plausibler Vollseite (>200 KB) und total>0 deutet
    # auf Soft-Empty/Soft-Challenge, die _is_challenge (<100 KB) nicht erkennt. Ein
    # Session-Reset + Retry glaettet diese Transienten (sonst: aspirational Fallback).
    if not items and len(resp.content) > 200_000 and (total or 0) > 0:
        _sessions.pop(domain, None)
        session = _get_session(domain)
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
            if resp.ok and not _is_challenge(resp):
                html  = resp.text
                total = _parse_total(html) or total
                items = _parse_items(html, query=query)
        except requests.RequestException:
            pass   # Erstantwort behalten (leer) — kein harter Fehler

    # KEIN [:limit]-Schnitt hier: der volle relevante Pool geht an sold.py, damit
    # der Median reproduzierbar ist (nicht abhaengig von der Best-Match-Reihenfolge).
    # Das Anzeige-Cap `limit` wendet sold.get_sold_listings auf die Item-Liste an.
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


def _parse_items(
    html:         str,
    query:        str | None = None,
    require_sold: bool       = True,
) -> list[SoldItem]:
    """
    Parst alle s-card-Bloecke aus dem HTML.
    Split an <li class="s-card"-Grenzen; je Block Preis/Titel/URL/Datum extrahieren.
    Sponsored Items (URL ohne www → kein _RE_URL-Match) werden automatisch gefiltert.

    require_sold: Block MUSS einen 'Verkauft'-Datum-Marker tragen (Default True).
                  Schuetzt gegen aktive/verwandte Listings, mit denen eBay die
                  Sold-Seite bei duennen Ergebnissen auffuellt.
    query:        Wenn gesetzt, wird je Block _is_relevant(title, query) geprueft;
                  off-target Treffer (falsche Set-/Modellnr) werden verworfen.
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

        # Verkaufsdatum — bei require_sold Pflicht (filtert aktive/related Fuelltreffer)
        dm        = _RE_SOLD_DATE.search(block)
        sold_date = dm.group(1).strip() if dm else ''
        if require_sold and not dm:
            continue

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

        # Relevanz-Filter: off-target Treffer (falsches Set/Modell) verwerfen
        if query and title != '-' and not _is_relevant(title, query):
            continue

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
