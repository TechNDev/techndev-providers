#!/usr/bin/env python3
"""
techndev-providers  gsheets/cli.py  v1.0.0
============================================
CLI fuer den gsheets-Connector — fuer manuelle Laeufe und damit Node-/andere
Komponenten ihn per Subprozess konsumieren koennen (CSV rein/raus).

Beispiele (aus techndev-providers/):
    python -m gsheets.cli tabs   --sheet <id>
    python -m gsheets.cli import --sheet <id> --tab Preise --out out.csv
    python -m gsheets.cli export --sheet <id> --tab Preise --csv in.csv
    python -m gsheets.cli export --sheet <id> --tab Preise --csv in.csv --append

Standard-Sheet/-Tab koennen via gsheets_config.json (gitignored) vorgegeben
werden: { "spreadsheet_id": "...", "tab": "..." }.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. tabs / import / export (CSV, ;-getrennt, UTF-8-BOM).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from ._auth  import GSheetsAuthError
from .client import GSheetsClient

CONFIG_PATH = Path("gsheets_config.json")


def _cfg() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _write_csv(path: str, rows: list[dict]) -> int:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main(argv=None) -> int:
    cfg = _cfg()
    ap = argparse.ArgumentParser(prog="gsheets", description="Google-Sheets-Connector CLI.")
    ap.add_argument("cmd", choices=["tabs", "import", "export"])
    ap.add_argument("--sheet", default=cfg.get("spreadsheet_id", ""), help="spreadsheetId")
    ap.add_argument("--tab", default=cfg.get("tab", ""), help="Tab-Name")
    ap.add_argument("--csv", help="Eingabe-CSV (export)")
    ap.add_argument("--out", help="Ausgabe-CSV (import)")
    ap.add_argument("--append", action="store_true", help="anhaengen statt ueberschreiben")
    ap.add_argument("--version", action="version", version=f"gsheets v{__version__}")
    args = ap.parse_args(argv)

    print(f"gsheets v{__version__}")
    try:
        gs = GSheetsClient(args.sheet)
    except GSheetsAuthError as e:
        print(f"FEHLER (Auth): {e}")
        return 1

    try:
        if args.cmd == "tabs":
            for t in gs.list_tabs():
                print(f"  {t}")
            return 0

        if not args.tab:
            ap.error("--tab erforderlich fuer import/export")

        if args.cmd == "import":
            rows = gs.read_table(args.tab)
            if args.out:
                n = _write_csv(args.out, rows)
                print(f"  {n} Zeilen -> {args.out}")
            else:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0

        if args.cmd == "export":
            if not args.csv:
                ap.error("--csv erforderlich fuer export")
            rows = _read_csv(args.csv)
            if args.append:
                gs.append_rows(args.tab, rows)
            else:
                gs.write_table(args.tab, rows)
            print(f"  {len(rows)} Zeilen -> Tab '{args.tab}'"
                  + (" (angehaengt)" if args.append else ""))
            return 0
    except Exception as e:                                   # noqa: BLE001
        print(f"FEHLER: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
