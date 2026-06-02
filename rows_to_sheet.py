#!/usr/bin/env python3
"""
techndev-providers  rows_to_sheet.py  v1.0.0
==============================================
Generisches Export-Tool: list[dict] (JSON via stdin) -> Google-Sheets-Tab.
Format-agnostisch — jede Komponente (MarginPilot, CLIs) kann ihre Tabellen so
in ein Sheet schreiben, ohne CSV-Dialekte abzustimmen.

Nutzung:
    echo '[{"ean":"...","preis":1.23}]' | python rows_to_sheet.py --sheet <id> --tab Export
    python product_catalog.py ... --json | python rows_to_sheet.py --sheet <id> --tab X --append

--append haengt an (ohne Kopfzeile, falls Tab schon Daten hat); sonst wird der
Tab geleert + neu geschrieben. spreadsheet_id faellt auf gsheets_config.json /
GSHEET_EXPORT_SHEET zurueck.

CHANGELOG
---------
v1.0.0 (2026-06-03)
  - Initiales Release. stdin JSON list[dict] -> write_table/append_rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gsheets import GSheetsAuthError, GSheetsClient

__version__ = "1.0.0"


def _cfg() -> dict:
    p = Path("gsheets_config.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main(argv=None) -> int:
    cfg = _cfg()
    ap = argparse.ArgumentParser(prog="rows_to_sheet",
                                 description="JSON-Zeilen (stdin) -> Google-Sheets-Tab.")
    ap.add_argument("--sheet", default=cfg.get("spreadsheet_id")
                    or os.environ.get("GSHEET_EXPORT_SHEET", ""), help="spreadsheetId")
    ap.add_argument("--tab", required=True, help="Ziel-Tab")
    ap.add_argument("--append", action="store_true", help="anhaengen statt ueberschreiben")
    ap.add_argument("--in", dest="infile", default=None, help="JSON-Datei (sonst stdin)")
    ap.add_argument("--version", action="version", version=f"rows_to_sheet v{__version__}")
    args = ap.parse_args(argv)

    if not args.sheet:
        print("FEHLER: --sheet (oder gsheets_config.json / GSHEET_EXPORT_SHEET) noetig.", file=sys.stderr)
        return 1

    raw = (Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()).strip()
    rows = json.loads(raw) if raw else []
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("items") or []
    if not isinstance(rows, list):
        print("FEHLER: Eingabe ist keine JSON-Liste.", file=sys.stderr)
        return 1

    try:
        gs = GSheetsClient(args.sheet)
        if args.append:
            gs.ensure_tab(args.tab)
            if not gs.read_values(f"{args.tab}!A1:A1") and rows:
                gs.append_rows(args.tab, [list(rows[0].keys())])   # Kopfzeile einmalig
            gs.append_rows(args.tab, rows)
        else:
            gs.write_table(args.tab, rows)
    except GSheetsAuthError as e:
        print(f"FEHLER (Auth): {e}", file=sys.stderr)
        return 1
    except Exception as e:                                 # noqa: BLE001
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1

    print(json.dumps({"written": len(rows), "tab": args.tab,
                      "mode": "append" if args.append else "overwrite"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
