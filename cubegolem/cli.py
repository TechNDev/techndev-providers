#!/usr/bin/env python3
"""
techndev-providers  cubegolem/cli.py  v1.0.0
==============================================
Duenne CLI fuer den cubegolem-Provider: Sektion(en) scrapen → CSV.
Kein Teil der oeffentlichen API — Convenience-Entry fuer manuelle Laeufe
und als Referenz-Consumer (Config-Auto-Provisioning + Cookie-Handling).

Lauf (aus techndev-providers/):
    python -m cubegolem.cli magic-the-gathering
    python -m cubegolem.cli --all --out ./export
    python -m cubegolem.cli --list

Beim ersten Start wird cubegolem_config.json aus einem Template angelegt;
dort die aus dem eingeloggten Browser exportierte Session-Cookie eintragen.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. argparse (--?/--version), Config-Auto-Provisioning,
    CSV-Export (;-getrennt, UTF-8-BOM), --list / --all / --no-prices.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from ._auth   import SessionExpiredError
from .scraper import CubeGolemProvider
from .store   import CubeGolemStore

CONFIG_PATH = Path("cubegolem_config.json")
CONFIG_TEMPLATE = {
    "session_cookie": "",
    "_hinweis": (
        "session_cookie: im eingeloggten Browser DevTools -> Application -> "
        "Cookies -> cubegolem.de -> alle als 'name=value; name2=value2' "
        "kopieren. Preise sind nur mit gueltiger Session sichtbar."
    ),
}

# CSV-Spalten (Reihenfolge wie die manuell erstellte MTG-CSV + Zusatzfelder)
CSV_FIELDS = [
    "section", "name", "ek_net", "base_net", "discount_pct",
    "release_date", "order_deadline", "in_stock", "ean", "sku",
    "manufacturer", "category", "image_url", "url",
]


def _load_config() -> dict:
    """Config laden; beim ersten Start Template anlegen und beenden."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(CONFIG_TEMPLATE, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Config-Template angelegt: {CONFIG_PATH.resolve()}")
        print("Bitte session_cookie eintragen und erneut starten.")
        sys.exit(2)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _write_csv(path: Path, products) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CSV_FIELDS)
        for p in products:
            d = p.to_dict()
            w.writerow([d.get(k, "") for k in CSV_FIELDS])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="cubegolem",
        description="cubegolem.de Scraper — Sektionen → CSV (Haendler-EK, netto).",
        add_help=False,
    )
    ap.add_argument("section", nargs="?", help="Sektions-Slug, z.B. magic-the-gathering")
    ap.add_argument("--all", action="store_true", help="alle Hauptkategorien scrapen")
    ap.add_argument("--list", action="store_true", help="nur Hauptkategorien auflisten")
    ap.add_argument("--out", default=".", help="Ausgabe-Verzeichnis (Default: .)")
    ap.add_argument("--no-prices", action="store_true",
                    help="ohne Detailseiten (nur Grid-Stammdaten + Datum)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Sektionen ueberspringen, deren CSV schon existiert (Resume)")
    ap.add_argument("--db", metavar="PFAD",
                    help="Lauf zusaetzlich in SQLite-Store schreiben (mit Historie)")
    ap.add_argument("--no-csv", action="store_true",
                    help="keine CSV schreiben (nur Store, mit --db)")
    ap.add_argument("--version", action="version", version=f"cubegolem v{__version__}")
    ap.add_argument("-h", "--help", "--?", action="help", help="diese Hilfe")
    args = ap.parse_args(argv)

    print(f"cubegolem v{__version__}")
    cfg = _load_config()
    prov = CubeGolemProvider(session_cookie=cfg.get("session_cookie", ""))

    if args.list:
        for s in prov.list_sections():
            print(f"  {s.slug:<34} {s.name}  ({len(s.subcategories)} Unterkat.)")
        return 0

    if args.all:
        targets = [s.slug for s in prov.list_sections()]
    elif args.section:
        targets = [args.section]
    else:
        ap.error("Sektions-Slug, --all oder --list angeben.")
        return 2

    out_dir = Path(args.out)
    store = CubeGolemStore(args.db) if args.db else None
    try:
        for slug in targets:
            csv_path = out_dir / f"cubegolem_{slug}.csv"
            if args.skip_existing and not store and csv_path.exists():
                print(f"  {slug}: uebersprungen (CSV existiert)")
                continue
            def _prog(done, total, s, _slug=slug):
                print(f"\r  {_slug}: {done}/{total}", end="", flush=True)
            products = prov.get_section(
                slug, with_prices=not args.no_prices, progress=_prog)
            print()
            if not args.no_csv:
                _write_csv(csv_path, products)
            msg = f"  -> {len(products)} Produkte"
            if store:
                st = store.record_run(products)
                msg += f"  [Store: {st['new']} neu, {st['changed']} geaendert]"
            if not args.no_csv:
                msg += f"  {csv_path}"
            print(msg)
    except SessionExpiredError as e:
        print(f"\nFEHLER: {e}")
        return 1
    if store:
        s = store.stats()
        print(f"\nStore {args.db}: {s['products']} Produkte, "
              f"{s['history_rows']} History-Zeilen, {s['runs']} Laeufe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
