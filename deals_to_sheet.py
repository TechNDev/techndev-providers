#!/usr/bin/env python3
"""
techndev-providers  deals_to_sheet.py  v1.0.0
===============================================
Haengt Watcher-Treffer (gemeinsames Datenmodell aus `mw scan --json` /
`kleinanzeigen_watcher.py scan --json`) an einen Google-Sheets-Tab an —
ein Langzeit-Deal-Log ueber Telegram hinaus, fuer beide Watcher nutzbar.

Nutzung (Pipe):
    mw scan --only-buy --json | python deals_to_sheet.py --sheet <id> --tab Deals
    python kleinanzeigen_watcher.py scan --json | python deals_to_sheet.py --sheet <id>

Oder aus Datei:  python deals_to_sheet.py --sheet <id> --in scan.json

spreadsheet_id faellt auf gsheets_config.json / GSHEET_DEALS_SHEET zurueck.
Append-Modus: vorhandene Zeilen bleiben; Kopfzeile wird einmalig geschrieben.

CHANGELOG
---------
v1.0.0 (2026-06-03)
  - Initiales Release. stdin/Datei -> append_rows (mit Header-Bootstrap).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from gsheets import GSheetsAuthError, GSheetsClient

__version__ = "1.0.0"

# Stabile Spaltenreihenfolge (gemeinsames Watcher-Modell + Log-Zeitstempel).
COLUMNS = ["logged_at", "source", "decision", "title", "price", "margin_eur",
           "margin_pct", "vk_brutto", "lego_set_no", "lego_name", "merchant",
           "url", "skip_reason"]


def _cfg() -> dict:
    p = Path("gsheets_config.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _row(d: dict, ts: str) -> list:
    out = []
    for c in COLUMNS:
        if c == "logged_at":
            out.append(ts)
        else:
            v = d.get(c)
            out.append("" if v is None else v)
    return out


def main(argv=None) -> int:
    cfg = _cfg()
    ap = argparse.ArgumentParser(prog="deals_to_sheet",
                                 description="Watcher-Treffer ins Google Sheet anhaengen.")
    ap.add_argument("--sheet", default=cfg.get("spreadsheet_id")
                    or os.environ.get("GSHEET_DEALS_SHEET", ""), help="spreadsheetId")
    ap.add_argument("--tab", default="Deals", help="Ziel-Tab (Default: Deals)")
    ap.add_argument("--in", dest="infile", default=None, help="JSON-Datei (sonst stdin)")
    ap.add_argument("--version", action="version", version=f"deals_to_sheet v{__version__}")
    args = ap.parse_args(argv)

    if not args.sheet:
        print("FEHLER: --sheet (oder gsheets_config.json / GSHEET_DEALS_SHEET) noetig.", file=sys.stderr)
        return 1

    raw = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    raw = raw.strip()
    items = json.loads(raw) if raw else []
    if not isinstance(items, list):
        items = items.get("items", []) if isinstance(items, dict) else []
    if not items:
        print("Keine Treffer zum Loggen.", file=sys.stderr)
        return 0

    ts = datetime.now().isoformat(timespec="seconds")
    rows = [_row(d, ts) for d in items if isinstance(d, dict)]

    try:
        gs = GSheetsClient(args.sheet)
        gs.ensure_tab(args.tab)
        existing = gs.read_values(f"{args.tab}!A1:A1")
        if not existing:                                   # leerer Tab -> Kopfzeile zuerst
            gs.append_rows(args.tab, [COLUMNS])
        gs.append_rows(args.tab, rows)
    except GSheetsAuthError as e:
        print(f"FEHLER (Auth): {e}", file=sys.stderr)
        return 1
    except Exception as e:                                 # noqa: BLE001
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1

    print(f"  {len(rows)} Treffer -> Tab '{args.tab}' angehaengt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
