#!/usr/bin/env python3
"""
techndev-providers  cubegolem/scraper.py  v1.0.0
==================================================
Live-Scraper fuer cubegolem.de (PrestaShop, B2B-Haendlershop).

Scraping-Strategie (gegen das HTML vom Mai 2026 verprobt):
  - Preise NUR eingeloggt sichtbar → Session-Cookie noetig (_auth.py).
  - /section/<slug>            : rendert nur eine generische Shell (Grid kommt
                                 client-seitig nach) → NICHT zum Enumerieren.
  - /category/<sub>?section=…  : server-gerendert + paginierbar (&page=N),
                                 15 Produkte/Seite im Container #js-product-list.
                                 → kanonischer Enumerations-Endpunkt.
  - /product/<slug>            : Detailseite mit EK (.current-price),
                                 Basispreis, Bild (.product-cover[data-src]),
                                 EAN, Art.-Nr., Hersteller.
  - Erscheinungsdatum/Bestellfrist stehen NUR im Grid, nicht auf der Detailseite.

Sektion ⇒ Produkte: Die Produkte einer Sektion sind die Vereinigung ihrer
Unterkategorien. Unterkategorien werden aus dem globalen Kategorie-<select>
abgeleitet (Reihenfolge kodiert die Hierarchie; Sub-Werte mit fuehrendem '_').

Preise sind NETTO. cubegolem weist am Preis keinen MwSt-Hinweis aus; in einem
B2B-Haendlershop sind EK-Preise konventionell netto. Annahme dokumentiert,
nicht serverseitig bestaetigt.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. CubeGolemProvider: list_sections(), get_section(),
    get_product(). Enumeration via #js-product-list, Detail-Parsing via Regex.
"""
from __future__ import annotations

import re
import sys
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ._auth   import SessionExpiredError, assert_logged_in, normalize_cookie
from ._models import Product, Section, now_iso
from ._rate   import http_limiter, _retry

__version__ = "1.0.0"

# ══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ══════════════════════════════════════════════════════════════════════════════

BASE_URL   = "https://cubegolem.de"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PER_PAGE       = 15     # Produkte je Grid-Seite (Shop-Default)
MAX_PAGE_GUARD = 100    # Sicherheitsnetz gegen Endlos-Paginierung

# Deutsche Monatskuerzel → Monatszahl (lowercase, erste 3 Zeichen)
_MONTHS = {
    "jan": "01", "feb": "02", "mär": "03", "mrz": "03", "mar": "03",
    "apr": "04", "mai": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "okt": "10", "nov": "11", "dez": "12",
}

# ── Detailseiten-Pattern ──────────────────────────────────────────────────────
RE_EK    = re.compile(r'current-price[\s\S]{0,300}?([\d.]+,\d{2})\s*(?:&euro;|€|&#8364;)', re.I)
RE_BASE  = re.compile(r'Basispreis:[\s\S]{0,200}?([\d.]+,\d{2})\s*(?:&euro;|€|&#8364;)', re.I)
RE_COVER = re.compile(r'<img[^>]*js-qv-product-cover[^>]*>', re.I)
RE_DATA  = re.compile(r'data-(?:zoom-image|src|image-large-src)="([^"]+)"', re.I)
RE_EAN   = re.compile(r'EAN:\s*(?:<[^>]+>\s*)*(\d{8,14})', re.I)
RE_SKU   = re.compile(r'Art\.?\s*Nr\.?:\s*(?:<[^>]+>\s*)*([A-Za-z0-9][A-Za-z0-9\-_.]{1,30})', re.I)
RE_MFR   = re.compile(r'href="[^"]*/manufacturer/[^"]*"[^>]*>\s*([^<]+?)\s*<', re.I)
RE_TITLE = re.compile(r'<title>\s*(.*?)\s*</title>', re.I | re.S)

# ── Grid-Pattern ──────────────────────────────────────────────────────────────
RE_ARTICLE     = re.compile(r'<article\b[\s\S]*?</article>', re.I)
RE_PROD_SLUG   = re.compile(r'/product/([a-z0-9][a-z0-9\-]*)', re.I)
RE_NAME_PNAME  = re.compile(r'class="[^"]*product-name[^"]*"[^>]*title="([^"]+)"', re.I)
RE_NAME_THUMB  = re.compile(r'product-thumbnail[^>]*title="([^"]+)"', re.I)
RE_NAME_ANY    = re.compile(r'/product/[^"]+"[^>]*title="([^"]+)"', re.I)

