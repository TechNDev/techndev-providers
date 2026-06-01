#!/usr/bin/env python3
"""
techndev-providers  cubegolem/_regression_mtg.py  v1.0.0
==========================================================
Live-Regressionstest: get_section('magic-the-gathering') gegen die
manuell validierte CSV (46 Produkte). Braucht gueltigen Cookie in
cubegolem_config.json.

Lauf (aus techndev-providers/):
    python -m cubegolem._regression_mtg
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .scraper import CubeGolemProvider

CONFIG = Path(__file__).resolve().parent.parent / "cubegolem_config.json"
MANUAL_CSV = Path(r"C:\Claude_FS\Code\cubegolem_magic-the-gathering_produkte.csv")


def _money(s: str) -> float | None:
    m = re.search(r"([\d.]+),(\d{2})", s or "")
    return float(m.group(1).replace(".", "") + "." + m.group(2)) if m else None


def _date(s: str) -> str | None:
    s = (s or "").strip()
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _slug(url: str) -> str:
    m = re.search(r"/product/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def load_expected() -> dict:
    exp = {}
    with MANUAL_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader((l for l in f if not l.startswith("#")), delimiter=";"):
            slug = _slug(row.get("url", ""))
            if not slug:
                continue
            exp[slug] = {
                "ek":   _money(row.get("ek_preis_netto", "")),
                "base": _money(row.get("basispreis_netto", "")),
                "release": _date(row.get("erscheinungsdatum", "")),  # 'lagernd' -> None
                "image": (row.get("bild_url") or "").strip(),
            }
    return exp


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    prov = CubeGolemProvider(session_cookie=cfg.get("session_cookie", ""))

    expected = load_expected()
    print(f"Soll: {len(expected)} Produkte aus der manuellen CSV.")
    print("Lade live …")
    done = {"n": 0}
    def prog(i, total, slug):
        done["n"] = i
        print(f"\r  {i}/{total}", end="", flush=True)
    products = prov.get_section("magic-the-gathering", progress=prog)
    print(f"\nLive: {len(products)} Produkte.")

    live = {p.slug: p for p in products}
    fields = ("ek", "base", "release", "image")
    mism = {f: 0 for f in fields}
    missing, extra = [], []
    rows = []

    for slug, e in expected.items():
        p = live.get(slug)
        if p is None:
            missing.append(slug)
            continue
        diffs = []
        if p.ek_net != e["ek"]:        diffs.append(f"ek {p.ek_net}!={e['ek']}"); mism["ek"]   += 1
        if p.base_net != e["base"]:    diffs.append(f"base {p.base_net}!={e['base']}"); mism["base"] += 1
        if p.release_date != e["release"]: diffs.append(f"rel {p.release_date}!={e['release']}"); mism["release"] += 1
        if (p.image_url or "") != e["image"]: diffs.append("img"); mism["image"] += 1
        if diffs:
            rows.append(f"  ~ {slug}: " + "; ".join(diffs))

    for slug in live:
        if slug not in expected:
            extra.append(slug)

    print("\n=== ERGEBNIS ===")
    print(f"Anzahl live vs soll: {len(products)} / {len(expected)}")
    print(f"Fehlend (im Soll, nicht live): {len(missing)}  {missing or ''}")
    print(f"Zusaetzlich (live, nicht im Soll): {len(extra)}  {extra or ''}")
    for f in fields:
        print(f"Abweichungen {f:8}: {mism[f]}")
    if rows:
        print("\nDetails:")
        print("\n".join(rows))

    ok = (not missing and not extra and all(v == 0 for v in mism.values()))
    print("\n" + ("REGRESSION OK — Provider == manuelle CSV." if ok
                  else "ABWEICHUNGEN gefunden (s.o.)."))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
