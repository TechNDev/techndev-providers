"""
techndev-providers  cubegolem  v1.0.0
=======================================
cubegolem.de Datenprovider — Haendler-EK-Preise (netto) + Stammdaten.

cubegolem.de ist ein B2B-Haendlershop (PrestaShop). Preise sind nur
eingeloggt sichtbar → ein Session-Cookie ist Pflicht (siehe _auth.py).

Exports:
  Product             — Produkt-Stammdaten + EK/Basispreis + Release/Bestellfrist
                        + EAN/SKU/Hersteller + Bild
  Section             — Hauptkategorie + Unterkategorie-Slugs
  CubeGolemProvider   — Live-Scraper (braucht session_cookie)
  CubeGolemCache      — SQLite-Cache: get() (Cache-first) / get_live() (live)
  SessionExpiredError — Session-Cookie fehlt/abgelaufen
  list_sections()     — Kurzform: alle Hauptkategorien
  get_section()       — Kurzform: alle Produkte einer Sektion
  now_iso()           — ISO-Timestamp-Helfer

Schnellstart:
    from cubegolem import CubeGolemProvider
    prov = CubeGolemProvider(session_cookie=COOKIE)
    produkte = prov.get_section("magic-the-gathering")

Hinweis: Preise sind NETTO (zzgl. MwSt) — Annahme, da der Shop keinen
MwSt-Hinweis am Preis ausweist.
"""
from ._auth    import SessionExpiredError
from ._models  import Product, Section, now_iso
from .scraper  import CubeGolemProvider, list_sections, get_section
from .cache    import CubeGolemCache
from .store    import CubeGolemStore

__all__ = [
    "Product",
    "Section",
    "now_iso",
    "CubeGolemProvider",
    "CubeGolemCache",
    "CubeGolemStore",
    "SessionExpiredError",
    "list_sections",
    "get_section",
]
__version__ = "1.0.0"
