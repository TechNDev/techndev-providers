"""
techndev-providers  gsheets  v1.0.0
=====================================
Zentraler Google-Sheets-Connector (REST v4 via urllib, keine Google-SDKs).
Bidirektional: jede Komponente kann exportieren UND importieren.

Auth: nutzt das bestehende OAuth-Setup aus combo-shorts-video weiter
(client_secret*.json + youtube-token.json mit spreadsheets-Scope).

Exports:
  GSheetsClient     — read_table/write_table/append_rows/read_values/...
  GSheetsAuthError  — Auth-/Credential-Fehler

Schnellstart:
    from gsheets import GSheetsClient
    gs = GSheetsClient("13xRjy...")           # spreadsheetId
    gs.write_table("Preise", produkte)        # EXPORT (list[dict])
    rows = gs.read_table("Preise")            # IMPORT -> list[dict]

CLI (auch fuer Node via Subprozess):
    python -m gsheets.cli export --sheet <id> --tab Preise --csv in.csv
    python -m gsheets.cli import --sheet <id> --tab Preise --out out.csv
    python -m gsheets.cli tabs   --sheet <id>
"""
from ._auth   import GSheetsAuthError
from .client  import GSheetsClient

__all__ = ["GSheetsClient", "GSheetsAuthError"]
__version__ = "1.0.0"
