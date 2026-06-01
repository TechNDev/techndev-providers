#!/usr/bin/env python3
"""
techndev-providers  enrich_cubegolem_amazon.py  v1.0.0
========================================================
Reichert den cubegolem-Store mit Amazon-Daten (SP-API) an.

Fuer jedes Store-Produkt mit EAN wird via pipelines.arbitrage.evaluate_arbitrage
die ASIN/Buy-Box/BSR + FBA-Marge (und optional eBay) ermittelt und in die
Tabelle amazon_enrichment derselben Store-DB geschrieben.

Resuemierbar: bereits kuerzlich angereicherte Produkte (innerhalb --refresh-days)
werden uebersprungen. Rate-Limiting erledigt amazon_sp intern.

Orchestrierungs-Tool (kein Teil des cubegolem-Pakets) — verdrahtet Provider-Store
+ Arbitrage-Pipeline + reseller_profitability.

Lauf (aus techndev-providers/):
    python enrich_cubegolem_amazon.py --db cubegolem_store.db
    python enrich_cubegolem_amazon.py --section magic-the-gathering --ebay
    python enrich_cubegolem_amazon.py --limit 200            # nur 200 je Aufruf

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_BASE = Path(__file__).resolve().parent
# reseller_profitability liegt als Sibling-Repo daneben
for _sib in (_BASE.parent / "reseller-profitability",):
    if _sib.exists() and str(_sib) not in sys.path:
        sys.path.insert(0, str(_sib))

from cubegolem import CubeGolemStore                       # noqa: E402
from cubegolem._models import now_iso                      # noqa: E402
from pipelines.arbitrage import evaluate_arbitrage         # noqa: E402

_TABLE = """
CREATE TABLE IF NOT EXISTS amazon_enrichment (
    slug            TEXT PRIMARY KEY,
    ean             TEXT,
    ek_net          REAL,
    found           INTEGER,
    asin            TEXT,
    title           TEXT,
    category        TEXT,
    bsr             INTEGER,
    buy_box_brutto  REAL,
    fba_fee_netto   REAL,
    fba_margin_eur  REAL,
    fba_roi         REAL,
    ebay_median     REAL,
    ebay_sold       INTEGER,
    errors          TEXT,
    enriched_at     TEXT
)
"""

_COLS = ["slug", "ean", "ek_net", "found", "asin", "title", "category", "bsr",
         "buy_box_brutto", "fba_fee_netto", "fba_margin_eur", "fba_roi",
         "ebay_median", "ebay_sold", "errors", "enriched_at"]


def _fresh(con, slug, days) -> bool:
    r = con.execute("SELECT enriched_at FROM amazon_enrichment WHERE slug=?",
                    (slug,)).fetchone()
    if not r or not r[0]:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(r[0])) < timedelta(days=days)
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cubegolem-Store mit Amazon-Daten anreichern.")
    ap.add_argument("--db", default="cubegolem_store.db", help="Store-DB")
    ap.add_argument("--section", default=None, help="nur diese Sektion")
    ap.add_argument("--limit", type=int, default=None, help="max. Produkte je Aufruf")
    ap.add_argument("--refresh-days", type=int, default=7,
                    help="kuerzlich Angereicherte ueberspringen (Default 7 Tage)")
    ap.add_argument("--ebay", action="store_true", help="auch eBay abfragen (langsamer)")
    args = ap.parse_args(argv)

    store = CubeGolemStore(args.db)
    con = sqlite3.connect(args.db)
    con.execute(_TABLE)
    con.commit()

    products = [p for p in store.latest(args.section) if p.ean and p.ek_net is not None]
    todo = [p for p in products if not _fresh(con, p.slug, args.refresh_days)]
    if args.limit:
        todo = todo[:args.limit]
    print(f"Produkte mit EAN: {len(products)} | zu tun: {len(todo)} "
          f"(eBay: {'an' if args.ebay else 'aus'})")

    found = 0
    for i, p in enumerate(todo, 1):
        try:
            res = evaluate_arbitrage(ek_netto=p.ek_net, ean=p.ean,
                                     include_ebay=args.ebay)
            ao = res.amazon_offer or {}
            rd = res.to_dict().get("results", {}).get("amazon_fba", {})
            eb = res.ebay_snapshot or {}
            row = {
                "slug": p.slug, "ean": p.ean, "ek_net": p.ek_net,
                "found": 1 if res.asin else 0, "asin": res.asin or "",
                "title": res.title or "", "category": res.category or "",
                "bsr": ao.get("bsr"), "buy_box_brutto": ao.get("buy_box_brutto"),
                "fba_fee_netto": ao.get("fba_fee_netto"),
                "fba_margin_eur": rd.get("margin_eur"), "fba_roi": rd.get("roi"),
                "ebay_median": eb.get("median_sold"), "ebay_sold": eb.get("sold_count"),
                "errors": "; ".join(res.errors)[:300], "enriched_at": now_iso(),
            }
            if res.asin:
                found += 1
        except Exception as e:                               # noqa: BLE001
            row = {c: None for c in _COLS}
            row.update(slug=p.slug, ean=p.ean, ek_net=p.ek_net, found=0,
                       errors=f"{type(e).__name__}: {e}"[:300], enriched_at=now_iso())
        con.execute(
            f"INSERT INTO amazon_enrichment ({','.join(_COLS)}) "
            f"VALUES ({','.join('?'*len(_COLS))}) "
            f"ON CONFLICT(slug) DO UPDATE SET "
            + ",".join(f"{c}=excluded.{c}" for c in _COLS[1:]),
            [row.get(c) for c in _COLS])
        con.commit()
        if i % 10 == 0 or i == len(todo):
            print(f"\r  {i}/{len(todo)}  Treffer: {found}", end="", flush=True)
    print()

    tot = con.execute("SELECT COUNT(*) FROM amazon_enrichment").fetchone()[0]
    hit = con.execute("SELECT COUNT(*) FROM amazon_enrichment WHERE found=1").fetchone()[0]
    con.close()
    print(f"amazon_enrichment gesamt: {tot} | mit ASIN: {hit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
