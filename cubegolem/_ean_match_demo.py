#!/usr/bin/env python3
"""
techndev-providers  cubegolem/_ean_match_demo.py  v1.0.0
==========================================================
Demo: EAN-Matching cubegolem → Amazon (SP-API) + eBay via pipelines.arbitrage.

Nimmt einige MTG-Produkte aus cubegolem (mit EK + EAN), schlaegt sie per EAN
auf Amazon/eBay nach und zeigt Treffer + Marge. Braucht:
  - gueltigen Cookie in cubegolem_config.json
  - SP-API-Creds (Auto-Discovery amazon-vorqualifizierung/amz_einkauf_config.json)
  - eBay-Creds (mydealz-watcher/ebay_config.json)

Lauf (aus techndev-providers/):
    python -m cubegolem._ean_match_demo
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# reseller_profitability liegt als eigenes Repo daneben (kein Submodul hier).
_RP = Path(r"C:\Claude_FS\Code\reseller-profitability")
if str(_RP) not in sys.path:
    sys.path.insert(0, str(_RP))

from .scraper import CubeGolemProvider
from pipelines.arbitrage import evaluate_arbitrage

CONFIG   = Path(__file__).resolve().parent.parent / "cubegolem_config.json"
EBAY_CFG = Path(r"C:\Claude_FS\Code\mydealz-watcher\ebay_config.json")

# Mix bereits erschienener MTG-Artikel (sollten auf Amazon gelistet sein).
SLUGS = [
    "mtg-aetherdrift-play-booster-display-30-boosters-de",
    "mtg-final-fantasy-commander-deck-display-4-decks-de",
    "mtg-final-fantasy-starter-kit-display-12-kits-de",
    "mtg-foundations-beginner-box-us-de",
    "mtg-marvels-spider-man-play-booster-display-30-boosters-us-en",
]


def _eur(x) -> str:
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(x, (int, float)) else "-"


def _pct(x) -> str:
    return f"{x*100:.1f} %" if isinstance(x, (int, float)) else "-"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    prov = CubeGolemProvider(session_cookie=cfg.get("session_cookie", ""))
    ebay_cfg = json.loads(EBAY_CFG.read_text(encoding="utf-8"))

    summary = []
    for slug in SLUGS:
        print("=" * 78)
        try:
            p = prov.get_product(slug)
        except Exception as e:
            print(f"  cubegolem-Fehler {slug}: {e}")
            continue
        print(f"cubegolem : {p.name}")
        print(f"            EK netto {_eur(p.ek_net)}  |  EAN {p.ean or '—'}  |  SKU {p.sku or '—'}")
        if not p.ean:
            print("            keine EAN → kein Matching moeglich")
            summary.append((p.name, p.ek_net, None, None, None, None))
            continue

        res = evaluate_arbitrage(
            ek_netto=p.ek_net or 0.0,
            ean=p.ean,
            ebay_credentials=ebay_cfg,
            include_ebay=True,
            profile="standard",
        )

        if res.asin:
            print(f"Amazon    : ASIN {res.asin}  |  {res.title[:54]}")
            ao = res.amazon_offer or {}
            print(f"            Buy-Box {_eur(ao.get('buy_box_brutto'))} brutto  |  "
                  f"BSR {ao.get('bsr') or '—'}  |  FBA-Seller {ao.get('fba_sellers')}")
        else:
            print("Amazon    : kein Treffer (EAN nicht gelistet)")

        eb = res.ebay_snapshot or {}
        if eb:
            print(f"eBay      : Median verkauft {_eur(eb.get('median_sold'))}  |  "
                  f"verkauft {eb.get('sold_count')}  |  aktiv {eb.get('active_total')}")

        rdict = res.to_dict().get("results", {})
        amz = rdict.get("amazon_fba", {})
        eby = rdict.get("ebay", {})
        if amz:
            print(f"Marge FBA : {_eur(amz.get('margin_eur'))}  |  "
                  f"Marge {_pct(amz.get('margin_pct'))}  |  ROI {_pct(amz.get('roi'))}")
        if eby:
            print(f"Marge eBay: {_eur(eby.get('margin_eur'))}  |  "
                  f"Marge {_pct(eby.get('margin_pct'))}  |  ROI {_pct(eby.get('roi'))}")
        if res.errors:
            print("Hinweise  : " + " | ".join(res.errors[:3]))

        summary.append((p.name, p.ek_net, res.asin,
                        (res.amazon_offer or {}).get("buy_box_brutto"),
                        amz.get("margin_eur"), eb.get("median_sold")))

    print("=" * 78)
    print("\nZUSAMMENFASSUNG")
    print(f"{'Produkt':<46}{'EK':>10}{'Amz-VK':>10}{'FBA-Marge':>11}")
    for name, ek, asin, vk, marge, _ in summary:
        print(f"{name[:45]:<46}{_eur(ek):>10}{_eur(vk):>10}{_eur(marge):>11}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
