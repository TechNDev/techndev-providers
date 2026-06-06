#!/usr/bin/env python3
"""cashback.manufacturer — Hersteller-Aktionen (z.B. LEGO): EAN -> fester/prozentualer
Cashback mit Ablaufdatum.

Config `manufacturer: [{ean, value, kind, valid_to?, cap?, min_order?, label?}]`.
Match per EAN. Herstelleraktion = 3rd party (Erstattung) -> vat_link=False (Default,
per Eintrag uebersteuerbar)."""
from __future__ import annotations

from typing import Iterable, Optional

from .base import CashbackOffer


class ManufacturerResolver:
    category = "manufacturer"

    def resolve(self, *, merchant: str = "", ean: Optional[str] = None,
                config: Optional[dict] = None) -> Iterable[CashbackOffer]:
        entries = (config or {}).get("manufacturer") or []
        if not ean:
            return []
        out: list[CashbackOffer] = []
        for e in entries:
            if str(e.get("ean") or "").strip() != str(ean).strip():
                continue
            value = float(e.get("value") or 0.0)
            if value <= 0:
                continue
            kind = e.get("kind") or "fixed"
            out.append(CashbackOffer(
                merchant=str(e.get("merchant") or "*"),
                kind=kind, value=value, category=self.category,
                vat_link=bool(e.get("vat_link", False)),
                ean=str(ean).strip(),
                valid_to=e.get("valid_to"), cap=e.get("cap"),
                min_order=e.get("min_order"),
                source="manufacturer",
                label=e.get("label") or (f"Hersteller {value:.2f} EUR" if kind == "fixed"
                                         else f"Hersteller {value*100:.1f} %"),
            ))
        return out