# ── Kategorie-<select>-Pattern ────────────────────────────────────────────────
RE_OPTION = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', re.I | re.S)


# ══════════════════════════════════════════════════════════════════════════════
# Parsing-Helfer
# ══════════════════════════════════════════════════════════════════════════════

def _money(s: str | None) -> float | None:
    """'1.234,56' → 1234.56 ; None/leer → None."""
    if not s:
        return None
    m = re.search(r'([\d.]+),(\d{2})', s)
    if not m:
        return None
    return float(m.group(1).replace(".", "") + "." + m.group(2))


def _de_date(s: str | None) -> str | None:
    """
    Deutsche Datumsformate → ISO 'YYYY-MM-DD'.
      'Fr., 02. Okt 2026' und '02.10.2026' werden unterstuetzt.
    None/unparsebar → None.
    """
    if not s:
        return None
    s = s.strip()
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', s)          # TT.MM.JJJJ
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r'(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]{3,})\.?\s*(\d{4})', s)  # TT. Mon JJJJ
    if m:
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            return f"{m.group(3)}-{mon}-{int(m.group(1)):02d}"
    return None


def _grid_slice(html: str) -> str:
    """
    Schneidet den Produkt-Listen-Container (#js-product-list) heraus —
    ohne fuehrende/abschliessende Promo-Karussells.
    """
    start = html.find("js-product-list")
    if start < 0:
        return ""                       # Section-Shell o.ae. → keine echte Liste
    ends = [p for p in (
        html.find('class="pagination', start),
        html.find("<nav", start),
        html.find("<footer", start),
        html.find('id="footer"', start),
    ) if p != -1]
    end = min(ends) if ends else len(html)
    return html[start:end]


def _parse_grid(html: str) -> list[dict]:
    """
    Liefert pro Grid-Card: {slug, name, release_date, order_deadline}.
    Nur Produkte aus #js-product-list (keine Promo-Artikel).
    """
    out: list[dict] = []
    for block in RE_ARTICLE.findall(_grid_slice(html)):
        ms = RE_PROD_SLUG.search(block)
        if not ms:
            continue
        slug = ms.group(1)
        name = (RE_NAME_PNAME.search(block) or RE_NAME_THUMB.search(block)
                or RE_NAME_ANY.search(block))
        name = unescape(name.group(1)).strip() if name else slug
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block))
        er = (re.search(r'Erscheint:\s*(\d{2}\.\d{2}\.\d{4})', text)
              or re.search(r'Erscheinungsdatum\s+([A-Za-zäöü.]*,?\s*\d{1,2}\.\s*[A-Za-zäöü]+\.?\s*\d{4})', text))
        fr = (re.search(r'Frist:\s*(\d{2}\.\d{2}\.\d{4})', text)
              or re.search(r'Bestellfrist\s+([A-Za-zäöü.]*,?\s*\d{1,2}\.\s*[A-Za-zäöü]+\.?\s*\d{4})', text))
        out.append({
            "slug": slug,
            "name": name,
            "release_date":   _de_date(er.group(1) if er else None),
            "order_deadline": _de_date(fr.group(1) if fr else None),
        })
    return out


def _parse_sections(html: str) -> list[Section]:
    """
    Baut die Sektions-/Unterkategorie-Hierarchie aus dem globalen <select>.
    Sektion = value ohne fuehrenden '_'; Unterkategorie = value mit '_'
    (Slug = value[1:]), gehoert zur zuletzt gesehenen Sektion.
    """
    sections: list[Section] = []
    current: Section | None = None
    for value, label in RE_OPTION.findall(html):
        value = value.strip()
        if not value or value == "all":
            continue
        name = unescape(re.sub(r"<[^>]+>", "", label)).strip().lstrip("·").strip()
        if value.startswith("_"):
            if current is not None:
                current.subcategories.append(value[1:])
        else:
            current = Section(slug=value, name=name)
            sections.append(current)
    return sections


# ══════════════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════════════

