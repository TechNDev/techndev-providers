#!/usr/bin/env python3
"""
techndev-providers  cubegolem/import_csv.py  v1.0.0
=====================================================
Importiert cubegolem-Export-CSVs in einen CubeGolemStore (eine Lauf-Aufnahme).
Nuetzlich, um aus vorhandenen CSV-Snapshots eine DB-Baseline zu erzeugen.

Lauf (aus techndev-providers/):
    python -m cubegolem.import_csv <db-pfad> <csv-glob> [run_ts]
Beispiel:
    python -m cubegolem.import_csv cubegolem_store.db "export/cubegolem_*.csv"

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. CSV (CLI-Format) -> Product -> store.record_run().
"""
from __future__ import annotations

import csv
import glob
import sys

from ._models import Product, now_iso
from .store   import CubeGolemStore


def _f(x):
    x = (x or "").strip()
    return float(x) if x not in ("", "None") else None


def load_csv(path: str) -> list[Product]:
    out: list[Product] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            url = r.get("url", "")
            out.append(Product(
                section=r.get("section", ""),
                slug=url.rsplit("/product/", 1)[-1],
                name=r.get("name", ""), url=url,
                ek_net=_f(r.get("ek_net")), base_net=_f(r.get("base_net")),
                discount_pct=_f(r.get("discount_pct")),
                release_date=r.get("release_date") or None,
                order_deadline=r.get("order_deadline") or None,
                in_stock=(r.get("in_stock") == "True"),
                category=r.get("category") or None,
                manufacturer=r.get("manufacturer") or None,
                ean=r.get("ean") or None, sku=r.get("sku") or None,
                image_url=r.get("image_url") or None,
                fetched_at=now_iso(),
            ))
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print("Nutzung: python -m cubegolem.import_csv <db> <csv-glob> [run_ts]")
        return 2
    db, pattern = argv[0], argv[1]
    run_ts = argv[2] if len(argv) > 2 else now_iso()

    products: list[Product] = []
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"Keine CSVs fuer Muster: {pattern}")
        return 1
    for p in files:
        rows = load_csv(p)
        products += rows
        print(f"  {p}: {len(rows)}")

    store = CubeGolemStore(db)
    res = store.record_run(products, run_ts=run_ts)
    print(f"\nLauf aufgenommen: {res}")
    print(f"Store-Stats: {store.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
