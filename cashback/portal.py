#!/usr/bin/env python3
"""cashback.portal — Cashback-Portale (Shoop/iGraal): Shop -> %-Satz (manuelle Config).

Portale haben keine offene API -> Saetze werden manuell in cashback_config.json
gepflegt (`portal: {merchant: rate}`). 3rd-party-Auszahlung -> vat_link=False."""
from __future__ import annotations

from typing import Iterable, Optional

from .base import CashbackOffer, _norm_merchant


class PortalResolver:
    category = "portal"

    def resolve(self, *, merchant: str = "", ean: Optional[str] = None,
                config: Optional[dict] = None) -> Iterable[CashbackOffer]:
        cfg = (config or {}).get("portal") or {}
        m = _norm_merchant(merchant)
        if not m or m not in cfg:
            return []
        rate = float(cfg[m])
        if rate <= 0:
            return []
        return [CashbackOffer(
            merchant=m, kind="percent", value=rate, category=self.category,
            vat_link=False, source="portal", label=f"Portal {rate*100:.1f} %",
        )]
