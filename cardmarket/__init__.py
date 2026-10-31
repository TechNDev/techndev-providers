#!/usr/bin/env python3
"""
cardmarket  v1.4.0
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
  cm.get_order(idOrder)                # Verkaufs-Order inkl. Produktblock
  cm.iter_orders("seller", "paid")     # alle bezahlten Orders (seitenweise)
  cm.send_order(idOrder)               # Versand bestaetigen paid -> sent (SCHREIBT)
  cm.set_tracking(idOrder, "RR123DE")  # Sendungsnummer, nur bei sent (SCHREIBT)
  cm.get_expansions() / cm.get_singles(idExpansion) / cm.get_product(id)
  cm.add_stock([{idProduct, count, price, condition, idLanguage, ...}])  # SCHREIBT
  cm.get_stock_article(idArticle) / cm.change_stock([{idArticle, ...}])  # PUT SCHREIBT

CHANGELOG
---------
v1.4.0  (2026-10-31)
  - API-Tageslimit: limit_stand() liefert den zuletzt gemeldeten Stand
    (X-Request-Limit-Count/-Max), auch nach HTTP 429.

v1.3.0  (2026-10-27)
  - Orders schreiben: send_order() bestaetigt den Versand (PUT /order/{id},
    action=send), set_tracking() setzt die Sendungsnummer (PUT /order/{id}/tracking).

v1.2.0  (2026-10-25)
  - Bestand schreiben: add_stock() per POST /stock, change_stock() per PUT /stock
    (XML, 100er-Pakete), get_stock_article() liest einen eigenen Artikel.
  - get_cheapest_offer: optionale Filter is_foil, exclude_seller, plain_only.
  - Lesend fuer die Zuordnung: get_expansions, get_singles (24-h-Cache), get_product.

v1.1.0  (2026-10-15)
  - Orders lesen: get_order / get_orders / iter_orders (Auftragsimport JTL).
  - 204-Antworten (leerer Body) liefern {} statt JSON-Fehler.

v1.0.0  (2026-06-12)
  - Initiales Release: OAuth1-Client, Bulk productlist/priceguide (+24h-Cache),
    get_price_guide/get_cheapest_offer, Config-Auto-Discovery, CLI.
"""
from __future__ import annotations

from .client import CardmarketClient, limit_stand, load_config

__version__ = "1.4.0"
__all__ = ["CardmarketClient", "limit_stand", "load_config"]
