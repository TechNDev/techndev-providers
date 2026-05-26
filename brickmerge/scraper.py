#!/usr/bin/env python3
"""
techndev-providers  brickmerge/scraper.py  v1.1.0
===================================================
Live-Scraper fuer brickmerge.de — Preise, Stammdaten + Produktdetails.

Scraping-Strategie:
  Server-gerendertes HTML — kein JS erforderlich.
  URL-Schema: https://www.brickmerge.de/<setno>/
  Alle Regex-Pattern sind gegen das Brickmerge-HTML vom Mai 2026 verprobt.

CHANGELOG
---------
v1.2.0  (2026-05-26)
  - minifig_count, minifig_exclusive_count: RE_MINIFIGS_TOTAL + RE_MINIFIGS_EXCL.
    Exklusiver Count wird in einem 300-Zeichen-Fenster nach dem Total-Match gesucht.

v1.1.0  (2026-05-26)
  - 14 neue Felder: piece_count, weight_part_g, weight_set_g, box_l/w/h_cm,
    age_min, release_month, eol_month, plc_months, dealer_pack_qty,
    best_price_30d, pov, pov_rate.
  - Hilfsfunktion _parse_month_year(): 'MM/YYYY' → 'YYYY-MM'.
  - Alle bestehenden Pattern unveraendert.

v1.0.0  (2026-05-25)
  - Initiales Release, extrahiert + erweitert aus pricesource_brickmerge.py.
  - seller_count: Anzahl aktiver Haendler via RE_SELLER_COUNT.
  - Alle bisherigen Pattern (UVP, Bestpreis, EAN, Name, 180d) unveraendert.
  - BrickmergeProvider.get_prices() gibt MarketPrices aus _models zurueck.
"""
from __future__ import annotations

import re
import sys
from html import unescape
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ._models import MarketPrices, now_iso

__version__ = "1.2.0"

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

# ── Produkt-Stammdaten-Pattern ────────────────────────────────────────────────
# Alle Pattern erwarten server-gerendertes HTML mit <strong>...</strong>.
# HTML-Entitaeten sind bereits auf UTF-8 dekodiert (resp.read().decode('utf-8')).

_INT_IN_STRONG   = r"<strong>\s*(\d+)\s*</strong>"
_FLOAT_IN_STRONG = r"<strong>\s*([\d.]+)\s*</strong>"

# Teileanzahl: "Teile: <strong>326</strong>"
RE_PIECE_COUNT = re.compile(r"Teile:\s*" + _INT_IN_STRONG, re.IGNORECASE)

# Alter: "Alter: <strong>8+</strong>"  oder  "Alter <strong>8</strong>+"
RE_AGE_MIN = re.compile(r"Alter[:\s]+<strong>\s*(\d+)\+?\s*</strong>", re.IGNORECASE)

# Gewicht: "Teilegewicht: <strong>≈228 g</strong>"
# ≈ ist U+2248 oder &asymp;, beide nach UTF-8-Decode als Literal vorhanden
RE_WEIGHT_PARTS = re.compile(
    r"Teilegewicht[:\s]+<strong>\s*[≈~]?\s*(\d+)\s*g\s*</strong>",
    re.IGNORECASE,
)
RE_WEIGHT_SET = re.compile(
    r"Setgewicht[:\s]+<strong>\s*[≈~]?\s*(\d+)\s*g\s*</strong>",
    re.IGNORECASE,
)

# OVP-Maße: "OVP-Maße: <strong>19.1 x 26.2 x 6.1 cm</strong>"
# Dezimaltrennzeichen ist Punkt (nicht Komma) bei Abmessungen auf brickmerge
RE_BOX_DIMS = re.compile(
    r"OVP-Ma[ße]{1,2}e?[:\s]+<strong>\s*"
    r"([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*cm\s*</strong>",
    re.IGNORECASE,
)

# Haendler-VE: "Händler-VE: <strong>6/Karton</strong>"
RE_DEALER_VE = re.compile(
    r"H[äa]ndler-VE[:\s]+<strong>\s*(\d+)/Karton\s*</strong>",
    re.IGNORECASE,
)

# Datumsangaben im Format "MM/YYYY": Release, EOL
RE_RELEASE = re.compile(r"Release[:\s]+<strong>\s*(\d{2}/\d{4})\s*</strong>", re.IGNORECASE)
RE_EOL_DATE = re.compile(r"EOL[:\s]+<strong>\s*(\d{2}/\d{4})\s*</strong>",     re.IGNORECASE)

# PLC: "PLC: <strong>19 Monate</strong>"
RE_PLC = re.compile(r"PLC[:\s]+<strong>\s*(\d+)\s*Monat\w*\s*</strong>", re.IGNORECASE)

# 30-Tage-Bestpreis (analog zu RE_BEST_180D)
RE_BEST_30D = re.compile(r"30\s*Tage\s*Bestpreis:\s*" + _PRICE_AFTER, re.IGNORECASE)

# POV-Wiederverkaufswert: "POV: ca. <strong>35,33&nbsp;&euro;</strong>"
# 'ca.' kann vor oder innerhalb des <strong>-Tags stehen
RE_POV = re.compile(
    r"POV:\s*(?:ca\.)?\s*<strong>\s*(?:ca\.)?\s*([\d.]+,\d{2})\s*(?:&nbsp;)?\s*&euro;\s*</strong>",
    re.IGNORECASE,
)
# POV-Rate: "Rate: 2,6" (deutsche Dezimalzahl, kein &euro;)
RE_POV_RATE = re.compile(r"Rate:\s*([\d]+,[\d]+)", re.IGNORECASE)

