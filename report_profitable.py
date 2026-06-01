#!/usr/bin/env python3
"""
techndev-providers  report_profitable.py  v1.0.0
==================================================
Erzeugt aus dem angereicherten cubegolem-Store einen Report der profitablen
Artikel (positive FBA-Marge bei cubegolem-EK), sortiert nach Marge.

Lauf (aus techndev-providers/):
    python report_profitable.py
    python report_profitable.py --min-margin 5 --max-bsr 200000
    python report_profitable.py --section arcane-tinmen --out report.csv

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. CSV (Excel-DE: ;-getrennt, Komma-Dezimal, UTF-8-BOM).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


def de(x, nd=2) -> str:
    """Float -> deutsche Dezimaldarstellung (Komma), '' bei None."""
    if x is None:
        return ""
    return f"{x:.{nd}f}".replace(".", ",")


FIELDS = ["rang", "section", "name", "ek_net", "amazon_vk", "fba_marge_eur",
          "roi_pct", "bsr", "fba_sellers_hinweis", "ean", "sku", "asin",
          "cubegolem_url"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report profitabler cubegolem-Artikel (FBA).")
    ap.add_argument("--db", default="cubegolem_store.db")
    ap.add_argument("--out", default="export/cubegolem_profitable_fba.csv")
    ap.add_argument("--min-margin", type=float, default=0.0,
                    help="Mindest-FBA-Marge in EUR (Default 0)")
    ap.add_argument("--max-bsr", type=int, default=None,
                    help="nur Artikel mit BSR <= Wert (0/None bleibt drin)")
    ap.add_argument("--section", default=None)
    args = ap.parse_args(argv)

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    where = ["a.found=1", "a.fba_margin_eur > ?"]
    params: list = [args.min_margin]
    if args.section:
        where.append("p.section = ?"); params.append(args.section)
    if args.max_bsr:
        # BSR 0 = unbekannt; mit reinnehmen (kann Nische sein)
        where.append("(a.bsr IS NULL OR a.bsr=0 OR a.bsr <= ?)")
        params.append(args.max_bsr)

    rows = c.execute(f"""
        SELECT p.section, a.title, a.ek_net, a.buy_box_brutto, a.fba_margin_eur,
               a.fba_roi, a.bsr, a.ean, p.sku, a.asin, p.url
        FROM amazon_enrichment a JOIN products p ON p.slug=a.slug
        WHERE {' AND '.join(where)}
        ORDER BY a.fba_margin_eur DESC
    """, params).fetchall()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(FIELDS)
        for i, r in enumerate(rows, 1):
            w.writerow([
                i, r["section"], r["title"], de(r["ek_net"]),
                de(r["buy_box_brutto"]), de(r["fba_margin_eur"]),
                de((r["fba_roi"] or 0) * 100, 0), r["bsr"] or "",
                "", r["ean"], r["sku"], r["asin"], r["url"],
            ])

    total = c.execute("SELECT COUNT(*) FROM amazon_enrichment WHERE fba_margin_eur>0").fetchone()[0]
    summe = sum(r["fba_margin_eur"] for r in rows)
    c.close()
    print(f"Profitabel gesamt: {total}")
    print(f"Im Report (nach Filtern): {len(rows)}")
    print(f"Summe FBA-Marge im Report: {de(summe)} EUR")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
