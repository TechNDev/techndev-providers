#!/usr/bin/env python3
"""
techndev-providers  brickmerge/scraper.py  v1.0.0
===================================================
Live-Scraper fuer brickmerge.de — Preise + Haendleranzahl.

Vorher: BrickmergeProvider in mydealz-watcher/pricesource_brickmerge.py.
Neu:    + seller_count (Anzahl aktiver Haendler aus der Detailseite)

Scraping-Strategie:
  Server-gerendertes HTML — kein JS erforderlich.
  URL-Schema: https://www.brickmerge.de/<setno>/
  Alle Regex-Pattern sind gegen das Brickmerge-HTML vom Mai 2026 verprobt.

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - Initiales Release, extrahiert + erweitert aus pricesource_brickmerge.py.
  - seller_count: neues Feld in MarketPrices — Anzahl aktiver Haendler
    via RE_SELLER_COUNT (Pattern: 'bei N Haendlern' / 'N Haendler').
  - Alle bisherigen Pattern (UVP, Bestpreis, EAN, Name, 180d) unveraendert.
  - BrickmergeProvider.get_prices() gibt jetzt MarketPrices aus _models statt
    aus pricesource (identisches Feldset + seller_count).
"""
from __future__ import annotations

import re
import sys
from html import unescape
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ._models import MarketPrices, now_iso

__version__ = "1.0.0"

# ══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ══════════════════════════════════════════════════════════════════════════════

BASE_URL   = "https://www.brickmerge.de"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ── Preis-Pattern ─────────────────────────────────────────────────────────────
# "<label>: <strong>N.NNN,NN&nbsp;&euro;</strong>"
_PRICE_AFTER = r"<strong>\s*([\d.]+,\d{2})\s*(?:&nbsp;)?\s*&euro;\s*</strong>"

RE_UVP_ORIG    = re.compile(r"urspr\.\s*UVP:\s*"        + _PRICE_AFTER)
RE_UVP_CURR    = re.compile(r"akt\.\s*UVP:\s*"          + _PRICE_AFTER)
RE_UVP_SINGLE  = re.compile(r"\|\s*UVP:\s*"             + _PRICE_AFTER)
RE_BEST_ALLTIME = re.compile(
    r"bisheriger\s*Bestpreis:\s*" + _PRICE_AFTER
    + r".*?vor\s+(\d+)\s+Tagen",
    re.DOTALL,
)
RE_BEST_180D   = re.compile(r"180\s*Tage\s*Bestpreis:\s*"       + _PRICE_AFTER)
RE_BEST_CURRENT = re.compile(r"akt\.\s*brickmerge\s*Preis:\s*ab\s*" + _PRICE_AFTER)

# ── EAN-Pattern ───────────────────────────────────────────────────────────────
RE_EAN = re.compile(r"EAN:\s*<strong>\s*(\d{8,14})\s*</strong>")

# ── Titel-Pattern ─────────────────────────────────────────────────────────────
RE_TITLE = re.compile(
    r"<title>\s*LEGO(?:&reg;)?\s+(?P<theme>.+?)\s+(?P<setno>\d{3,6})\s+"
    r"(?P<name>.+?)\s+Preisvergleich",
    re.IGNORECASE,
)