# ── Minifiguren-Pattern ───────────────────────────────────────────────────────
# "Minifiguren: <strong>11</strong> (davon <strong>10</strong> exklusiv...)"
# oder: "davon 10 exklusiv in diesem Set" (ohne inner <strong>)
RE_MINIFIGS_TOTAL = re.compile(
    r"Minifiguren[:\s]+<strong>\s*(\d+)\s*</strong>",
    re.IGNORECASE,
)
RE_MINIFIGS_EXCL = re.compile(
    r"davon\s+(?:<[^>]+>)?(\d+)(?:<[^>]+>)?\s+exklusiv",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════════════

def _to_float(de_price: str) -> float:
    """'1.299,00' -> 1299.00 ; '679,99' -> 679.99  (Deutsche Schreibweise)"""
    return float(de_price.replace(".", "").replace(",", "."))


def _parse_month_year(raw: str) -> str | None:
    """
    Konvertiert Brickmerge-Datumsformat 'MM/YYYY' nach ISO 'YYYY-MM'.
    Gibt None zurueck wenn raw kein gueltiges Format hat.
    """
    try:
        mm, yyyy = raw.strip().split("/")
        if len(mm) == 2 and len(yyyy) == 4:
            return f"{yyyy}-{mm}"
    except (ValueError, AttributeError):
        pass
    return None


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


def _extract_int(pattern: re.Pattern, html: str) -> int | None:
    """Extrahiert erste Capture-Group aus pattern als int, oder None."""
    m = pattern.search(html)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _extract_box_dims(html: str) -> tuple[float | None, float | None, float | None]:
    """
    Extrahiert OVP-Abmessungen (L x B x H) in cm.
    Gibt (None, None, None) wenn kein Match.
    Dezimaltrennzeichen ist Punkt (englische Schreibweise bei Massen auf brickmerge).
    """
    m = RE_BOX_DIMS.search(html)
    if not m:
        return None, None, None
    try:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    except (TypeError, ValueError):
        return None, None, None


def _extract_pov_rate(html: str) -> float | None:
    """Extrahiert die POV-Rate (z.B. '2,6') als float."""
    m = RE_POV_RATE.search(html)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _extract_minifigs(html: str) -> tuple[int | None, int | None]:
    """
    Extrahiert Minifiguren-Gesamtanzahl und exklusive Anzahl.

    Brickmerge zeigt z.B.:
      'Minifiguren: <strong>11</strong> (davon <strong>10</strong> exklusiv in diesem Set)'
      'Minifiguren: <strong>11</strong> (davon 10 exklusiv in diesem Set)'

    Gibt (total, exclusive) zurueck; exclusive ist None wenn nicht angegeben
    (Set hat keine exklusiven Figs, oder Brickmerge zeigt es nicht an).
    Gibt (None, None) zurueck wenn keine Minifiguren auf der Seite.
    """
    m_total = RE_MINIFIGS_TOTAL.search(html)
    if not m_total:
        return None, None
    total = int(m_total.group(1))
    # Exklusiv-Angabe im 300-Zeichen-Fenster direkt nach dem Total-Match suchen
    window = html[m_total.start(): m_total.start() + 300]
    m_excl = RE_MINIFIGS_EXCL.search(window)
    excl = int(m_excl.group(1)) if m_excl else None
    return total, excl


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
        box_l, box_w, box_h = _extract_box_dims(html)

        # Datumsfelder: 'MM/YYYY' → 'YYYY-MM'
        m_release = RE_RELEASE.search(html)
        m_eol     = RE_EOL_DATE.search(html)

        return MarketPrices(
            set_no                      = set_no,
            name                        = _extract_name(html),
            ean                         = ean,
            uvp_original                = uvp_orig,
            uvp_current                 = uvp_curr,
            best_price_alltime          = _to_float(m_alltime.group(1)) if m_alltime else None,
            best_price_alltime_days_ago = int(m_alltime.group(2))       if m_alltime else None,
            best_price_180d             = _extract_price(RE_BEST_180D,    html),
            best_price_30d              = _extract_price(RE_BEST_30D,     html),
            best_price_current          = _extract_price(RE_BEST_CURRENT, html),
            seller_count                = _extract_seller_count(html),
            # ── Produkt-Stammdaten ──────────────────────────────────────────
            piece_count                 = _extract_int(RE_PIECE_COUNT,  html),
            age_min                     = _extract_int(RE_AGE_MIN,      html),
            weight_part_g               = _extract_int(RE_WEIGHT_PARTS, html),
            weight_set_g                = _extract_int(RE_WEIGHT_SET,   html),
            box_l_cm                    = box_l,
            box_w_cm                    = box_w,
            box_h_cm                    = box_h,
            dealer_pack_qty             = _extract_int(RE_DEALER_VE, html),
            release_month               = _parse_month_year(m_release.group(1)) if m_release else None,
            eol_month                   = _parse_month_year(m_eol.group(1))     if m_eol     else None,
            plc_months                  = _extract_int(RE_PLC, html),
            # ── POV ─────────────────────────────────────────────────────────
            pov                         = _extract_price(RE_POV, html),
            pov_rate                    = _extract_pov_rate(html),
            # ── Minifiguren ──────────────────────────────────────────────
            **dict(zip(
                ('minifig_count', 'minifig_exclusive_count'),
                _extract_minifigs(html),
            )),
            source                      = self.name,
            url                         = fetch_url,
            fetched_at                  = now_iso(),
        )
