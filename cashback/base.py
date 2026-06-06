#!/usr/bin/env python3
"""
cashback.base
=============
Datenmodell + Resolver-Protokoll fuer den Cashback-Layer (analog sources/PriceSource).

Cashback ist eine dritte EK-Korrektur (neben Warenpreis + Inbound-Versand) und reiner
ANZEIGE-Wert — er beeinflusst NICHT die KAUFEN/ABLEHNEN-Gates (siehe reseller-
profitability.annotate_cashback). Jeder Resolver liefert anwendbare CashbackOffer[]
fuer einen Merchant/EAN; best_cashback() waehlt + stapelt + verrechnet sie netto.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, runtime_checkable


@dataclass
class CashbackOffer:
    """Ein anwendbarer Cashback. value je nach kind: percent (0.06=6 %) oder fixed (EUR)."""
    merchant:  str                       # Shop-Slug/Domain, z.B. "galeria" (oder "*" = ueberall)
    kind:      str                       # "percent" | "fixed"
    value:     float                     # 0.06  oder  20.00
    category:  str = "portal"            # "portal" | "card" | "manufacturer" (Stapel-Gruppe)
    vat_link:  bool = False              # True = Rechnungs-/Lieferantenrabatt (Vorsteuer -> /1.19)
                                         # False = Portal/Karte (3rd party -> voller Netto-Abzug)
    ean:       Optional[str] = None      # None = merchant-weit; gesetzt = produktspezifisch
    valid_to:  Optional[str] = None      # ISO 'YYYY-MM-DD'; abgelaufen -> ignoriert
    cap:       Optional[float] = None     # max. EUR Cashback
    min_order: Optional[float] = None     # Mindestbestellwert (brutto)
    exclusive: bool = True               # nicht mit anderen derselben Gruppe stapelbar
    source:    str = "manual"            # "shoop" | "igraal" | "mydealz" | "card" ...
    label:     str = ""                  # Anzeige, z.B. "Shoop 6 %"


@runtime_checkable
class CashbackResolver(Protocol):
    """Eine Cashback-Quelle. category = Stapel-Gruppe (portal/card/manufacturer)."""
    category: str

    def resolve(self, *, merchant: str = "", ean: Optional[str] = None,
                config: Optional[dict] = None) -> Iterable[CashbackOffer]:
        ...


def _norm_merchant(s: str | None) -> str:
    """Shop-Domain/-Slug normalisieren: lowercase, ohne www./https://, ohne Pfad."""
    if not s:
        return ""
    s = str(s).strip().lower()
    for pre in ("https://", "http://", "www."):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.split("/")[0].strip()
