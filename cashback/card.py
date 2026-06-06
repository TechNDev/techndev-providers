#!/usr/bin/env python3
"""cashback.card — Kreditkarten-Cashback: globaler %-Satz auf JEDEN Kauf.

Config `card: <rate>` (z.B. 0.01 = 1 %). Gilt merchant-uebergreifend (merchant="*").
3rd-party-Auszahlung -> vat_link=False."""
from __future__ import annotations

from typing import Iterable, Optional

from .base import CashbackOffer


class CardResolver:
    category = "card"

    def resolve(self, *, merchant: str = "", ean: Optional[str] = None,
                config: Optional[dict] = None) -> Iterable[CashbackOffer]:
        rate = float((config or {}).get("card") or 0.0)
        if rate <= 0:
            return []
        return [CashbackOffer(
            merchant="*", kind="percent", value=rate, category=self.category,
            vat_link=False, source="card", label=f"Karte {rate*100:.1f} %",
        )]
