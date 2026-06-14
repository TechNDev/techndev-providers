#!/usr/bin/env python3
"""
cardmarket  v1.0.0
====================
Provider fuer die Cardmarket-(MKM-)API — Sealed-TCG-Arbitrage (Cardmarket → Amazon).

Reine urllib-Implementierung (kein SDK), OAuth1.0a HMAC-SHA1 (Dedicated-App).
Bulk-Dateien (productlist + priceguide, 1x/Tag aktualisiert) fuer breites
Screening; /articles fuer die LIVE-Kaufentscheidung je Produkt.

Oeffentliche API:
  from cardmarket import CardmarketClient, load_config
  cm = CardmarketClient(load_config())
  cm.account()                         # Auth-Check
  cm.fetch_product_list()              # [{idProduct, Name, Category, Expansion, ...}]
  cm.fetch_price_guide()               # {idProduct: {low, trend, de_pro_low, uvp, ...}}
  cm.get_cheapest_offer(idProduct)     # live guenstigstes Angebot (+ Verkaeufertyp)

CHANGELOG
---------
v1.0.0  (2026-06-12)
  - Initiales Release: OAuth1-Client, Bulk productlist/priceguide (+24h-Cache),
    get_price_guide/get_cheapest_offer, Config-Auto-Discovery, CLI.
"""
from __future__ import annotations

from .client import CardmarketClient, load_config

__version__ = "1.0.0"
__all__ = ["CardmarketClient", "load_config"]
