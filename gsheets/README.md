# gsheets — Provider

Zentraler **Google-Sheets-Connector** für alle TechNDev-Komponenten — **bidirektional** (Export *und* Import). Teil von `techndev-providers`, Nutzung wie die übrigen Provider.

Pure `urllib` + Google Sheets REST v4 — **keine Google-SDK-Abhängigkeit**. Auth nutzt das **bestehende OAuth-Setup** aus `combo-shorts-video` weiter (`client_secret*.json` + `youtube-token.json` mit `spreadsheets`-Scope).

## Schnellstart (Python)

```python
from gsheets import GSheetsClient

gs = GSheetsClient("13xRjy...")               # spreadsheetId

# EXPORT — Komponente schreibt ihre Daten ins Sheet
gs.write_table("Preise", produkte)            # produkte = list[dict] (Keys = Spalten)
gs.append_rows("Log", [{"ts": ..., "msg": ...}])

# IMPORT — Komponente liest aus dem Sheet
rows = gs.read_table("Preise")                # -> list[dict], Zahlen bleiben Zahlen
matrix = gs.read_values("Preise!A1:C10")      # rohe Zellmatrix

gs.list_tabs(); gs.ensure_tab("Neu"); gs.delete_tab("Alt")
```

`write_table` legt den Tab bei Bedarf an, leert ihn und schreibt ab A1.
Zeilen sind **`list[dict]`** (Spalten = Keys) oder **`list[list]`** (rohe Werte).

## CLI (auch für Node/andere Komponenten via Subprozess)

```bash
python -m gsheets.cli tabs   --sheet <id>
python -m gsheets.cli import --sheet <id> --tab Preise --out out.csv
python -m gsheets.cli export --sheet <id> --tab Preise --csv in.csv
python -m gsheets.cli export --sheet <id> --tab Log    --csv neu.csv --append
```

Standardwerte via `gsheets_config.json` (gitignored): `{ "spreadsheet_id": "...", "tab": "..." }`.

## Auth

Wiederverwendet `combo-shorts-video/client_secret*.json` + `youtube-token.json`.
Auflösung je Datei: expliziter Pfad → Env `GSHEETS_CLIENT_SECRET` / `GSHEETS_TOKEN` → Auto-Discovery (`combo-shorts-video/` in übergeordneten Verzeichnissen).

> Das Token **muss** den `spreadsheets`-Scope haben. Fehlt er, `youtube-auth.mjs` in combo-shorts mit erweitertem Scope einmal neu ausführen. Bei Auth-Fehlern: `GSheetsAuthError`.

## Hinweise
- **Locale:** Beim Schreiben werden Zahlen als Zahlen abgelegt. Lesen erfolgt mit `UNFORMATTED_VALUE` → keine Locale-Formatierung (1.5 bleibt 1.5, nicht „1,5").
- **Access-Token** wird per `refresh_token` geholt und modulweit bis kurz vor Ablauf gecacht; 401 löst genau einen Refresh-Retry aus.
- Keine neuen Dependencies — läuft mit der vorhandenen Python-Standardbibliothek.