class CubeGolemProvider:
    """
    Live-Scraper fuer cubegolem.de.

    session_cookie: Cookie aus dem eingeloggten Browser (siehe _auth.py).
                    Ohne gueltige Session sind keine Preise abrufbar.

    Typische Nutzung:
        prov = CubeGolemProvider(session_cookie=cfg["cookie"])
        for sec in prov.list_sections():
            print(sec.slug, sec.name, len(sec.subcategories))
        produkte = prov.get_section("magic-the-gathering")
    """

    def __init__(self, session_cookie="", *, base_url: str = BASE_URL,
                 timeout: float = 30.0):
        self.cookie   = normalize_cookie(session_cookie)
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    # ── HTTP ──────────────────────────────────────────────────────────────────
    @_retry
    def _get(self, path: str) -> str:
        """GET <base>/<path> → HTML. Rate-limited + Retry. Wirft bei HTTP-Fehler."""
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "User-Agent":      USER_AGENT,
            "Accept-Language": "de-DE,de;q=0.9",
            "Accept":          "text/html,application/xhtml+xml",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        http_limiter.wait()
        try:
            with urlopen(Request(url, headers=headers), timeout=self.timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} fuer {url}") from e
        except URLError as e:
            raise RuntimeError(f"Netzwerkfehler fuer {url}: {e.reason}") from e

    # ── Sektionen ─────────────────────────────────────────────────────────────
    def list_sections(self) -> list[Section]:
        """Alle Hauptkategorien inkl. ihrer Unterkategorie-Slugs."""
        return _parse_sections(self._get("/"))

    # ── Produkt-Detail ────────────────────────────────────────────────────────
    def get_product(self, slug: str, *, section: str = "",
                    category: str | None = None,
                    release_date: str | None = None,
                    order_deadline: str | None = None,
                    validate: bool = True) -> Product:
        """
        Detailseite eines Produkts → Product (Preise live).
        release_date/order_deadline koennen aus dem Grid uebergeben werden
        (stehen nicht auf der Detailseite).
        """
        html = self._get(f"/product/{slug}")
        if validate:
            assert_logged_in(html)

        ek   = _money(RE_EK.search(html).group(1)) if RE_EK.search(html) else None
        base = _money(RE_BASE.search(html).group(1)) if RE_BASE.search(html) else None
        # Fehlt der Basispreis, gilt der EK als Listenpreis (kein Rabatt).
        if base is None and ek is not None:
            base = ek
        discount = None
        if ek is not None and base:
            discount = round(1 - ek / base, 3)

        image = None
        mc = RE_COVER.search(html)
        if mc:
            md = RE_DATA.search(mc.group(0))
            image = md.group(1) if md else None

        ean = RE_EAN.search(html)
        sku = RE_SKU.search(html)
        mfr = RE_MFR.search(html)
        name = ""
        mt = RE_TITLE.search(html)
        if mt:
            name = re.sub(r"\s*[-–]\s*CubeGolem.*$", "", unescape(mt.group(1))).strip()

        return Product(
            section=section,
            slug=slug,
            name=name or slug,
            url=f"{self.base_url}/product/{slug}",
            ek_net=ek,
            base_net=base,
            discount_pct=discount,
            release_date=release_date,
            order_deadline=order_deadline,
            in_stock=release_date is None,
            category=category,
            manufacturer=unescape(mfr.group(1)).strip() if mfr else None,
            ean=ean.group(1) if ean else None,
            sku=sku.group(1) if sku else None,
            image_url=image,
            fetched_at=now_iso(),
            price_is_live=True,
        )

    # ── Sektion ⇒ Produkte ────────────────────────────────────────────────────
    def _iter_pages(self, path_template: str):
        """Generator: Grid-Eintraege ueber paginierte Seiten ('{page}'-Platzhalter)."""
        page = 1
        while page <= MAX_PAGE_GUARD:
            entries = _parse_grid(self._get(path_template.format(page=page)))
            if not entries:
                break
            yield from entries
            if len(entries) < PER_PAGE:
                break
            page += 1

    def iter_grid(self, section_slug: str, subcategory: str):
        """Generator: alle Grid-Eintraege einer Unterkategorie (paginiert)."""
        sub = quote(subcategory, safe="")          # Slugs koennen Leerzeichen u.ae. enthalten
        sec = quote(section_slug, safe="")
        yield from self._iter_pages(
            f"/category/{sub}?section={sec}&page={{page}}")

    def iter_section_grid(self, section_slug: str):
        """Generator: Grid-Eintraege direkt ueber die Sektionsseite (Fallback
        fuer Sektionen ohne Unterkategorien)."""
        sec = quote(section_slug, safe="")
        yield from self._iter_pages(f"/section/{sec}?page={{page}}")

    def get_section(self, section_slug: str, *, with_prices: bool = True,
                    max_products: int | None = None,
                    progress=None) -> list[Product]:
        """
        Alle Produkte einer Sektion (Vereinigung aller Unterkategorien),
        dedupliziert. with_prices=False ueberspringt die Detailseiten-Abrufe
        (nur Grid-Stammdaten: name + Datum).

        progress: optionales Callback progress(done, total, slug).
        """
        section = next((s for s in self.list_sections()
                        if s.slug == section_slug), None)
        if section is None:
            raise ValueError(f"Unbekannte Sektion: {section_slug!r}")

        # 1) Grid-Eintraege sammeln + deduplizieren.
        #    Primaer ueber Unterkategorien; hat eine Sektion keine, direkt ueber
        #    die Sektionsseite (z.B. rise-tcg: Produkte ohne Unterkategorie).
        grid: dict[str, dict] = {}

        def _collect(entries, category):
            for e in entries:
                prev = grid.get(e["slug"])
                if prev is None:
                    e["category"] = category
                    grid[e["slug"]] = e
                elif e["release_date"] and not prev["release_date"]:
                    prev.update(release_date=e["release_date"],
                                order_deadline=e["order_deadline"])

        if section.subcategories:
            for sub in section.subcategories:
                try:
                    _collect(self.iter_grid(section_slug, sub), sub)
                except Exception as e:                       # noqa: BLE001
                    print(f"  [warn] Unterkategorie {sub!r}: {e}", file=sys.stderr)
        else:
            try:
                _collect(self.iter_section_grid(section_slug), None)
            except Exception as e:                           # noqa: BLE001
                print(f"  [warn] Sektion {section_slug!r}: {e}", file=sys.stderr)

        slugs = list(grid)
        if max_products is not None:
            slugs = slugs[:max_products]

        # 2) Detailseiten (Preise/Bild/EAN) oder nur Grid-Stammdaten
        def _grid_only(g) -> Product:
            return Product(
                section=section_slug, slug=g_slug, name=g["name"],
                url=f"{self.base_url}/product/{g_slug}",
                ek_net=None, base_net=None, discount_pct=None,
                release_date=g["release_date"],
                order_deadline=g["order_deadline"],
                in_stock=g["release_date"] is None,
                category=g.get("category"), fetched_at=now_iso(),
            )

        products: list[Product] = []
        total = len(slugs)
        for i, g_slug in enumerate(slugs, 1):
            g = grid[g_slug]
            if with_prices:
                try:
                    p = self.get_product(
                        g_slug, section=section_slug, category=g.get("category"),
                        release_date=g["release_date"],
                        order_deadline=g["order_deadline"], validate=(i == 1),
                    )
                    # Detail-<title> ist der lesbare Name; Grid-Name nur als
                    # Fallback (bei Zubehoer fehlt das title-Attribut -> dort = slug).
                    if not p.name:
                        p.name = g["name"]
                except SessionExpiredError:
                    raise                           # Session weg → Lauf abbrechen
                except Exception as e:              # noqa: BLE001
                    print(f"  [warn] {g_slug}: {e}", file=sys.stderr)
                    p = _grid_only(g)               # Zeile nicht verlieren
            else:
                p = _grid_only(g)
            products.append(p)
            if progress:
                progress(i, total, g_slug)
        return products


# ══════════════════════════════════════════════════════════════════════════════
# Modul-weite Convenience
# ══════════════════════════════════════════════════════════════════════════════

def list_sections(session_cookie="") -> list[Section]:
    """Kurzform ohne explizite Provider-Instanz."""
    return CubeGolemProvider(session_cookie).list_sections()


def get_section(section_slug: str, session_cookie="", **kwargs) -> list[Product]:
    """Kurzform ohne explizite Provider-Instanz."""
    return CubeGolemProvider(session_cookie).get_section(section_slug, **kwargs)