# ── Haendleranzahl-Pattern ────────────────────────────────────────────────────
# Brickmerge zeigt z.B. "ab 19,99 € bei 12 Händlern" oder "12 Händler"
RE_SELLER_COUNT = re.compile(
    r"bei\s+(\d+)\s+H[äa]ndlern?"           # "bei 12 Händlern"
    r"|(\d+)\s+H[äa]ndler(?:n\b|\b(?!s))",  # "12 Händler" (nicht "Händlers" o.ä.)
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════════════

def _to_float(de_price: str) -> float:
    """'1.299,00' -> 1299.00 ; '679,99' -> 679.99"""
    return float(de_price.replace(".", "").replace(",", "."))


def _extract_price(pattern: re.Pattern, html: str) -> float | None:
    m = pattern.search(html)
    return _to_float(m.group(1)) if m else None


def _extract_name(html: str) -> str | None:
    """Extrahiert 'Theme Name' aus dem <title>-Tag."""
    m = RE_TITLE.search(html)
    if not m:
        return None
    return unescape(f"{m.group('theme')} {m.group('name')}".strip())


def _extract_seller_count(html: str) -> int | None:
    """
    Extrahiert die Haendleranzahl aus dem HTML.
    Prueft beide Alternativen des RE_SELLER_COUNT-Patterns.
    Gibt None zurueck wenn kein Match.
    """
    m = RE_SELLER_COUNT.search(html)
    if not m:
        return None
    # Gruppe 1: "bei N Haendlern"; Gruppe 2: "N Haendler"
    raw = m.group(1) or m.group(2)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════════════

class BrickmergeProvider:
    """
    Live-Scraper fuer brickmerge.de.
    Liefert pro LEGO-Set-Nummer einen MarketPrices-Snapshot inkl. Haendleranzahl.

    Instanzierung:
        prov    = BrickmergeProvider(timeout=20)
        result  = prov.get_prices('10294')
        if result:
            print(result.best_price_current, result.seller_count)
    """

    name = "brickmerge"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def _fetch(self, url: str) -> str | None:
        """Laedt die Detailseite. Gibt None bei 404, reraised andere Fehler."""
        req = Request(url, headers={"User-Agent": USER_AGENT,
                                    "Accept":     "text/html,*/*"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            if e.code == 404:
                return None
            raise

    def get_prices(
        self,
        set_no:    str,
        *,
        ean_hint:  str   | None = None,
        uvp_hint:  float | None = None,
        url_hint:  str   | None = None,
    ) -> MarketPrices | None:
        """
        Scrapt die brickmerge.de-Detailseite fuer set_no.

        Optionale Katalog-Hints (aus SetCatalog) zur Optimierung:
          ean_hint  — EAN direkt aus Katalog; EAN-Regex wird uebersprungen.
          uvp_hint  — UVP aus Katalog; Fallback wenn HTML-Regex None liefert.
          url_hint  — Kanonische URL aus Katalog; verhindert Redirect.

        Gibt None zurueck wenn das Set bei Brickmerge nicht gefunden (404).
        Netzwerk-/Parse-Fehler werden als Exceptions weitergegeben.
        """
        fetch_url = url_hint or f"{BASE_URL}/{set_no}/"
        html = self._fetch(fetch_url)
        if html is None:
            return None

        # UVP: urspr./akt.-Split bevorzugt (aktive Sets);
        # Fallback: einzelnes "UVP:" (aeltere EOL-Sets); dann Katalog-Hint.
        uvp_orig = _extract_price(RE_UVP_ORIG, html)
        uvp_curr = _extract_price(RE_UVP_CURR, html)
        if uvp_orig is None and uvp_curr is None:
            uvp_orig = _extract_price(RE_UVP_SINGLE, html)
        if uvp_orig is None and uvp_curr is None and uvp_hint is not None:
            uvp_orig = uvp_hint

        # EAN: Katalog-Hint bevorzugt, dann HTML-Regex
        m_ean = RE_EAN.search(html)
        ean   = ean_hint or (m_ean.group(1) if m_ean else None)

        m_alltime = RE_BEST_ALLTIME.search(html)
        return MarketPrices(
            set_no                      = set_no,
            name                        = _extract_name(html),
            ean                         = ean,
            uvp_original                = uvp_orig,
            uvp_current                 = uvp_curr,
            best_price_alltime          = _to_float(m_alltime.group(1)) if m_alltime else None,
            best_price_alltime_days_ago = int(m_alltime.group(2))       if m_alltime else None,
            best_price_180d             = _extract_price(RE_BEST_180D, html),
            best_price_current          = _extract_price(RE_BEST_CURRENT, html),
            seller_count                = _extract_seller_count(html),
            source                      = self.name,
            url                         = fetch_url,
            fetched_at                  = now_iso(),
        )
