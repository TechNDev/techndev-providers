#!/usr/bin/env python3
"""
techndev-providers  brickmerge/_models.py  v1.1.0
===================================================
Gemeinsame Datenklassen fuer den brickmerge-Provider.
Vorher: SetInfo in mydealz-watcher/setcatalog.py,
        MarketPrices + now_iso() in mydealz-watcher/pricesource.py.

CHANGELOG
---------
v1.2.0  (2026-05-26)
  - MarketPrices: minifig_count, minifig_exclusive_count (beide None-defaulted).
    Exklusive Minifiguren koennen eigenstaendigen Wiederverkaufswert haben.

v1.1.0  (2026-05-26)
  - MarketPrices: 14 neue optionale Felder (alle None-defaulted → 100 %
    rueckwaertskompatibel). Neu: piece_count, weight_part_g, weight_set_g,
    box_l_cm, box_w_cm, box_h_cm, age_min, release_month, eol_month,
    plc_months, dealer_pack_qty, best_price_30d, pov, pov_rate.

v1.0.0  (2026-05-25)
  - SetInfo: wie setcatalog.SetInfo + status ('active'|'eol') + eol_year.
    Defaults: status='active', eol_year=None → 100 % rueckwaertskompatibel.
  - MarketPrices: wie pricesource.MarketPrices + seller_count (None=unbekannt).
    Bestehende Aufrufer, die seller_count nicht setzen, bekommen None.
  - now_iso(): einheitlicher ISO-Timestamp fuer fetched_at.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime

__version__ = "1.2.0"


# ══════════════════════════════════════════════════════════════════════════════
# Set-Stammdaten
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SetInfo:
    """
    Alle katalogisierten Felder zu einem LEGO-Set.

    status: 'active' = im aktuellen Brickmerge-Katalog,
            'eol'    = aus einem EOL-Jahrgang-CSV.
    eol_year: None fuer aktive Sets; Jahreszahl fuer EOL-Sets
              (z.B. 2023 = EOL-Liste 2023).
    """
    set_no:         str
    name:           str
    theme:          str
    uvp:            float | None
    year:           int   | None
    ean:            str   | None
    asin:           str   | None
    brickmerge_url: str   | None
    status:         str         = "active"   # 'active' | 'eol'
    eol_year:       int | None  = None

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Marktpreis-Snapshot
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketPrices:
    """
    Marktpreis-Snapshot fuer ein LEGO-Set aus einer Preisquelle.

    Alle Preise in EUR inkl. MwSt.
    None = Feld beim Provider nicht verfuegbar oder nicht parsebar.

    seller_count:   Anzahl aktiver Haendler bei brickmerge.de.
    piece_count:    Anzahl Teile laut Brickmerge.
    weight_part_g:  Teilegewicht in Gramm (ohne Verpackung), gerundet.
    weight_set_g:   Setgewicht in Gramm (mit Verpackung), gerundet.
    box_l_cm:       OVP-Laenge in cm.
    box_w_cm:       OVP-Breite in cm.
    box_h_cm:       OVP-Hoehe in cm.
    age_min:        Empfohlenes Mindestalter (z.B. 8 fuer '8+').
    release_month:  Erscheinungsmonat als ISO-String 'YYYY-MM'.
    eol_month:      End-of-Life-Monat als ISO-String 'YYYY-MM'.
    plc_months:     Produktlebenszyklus in Monaten.
    dealer_pack_qty: Haendler-Verpackungseinheit (Stueck je Karton).
    best_price_30d: Bestpreis der letzten 30 Tage in EUR.
    pov:            Brickmerge POV-Wiederverkaufswert in EUR.
    pov_rate:       POV-Rate (Vielfaches des aktuellen Marktpreises).
    """
    set_no:                       str
    name:                         str   | None
    ean:                          str   | None
    uvp_original:                 float | None
    uvp_current:                  float | None
    best_price_alltime:           float | None
    best_price_alltime_days_ago:  int   | None
    best_price_180d:              float | None
    best_price_current:           float | None
    source:                       str
    url:                          str
    fetched_at:                   str
    # ── optionale Felder (alle None-defaulted → rueckwaertskompatibel) ────────
    seller_count:                 int   | None = None
    piece_count:                  int   | None = None
    weight_part_g:                int   | None = None
    weight_set_g:                 int   | None = None
    box_l_cm:                     float | None = None
    box_w_cm:                     float | None = None
    box_h_cm:                     float | None = None
    age_min:                      int   | None = None
    release_month:                str   | None = None   # 'YYYY-MM'
    eol_month:                    str   | None = None   # 'YYYY-MM'
    plc_months:                   int   | None = None
    dealer_pack_qty:              int   | None = None
    best_price_30d:               float | None = None
    pov:                          float | None = None
    pov_rate:                     float | None = None
    minifig_count:                int   | None = None   # Gesamtzahl Minifiguren
    minifig_exclusive_count:      int   | None = None   # davon exklusiv in diesem Set

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Hilfen
# ══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    """Einheitlicher ISO-Timestamp fuer fetched_at-Felder."""
    return datetime.now().isoformat(timespec="seconds")
