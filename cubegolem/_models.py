#!/usr/bin/env python3
"""
techndev-providers  cubegolem/_models.py  v1.0.0
==================================================
Gemeinsame Datenklassen fuer den cubegolem-Provider.

cubegolem.de ist ein B2B-Haendler-Shop (PrestaShop). Preise sind nur
eingeloggt sichtbar; der hier abgebildete EK ist der Haendler-Einkaufspreis
(netto, zzgl. MwSt — der Shop weist keinen MwSt-Hinweis am Preis aus,
Annahme netto, siehe scraper.py).

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release.
  - Product: Stammdaten + EK/Basispreis (netto) + Release-/Bestellfrist +
    EAN/SKU/Hersteller + Bild. price_is_live-Flag analog brickmerge.
  - Section: Hauptkategorie + Liste ihrer Unterkategorie-Slugs.
  - now_iso(): einheitlicher ISO-Timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime

__version__ = "1.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# Sektion (Hauptkategorie)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Section:
    """
    Eine Hauptkategorie ("Sektion") des Shops.

    slug:           URL-Slug, z.B. 'magic-the-gathering'
                    → https://cubegolem.de/section/<slug>
    name:           Anzeigename, z.B. 'Magic: The Gathering'
    subcategories:  Slugs der Unterkategorien (ohne fuehrenden Unterstrich),
                    abrufbar via /category/<sub>?section=<slug>.
    product_count:  Vom Shop gemeldete Artikelzahl (None = unbekannt).
    """
    slug:           str
    name:           str
    subcategories:  list[str] = field(default_factory=list)
    product_count:  int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Produkt
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Product:
    """
    Ein Produkt aus cubegolem.de.

    Preise in EUR. ek_net/base_net sind NETTO (siehe Modul-Docstring).
      ek_net:       Tatsaechlicher Haendler-EK (.current-price), nach Rabatt.
      base_net:     Listenpreis ("Basispreis:"). Fehlt der Basispreis im Shop,
                    wird base_net == ek_net gesetzt und discount_pct = 0.0.
      discount_pct: 1 - ek_net/base_net, auf 3 Nachkommastellen gerundet
                    (0.20 = 20 %). None nur wenn base_net unbekannt.

    release_date:   Erscheinungsdatum als ISO 'YYYY-MM-DD' (Vorbestellung).
                    None = kein Vorbestelldatum hinterlegt ⇒ in_stock=True.
    order_deadline: Bestellfrist (Vorbestellung) als ISO 'YYYY-MM-DD' oder None.
    in_stock:       True wenn kein release_date in der Zukunft hinterlegt ist
                    (= regulaer lieferbar/lagernd).

    ean / sku:      EAN (GTIN) und Hersteller-Art.-Nr. von der Detailseite —
                    Schluessel fuer EAN-Matching zu Amazon/eBay.

    price_is_live:  True  = ek_net/base_net gerade live geholt.
                    False = aus SQLite-Cache (TTL noch gueltig oder Offline-
                            Fallback). Anzeigeschichten MUESSEN das pruefen.
    """
    section:        str
    slug:           str
    name:           str
    url:            str
    # ── Preise (netto) ───────────────────────────────────────────────────────
    ek_net:         float | None
    base_net:       float | None
    discount_pct:   float | None
    currency:       str = "EUR"
    # ── Verfuegbarkeit ───────────────────────────────────────────────────────
    release_date:   str | None  = None   # 'YYYY-MM-DD'
    order_deadline: str | None  = None   # 'YYYY-MM-DD'
    in_stock:       bool        = True
    # ── Stammdaten ───────────────────────────────────────────────────────────
    category:       str | None  = None   # Unterkategorie-Slug (Fundort)
    manufacturer:   str | None  = None
    ean:            str | None  = None
    sku:            str | None  = None   # Hersteller-Art.-Nr.
    image_url:      str | None  = None
    # ── Herkunft ─────────────────────────────────────────────────────────────
    fetched_at:     str         = ""
    price_is_live:  bool        = True

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Hilfen
# ══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    """Einheitlicher ISO-Timestamp fuer fetched_at-Felder."""
    return datetime.now().isoformat(timespec="seconds")
